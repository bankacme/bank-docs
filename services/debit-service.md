# `debit-service`

## 1. Resumen

| Campo | Valor |
|---|---|
| Propósito | Dueño de las tarjetas de débito y de los pagos hechos con ellas. Publica cuál es la cuenta principal de cada tarjeta |
| Bounded context | Tarjetas de débito |
| Fase | P3 (servicio nuevo: **sin llamadas REST a otros servicios**, todo por Kafka) |
| Puerto | 8087 |
| Base de datos | MongoDB: `debit_cards`, `debit_payments` + read models |
| Depende de | Eventos de `customer-service`, `account-service` y `credit-service`. Pide los retiros a `transaction-service` por Kafka |

## 2. Responsabilidades

**Hace:**
- CRUD de tarjetas de débito asociadas a las cuentas del cliente.
- Gestionar las cuentas asociadas y la **cuenta principal** de cada tarjeta.
- Registrar **pagos con tarjeta**: valida la tarjeta, pide el retiro a `transaction-service` y guarda el resultado.
- Consultar los pagos de una tarjeta (base del reporte de "últimos 10 movimientos" de débito).
- Publicar el estado de la tarjeta para que `yanki-service` sepa cuál es la cuenta principal.
- Bloquear la emisión de tarjetas a clientes con deuda vencida (RF-60).

**No hace:**
- Saldos ni reglas de movimiento (`account-service`).
- Ejecutar el retiro ni guardar el historial de la cuenta (`transaction-service`).
- Pagos ni recargas de Yanki: los pide `yanki-service` a `transaction-service`; aquí solo se publica el estado de la tarjeta.
- Validar CVV, PIN ni autorización de comercios (fuera de alcance del demo).

## 3. Modelo de dominio (DDD)

### 3.1 Aggregates y entidades
| Elemento | Tipo | Descripción |
|---|---|---|
| `DebitCard` | Aggregate root | Tarjeta con sus cuentas asociadas, cuenta principal, vencimiento y estado |
| `DebitPayment` | Aggregate root | Un pago con tarjeta y su estado (`PENDING` → `COMPLETED`/`FAILED`). Es el registro de movimientos de la tarjeta y el estado de la saga de un solo paso |

Atributos de `DebitCard`: `id`, `customerId`, `cardNumber`, `expiryDate`, `linkedAccounts` (VO con la cuenta principal), `status`, `version`, `createdAt`, `updatedAt`.
Comportamiento: `issue(...)`, `replaceAccounts(accounts, main)`, `linkAccount(ref)`, `unlinkAccount(accountId)`, `changeMainAccount(accountId)`, `onAccountClosed(accountId)`, `close()`, `assertUsable(date)`.

Atributos de `DebitPayment`: `id`, `operationId`, `cardId`, `customerId`, `accountId` (cuenta de cargo), `amount`, `description`, `status`, `failureReason`, `transactionId`, `fee` (comisión de la cuenta, opcional), `resultingBalance`, `requestedAt`, `completedAt`.
Comportamiento: `request(...)`, `complete(transactionId, resultingBalance)`, `fail(reason)`.

### 3.2 Value objects
| VO | Campos | Validaciones |
|---|---|---|
| `DebitCardId`, `DebitPaymentId`, `OperationId`, `CustomerId`, `AccountId` | `value` | No vacío |
| `CardNumber` | `value` | 16 dígitos ficticios generados; se expone enmascarado (`**** 1234`) |
| `ExpiryDate` | `yearMonth` | 5 años desde la emisión; `isExpired(date)` |
| `LinkedAccounts` | `accountIds` (ordenadas), `mainAccountId` | No vacía, sin duplicados, la principal está incluida |
| `Money` | `amount` (BigDecimal, 2 decimales), `currency` | Monto > 0. Solo `PEN` |
| `FailureReason` | `code`, `message` | Código no vacío |
| `AccountSnapshot` | `accountId`, `customerId`, `type`, `status` | Dato de lectura, no es aggregate |
| `CustomerSnapshot` | `customerId`, `status`, `updatedAt` | Dato de lectura, no es aggregate (ya no guarda el `type`: no se usa) |

### 3.3 Enums
| Enum | Valores |
|---|---|
| `DebitCardStatus` | `ACTIVE`, `CLOSED` (vencida se calcula por fecha) |
| `DebitPaymentStatus` | `PENDING`, `COMPLETED`, `FAILED` |
| `AccountType` | `SAVINGS`, `CHECKING`, `FIXED_TERM` (solo en el snapshot) |

### 3.4 Reglas de negocio e invariantes
| # | Regla | Dónde se aplica |
|---|---|---|
| 1 | El cliente debe existir y estar `ACTIVE` | `IssuancePolicy` (dato del read model) |
| 2 | Cliente con **deuda vencida** no puede adquirir una tarjeta (RF-60) | `IssuancePolicy` (read model `overdue_customers`) |
| 3 | La tarjeta se emite con al menos una cuenta; todas del mismo cliente, `ACTIVE` y de tipo `SAVINGS` o `CHECKING` (plazo fijo no admite pagos frecuentes) | `IssuancePolicy` |
| 4 | Si no se indica la cuenta principal, es la primera cuenta | `LinkedAccounts` |
| 5 | Siempre hay al menos una cuenta y la principal está entre las asociadas | `LinkedAccounts` |
| 6 | Asociar una cuenta: del mismo cliente, `ACTIVE`, `SAVINGS`/`CHECKING`, aún no asociada | `LinkedAccountsPolicy` |
| 7 | Desasociar: no se puede quitar la cuenta principal ni la última cuenta | `DebitCard.unlinkAccount` |
| 8 | Cambiar la principal: debe estar entre las asociadas | `DebitCard.changeMainAccount` |
| 9 | Si se cierra una cuenta asociada: se desasocia; si era la principal pasa a serlo la siguiente; si no queda ninguna, la tarjeta se cierra | `DebitCard.onAccountClosed` |
| 10 | Una tarjeta cerrada o vencida no admite pagos ni cambios | `DebitCard.assertUsable` |
| 11 | Pago: tarjeta usable, cliente `ACTIVE`, monto > 0. Se carga a la cuenta principal, o a otra asociada si se indica | `PayWithDebitCardUseCase` |
| 12 | Idempotencia: una misma `operationId` no genera otro pago; repetirla devuelve el original (con otros datos: 409 `OPERATION_ID_REUSED`). Se evalúa **antes** que la validez de la tarjeta. `operationId` de 8 a 56 caracteres | Caso de uso + índice único |
| 13 | El pago pasa a `COMPLETED` cuando `transaction-service` registra el retiro, o a `FAILED` con el motivo (`INSUFFICIENT_FUNDS`, `MONTHLY_LIMIT_EXCEEDED`, `ACCOUNT_INACTIVE`…) | `DebitPayment.complete/fail` |
| 14 | Un pago `PENDING` de más de N minutos se reenvía con la misma `operationId` (idempotente aguas abajo), hasta `debit.recovery.max-attempts` veces | `RecoverPendingPaymentsUseCase` |
| 15 | Cierre (baja lógica) solo si no hay pagos `PENDING` | `CloseDebitCardUseCase` |
| 16 | Las consultas devuelven los pagos más recientes primero; `size` máximo 100 | Caso de uso |
| 17 | `CUSTOMER` solo ve y usa sus propias tarjetas | Caso de uso (por `customerId`) |

### 3.5 Domain services
| Servicio | Responsabilidad |
|---|---|
| `IssuancePolicy` | Valida las reglas 1, 2, 3 y 6. Puro: recibe los snapshots ya obtenidos. Cadena de validaciones (**Chain of Responsibility**) |
| `CardNumberGenerator` | Genera el número ficticio de tarjeta |

### 3.6 Eventos de dominio
| Evento | Cuándo | Datos mínimos |
|---|---|---|
| `DebitCardIssued` | Al emitir | Estado completo: `cardId`, `customerId`, `maskedNumber`, `accountIds`, `mainAccountId`, `expiryDate`, `status`, `updatedAt` |
| `DebitCardUpdated` | Cambian las cuentas o la principal | Estado completo: `cardId`, `customerId`, `maskedNumber`, `accountIds`, `mainAccountId`, `expiryDate`, `status`, `updatedAt` |
| `DebitCardClosed` | Al cerrar | Estado completo con `status = CLOSED` |
| `DebitPaymentRequested` | Pago creado `PENDING` | `operationId`, `paymentId`, `cardId`, `customerId`, `accountId`, `amount`, `description` |
| `DebitPaymentCompleted` | Pago `COMPLETED` | `paymentId`, `cardId`, `customerId`, `accountId`, `amount`, `fee`, `description`, `resultingBalance`, `occurredAt` |
| `DebitPaymentFailed` | Pago `FAILED` | `paymentId`, `cardId`, `reasonCode` |

Los eventos de tarjeta llevan siempre la cuenta principal, porque `yanki-service` la necesita.

## 4. Casos de uso y puertos

### 4.1 Puertos de entrada (casos de uso)
| Caso de uso | Retorno | Descripción |
|---|---|---|
| `IssueDebitCardUseCase` | `Single<DebitCard>` | Aplica `IssuancePolicy`, genera número y vencimiento, guarda |
| `FindDebitCardUseCase` / `FindDebitCardsUseCase` | `Single` / `Flowable` | Por id; lista con `customerId`, `status` |
| `UpdateDebitCardUseCase` | `Single<DebitCard>` | Reemplaza el conjunto de cuentas y la principal |
| `LinkAccountUseCase` / `UnlinkAccountUseCase` | `Single<DebitCard>` | Asocia o desasocia una cuenta |
| `ChangeMainAccountUseCase` | `Single<DebitCard>` | Cambia la cuenta principal |
| `CloseDebitCardUseCase` | `Completable` | Baja lógica |
| `PayWithDebitCardUseCase` | `Single<DebitPayment>` | Crea el pago `PENDING`, pide el retiro y espera el resultado un máximo de 1,5 s |
| `FindDebitPaymentUseCase` | `Single<DebitPayment>` | Estado de un pago |
| `FindCardPaymentsUseCase` | `Single<PageView<DebitPayment>>` | Pagos de una tarjeta con rango de fechas y página |
| `HandleMovementResultUseCase` | `Completable` | Recibe el resultado del retiro y completa o falla el pago |
| `HandleAccountClosedUseCase` | `Completable` | Aplica la regla 9 |
| `RecoverPendingPaymentsUseCase` | `Single<RecoveryResult>` | Reenvía pagos pendientes |

### 4.2 Puertos de salida
| Puerto | Métodos | Adaptador |
|---|---|---|
| `DebitCardRepositoryPort` | `save`, `findById`, `findAll(filters)`, `findByAccount(accountId)` | Mongo |
| `DebitPaymentRepositoryPort` | `save`, `findById`, `findByOperationId`, `findByCard(range, page)`, `findPendingOlderThan(instant)` | Mongo |
| `CustomerLookupPort` | `findById` → `CustomerSnapshot` | Read model (Mongo) |
| `AccountLookupPort` | `findById`, `findByIds` → `AccountSnapshot` | Read model (Mongo) |
| `OverdueDebtPort` | `hasOverdueDebt(customerId)` | Read model (Mongo) |
| `MovementRequestPort` | `request(paymentRequest)` → `Completable` | Kafka (`debit.payment.requested`) |
| `PaymentResultAwaiterPort` | `await(paymentId, timeout)` → `Maybe<PaymentOutcome>` | En memoria (se completa cuando llega el resultado) |
| `DebitEventPublisherPort` | `publish(event)` | Kafka |

Todos los puertos tienen adaptador desde el inicio: no existe versión REST porque es un servicio nuevo de P3.

## 5. API (contrato OpenAPI)

Base: `/api/v1`. Roles aplican cuando `security.enabled=true`.

| Método | Ruta | Descripción | Roles | Éxito | Errores |
|---|---|---|---|---|---|
| `POST` | `/debit-cards` | Emitir (`customerId`, `accountIds`, `mainAccountId` opcional) | `ADMIN`, `TELLER`, `CUSTOMER` (para sí) | 201 | 400, 422 |
| `GET` | `/debit-cards` | Listar (`customerId`, `status`) | `ADMIN`, `TELLER`, `CUSTOMER` (las suyas) | 200 | — |
| `GET` | `/debit-cards/{id}` | Obtener | `ADMIN`, `TELLER`, `CUSTOMER` (suya) | 200 | 404 |
| `PUT` | `/debit-cards/{id}` | Reemplazar cuentas y principal | `ADMIN`, `TELLER`, `CUSTOMER` (suya) | 200 | 400, 404, 422 |
| `DELETE` | `/debit-cards/{id}` | Baja lógica | `ADMIN`, `TELLER`, `CUSTOMER` (suya) | 204 | 404, 422 |
| `POST` | `/debit-cards/{id}/accounts` | Asociar una cuenta (`accountId`) | `ADMIN`, `TELLER`, `CUSTOMER` (suya) | 200 | 404, 422 |
| `DELETE` | `/debit-cards/{id}/accounts/{accountId}` | Desasociar una cuenta | `ADMIN`, `TELLER`, `CUSTOMER` (suya) | 200 | 404, 422 |
| `PUT` | `/debit-cards/{id}/main-account` | Cambiar la cuenta principal (`accountId`) | `ADMIN`, `TELLER`, `CUSTOMER` (suya) | 200 | 404, 422 |
| `POST` | `/debit-cards/{id}/payments` | Pagar (`operationId`, `amount`, `description`, `accountId` opcional) | `ADMIN`, `TELLER`, `CUSTOMER` (suya) | 201 (completado) o 202 (en proceso); repetición de un pago completado: 200 | 400, 404, 409, 422 |
| `GET` | `/debit-cards/{id}/payments` | Pagos de la tarjeta (`from`, `to`, `status`, `operationId`, `page`, `size`); más recientes primero. Los "últimos 10" usan `status=COMPLETED` | `ADMIN`, `TELLER`, `CUSTOMER` (suya) | 200 | 400, 404 |
| `GET` | `/debit-cards/{id}/payments/{paymentId}` | Estado de un pago | `ADMIN`, `TELLER`, `CUSTOMER` (suya) | 200 | 404 |
| `POST` | `/debit-payment-recovery-runs` | Ejecutar ahora la recuperación de pagos pendientes | `ADMIN` | 200 | — |

Notas:
- Si el resultado no llega en 1,5 s, el pago responde **202** con estado `PENDING`; el cliente consulta con el `GET` del pago o repite con la misma `operationId`.
- Los "últimos 10 movimientos" de una tarjeta de débito son `GET /debit-cards/{id}/payments?size=10`.
- Códigos 422: `CUSTOMER_NOT_FOUND`, `CUSTOMER_INACTIVE`, `OVERDUE_DEBT`, `ACCOUNT_NOT_ELIGIBLE`, `ACCOUNT_NOT_FOUND`, `ACCOUNT_ALREADY_LINKED`, `MAX_ACCOUNTS_REACHED`, `MAIN_ACCOUNT_REQUIRED`, `LAST_ACCOUNT`, `CARD_NOT_USABLE`, `CARD_HAS_PENDING_PAYMENTS`, y los de la cuenta en un pago rechazado (`INSUFFICIENT_FUNDS`, `MONTHLY_LIMIT_EXCEEDED`, …, con el `paymentId`). 404 `DEBIT_CARD_NOT_FOUND`, `DEBIT_PAYMENT_NOT_FOUND`, `ACCOUNT_NOT_LINKED`; 409 `OPERATION_ID_REUSED`, `CONCURRENT_MODIFICATION`. Cuerpo estándar: `{ timestamp, status, code, message, path }`.
- **Contrato exacto** (campos, tipos, documentos Mongo, índices, algoritmo del pago y de la recuperación, tratamiento de cada evento y datos de demo): `contracts/debit-service/openapi.yaml` y `data-model.md`. Si difieren de esta ficha, el contrato manda.
- **Cambios respecto a la primera versión:** repetir un pago `PENDING` vuelve a pedir el retiro y espera otros 1,5 s; un cliente `INACTIVE` no puede pagar; el cierre automático por cierre de cuenta no mira los pagos pendientes; la recuperación tiene tope de reenvíos; `MAX_ACCOUNTS_REACHED` (máximo 10 cuentas por tarjeta).
- Los read models son **eventualmente consistentes**: si una cuenta se acaba de crear y su snapshot aún no llegó, el rechazo es transitorio y se repite.

## 6. Persistencia y caché
- **`debit_cards`:** un documento por tarjeta con `linkedAccounts` embebido. Índices: único en `cardNumber`; (`customerId`, `status`); `linkedAccounts.accountIds` para reaccionar a eventos de cuenta. Control optimista con `version`.
- **`debit_payments`:** un documento por pago. Índices: único en `operationId`; (`cardId`, `requestedAt` desc); (`status`, `requestedAt`) para la recuperación.
- **Read models:** `customer_snapshots`, `account_snapshots`, `overdue_customers`, alimentados por eventos. Las actualizaciones son idempotentes. `overdue_customers` guarda `customerId`, `overdue` y `updatedAt` y aplica el evento solo si `occurredAt` ≥ `updatedAt` (ver `flows/04-overdue-debt.md`).
- **Caché:** no aplica. No hay datos maestros propios.

## 7. Mensajería (Kafka)
| Publica | Consume |
|---|---|
| `debit.card.created`, `debit.card.updated`, `debit.card.closed`, `debit.payment.requested`, `debit.payment.completed`, `debit.payment.failed` | `customer.created/updated/deleted`, `account.created/updated/deleted`, `credit.overdue.detected/cleared`, `transaction.registered` y `transaction.failed` (solo los de tipo `DEBIT_PAYMENT`, correlacionados por `operationId`) |

Los nombres finales y la matriz de eventos se reconcilian al diseñar los flujos.

**Rol en sagas:** **iniciador de una saga de un solo paso**. Crea el pago `PENDING`, pide el retiro y termina en `COMPLETED` o `FAILED`. No necesita compensación porque no hay pasos posteriores; la recuperación y la idempotencia cubren la pérdida de mensajes.

## 8. Filesystem

```
debit-service/
├── pom.xml
├── Dockerfile
├── checkstyle.xml
├── README.md
├── docs/
│   ├── sequence/
│   └── uml/
└── src/
    ├── main/
    │   ├── java/com/bank/debit/
    │   │   ├── DebitServiceApplication.java
    │   │   ├── domain/
    │   │   │   ├── model/
    │   │   │   │   ├── DebitCard.java, DebitPayment.java   (aggregate roots)
    │   │   │   │   ├── DebitCardId.java, DebitPaymentId.java, OperationId.java, CustomerId.java, AccountId.java
    │   │   │   │   ├── CardNumber.java, ExpiryDate.java, LinkedAccounts.java, Money.java, FailureReason.java
    │   │   │   │   ├── AccountSnapshot.java, CustomerSnapshot.java   (datos de lectura)
    │   │   │   │   └── DebitCardStatus.java, DebitPaymentStatus.java, AccountType.java
    │   │   │   ├── service/
    │   │   │   │   ├── IssuancePolicy.java
    │   │   │   │   ├── LinkedAccountsPolicy.java
    │   │   │   │   ├── validation/                         (eslabones de la cadena)
    │   │   │   │   └── CardNumberGenerator.java
    │   │   │   ├── event/                                  (6 eventos de dominio)
    │   │   │   └── exception/                              (DebitCardNotFoundException, BusinessRuleViolationException con code, ...)
    │   │   ├── application/
    │   │   │   ├── command/
    │   │   │   ├── view/                                   (PageView, RecoveryResult, PaymentOutcome)
    │   │   │   ├── port/
    │   │   │   │   ├── in/                                 (casos de uso)
    │   │   │   │   └── out/                                (8 puertos)
    │   │   │   └── usecase/
    │   │   └── infrastructure/
    │   │       ├── adapter/
    │   │       │   ├── in/rest/                            (DebitCardController, DebitPaymentController, RecoveryController, GlobalExceptionHandler)
    │   │       │   ├── in/scheduler/                       (PendingPaymentRecoveryScheduler)
    │   │       │   ├── in/kafka/                           (CustomerEventsConsumer, AccountEventsConsumer, OverdueEventsConsumer, MovementResultsConsumer)
    │   │       │   ├── out/persistence/                    (documentos, repositorios, adaptadores)
    │   │       │   ├── out/readmodel/                      (snapshots de cliente, cuenta y deuda)
    │   │       │   ├── out/kafka/                          (MovementRequestKafkaPublisher, DebitEventKafkaPublisher)
    │   │       │   └── out/awaiter/                        (InMemoryPaymentResultAwaiter)
    │   │       ├── mapper/
    │   │       └── config/                                 (beans, Clock, Mongo, Kafka, seguridad)
    │   └── resources/
    │       ├── openapi/debit-service.yaml
    │       ├── application.yml                     (solo nombre y config.import; el resto viene del Config Server)
    │       └── logback-spring.xml
    └── test/java/com/bank/debit/
```

Los DTOs REST se generan desde el contrato.

## 9. Stack y configuración

**Base común:** Java 17, Spring Boot 3.x, Maven, WebFlux, RxJava 3, Lombok, MapStruct, Logback.

| Función | Dependencia |
|---|---|
| Web | `spring-boot-starter-webflux` (sin cliente HTTP hacia otros servicios) |
| Persistencia | `spring-boot-starter-data-mongodb-reactive` (`RxJava3CrudRepository`) |
| Eventos | `reactor-kafka` o `spring-kafka` |
| Proceso de recuperación | `@Scheduled` de Spring con `java.time.Clock` inyectado |
| Contrato | `openapi-generator-maven-plugin` |
| Config / Discovery | `spring-cloud-starter-config`, `spring-cloud-starter-netflix-eureka-client` |
| Seguridad | `spring-boot-starter-oauth2-resource-server` |
| Calidad y pruebas | Checkstyle, Jacoco, JUnit 5, Mockito, `reactor-test`, RxJava `TestObserver`, WebTestClient |

**Propiedades en Config Server:** puerto, Mongo, Kafka (tópicos y grupo de consumo), vigencia de la tarjeta (5 años), espera del resultado (1,5 s), minutos para considerar un pago pendiente, tamaño máximo de página, cron de recuperación, `security.enabled`.

**Resiliencia:** al no haber llamadas REST no aplica circuit breaker. El límite de **2 s** se aplica a la espera del resultado del pago (`timeout` de RxJava). La resiliencia real viene de la idempotencia por `operationId`, del estado persistido del pago y de la recuperación de pendientes.

## 10. Estrategia de pruebas
| Capa | Qué se prueba | Herramientas |
|---|---|---|
| Dominio: `LinkedAccounts` | Invariantes: no vacía, sin duplicados, principal incluida | JUnit 5 parametrizado |
| Dominio: `DebitCard` | Asociar, desasociar, cambiar principal, cierre, tarjeta vencida, `onAccountClosed` (promover, cerrar) | JUnit 5 con `Clock` fijo |
| Dominio: `DebitPayment` y políticas | Transiciones válidas e inválidas; `IssuancePolicy` (deuda vencida, cuentas no elegibles, cliente inactivo) | JUnit 5 |
| Casos de uso | Emisión; pago completado; pago rechazado; espera agotada → 202; idempotencia; recuperación | Mockito + `TestObserver` |
| Consumers | Eventos duplicados y desordenados; resultado que llega antes de esperar; solo eventos `DEBIT_PAYMENT` | Pruebas de consumidor |
| Persistencia | Índices únicos, consulta paginada, control de versión | Testcontainers *(opcional)* |
| Controllers y scheduler | Contrato, códigos 201/202/200, roles | WebTestClient |
| Cobertura | Todo método público nuevo con prueba y reporte | Jacoco |

## 11. Diagramas a elaborar
- [ ] Secuencia: emitir tarjeta (validaciones con read models)
- [ ] Secuencia: pago con tarjeta exitoso (petición → `transaction-service` → resultado)
- [ ] Secuencia: pago rechazado por saldo insuficiente
- [ ] Secuencia: pago pendiente y recuperación
- [ ] Secuencia: cierre de la cuenta principal y promoción de otra
- [ ] UML del dominio (`DebitCard`, `LinkedAccounts`, `DebitPayment`)

## 12. Decisiones y pendientes
- **Decidido:**
  - Servicio nuevo de P3: solo Kafka entre servicios; validaciones con read models propios.
  - El pago se carga a la **cuenta principal** (o a otra asociada si se indica); no hay reintento automático en otras cuentas.
  - Solo cuentas `SAVINGS` y `CHECKING` pueden asociarse a una tarjeta.
  - Los "movimientos de la tarjeta" son los `DebitPayment` de este servicio. El reporte de "últimos 10" de débito se habilita en P3, cuando existe la tarjeta.
  - Los eventos de tarjeta llevan la cuenta principal para que `yanki-service` los use.
  - Respuesta 201 si el resultado llega en 1,5 s y 202 si no.
  - Sin límite de tarjetas por cliente; solo moneda `PEN`.
- **Pendiente:**
  - La espera del resultado usa un mecanismo en memoria: con varias instancias, otra puede recibir el resultado y el cliente terminará con 202 y consulta. **Aceptado para el demo** (ver `flows/02-debit-payment.md`).
  - ~~Contrato del comando de retiro~~ **Definido** en `flows/02-debit-payment.md` (borrador; se cierra en el documento de Kafka).
  - Límite diario de pagos con tarjeta: fuera de alcance por ahora.
  - Publicación confiable de eventos (patrón outbox), común a todos los servicios.
  - Cliente que pasa a `INACTIVE`: sus tarjetas siguen abiertas (solo se le impide pagar). Cerrarlas por el evento `customer` queda fuera del demo. Detalle en `contracts/debit-service/data-model.md`, sección 9.

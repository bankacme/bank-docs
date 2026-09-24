# `yanki-service`

## 1. Resumen

| Campo | Valor |
|---|---|
| Propósito | Dueño de los monederos móviles Yanki: registro, pagos por número de celular y asociación opcional a una tarjeta de débito |
| Bounded context | Monedero móvil |
| Fase | P3 (servicio nuevo: **sin llamadas REST a otros servicios**, todo por Kafka) |
| Puerto | 8088 |
| Base de datos | MongoDB: `wallets`, `wallet_payments` + read models |
| Depende de | Eventos de `customer-service` y `debit-service`. Pide los movimientos de cuenta a `transaction-service` por Kafka. Su usuario se crea en `auth-service` |

## 2. Responsabilidades

**Hace:**
- CRUD de monederos (documento, celular, IMEI y correo). **No exige ser cliente del banco.**
- Enviar y recibir pagos usando solo el **número de celular**.
- Asociar y desasociar el monedero a una **tarjeta de débito** del banco.
- Orquestar la **saga del pago** (débito del origen → crédito del destino → compensación).
- Mantener el saldo propio de los monederos que no están asociados a una tarjeta.
- Consultar los pagos enviados y recibidos.

**No hace:**
- Crear usuarios ni contraseñas (`auth-service`; el monedero solo guarda el `ownerUserId` del token).
- Saldos de cuentas bancarias ni sus reglas (`account-service`).
- Ejecutar movimientos de cuenta ni guardar su historial (`transaction-service`).
- Estado de las tarjetas (`debit-service`): solo lo consume.
- Recargas de efectivo o desde otras entidades (fuera de alcance).

## 3. Modelo de dominio (DDD)

### 3.1 Aggregates y entidades
| Elemento | Tipo | Descripción |
|---|---|---|
| `Wallet` | Aggregate root | Monedero de una persona. Tiene **dos modos**: *independiente* (con saldo propio) o *asociado a una tarjeta* (sin saldo propio; usa la cuenta principal) |
| `WalletPayment` | Aggregate root | Un pago entre dos monederos y el estado de su saga. Es el historial de pagos Yanki |

Atributos de `Wallet`: `id`, `ownerUserId`, `document`, `phoneNumber`, `imei`, `email`, `balance`, `linkedCardId` (opcional), `appliedLegs`, `status`, `version`, `createdAt`, `updatedAt`.
Comportamiento: `open(...)`, `updateContact(email, imei)`, `linkCard(cardId)`, `unlinkCard()`, `debit(amount, legId)`, `credit(amount, legId)`, `close()`. Derivado: `isLinked()`.

Atributos de `WalletPayment`: `id`, `operationId`, `senderWalletId`, `receiverWalletId`, `amount`, `description`, `route` (origen y destino resueltos), `status`, `failureReason`, `requestedAt`, `completedAt`, `version`.
Comportamiento (transiciones): `sourceDebited()`, `completed()`, `failed(reason)`, `startCompensation()`, `compensated()`, `compensationFailed()`.

Estados de `WalletPayment` (igual que la transferencia de `transaction-service`):

```
STARTED ──origen debitado──▶ SOURCE_DEBITED ──destino acreditado──▶ COMPLETED
   │                              │
   │ débito rechazado             │ crédito rechazado
   ▼                              ▼
 FAILED                     COMPENSATING ──devolución ok──▶ COMPENSATED
                                  │
                                  └──falla tras reintentos──▶ COMPENSATION_FAILED (revisión manual)
```

### 3.2 Value objects
| VO | Campos | Validaciones *(simplificadas para el demo)* |
|---|---|---|
| `WalletId`, `WalletPaymentId`, `OperationId` | `value` | No vacío |
| `Document` | `type`, `number` | DNI: 8 dígitos. CEX: 9–12 alfanuméricos. PASSPORT: 6–12 alfanuméricos |
| `PhoneNumber` | `value` | 9 dígitos, empieza con 9. Se expone enmascarado a terceros |
| `Imei` | `value` | 15 dígitos. Nunca se devuelve completo |
| `Email` | `value` | Formato válido, en minúsculas |
| `Money` | `amount` (BigDecimal, 2 decimales), `currency` | Monto ≥ 0; > 0 al operar. Solo `PEN` |
| `AppliedLegs` | conjunto acotado (últimos 50) de `legId` | Sirve para ignorar operaciones repetidas sobre el saldo |
| `PaymentRoute` | `sourceKind`, `sourceAccountId`, `targetKind`, `targetAccountId` | Se resuelve al iniciar el pago y no cambia |
| `FailureReason` | `code`, `message` | Código no vacío |
| `DebitCardSnapshot` | `cardId`, `customerId`, `mainAccountId`, `status` | Dato de lectura, no es aggregate |
| `CustomerSnapshot` | `customerId`, `document`, `status` | Dato de lectura, no es aggregate |

### 3.3 Enums
| Enum | Valores |
|---|---|
| `DocumentType` | `DNI`, `CEX`, `PASSPORT` |
| `WalletStatus` | `ACTIVE`, `CLOSED` |
| `FundingKind` | `WALLET` (saldo del monedero), `ACCOUNT` (cuenta principal de la tarjeta) |
| `WalletPaymentStatus` | `STARTED`, `SOURCE_DEBITED`, `COMPLETED`, `FAILED`, `COMPENSATING`, `COMPENSATED`, `COMPENSATION_FAILED` |
| `PaymentDirection` | `SENT`, `RECEIVED` (solo para consultas) |

### 3.4 Reglas de negocio e invariantes
| # | Regla | Dónde se aplica |
|---|---|---|
| 1 | Registro con documento, celular, IMEI y correo válidos. **No requiere ser cliente.** El documento es el **del token** (no el del cuerpo), y solo DNI, CEX o PASSPORT: un usuario con RUC recibe 422 `DOCUMENT_TYPE_NOT_ALLOWED` | Value objects / `Wallet.open` |
| 2 | Una persona, un monedero: documento único, celular único y un solo monedero por usuario (`ownerUserId`) | Caso de uso + índices únicos |
| 3 | Documento y celular no cambian; sí se pueden actualizar correo e IMEI | `Wallet.updateContact` |
| 4 | El dueño es el usuario del token (`sub`); solo él (o `ADMIN`/`TELLER` en consulta) accede a su monedero | Caso de uso |
| 5 | Asociar a tarjeta: la tarjeta está `ACTIVE`, el **documento del titular de la tarjeta coincide con el del monedero** y la tarjeta no está asociada a otro monedero | `LinkCardPolicy` |
| 6 | Para asociar, el saldo propio del monedero debe ser 0 y no debe haber pagos en curso (como emisor o receptor) | `Wallet.linkCard` + caso de uso |
| 7 | Asociado a una tarjeta, el monedero **no tiene saldo propio**: enviar debita y recibir acredita **solo la cuenta principal** de la tarjeta | `PaymentRoutingPolicy` |
| 8 | Desasociar se permite siempre; el monedero vuelve a ser independiente con saldo 0. Si la tarjeta se cierra, se desasocia automáticamente | `Wallet.unlinkCard` + consumidor |
| 9 | Pago: monto > 0, el destino se busca por **celular**, origen ≠ destino y ambos monederos `ACTIVE` | `PaymentRoutingPolicy` |
| 10 | Monedero independiente como origen: el saldo debe alcanzar (sin saldo negativo) | `Wallet.debit` |
| 11 | Cuenta como origen o destino: las reglas y comisiones las aplica `account-service` (saldo, límites, cuenta activa) y su rechazo llega como motivo | Delegado |
| 12 | Saga del pago: 1) debitar origen, 2) acreditar destino, 3) si el paso 2 falla, **devolver** el paso 1 | `WalletPayment` + casos de uso |
| 13 | La cuenta de origen y de destino se **resuelven al iniciar** y se guardan; la compensación usa las mismas aunque luego cambie la cuenta principal | `PaymentRoute` |
| 14 | Idempotencia: la misma `operationId` no genera otro pago. Cada movimiento lleva un `legId` (`<id>-OUT`, `<id>-IN`, `<id>-REV`) y el monedero ignora los repetidos | Caso de uso + `AppliedLegs` |
| 15 | La compensación es idempotente y se reintenta; si agota los reintentos queda `COMPENSATION_FAILED` para revisión manual | `WalletPayment.compensationFailed` |
| 16 | Un pago no terminado en N minutos se **retoma** con las mismas `operationId` y `legId` | `RecoverPendingPaymentsUseCase` |
| 17 | Cierre (baja lógica) solo con saldo 0, sin tarjeta asociada y sin pagos en curso; un monedero cerrado no envía ni recibe | `Wallet.close` |
| 18 | Una tarjeta pertenece como máximo a un monedero | Índice único parcial en `linkedCardId` |

Combinaciones de un pago (`PaymentRoutingPolicy`):

| Origen | Destino | Pasos |
|---|---|---|
| Saldo del monedero | Saldo del monedero | Ambos pasos locales |
| Cuenta principal | Saldo del monedero | Retiro remoto → crédito local |
| Saldo del monedero | Cuenta principal | Débito local → depósito remoto |
| Cuenta principal | Cuenta principal | Retiro remoto → depósito remoto |

### 3.5 Domain services
| Servicio | Responsabilidad |
|---|---|
| `PaymentRoutingPolicy` | Valida la regla 9 y resuelve el `PaymentRoute` (origen y destino por monedero). Puro: recibe los monederos y los snapshots de tarjeta |
| `LinkCardPolicy` | Valida la regla 5. Puro: recibe el monedero, los snapshots de tarjeta y de cliente, y si la tarjeta ya está asociada |

### 3.6 Eventos de dominio
| Evento | Cuándo | Datos mínimos |
|---|---|---|
| `WalletOpened` / `WalletUpdated` / `WalletClosed` | Ciclo de vida | `walletId`, `phoneNumber` (enmascarado), `status`, `linked` |
| `WalletCardLinked` / `WalletCardUnlinked` | Cambia la asociación | Estado completo del monedero (`walletId`, `maskedPhone`, `status`, `linkedCardId?`) más `cardId`: el tópico `wallet` está compactado |
| `WalletPaymentCompleted` | Pago `COMPLETED` | `paymentId`, `senderWalletId`, `receiverWalletId`, `amount` |
| `WalletPaymentFailed` | Pago `FAILED`, `COMPENSATED` o `COMPENSATION_FAILED` | `paymentId`, `status`, `reasonCode` |

## 4. Casos de uso y puertos

### 4.1 Puertos de entrada (casos de uso)
| Caso de uso | Retorno | Descripción |
|---|---|---|
| `OpenWalletUseCase` | `Single<Wallet>` | Valida unicidad y crea el monedero del usuario del token |
| `FindWalletUseCase` / `FindMyWalletUseCase` / `FindWalletsUseCase` | `Single` / `Single` / `Flowable` | Por id; el del usuario actual; lista para `ADMIN`/`TELLER` |
| `UpdateWalletUseCase` | `Single<Wallet>` | Correo e IMEI |
| `CloseWalletUseCase` | `Completable` | Baja lógica |
| `LinkDebitCardUseCase` / `UnlinkDebitCardUseCase` | `Single<Wallet>` | Asocia (con `LinkCardPolicy`) o desasocia |
| `GetWalletBalanceUseCase` | `Single<WalletBalanceView>` | Independiente: saldo propio. Asociado: modo `LINKED`, tarjeta y cuenta principal enmascarada (el saldo real está en `account-service`) |
| `SendPaymentUseCase` | `Single<WalletPayment>` | Resuelve la ruta, inicia la saga y espera el resultado hasta 1,5 s |
| `FindPaymentUseCase` / `FindWalletPaymentsUseCase` | `Single` / `Single<PageView>` | Estado de un pago; pagos enviados/recibidos con filtros y página |
| `HandleMovementResultUseCase` | `Completable` | Recibe resultados de `transaction-service` y avanza la saga |
| `HandleCardClosedUseCase` | `Completable` | Desasocia el monedero cuando la tarjeta se cierra |
| `RecoverPendingPaymentsUseCase` | `Single<RecoveryResult>` | Retoma pagos no terminados |

La coordinación de pasos vive en `PaymentSagaCoordinator` (capa de aplicación): cada paso es local (monedero) o remoto (puerto), según la ruta.

### 4.2 Puertos de salida
| Puerto | Métodos | Adaptador |
|---|---|---|
| `WalletRepositoryPort` | `save`, `findById`, `findByPhone`, `findByDocument`, `findByOwnerUserId`, `findAll(filters)`, `existsByLinkedCardId` | Mongo |
| `WalletPaymentRepositoryPort` | `save`, `findById`, `findByOperationId`, `findByWallet(direction, range, page)`, `findInProgressOlderThan(instant)` | Mongo |
| `CustomerLookupPort` | `findById` → `CustomerSnapshot` | Read model (Mongo) |
| `DebitCardLookupPort` | `findById` → `DebitCardSnapshot` | Read model (Mongo) |
| `AccountMovementRequestPort` | `requestMovement(legId, accountId, type, amount)` y `requestReversal(legId, accountId)` → `Completable` | Kafka |
| `PaymentResultAwaiterPort` | `await(paymentId, timeout)` → `Maybe<WalletPayment>` (estado final) | En memoria |
| `WalletEventPublisherPort` | `publish(event)` | Kafka |

Cada resultado de movimiento remoto **reanuda** la saga por `HandleMovementResultUseCase`, aunque el cliente ya haya recibido un 202. El awaiter solo condiciona la respuesta HTTP.

## 5. API (contrato OpenAPI)

Base: `/api/v1`. Roles aplican cuando `security.enabled=true`. El dueño se toma del `sub` del token.

| Método | Ruta | Descripción | Roles | Éxito | Errores |
|---|---|---|---|---|---|
| `POST` | `/wallets` | Crear monedero (`phoneNumber`, `imei`, `email`). **El documento se toma del token** (`documentType`, `documentNumber`) | `YANKI_USER`, `CUSTOMER` | 201 | 400, 409, 422 |
| `GET` | `/wallets/me` | Mi monedero | `YANKI_USER`, `CUSTOMER` | 200 | 404 |
| `GET` | `/wallets` | Listar (`status`) | `ADMIN`, `TELLER` | 200 | — |
| `GET` | `/wallets/{id}` | Obtener | Dueño, `ADMIN`, `TELLER` | 200 | 404 |
| `PUT` | `/wallets/{id}` | Actualizar correo e IMEI | Dueño, `ADMIN` | 200 | 400, 404 |
| `DELETE` | `/wallets/{id}` | Baja lógica | Dueño, `ADMIN` | 204 | 404, 422 |
| `GET` | `/wallets/{id}/balance` | Saldo (independiente) o modo asociado | Dueño, `ADMIN`, `TELLER` | 200 | 404 |
| `PUT` | `/wallets/{id}/debit-card` | Asociar tarjeta (`cardId`) | Dueño | 200 | 404, 422 |
| `DELETE` | `/wallets/{id}/debit-card` | Desasociar tarjeta | Dueño | 200 | 404 |
| `POST` | `/wallets/{id}/payments` | Enviar pago (`operationId`, `receiverPhone`, `amount`, `description`) | Dueño | 201 (completado) o 202 (en proceso); repetición: 200 | 400, 404, 422 |
| `GET` | `/wallets/{id}/payments` | Pagos (`direction`, `from`, `to`, `page`, `size`); más recientes primero | Dueño, `ADMIN`, `TELLER` | 200 | 400, 404 |
| `GET` | `/wallets/{id}/payments/{paymentId}` | Estado de un pago | Dueño, `ADMIN`, `TELLER` | 200 | 404 |
| `POST` | `/wallet-payment-recovery-runs` | Ejecutar ahora la recuperación de pagos pendientes | `ADMIN` | 200 | — |

Notas:
- **Registro completo en tres pasos:** `POST /auth/register` (sin token) crea el usuario; `POST /auth/login` da el token; con ese token, `POST /wallets` crea el monedero con el documento del token. Un cliente del banco que ya tiene usuario salta el primer paso (ver `flows/05-customer-onboarding-and-access.md`).
- El receptor solo se identifica por su celular; la respuesta al emisor muestra el celular enmascarado y ningún otro dato del receptor.
- Si el resultado no llega en 1,5 s, el pago responde **202** `PENDING`; se consulta con el `GET` del pago o se repite con la misma `operationId`.
- **Contrato completo:** `contracts/yanki-service/openapi.yaml` (API) y `contracts/yanki-service/data-model.md` (tipos, documentos, saga paso a paso, eventos). Si algo difiere de esta ficha, mandan los contratos.
- `GET .../balance` en modo `LINKED` devuelve el `mainAccountId` de la tarjeta (este servicio no consume el tópico `account`, así que no conoce el número enmascarado de la cuenta).
- Repetir un `operationId` responde 200 (completado), 422 idéntico (rechazado) o 202 (en curso; se retoma la saga y se espera otros 1,5 s). `operationId` de 8 a 56 caracteres. Otro monto, celular o monedero con el mismo id: 409 `OPERATION_ID_REUSED`.
- Códigos: 409 `DOCUMENT_ALREADY_REGISTERED`, `PHONE_ALREADY_REGISTERED`, `USER_ALREADY_HAS_WALLET`. 422 `DOCUMENT_TYPE_NOT_ALLOWED`, `WALLET_HAS_BALANCE`, `WALLET_HAS_CARD`, `WALLET_HAS_PAYMENTS_IN_PROGRESS`, `WALLET_ALREADY_LINKED`, `CARD_NOT_FOUND`, `CARD_NOT_ACTIVE`, `CARD_OWNER_MISMATCH`, `CARD_ALREADY_LINKED`, `RECEIVER_NOT_FOUND`, `SAME_WALLET`, `WALLET_INACTIVE`, `INSUFFICIENT_FUNDS` y los motivos de `account-service` (`MONTHLY_LIMIT_EXCEEDED`, `ACCOUNT_INACTIVE`…). Cuerpo estándar: `{ timestamp, status, code, message, path }`.
- Al ser datos personales (documento, celular, IMEI, correo), **no se escriben completos en los logs** y las respuestas a terceros los enmascaran.

## 6. Persistencia y caché
- **`wallets`:** un documento por monedero. Índices únicos **parciales sobre `status = ACTIVE`**: (`document.type`, `document.number`), `phoneNumber`, `ownerUserId` (al cerrar un monedero, esa persona puede abrir otro); índice único parcial sobre `linkedCardId` (`$exists`). Control optimista con `version`, clave para el saldo.
- **`wallet_payments`:** un documento por pago con su ruta y `visibleTo` (emisor desde el inicio; receptor solo al llegar a `COMPLETED`, para resolver el historial de ambas direcciones con una consulta). Índices: único en `operationId`; (`senderWalletId`, `requestedAt` desc); (`receiverWalletId`, `status`, `requestedAt` desc); (`visibleTo`, `requestedAt` desc) multiclave; (`status`, `updatedAt`) para la recuperación.
- **Read models:** `customer_snapshots` (id, documento, estado) y `debit_card_snapshots` (id, cliente, cuenta principal, estado), alimentados por eventos e idempotentes.
- **Caché:** no aplica. No hay datos maestros propios.

## 7. Mensajería (Kafka)
| Publica | Consume |
|---|---|
| Eventos: `yanki.wallet.created/updated/closed`, `yanki.wallet.card.linked`, `yanki.wallet.card.unlinked`, `yanki.payment.completed`, `yanki.payment.failed`. Comandos a `transaction-service`: `yanki.movement.requested`, `yanki.movement.reversal.requested` | `customer.created/updated/deleted`, `debit.card.created/updated/closed`, `transaction.registered` y `transaction.failed` (movimientos de tipo Yanki, correlacionados por `legId`) `transaction.reversed` y `transaction.reversal.failed` (resultado de las devoluciones remotas, correlacionadas por `<op>-REV`) |

Los eventos de Yanki no tienen consumidores obligatorios; sirven para trazabilidad y reportes. Los nombres finales se fijan al diseñar los flujos.

**Rol en sagas:** **iniciador y orquestador** del pago Yanki. Es el único servicio cuyos pasos pueden ser **locales** (saldo del monedero) o **remotos** (cuentas vía `transaction-service`). Cada resultado reanuda la saga; la compensación devuelve el débito si el crédito falla.

## 8. Filesystem

```
yanki-service/
├── pom.xml
├── Dockerfile
├── checkstyle.xml
├── README.md
├── docs/
│   ├── sequence/
│   └── uml/
└── src/
    ├── main/
    │   ├── java/com/bank/yanki/
    │   │   ├── YankiServiceApplication.java
    │   │   ├── domain/
    │   │   │   ├── model/
    │   │   │   │   ├── Wallet.java, WalletPayment.java     (aggregate roots)
    │   │   │   │   ├── WalletId.java, WalletPaymentId.java, OperationId.java
    │   │   │   │   ├── Document.java, PhoneNumber.java, Imei.java, Email.java, Money.java
    │   │   │   │   ├── AppliedLegs.java, PaymentRoute.java, FailureReason.java
    │   │   │   │   ├── DebitCardSnapshot.java, CustomerSnapshot.java   (datos de lectura)
    │   │   │   │   └── DocumentType.java, WalletStatus.java, FundingKind.java, WalletPaymentStatus.java, PaymentDirection.java
    │   │   │   ├── service/
    │   │   │   │   ├── PaymentRoutingPolicy.java
    │   │   │   │   └── LinkCardPolicy.java
    │   │   │   ├── event/                                  (eventos de dominio)
    │   │   │   └── exception/                              (WalletNotFoundException, BusinessRuleViolationException con code, InvalidTransitionException, ...)
    │   │   ├── application/
    │   │   │   ├── command/
    │   │   │   ├── view/                                   (PageView, WalletBalanceView, RecoveryResult)
    │   │   │   ├── port/
    │   │   │   │   ├── in/                                 (casos de uso)
    │   │   │   │   └── out/                                (7 puertos)
    │   │   │   ├── saga/                                   (PaymentSagaCoordinator)
    │   │   │   └── usecase/
    │   │   └── infrastructure/
    │   │       ├── adapter/
    │   │       │   ├── in/rest/                            (WalletController, WalletPaymentController, RecoveryController, GlobalExceptionHandler)
    │   │       │   ├── in/scheduler/                       (PendingPaymentRecoveryScheduler)
    │   │       │   ├── in/kafka/                           (CustomerEventsConsumer, DebitCardEventsConsumer, MovementResultsConsumer)
    │   │       │   ├── out/persistence/                    (documentos, repositorios, adaptadores)
    │   │       │   ├── out/readmodel/                      (snapshots de cliente y tarjeta)
    │   │       │   ├── out/kafka/                          (AccountMovementKafkaPublisher, YankiEventKafkaPublisher)
    │   │       │   └── out/awaiter/                        (InMemoryPaymentResultAwaiter)
    │   │       ├── mapper/
    │   │       └── config/                                 (beans, Clock, Mongo, Kafka, seguridad, enmascarado de logs)
    │   └── resources/
    │       ├── openapi/yanki-service.yaml
    │       ├── application.yml                     (solo nombre y config.import; el resto viene del Config Server)
    │       └── logback-spring.xml
    └── test/java/com/bank/yanki/
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

**Propiedades en Config Server:** puerto, Mongo, Kafka (tópicos y grupo), espera del resultado del pago (1,5 s), minutos para considerar un pago pendiente, reintentos de compensación, tamaño del conjunto `AppliedLegs`, tamaño máximo de página, cron de recuperación, `security.enabled`.

**Resiliencia:** sin llamadas REST no aplica circuit breaker; los 2 s se aplican a la espera del resultado (`timeout` de RxJava). La resiliencia real es: saga con estado persistido, `operationId` y `legId` idempotentes, compensación con reintentos y recuperación de pendientes.

## 10. Estrategia de pruebas
| Capa | Qué se prueba | Herramientas |
|---|---|---|
| Dominio: `Wallet` | Modo independiente vs asociado, débito/crédito, saldo insuficiente, asociar con saldo (rechazo), `legId` repetido, cierre | JUnit 5 (sin Spring) |
| Dominio: `WalletPayment` | Todas las transiciones de la saga, incluidas las inválidas | JUnit 5 |
| Dominio: políticas | `PaymentRoutingPolicy` en las 4 combinaciones y sus errores; `LinkCardPolicy` (titular distinto, tarjeta ocupada o inactiva) | JUnit 5 parametrizado |
| Casos de uso | Cada combinación de pago; destino rechaza → compensación; compensación fallida; idempotencia; 202 por espera agotada; recuperación; asociación por tarjeta cerrada | Mockito + `TestObserver` |
| Consumers | Eventos duplicados y desordenados; resultado que llega antes de esperar; solo movimientos Yanki | Pruebas de consumidor |
| Persistencia | Índices únicos (incluido el parcial), control de versión | Testcontainers *(opcional)* |
| Controllers y scheduler | Contrato, códigos 201/202/200/409, enmascarado, roles | WebTestClient |
| Cobertura | Todo método público nuevo con prueba y reporte | Jacoco |

## 11. Diagramas a elaborar
- [ ] Secuencia: registro completo (usuario en `auth-service` + monedero)
- [ ] Secuencia: asociar el monedero a una tarjeta de débito
- [ ] Secuencia: pago entre monederos independientes
- [ ] Secuencia: pago de cuenta principal a monedero
- [ ] Secuencia: pago de monedero a cuenta con rechazo y devolución local
- [ ] Secuencia: pago cuenta a cuenta con compensación
- [ ] Secuencia: recuperación de un pago pendiente
- [ ] UML del dominio y diagrama de estados de `WalletPayment`

## 12. Decisiones y pendientes
- **Decidido:**
  - El monedero tiene dos modos: independiente (saldo propio) o asociado a tarjeta (sin saldo propio, usa la cuenta principal para enviar y recibir).
  - Para asociar, el saldo propio debe ser 0; la tarjeta debe pertenecer a alguien con el **mismo documento** que el monedero.
  - `yanki-service` orquesta su propia saga; `transaction-service` ejecuta cada movimiento de cuenta y `account-service` aplica sus reglas.
  - Pasos locales con idempotencia por `legId` dentro del monedero; pasos remotos por `operationId`.
  - Cuentas de origen y destino se fijan al iniciar el pago.
  - No hay recarga: en el demo, el primer saldo de un monedero independiente llega recibiendo un pago de un monedero asociado a tarjeta.
  - Un usuario, un documento, un celular y una tarjeta como máximo por monedero.
  - Datos personales enmascarados en respuestas a terceros y en logs.
  - (Contrato) Los índices únicos de documento, celular y usuario son parciales sobre `ACTIVE`; `yanki.wallet.card.linked/unlinked` llevan el estado completo; el receptor con tarjeta no activa se informa como `RECEIVER_NOT_FOUND`; la devolución local (`refund`) nunca se rechaza; asociar exige además tarjeta no vencida y titular `ACTIVE`; sin transacciones de Mongo (idempotencia por `legId`).
  - Los movimientos de cuenta originados por Yanki se registran en `transaction-service` como `YANKI_PAYMENT_OUT` / `YANKI_PAYMENT_IN`.
- **Pendiente:**
  - **Regla de deuda vencida (RF-60):** no se aplica al monedero porque no es un producto de crédito ni exige ser cliente. Conviene confirmarlo con el instructor.
  - **Alcance del saldo propio:** el enunciado habla de "cargar o acreditar la cuenta principal" al asociar; no menciona recargas ni saldo propio. Conviene confirmar si se espera algo más para monederos sin tarjeta.
  - ~~Contrato de los comandos y del resultado de las reversas~~ **Definido** (borrador) en `flows/03-yanki-payment.md`; se cierra en el documento de Kafka. La espera (1,5 s por defecto) es configurable y siempre menor que el timeout de 2 s del Gateway.
  - Monto máximo por pago (configurable): no se definió.
  - La espera en memoria del resultado se revisa con varias instancias, igual que en `debit-service`.
  - Brecha de seguridad conocida: el registro Yanki no exige prueba de identidad (ver definición general, 10.3).
  - Publicación confiable de eventos (patrón outbox), común a todos los servicios.

# `transaction-service`

## 1. Resumen

| Campo | Valor |
|---|---|
| Propósito | Dueño del **historial de movimientos** de todos los productos y puerta de entrada de depósitos, retiros y transferencias. Orquesta la saga de transferencias |
| Bounded context | Movimientos y transferencias |
| Fase | P1 (depósitos, retiros, historial), P2 (transferencias), P3 (saga por eventos, movimientos pedidos por otros servicios) |
| Puerto | 8084 |
| Base de datos | MongoDB: `transactions`, `transfers` (+ read model en P3) |
| Depende de | `account-service` (aplica los movimientos sobre el saldo). Recibe registros de `credit-service`. REST en P1/P2, eventos en P3 |

## 2. Responsabilidades

**Hace:**
- Registrar **depósitos** y **retiros** de cuentas: pide a `account-service` aplicar el movimiento y guarda el resultado.
- Ejecutar **transferencias** entre cuentas propias y a terceros del mismo banco, con compensación si algo falla.
- Guardar el **historial** de movimientos de cuentas, créditos y tarjetas de crédito.
- Consultar movimientos por producto (RF-32) con filtros de fechas y paginación; base de los reportes.
- Registrar las comisiones que informa `account-service` como movimientos propios.
- Recuperar operaciones que quedaron a medias (pendientes).

**No hace:**
- Saldos ni reglas de límites o comisiones (`account-service`).
- Saldos, límites o vencimientos de créditos y tarjetas (`credit-service`); aquí solo se **registra** lo que ese servicio ya aplicó.
- Reportes (`report-service`).
- Transferencias hacia productos de crédito: los pagos de crédito son de `credit-service`.

## 3. Modelo de dominio (DDD)

### 3.1 Aggregates y entidades
| Elemento | Tipo | Descripción |
|---|---|---|
| `Transaction` | Aggregate root | **Un movimiento sobre un solo producto**: depósito, retiro, pata de transferencia, comisión, pago o consumo de crédito. Es el registro del historial |
| `Transfer` | Aggregate root | Estado de una transferencia. Es la **máquina de estados de la saga**: enlaza las dos patas y la compensación |

Atributos de `Transaction`: `id`, `operationId`, `product` (ProductRef), `customerId` (dueño del producto), `type`, `amount`, `resultingBalance`, `status`, `failureReason` (opcional), `transferId` (opcional), `parentTransactionId` (comisiones), `payerCustomerId` (pagos de terceros), `description`, `reversal` (opcional: `operationId` `<op>-REV`, `outcome`, `reasonCode`), `occurredAt` (fecha de aceptación, no cambia), `createdAt`, `updatedAt`.
Comportamiento: `pending(...)` y `record(...)` (factories), `complete(resultingBalance)`, `fail(reason)`, `markReversed()`, `updateDescription(text)`, `discard()`.

Atributos de `Transfer`: `id`, `operationId`, `sourceAccountId`, `targetAccountId`, `amount`, `kind`, `status`, `failureReason`, `debitTransactionId`, `creditTransactionId`, `compensationAttempts`, `description`, `sourceCustomerId` y `targetCustomerId` (no se exponen), `requestedBy`, `version`, `createdAt`, `updatedAt`.
Comportamiento (transiciones): `sourceDebited(txId)`, `completed(txId)`, `failed(reason)`, `startCompensation()`, `compensated()`, `compensationFailed()`.

Estados de `Transfer`:

```
STARTED ──débito ok──▶ SOURCE_DEBITED ──crédito ok──▶ COMPLETED
   │                        │
   │ débito rechazado       │ crédito rechazado
   ▼                        ▼
 FAILED               COMPENSATING ──reversa ok──▶ COMPENSATED
                            │
                            └──reversa falla tras reintentos──▶ COMPENSATION_FAILED (revisión manual)
```

### 3.2 Value objects
| VO | Campos | Validaciones |
|---|---|---|
| `TransactionId`, `TransferId`, `OperationId` | `value` | No vacío |
| `ProductRef` | `productId`, `productType` | No vacío |
| `Money` | `amount` (BigDecimal, 2 decimales), `currency` | Monto > 0 al operar. Solo `PEN` |
| `FailureReason` | `code`, `message` | Código no vacío (p. ej. `INSUFFICIENT_FUNDS`) |
| `DateRange` | `from`, `to` | `from` ≤ `to` |
| `AccountSnapshot` | `accountId`, `customerId`, `type`, `status` | Dato de lectura, no es aggregate |

### 3.3 Enums
| Enum | Valores |
|---|---|
| `ProductType` | `ACCOUNT`, `CREDIT`, `CREDIT_CARD` |
| `TransactionType` | `DEPOSIT`, `WITHDRAWAL`, `TRANSFER_OUT`, `TRANSFER_IN`, `FEE`, `CREDIT_PAYMENT`, `CARD_PAYMENT`, `CARD_CHARGE`, `DEBIT_PAYMENT` (P3), `YANKI_PAYMENT_OUT` (P3), `YANKI_PAYMENT_IN` (P3) |
| `TransactionStatus` | `PENDING`, `COMPLETED`, `FAILED`, `REVERSED`, `DISCARDED` |
| `TransferKind` | `OWN`, `THIRD_PARTY` |
| `TransferStatus` | `STARTED`, `SOURCE_DEBITED`, `COMPLETED`, `FAILED`, `COMPENSATING`, `COMPENSATED`, `COMPENSATION_FAILED` |

`resultingBalance` significa el saldo de la cuenta, el saldo pendiente del crédito o el monto usado de la tarjeta, según el tipo de producto.

### 3.4 Reglas de negocio e invariantes
| # | Regla | Dónde se aplica |
|---|---|---|
| 1 | Monto > 0 | `Money` / factories |
| 2 | **Idempotencia:** una misma `operationId` no crea otro movimiento; repetir la petición devuelve el resultado original | Caso de uso + índice único |
| 3 | Un movimiento nace `PENDING` y pasa a `COMPLETED` o `FAILED` según lo que responda `account-service`; el motivo de rechazo se conserva (`INSUFFICIENT_FUNDS`, `MONTHLY_LIMIT_EXCEEDED`, `NOT_ALLOWED_DAY`, `ACCOUNT_INACTIVE`…) | `Transaction.complete/fail` |
| 4 | `CUSTOMER` solo deposita y retira en sus propias cuentas (`TELLER`/`ADMIN` en cualquiera) | Caso de uso (con `AccountLookupPort`) |
| 5 | Transferencia: origen ≠ destino, ambas cuentas existentes y activas, monto > 0 | `TransferPolicy` |
| 6 | El tipo de transferencia se calcula: `OWN` si ambas cuentas son del mismo cliente; si no, `THIRD_PARTY` | `TransferPolicy` |
| 7 | `CUSTOMER` solo transfiere desde sus propias cuentas; el destino puede ser de terceros (excepción del enunciado) | Caso de uso |
| 8 | Saga de transferencia: 1) retirar del origen, 2) depositar en el destino, 3) si el paso 2 falla, **revertir** el paso 1 | `Transfer` + casos de uso |
| 9 | La compensación es idempotente y se reintenta; si agota los reintentos queda `COMPENSATION_FAILED` para revisión manual | `Transfer.compensationFailed` |
| 10 | Cada cuenta aplica **sus propias reglas y comisiones** a su pata (retiro en el origen, depósito en el destino) | Delegado a `account-service` |
| 11 | Si `account-service` informa una comisión, se guarda como movimiento `FEE` enlazado al movimiento padre | Caso de uso |
| 12 | Los movimientos de crédito y tarjeta se **registran, no se validan**: el efecto ya ocurrió en `credit-service`. Los duplicados se ignoran por `operationId` | `RecordExternalMovementUseCase` |
| 13 | El historial es inmutable: no se cambian monto, tipo ni producto. Solo se edita la `description`, y solo se descartan (baja lógica) los registros `FAILED` | `Transaction` |
| 14 | Un movimiento `PENDING` de más de N minutos se **reintenta** con la misma `operationId` (idempotente en `account-service`) | `RecoverPendingOperationsUseCase` |
| 15 | Las consultas devuelven los más recientes primero; `size` máximo 100; `from` ≤ `to` | `DateRange` + caso de uso |
| 16 | `CUSTOMER` solo ve movimientos de sus propios productos | Caso de uso (por `customerId` del movimiento) |
| 17 | Las `operationId` de las patas se derivan de la de la transferencia: `<id>-OUT`, `<id>-IN`, `<id>-REV`; la de una comisión, `<id>-FEE` | `Transfer` / caso de uso |

### 3.5 Domain services
| Servicio | Responsabilidad |
|---|---|
| `TransferPolicy` | Valida las reglas 5 y 6. Puro: recibe los `AccountSnapshot` de origen y destino y devuelve el `TransferKind` o el error de negocio |

### 3.6 Eventos de dominio
| Evento | Cuándo | Datos mínimos |
|---|---|---|
| `TransactionRegistered` | Un movimiento queda `COMPLETED` | `transactionId`, `operationId`, `productId`, `productType`, `customerId`, `type`, `amount`, `fee` (opcional), `resultingBalance`, `description`, `transferId` (opcional), `parentTransactionId` (opcional, en comisiones), `payerCustomerId` (opcional), `occurredAt`. También se publica para las comisiones `FEE` |
| `TransactionFailed` | Un movimiento queda `FAILED` | `transactionId`, `operationId`, `productId`, `reasonCode` |
| `TransactionReversed` | Un movimiento pasa a `REVERSED` (P3): compensación de una transferencia o pedido de `yanki-service`. Sus comisiones `FEE` se revierten con él | `operationId` (de la reversa), `originalOperationId`, `productId`, `amount`, `resultingBalance`, `occurredAt` |
| `TransactionReversalFailed` | La cuenta rechazó la reversa | `operationId`, `originalOperationId`, `reasonCode` |
| `TransferCompleted` | Transferencia `COMPLETED` | `transferId`, `sourceAccountId`, `targetAccountId`, `amount`, `kind` |
| `TransferFailed` | Transferencia `FAILED`, `COMPENSATED` o `COMPENSATION_FAILED` | `transferId`, `status`, `reasonCode` |

## 4. Casos de uso y puertos

### 4.1 Puertos de entrada (casos de uso)
| Caso de uso | Retorno | Descripción |
|---|---|---|
| `RegisterDepositUseCase` | `Single<Transaction>` | Reserva la operación, pide el depósito, guarda resultado y comisión |
| `RegisterWithdrawalUseCase` | `Single<Transaction>` | Igual, con retiro |
| `StartTransferUseCase` | `Single<Transfer>` | Valida con `TransferPolicy` y ejecuta los pasos de la saga, guardando el estado tras cada paso |
| `FindTransferUseCase` / `FindTransfersUseCase` | `Single` / `Flowable` | Por id; lista con filtro de cuenta |
| `FindTransactionUseCase` | `Single<Transaction>` | Por id |
| `FindProductTransactionsUseCase` | `Single<PageView<Transaction>>` | Historial de un producto (`DateRange`, `type`, `status`, página) |
| `FindTransactionsUseCase` | `Single<PageView<Transaction>>` | Consulta administrativa (`customerId`, `type`, `status`, fechas) |
| `UpdateTransactionDescriptionUseCase` | `Single<Transaction>` | Solo la descripción |
| `DiscardTransactionUseCase` | `Completable` | Baja lógica de un movimiento `FAILED` |
| `RecordExternalMovementUseCase` | `Single<Transaction>` | Registra movimientos de crédito y tarjeta (REST en P1/P2, evento en P3) |
| `HandleMovementResultUseCase` | `Completable` | P3: recibe `applied`/`rejected`/`reversed` de `account-service` y avanza la saga |
| `RecordRequestedMovementUseCase` | `Completable` | P3: consume los pedidos de `debit-service` (`DEBIT_PAYMENT`) y `yanki-service` (`YANKI_PAYMENT_OUT/IN` y sus **reversas**, con respuesta `transaction.reversed` o `transaction.reversal.failed`), crea el movimiento y pide la operación a la cuenta. **Idempotente con reemisión:** ante una `operationId` conocida no crea otro registro y, si ya terminó, vuelve a publicar `transaction.registered/failed` |
| `RecoverPendingOperationsUseCase` | `Single<RecoveryResult>` | Reintenta movimientos y transferencias pendientes |

### 4.2 Puertos de salida
| Puerto | Métodos | Adaptador (fase) |
|---|---|---|
| `TransactionRepositoryPort` | `save`, `findById`, `findByOperationId`, `findByProduct(range, filters, page)`, `findAll(filters, page)`, `findPendingOlderThan(instant)` | Mongo (P1) |
| `UnitOfWorkPort` | `inTransaction(Single<T>)` | Transacción de Mongo (P1): `Transfer` + `Transaction`, o movimiento + su `FEE`, se guardan juntos |
| `TransferRepositoryPort` | `save`, `findById`, `findByOperationId`, `findAll(filters)`, `findInProgressOlderThan(instant)` | Mongo (P2) |
| `AccountMovementPort` | `apply(operationId, accountId, type, amount, date)` y `reverse(operationId, accountId)`, ambos → `Single<MovementOutcome>` (aplicado o rechazado con motivo, comisión y saldo) | REST + circuit breaker (P1/P2) → Kafka con correlación por `operationId` (P3) |
| `AccountLookupPort` | `findById` → `AccountSnapshot` | REST + circuit breaker (P1/P2) → read model (P3) |
| `TransactionEventPublisherPort` | `publish(event)` | No-op (P1/P2) → Kafka (P3) |

**P2 vs P3 (ver `flows/01-transfer.md`, sección 7.1):** la lógica de avance de la saga es una sola función. En P2 la llama la propia petición tras cada respuesta REST de `account-service`. En P3 el adaptador de `AccountMovementPort` solo **envía** el comando (Kafka) y el único que avanza la saga es `HandleMovementResultUseCase`, al llegar el resultado; la petición solo espera (hasta 1,5 s) el estado terminal y, si no llega, responde 202.

## 5. API (contrato OpenAPI)

Base: `/api/v1`. Roles aplican cuando `security.enabled=true`.

| Método | Ruta | Descripción | Roles | Éxito | Errores |
|---|---|---|---|---|---|
| `POST` | `/deposits` | Depositar (`operationId`, `accountId`, `amount`, `description`) | `ADMIN`, `TELLER`, `CUSTOMER` (cuenta propia) | 201 (repetición: 200; sin respuesta de la cuenta: 202) | 400, 403, 404, 409, 422, 503 |
| `POST` | `/withdrawals` | Retirar (mismos campos) | `ADMIN`, `TELLER`, `CUSTOMER` (cuenta propia) | 201 (repetición: 200; sin respuesta de la cuenta: 202) | 400, 403, 404, 409, 422, 503 |
| `POST` | `/transfers` | Transferir (`operationId`, `sourceAccountId`, `targetAccountId`, `amount`, `description`) | `ADMIN`, `TELLER`, `CUSTOMER` (origen propio) | 201 (completada) o 202 (en proceso) | 400, 404, 422, 503 |
| `GET` | `/transfers/{id}` | Estado de la transferencia | `ADMIN`, `TELLER`, `CUSTOMER` (si el origen es suyo) | 200 | 404 |
| `GET` | `/transfers` | Listar (`accountId`, `status`, `operationId`) | `ADMIN`, `TELLER` | 200 | — |
| `GET` | `/products/{productId}/transactions` | Historial de un producto (`from`, `to`, `type`, `status`, `page`, `size`); más recientes primero. Producto sin movimientos: página vacía (no hay 404) | `ADMIN`, `TELLER`, `CUSTOMER` (suyo) | 200 | 400, 403 |
| `GET` | `/transactions/{id}` | Obtener un movimiento | `ADMIN`, `TELLER`, `CUSTOMER` (suyo) | 200 | 404 |
| `GET` | `/transactions` | Consulta administrativa (`customerId` **obligatorio**, `type`, `status`, `from`, `to`, `page`, `size`) | `ADMIN`, `TELLER` | 200 | 400 |
| `PUT` | `/transactions/{id}` | Editar la descripción | `ADMIN` | 200 | 400, 404 |
| `DELETE` | `/transactions/{id}` | Descartar un movimiento `FAILED` | `ADMIN` | 204 | 404, 422 |
| `POST` | `/transactions/records` | **Interno.** Registrar un movimiento de crédito o tarjeta | Interno (no se publica en el Gateway) | 201 (duplicado: 200) | 400, 422 |
| `POST` | `/transaction-recovery-runs` | Ejecutar ahora la recuperación de pendientes (`olderThanMinutes` opcional) | `ADMIN` | 200 | 400 |

Notas:
- La respuesta de un depósito o retiro incluye el movimiento y, si hubo comisión, el movimiento `FEE` enlazado.
- Si `account-service` no responde en 2 s, la respuesta es **202** y la operación queda `PENDING`; el cliente puede repetir con la **misma** `operationId` sin riesgo de duplicar. El 503 queda para cuando no se pudo consultar la cuenta y no se creó nada.
- **Contrato exacto** (campos, tipos, ejemplos, validaciones, reglas de la saga, documentos Mongo y datos de demo): `contracts/transaction-service/openapi.yaml` y `data-model.md`. Si difieren de esta ficha, el contrato manda.
- Códigos 422: `INVALID_AMOUNT`, `SAME_ACCOUNT`, `ACCOUNT_INACTIVE`, `INSUFFICIENT_FUNDS`, `MONTHLY_LIMIT_EXCEEDED`, `NOT_ALLOWED_DAY`, `INVALID_DATE`, `NOT_DISCARDABLE`, `TRANSACTION_DISCARDED`, `INVALID_TYPE_FOR_PRODUCT`; 409 `OPERATION_ID_REUSED`. Los 422 de depósito, retiro y transferencia traen además `transactionId` o `transferId` y `transferStatus`. Cuerpo estándar: `{ timestamp, status, code, message, path }`.
- `report-service` usa `GET /products/{productId}/transactions` en P2 (reporte por intervalo y últimos 10 movimientos con `size=10`).

## 6. Persistencia y caché
- **`transactions`:** un documento por movimiento. Índices: único en `operationId`; (`productId`, `occurredAt` desc); (`customerId`, `occurredAt` desc); (`status`, `createdAt`) para la recuperación; `transferId`.
- **`transfers`:** un documento por transferencia con `version` para control optimista. Índices: único en `operationId`; (`status`, `updatedAt`).
- **Consulta paginada:** consultas derivadas de Spring Data con `Pageable` y conteo aparte, sin `@Query`.
- **Read model (P3):** `account_snapshots` (id, cliente, tipo, estado), alimentado por `account.*`.
- **Caché:** no aplica. No hay datos maestros propios.

## 7. Mensajería (Kafka) — P3
| Publica | Consume |
|---|---|
| Eventos: `transaction.registered`, `transaction.failed`, `transaction.reversed`, `transaction.reversal.failed`, `transfer.completed`, `transfer.failed`. Comandos a `account-service`: `transaction.movement.requested`, `transaction.movement.reversal.requested` | `account.movement.applied/rejected/reversed`, `account.created/updated/deleted`, `credit.payment.registered`, `credit.card.charge.registered`, y los pedidos de movimiento de `debit-service` y `yanki-service` (contrato en los flujos) |

Los nombres finales de comandos y eventos se fijan al diseñar las sagas.

**Rol en sagas:** **iniciador y orquestador** de la transferencia (retirar origen → depositar destino → compensar). También ejecutará los retiros y depósitos que pidan `debit-service` y `yanki-service`, reutilizando el mismo mecanismo con `operationId` e idempotencia.

## 8. Filesystem

```
transaction-service/
├── pom.xml
├── Dockerfile
├── checkstyle.xml
├── README.md
├── docs/
│   ├── sequence/
│   └── uml/
└── src/
    ├── main/
    │   ├── java/com/bank/transaction/
    │   │   ├── TransactionServiceApplication.java
    │   │   ├── domain/
    │   │   │   ├── model/
    │   │   │   │   ├── Transaction.java, Transfer.java     (aggregate roots)
    │   │   │   │   ├── TransactionId.java, TransferId.java, OperationId.java
    │   │   │   │   ├── ProductRef.java, Money.java, FailureReason.java, DateRange.java
    │   │   │   │   ├── MovementOutcome.java                (resultado de account-service)
    │   │   │   │   ├── AccountSnapshot.java                (dato de lectura)
    │   │   │   │   └── ProductType.java, TransactionType.java, TransactionStatus.java, TransferKind.java, TransferStatus.java
    │   │   │   ├── service/
    │   │   │   │   └── TransferPolicy.java
    │   │   │   ├── event/                                  (4 eventos de dominio)
    │   │   │   └── exception/                              (TransactionNotFoundException, BusinessRuleViolationException con code, InvalidTransitionException, ...)
    │   │   ├── application/
    │   │   │   ├── command/
    │   │   │   ├── view/                                   (PageView, RecoveryResult)
    │   │   │   ├── port/
    │   │   │   │   ├── in/                                 (casos de uso)
    │   │   │   │   └── out/                                (5 puertos)
    │   │   │   └── usecase/
    │   │   └── infrastructure/
    │   │       ├── adapter/
    │   │       │   ├── in/rest/                            (TransactionController, TransferController, InternalRecordController, RecoveryController, GlobalExceptionHandler)
    │   │       │   ├── in/scheduler/                       (PendingRecoveryScheduler)
    │   │       │   ├── in/kafka/                           (AccountResultsConsumer, AccountEventsConsumer, CreditEventsConsumer, RequestedMovementsConsumer; P3)
    │   │       │   ├── out/persistence/                    (documentos, repositorios, adaptadores)
    │   │       │   ├── out/rest/                           (AccountMovementRestAdapter, AccountLookupRestAdapter; P1/P2 con circuit breaker)
    │   │       │   ├── out/readmodel/                      (adaptador de snapshots; P3)
    │   │       │   └── out/kafka/                          (AccountMovementKafkaAdapter con correlación, TransactionEventKafkaPublisher; P3)
    │   │       ├── mapper/
    │   │       └── config/                                 (beans, Clock, Mongo, Resilience4j, Kafka, seguridad)
    │   └── resources/
    │       ├── openapi/transaction-service/openapi.yaml   (+ openapi/common/common-schemas.yaml)
    │       ├── application.yml                     (solo nombre y config.import; el resto viene del Config Server)
    │       └── logback-spring.xml
    └── test/java/com/bank/transaction/
```

Los DTOs REST se generan desde el contrato.

## 9. Stack y configuración

**Base común:** Java 17, Spring Boot 3.x, Maven, WebFlux, RxJava 3, Lombok, MapStruct, Logback.

| Función | Dependencia |
|---|---|
| Web y cliente HTTP | `spring-boot-starter-webflux` (WebClient) |
| Persistencia | `spring-boot-starter-data-mongodb-reactive` (`RxJava3CrudRepository`) |
| Resiliencia | `resilience4j-spring-boot3` + `resilience4j-reactor`: circuit breaker, time limiter de 2 s y reintentos con la misma `operationId` |
| Proceso de recuperación | `@Scheduled` de Spring con `java.time.Clock` inyectado |
| Contrato | `openapi-generator-maven-plugin` |
| Config / Discovery | `spring-cloud-starter-config`, `spring-cloud-starter-netflix-eureka-client` |
| Eventos y seguridad (P3) | `reactor-kafka`/`spring-kafka`, `spring-boot-starter-oauth2-resource-server` |
| Calidad y pruebas | Checkstyle, Jacoco, JUnit 5, Mockito, `reactor-test`, RxJava `TestObserver`, WebTestClient |

**Propiedades en Config Server:** puerto, Mongo, URL de `account-service`, timeouts, reintentos y umbrales del circuit breaker, minutos antes de considerar pendiente una operación, reintentos de compensación, tamaño máximo de página, cron de recuperación, Kafka, `security.enabled`.

**Resiliencia:** este es el servicio más expuesto a fallos de otro servicio. El circuit breaker con timeout de 2 s protege las llamadas a `account-service`; como el efecto puede haberse aplicado aunque la respuesta se pierda, la seguridad viene de la **idempotencia por `operationId`** y del estado persistido (`PENDING`, `SOURCE_DEBITED`, `COMPENSATING`), que permite reintentar sin duplicar.

## 10. Estrategia de pruebas
| Capa | Qué se prueba | Herramientas |
|---|---|---|
| Dominio: `Transaction` | Transiciones válidas e inválidas, comisión enlazada, edición y descarte permitidos | JUnit 5 (sin Spring) |
| Dominio: `Transfer` | Todas las transiciones de la máquina de estados, incluidas las inválidas | JUnit 5 |
| Dominio: `TransferPolicy` | Mismo origen y destino, cuenta inactiva, `OWN` vs `THIRD_PARTY` | JUnit 5 parametrizado |
| Casos de uso | Depósito y retiro (éxito, rechazo, comisión, idempotencia, timeout → `PENDING`); transferencia feliz; crédito rechazado con compensación; compensación fallida; registro externo duplicado; recuperación | Mockito + `TestObserver` |
| Adaptadores REST salientes | Timeout de 2 s, circuit breaker, reintento con la misma `operationId` | WireMock o `MockWebServer` |
| Persistencia | Índice único de `operationId`, consultas paginadas, control de versión | Testcontainers *(opcional)* |
| Controllers, scheduler y consumers | Contrato, códigos (201/200/202/503), roles; eventos duplicados | WebTestClient |
| Cobertura | Reporte de todo el código | Jacoco |

## 11. Diagramas a elaborar
- [ ] Secuencia: depósito con comisión e idempotencia
- [ ] Secuencia: transferencia exitosa (débito → crédito)
- [ ] Secuencia: transferencia con compensación (crédito rechazado → reversa del débito)
- [ ] Secuencia: registro de un pago de crédito (REST en P1/P2, evento en P3)
- [ ] Secuencia: recuperación de una operación pendiente
- [ ] UML del dominio y diagrama de estados de `Transfer`

## 12. Decisiones y pendientes
- **Decidido:**
  - `transaction-service` es dueño del historial y la **única puerta de entrada** para mover saldo de cuentas; el saldo y sus reglas siguen en `account-service`.
  - Dos aggregates: `Transaction` (un movimiento de un producto) y `Transfer` (estado de la saga). Así el historial de un producto es una consulta simple.
  - La comisión se guarda como un movimiento `FEE` aparte.
  - Los movimientos de crédito y tarjeta se registran sin validar.
  - Historial inmutable: solo se edita la descripción y solo se descartan los `FAILED`.
  - Las transferencias son solo entre cuentas, en `PEN`; cada cuenta aplica sus reglas y comisiones a su pata.
  - Igual código de casos de uso en P1/P2 y P3: cambia solo el adaptador de `AccountMovementPort`.
  - Las operaciones pendientes se reintentan con la misma `operationId` (por el cliente o por el proceso de recuperación).
  - Contrato detallado en `contracts/transaction-service/`: `operationId` de cliente de máximo 56 caracteres (sufijos derivados); `Transaction.reversal` embebido para correlacionar reversas; escritura conjunta con transacción de Mongo; el historial usa consultas derivadas por combinación de filtros, y la consulta administrativa exige `customerId`.
  - El reporte de "últimos 10 movimientos" de la tarjeta de débito se arma con los pagos de `debit-service`; aquí el retiro queda registrado como `DEBIT_PAYMENT` sobre la cuenta. Los eventos `transaction.registered`/`transaction.failed` llevan `operationId` para que quien pidió el movimiento lo correlacione.
- **Pendiente:**
  - **CRUD del historial:** el enunciado pide CRUD para todas las entidades de negocio, pero un historial financiero no debería editarse ni borrarse. Se aplicó la versión restringida; conviene confirmarlo con el instructor.
  - ~~Saga asíncrona vs. comando con espera~~ **Resuelto** en `flows/01-transfer.md`: el consumidor es el único que avanza la saga en P3; la petición espera hasta 1,5 s el estado terminal y, si no llega, responde 202.
  - ~~Resultado de las reversas pedidas por `yanki-service`~~ **Definido** en `flows/03-yanki-payment.md`: eventos `transaction.reversed` y `transaction.reversal.failed`.
  - ~~Contrato de los movimientos pedidos por `debit-service` y `yanki-service`~~ **Definido** en `contracts/events/kafka-contract.md` (6.9) y `contracts/transaction-service/data-model.md` (2.5 y 7.2).
  - Publicación confiable de eventos (patrón outbox), común a todos los servicios.
  - ~~Reversa de un depósito sin saldo suficiente~~ **Definido:** `account-service` la rechaza (`INSUFFICIENT_FUNDS`) y la saga termina en `COMPENSATION_FAILED`.

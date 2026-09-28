# `account-service`

## 1. Resumen

| Campo | Valor |
|---|---|
| Propósito | Dueño de las cuentas bancarias (pasivos): apertura, saldo y reglas de cada movimiento |
| Bounded context | Cuentas bancarias |
| Fase | P1 (perfiles VIP/PYME, comisiones y monto de apertura en P2; eventos, read models y Redis en P3) |
| Puerto | 8082 |
| Base de datos | MongoDB: `accounts`, `account_products`, `account_operations` (+ read models en P3) |
| Depende de | `customer-service` (tipo, perfil y estado del cliente) y `credit-service` (tarjeta de crédito activa y deuda vencida). REST en P1/P2, eventos en P3 |

## 2. Responsabilidades

**Hace:**
- CRUD de cuentas: ahorro, corriente y plazo fijo.
- Validar las reglas de apertura por tipo de cliente y perfil (personal, empresarial, VIP, PYME).
- Gestionar titulares y firmantes de cuentas empresariales.
- Ser **dueño del saldo y de las reglas de cada movimiento**: monto, límites mensuales, día del plazo fijo, transacciones libres y comisión.
- Aplicar y **revertir** movimientos de forma idempotente (base para las sagas).
- Exponer saldo y promedio diario (VIP).
- Mantener el catálogo de condiciones por tipo de cuenta y perfil.

**No hace:**
- Historial de movimientos (`transaction-service`: registra, consulta y decide las transferencias; aquí solo se aplica el efecto sobre el saldo).
- Tarjetas de débito (`debit-service`).
- Créditos y tarjetas de crédito (`credit-service`).
- Reportes (`report-service`).

## 3. Modelo de dominio (DDD)

### 3.1 Aggregates y entidades
| Elemento | Tipo | Descripción |
|---|---|---|
| `Account` | Aggregate root | Cuenta con su saldo, condiciones vigentes, titulares, firmantes y actividad del mes. Protege todas las invariantes de saldo y movimientos |
| `AccountProduct` | Aggregate root (catálogo) | Condiciones por (tipo de cuenta, perfil). Dato maestro; se copia a la cuenta al abrirla |

Atributos de `Account`: `id`, `accountNumber`, `customerId` (solo referencia), `customerType`, `customerProfile` (foto al abrir), `type`, `alias`, `balance`, `conditions` (copia), `movementDayOfMonth` (solo plazo fijo), `holders`, `signers`, `monthlyActivity`, `balanceTracker`, `status`, `version`, `createdAt`, `updatedAt`.

Comportamiento: `open(...)` (factory), `updateParties(...)`, `applyMovement(operationId, type, amount, date)` → `MovementResult`, `reverseMovement(...)`, `close()`.

### 3.2 Value objects
| VO | Campos | Validaciones |
|---|---|---|
| `AccountId` | `value` | No vacío |
| `AccountNumber` | `value` | 14 dígitos |
| `Money` | `amount` (BigDecimal, 2 decimales), `currency` | No nulo. Solo `PEN` en el demo. Operaciones `plus`, `minus`, `isGreaterThan` |
| `AccountParty` | `documentType`, `documentNumber`, `fullName` | Documento válido; nombre no vacío. Titulares y firmantes son datos propios, **no requieren ser clientes** |
| `AccountConditions` | `maintenanceFee`, `minimumOpeningAmount`, `monthlyMovementLimit` (opcional), `freeTransactionsLimit`, `transactionFee`, `minimumDailyAverage` (opcional), `requiresCreditCard` | Montos ≥ 0, límites ≥ 0 |
| `MonthlyActivity` | `yearMonth`, `movementCount` | Se reinicia al cambiar de mes |
| `BalanceTracker` | `yearMonth`, `accumulatedBalanceDays`, `lastBalance`, `lastChangeDate` | Acumula saldo × días para calcular el promedio diario del mes |
| `MovementResult` | `newBalance`, `fee`, `movementNumber` | Resultado inmutable de aplicar un movimiento |

### 3.3 Enums
| Enum | Valores |
|---|---|
| `AccountType` | `SAVINGS`, `CHECKING`, `FIXED_TERM` |
| `AccountStatus` | `ACTIVE`, `INACTIVE` |
| `MovementType` | `DEPOSIT`, `WITHDRAWAL` |
| `PartyRole` | `HOLDER`, `SIGNER` |

### 3.4 Reglas de negocio e invariantes
| # | Regla | Dónde se aplica |
|---|---|---|
| 1 | El cliente debe existir y estar `ACTIVE` | `AccountOpeningPolicy` (dato vía `CustomerLookupPort`) |
| 2 | Cliente con **deuda vencida** no puede abrir cuenta (P3) | `AccountOpeningPolicy` (dato vía `OverdueDebtPort`) |
| 3 | `PERSONAL`: máximo 1 ahorro y 1 corriente; plazo fijo sin límite | `AccountOpeningPolicy` + índice parcial en Mongo contra carreras |
| 4 | `BUSINESS`: solo corriente, cantidad ilimitada | `AccountOpeningPolicy` |
| 5 | `BUSINESS`: ≥ 1 titular y 0..n firmantes. `PERSONAL`: sin titulares ni firmantes extra | `Account.open` |
| 6 | Monto de apertura ≥ mínimo de la condición (puede ser 0); pasa a ser el saldo inicial | `Account.open` |
| 7 | Si la condición exige tarjeta de crédito (VIP ahorro, PYME corriente), el cliente debe tener una en estado `ACTIVE` (una `OVERDUE` o `CLOSED` no cuenta) | `AccountOpeningPolicy` (dato vía `CreditCardLookupPort`) |
| 8 | Plazo fijo: exige `movementDayOfMonth` (1–28); solo 1 movimiento al mes y únicamente ese día | `Account.applyMovement` |
| 9 | Ahorro: tope de movimientos mensuales; al superarlo se rechaza | `Account.applyMovement` |
| 10 | Toda cuenta: hasta N transacciones libres al mes; las siguientes cobran comisión, que se descuenta del saldo | `Account.applyMovement` |
| 11 | Monto > 0. Un retiro no puede dejar saldo negativo (monto + comisión ≤ saldo) | `Account.applyMovement` |
| 12 | Cuenta `INACTIVE` no admite movimientos | `Account.applyMovement` |
| 13 | Cierre (baja lógica) solo con saldo 0 | `Account.close` |
| 14 | Idempotencia: una misma `operationId` no se aplica dos veces | Caso de uso + `OperationLogPort` |
| 15 | Promedio diario VIP: se calcula y se expone; incumplirlo **no bloquea** nada | `BalanceTracker` |
| 16 | Tipo, cliente y moneda no cambian; el saldo solo cambia por movimientos | `Account` |
| 17 | Reversa: devuelve saldo, comisión y contador de movimientos. Idempotente | `Account.reverseMovement` |

**Condiciones de ejemplo** (catálogo inicial, editables). Si no existe la combinación (tipo, perfil), se usa `STANDARD` del mismo tipo:

| Tipo / Perfil | Mantenimiento | Apertura mín. | Tope mensual | Libres/mes | Comisión | Prom. diario mín. | Exige tarjeta |
|---|---|---|---|---|---|---|---|
| `SAVINGS` / `STANDARD` | 0 | 0 | 10 | 5 | 2.00 | — | No |
| `SAVINGS` / `VIP` | 0 | 0 | 10 | 5 | 2.00 | 500 | Sí |
| `CHECKING` / `STANDARD` | 15 | 0 | — | 5 | 2.00 | — | No |
| `CHECKING` / `PYME` | 0 | 0 | — | 5 | 2.00 | — | Sí |
| `FIXED_TERM` / `STANDARD` | 0 | 100 | 1 | 5 | 2.00 | — | No |

### 3.5 Domain services
| Servicio | Responsabilidad |
|---|---|
| `AccountOpeningPolicy` | Valida las reglas 1–7. Es puro: recibe los datos ya obtenidos (cliente, cantidad de cuentas por tipo, tarjeta, deuda) y no llama a nadie. Se implementa como cadena de validaciones (**Chain of Responsibility**) |
| `AccountNumberGenerator` | Genera el número de cuenta de 14 dígitos |

### 3.6 Eventos de dominio
| Evento | Cuándo | Datos mínimos |
|---|---|---|
| `AccountCreated` | Al abrir | Estado: `accountId`, `maskedNumber`, `customerId`, `type`, `status`, `updatedAt` (sin saldo, que cambia con cada movimiento) |
| `AccountUpdated` | Al cambiar alias, titulares o firmantes | Estado (los mismos campos) |
| `AccountClosed` | Al dar de baja | Estado con `status = INACTIVE` |
| `MovementApplied` | Movimiento aplicado | `operationId`, `accountId`, `type`, `amount`, `fee`, `newBalance`, `movementNumber` |
| `MovementRejected` | Movimiento rechazado | `operationId`, `accountId`, `reasonCode` |
| `MovementReversed` | Reversa aplicada | `operationId`, `accountId`, `newBalance` |
| `MovementReversalRejected` | Reversa rechazada (operación inexistente o cuenta cerrada) | `operationId`, `accountId`, `reasonCode` |

Campos, tópicos y claves de todos estos mensajes: `contracts/events/kafka-contract.md`.

Códigos de rechazo: `INSUFFICIENT_FUNDS`, `ACCOUNT_INACTIVE`, `MONTHLY_LIMIT_EXCEEDED`, `NOT_ALLOWED_DAY`, `INVALID_AMOUNT`, `INVALID_DATE`, `OPERATION_ID_REUSED`; en reversas también `OPERATION_NOT_FOUND`, `OPERATION_NOT_APPLIED`.

## 4. Casos de uso y puertos

### 4.1 Puertos de entrada (casos de uso)
| Caso de uso | Retorno | Descripción |
|---|---|---|
| `OpenAccountUseCase` | `Single<Account>` | Reúne los datos por puertos, aplica `AccountOpeningPolicy`, crea y guarda |
| `FindAccountByIdUseCase` | `Single<Account>` | Error `AccountNotFoundException` si no existe |
| `FindAccountsUseCase` | `Flowable<Account>` | Filtros: `customerId`, `type`, `status` |
| `GetBalanceUseCase` | `Single<BalanceView>` | Saldo, moneda y, si aplica, promedio diario y cumplimiento del mínimo |
| `UpdateAccountUseCase` | `Single<Account>` | Alias, titulares y firmantes (empresarial) |
| `CloseAccountUseCase` | `Completable` | Baja lógica con saldo 0 |
| `ApplyMovementUseCase` | `Single<MovementResult>` | Depósito o retiro idempotente por `operationId` |
| `ReverseMovementUseCase` | `Single<MovementResult>` | Compensación idempotente |
| `FindConditionsUseCase` | `Flowable<AccountProduct>` | Consulta el catálogo |
| `UpdateConditionsUseCase` | `Single<AccountProduct>` | Edita las condiciones (no afecta cuentas ya abiertas) |

### 4.2 Puertos de salida
| Puerto | Métodos | Adaptador (fase) |
|---|---|---|
| `AccountRepositoryPort` | `save`, `findById`, `findAll(filters)`, `countActiveByCustomerAndType` | Mongo (P1) |
| `AccountProductRepositoryPort` | `findByTypeAndProfile`, `findAll`, `save` | Mongo (P1) |
| `OperationLogPort` | `find(operationId)`, `save(operation)` | Mongo `account_operations` (P1) |
| `CustomerLookupPort` | `findById` → `CustomerSnapshot` (tipo, perfil, estado) | REST + circuit breaker (P1/P2) → read model (P3) |
| `CreditCardLookupPort` | `hasActiveCreditCard(customerId)` (solo estado `ACTIVE`; una tarjeta vencida no cuenta) | No-op, siempre `false` (P1, `credit-service` todavía no existe en la receta 1.4 ni hay condiciones VIP/PYME que lo consulten) → REST + circuit breaker (P2, tarea 2.4) → read model (P3) |
| `OverdueDebtPort` | `hasOverdueDebt(customerId)` | No-op, siempre `false` (P1/P2) → read model (P3) |
| `UnitOfWorkPort` | `inTransaction(Single<T>)` | Transacción de Mongo (P1). Escribe `accounts` y `account_operations` juntos; ver `contracts/account-service/data-model.md` 2.4 |
| `AccountEventPublisherPort` | `publish(event)` | No-op (P1/P2) → Kafka (P3) |
| `ProductCachePort` | `get`, `put`, `evict` | No-op (P1/P2) → Redis (P3) |

## 5. API (contrato OpenAPI)

Base: `/api/v1`. Roles aplican cuando `security.enabled=true`.

| Método | Ruta | Descripción | Roles | Éxito | Errores |
|---|---|---|---|---|---|
| `POST` | `/accounts` | Abrir cuenta | `ADMIN`, `TELLER`, `CUSTOMER` (solo para sí) | 201 | 400, 404, 422 |
| `GET` | `/accounts` | Listar (`customerId`, `type`, `status`) | `ADMIN`, `TELLER`, `CUSTOMER` (solo las suyas) | 200 | — |
| `GET` | `/accounts/{id}` | Obtener | `ADMIN`, `TELLER`, `CUSTOMER` (solo suya) | 200 | 404 |
| `GET` | `/accounts/{id}/balance` | Saldo y promedio diario | `ADMIN`, `TELLER`, `CUSTOMER` (solo suya) | 200 | 404 |
| `PUT` | `/accounts/{id}` | Actualizar alias, titulares y firmantes | `ADMIN`, `TELLER` | 200 | 400, 404, 422 |
| `DELETE` | `/accounts/{id}` | Baja lógica (saldo 0) | `ADMIN`, `TELLER` | 204 | 404, 422 |
| `GET` | `/account-conditions` | Ver catálogo de condiciones | `ADMIN`, `TELLER`, `CUSTOMER` | 200 | — |
| `PUT` | `/account-conditions/{id}` | Editar condiciones | `ADMIN` | 200 | 400, 404 |
| `POST` | `/accounts/{id}/movements` | **Interno.** Aplicar depósito/retiro (`operationId`, `type`, `amount`, `date`) | Interno (no se publica en el Gateway) | 200 | 404, 422 |
| `POST` | `/accounts/{id}/movements/{operationId}/reversal` | **Interno.** Compensar un movimiento | Interno | 200 | 404, 422 |

Los dos endpoints internos los usa `transaction-service` en P1/P2. En P3 se reemplazan por comandos y eventos de Kafka.

**Contrato exacto** (campos, tipos, ejemplos, validaciones, cálculos, documentos Mongo y datos de demo): `contracts/account-service/openapi.yaml` y `data-model.md`. Si difieren de esta ficha, el contrato manda.

Errores: 400 formato, 404 no existe, 409 conflicto (`CONCURRENT_MODIFICATION`, `OPERATION_ID_REUSED`), 422 regla de negocio (con `code`: `SAVINGS_LIMIT_REACHED`, `CHECKING_LIMIT_REACHED`, `CREDIT_CARD_REQUIRED`, `OVERDUE_DEBT`, `INSUFFICIENT_FUNDS`, `NOT_ALLOWED_DAY`, etc.; lista completa en el contrato), 503 si `customer-service` o `credit-service` no responden en 2 s. Cuerpo estándar: `{ timestamp, status, code, message, path }`.

## 6. Persistencia y caché
- **`accounts`:** documento por cuenta con `conditions`, `holders`, `signers`, `monthlyActivity` y `balanceTracker` embebidos. Índices: único en `accountNumber`; (`customerId`, `type`, `status`).
- **Concurrencia:** control optimista con campo `version`; ante conflicto se reintenta. Es clave para no perder actualizaciones de saldo.
- **Carrera en la regla 3:** dos índices únicos parciales sobre `customerId` (uno para ahorro y otro para corriente) con filtro `customerType = PERSONAL`, `type` y `status = ACTIVE`.
- **Atomicidad:** aplicar o revertir un movimiento escribe `accounts` y `account_operations` en una transacción de Mongo (replica set de un nodo).
- **`account_products`:** catálogo, sembrado al arrancar con la tabla de la sección 3.4.
- **`account_operations`:** registro de operaciones (`_id` = `operationId`, cuenta, tipo, monto, comisión, estado `APPLIED`/`REJECTED`/`REVERSED`). Da la idempotencia y los datos para revertir; los rechazos también se guardan.
- **Read models (P3):** `customer_snapshots` (id, tipo, perfil, estado), `credit_card_snapshots` (cliente y tarjeta con su estado; **solo una tarjeta `ACTIVE` cumple** el requisito VIP/PYME, una `OVERDUE` o `CLOSED` no), `overdue_customers` (`customerId`, `overdue`, `updatedAt`; se aplica el evento solo si `occurredAt` ≥ `updatedAt`; ver `flows/04-overdue-debt.md`).
- **Caché (P3):** cache-aside del catálogo `account_products` en Redis, evicción al editar.

## 7. Mensajería (Kafka) — P3
| Publica | Consume |
|---|---|
| `account.created`, `account.updated`, `account.deleted`, `account.movement.applied`, `account.movement.rejected`, `account.movement.reversed` | `customer.created/updated/deleted`, `credit.card.created/updated/closed`, `credit.overdue.detected/cleared`, comandos de movimiento y reversa emitidos por `transaction-service` |

Los nombres finales de los comandos de movimiento se fijan al diseñar las sagas.

**Rol en sagas:** **participante**. Ejecuta el paso "aplicar movimiento" y la compensación "revertir movimiento" en transferencias, pagos con débito y pagos Yanki asociados a débito. Responde siempre con un evento de éxito o de rechazo y es idempotente.

## 8. Filesystem

```
account-service/
├── pom.xml
├── Dockerfile
├── checkstyle.xml
├── README.md
├── docs/
│   ├── sequence/
│   └── uml/
└── src/
    ├── main/
    │   ├── java/com/bank/account/
    │   │   ├── AccountServiceApplication.java
    │   │   ├── domain/
    │   │   │   ├── model/
    │   │   │   │   ├── Account.java                (aggregate root)
    │   │   │   │   ├── AccountProduct.java         (aggregate root, catálogo)
    │   │   │   │   ├── AccountId.java, AccountNumber.java, Money.java
    │   │   │   │   ├── AccountParty.java, AccountConditions.java
    │   │   │   │   ├── MonthlyActivity.java, BalanceTracker.java, MovementResult.java
    │   │   │   │   ├── CustomerSnapshot.java       (dato de lectura, no es aggregate)
    │   │   │   │   └── AccountType.java, AccountStatus.java, MovementType.java, PartyRole.java
    │   │   │   ├── service/
    │   │   │   │   ├── AccountOpeningPolicy.java
    │   │   │   │   ├── validation/                 (eslabones de la cadena de validación)
    │   │   │   │   └── AccountNumberGenerator.java
    │   │   │   ├── event/                          (6 eventos de dominio)
    │   │   │   └── exception/                      (AccountNotFoundException, BusinessRuleViolationException con code, ...)
    │   │   ├── application/
    │   │   │   ├── command/                        (OpenAccountCommand, ApplyMovementCommand, ...)
    │   │   │   ├── port/
    │   │   │   │   ├── in/                         (10 casos de uso)
    │   │   │   │   └── out/                        (9 puertos)
    │   │   │   └── usecase/
    │   │   └── infrastructure/
    │   │       ├── adapter/
    │   │       │   ├── in/rest/                    (AccountController, ConditionsController, InternalMovementController, GlobalExceptionHandler)
    │   │       │   ├── in/kafka/                   (CustomerEventsConsumer, CreditEventsConsumer, MovementCommandsConsumer)
    │   │       │   ├── out/persistence/            (documentos + repositorios + adaptadores de accounts, products, operations)
    │   │       │   ├── out/rest/                   (CustomerRestAdapter, CreditRestAdapter; P1/P2 con circuit breaker)
    │   │       │   ├── out/readmodel/              (adaptadores de snapshots; P3)
    │   │       │   ├── out/kafka/                  (AccountEventKafkaPublisher)
    │   │       │   └── out/cache/                  (ProductRedisCacheAdapter)
    │   │       ├── mapper/
    │   │       └── config/                         (beans, Mongo, Resilience4j, Kafka, Redis, seguridad, AccountProductSeeder)
    │   └── resources/
    │       ├── openapi/account-service/openapi.yaml   (+ openapi/common/common-schemas.yaml)
    │       ├── application.yml                     (solo nombre y config.import; el resto viene del Config Server)
    │       └── logback-spring.xml
    └── test/java/com/bank/account/
```

Los DTOs REST se generan desde el contrato. Los adaptadores `out/rest` y `out/readmodel` implementan los mismos puertos: en P3 se cambia el adaptador sin tocar dominio ni casos de uso.

## 9. Stack y configuración

**Base común:** Java 17, Spring Boot 3.x, Maven, WebFlux, RxJava 3, Lombok, MapStruct, Logback.

| Función | Dependencia |
|---|---|
| Web y cliente HTTP | `spring-boot-starter-webflux` (WebClient para `out/rest`) |
| Persistencia | `spring-boot-starter-data-mongodb-reactive` (`RxJava3CrudRepository`) |
| Resiliencia | `resilience4j-spring-boot3` + `resilience4j-reactor` (circuit breaker y time limiter de 2 s en las llamadas salientes) |
| Contrato | `openapi-generator-maven-plugin` |
| Config / Discovery | `spring-cloud-starter-config`, `spring-cloud-starter-netflix-eureka-client` |
| Eventos, caché, seguridad (P3) | `reactor-kafka`/`spring-kafka`, `spring-boot-starter-data-redis-reactive`, `spring-boot-starter-oauth2-resource-server` |
| Calidad y pruebas | Checkstyle, Jacoco, JUnit 5, Mockito, `reactor-test`, RxJava `TestObserver`, WebTestClient |

**Propiedades en Config Server:** puerto, Mongo, URLs de `customer-service` y `credit-service` (o nombres Eureka), timeouts y umbrales del circuit breaker, Kafka, Redis, TTL de caché, `security.enabled`.

**Resiliencia:** aquí sí hay llamadas salientes (clientes y tarjetas en P1/P2), así que es el servicio donde el circuit breaker con timeout de 2 s se aplica de forma natural. Si falla una consulta, la apertura de cuenta responde 503; los movimientos no dependen de otros servicios.

## 10. Estrategia de pruebas
| Capa | Qué se prueba | Herramientas |
|---|---|---|
| Dominio: `Account` | Casos por tipo: ahorro (tope), plazo fijo (día y 1 movimiento), comisión tras las libres, saldo insuficiente, cambio de mes, reversa, promedio diario | JUnit 5 (sin Spring), pruebas parametrizadas |
| Dominio: políticas | `AccountOpeningPolicy` con cada regla 1–7 (personal, empresarial, VIP, PYME, deuda vencida) | JUnit 5 |
| Casos de uso | Flujos con puertos simulados, idempotencia por `operationId`, reintento por conflicto de versión | Mockito + `TestObserver` |
| Adaptadores REST salientes | Timeout de 2 s y circuit breaker abierto | WireMock o `MockWebServer` |
| Persistencia | Índices únicos y parcial, control de versión | Testcontainers *(opcional)* |
| Controllers y consumers | Contrato, códigos, errores; consumo idempotente | WebTestClient, tests de consumidor |
| Cobertura | Reporte de todo el código | Jacoco |

## 11. Diagramas
- [x] Secuencia: abrir cuenta (P1/P2 con REST y P3 con read models)
- [x] Secuencia: aplicar retiro con comisión e idempotencia
- [x] Secuencia: compensación (reversa) de un movimiento
- [x] UML del dominio (`Account`, `AccountProduct` y VO)

Como en `customer-service`, los diagramas viven en el propio repo del servicio, no aquí:
`account-service/docs/sequence/` (abrir cuenta, aplicar movimiento, revertir movimiento) y
`account-service/docs/uml/account-domain.md`.

## 12. Decisiones y pendientes
- **Decidido:**
  - `account-service` es dueño del saldo y de las reglas de movimiento; `transaction-service` es dueño del historial.
  - Titulares y firmantes son datos propios de la cuenta (documento + nombre), no clientes. Esto cierra el pendiente de `customer-service`.
  - El catálogo de condiciones vive en Mongo con Redis en P3 (dato maestro). Cada cuenta guarda una copia de sus condiciones al abrirse.
  - Idempotencia por `operationId` y control optimista de versión para el saldo.
  - Un cliente VIP solo tiene condiciones especiales en su cuenta de ahorro, y un PYME solo en su cuenta corriente; el resto usa `STANDARD`.
  - Solo moneda `PEN`.
  - Contrato detallado en `contracts/account-service/`: transacción de Mongo para saldo y operación; catálogo con id natural `<TIPO>_<PERFIL>`; dos códigos de tope de cuentas personales; rechazos guardados e idempotentes; comisión también en depósitos.
- **Pendiente:**
  - Qué ocurre si una cuenta VIP no cumple el promedio mínimo: por ahora solo se informa.
  - Cobro automático de la comisión de mantenimiento: por ahora es una condición; un job mensual es opcional.
  - Publicación confiable de eventos (patrón outbox) y nombres finales de los comandos: se definen al diseñar las sagas.
  - Reversa de un depósito sin saldo suficiente: se rechaza con `INSUFFICIENT_FUNDS` (en la práctica las sagas solo revierten retiros).
  - **La reversa se aplica aunque la cuenta esté `INACTIVE`** (compensar es devolver dinero, no operar). Si la cuenta fue cerrada, la reversa falla y la saga termina en `COMPENSATION_FAILED` (ver `flows/01-transfer.md`).

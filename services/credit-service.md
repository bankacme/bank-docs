# `credit-service`

## 1. Resumen

| Campo | Valor |
|---|---|
| Propósito | Dueño de los productos de crédito (créditos y tarjetas de crédito), sus pagos y consumos, y **fuente de verdad de la deuda vencida** |
| Bounded context | Créditos y tarjetas de crédito (activos) |
| Fase | P1 (pagos de terceros, bloqueo por deuda vencida, eventos en P3) |
| Puerto | 8083 |
| Base de datos | MongoDB: `credits`, `credit_cards`, `credit_operations` (+ read model en P3) |
| Depende de | `customer-service` (tipo y estado del cliente) y `transaction-service` (registrar el historial en P1/P2). REST en P1/P2, eventos en P3 |

## 2. Responsabilidades

**Hace:**
- CRUD de créditos (personal y empresarial) y de tarjetas de crédito.
- Registrar **pagos** (propios y de terceros) y **consumos** de tarjeta con control de línea.
- Consultar saldo disponible de tarjetas (RF-31) y saldo pendiente de créditos.
- **Detectar deuda vencida** con un proceso diario y una ejecución manual; avisar a los demás servicios.
- Bloquear la adquisición de nuevos productos si el cliente tiene deuda vencida (RF-60).

**No hace:**
- Historial de movimientos: lo guarda `transaction-service`; aquí solo se registra el efecto sobre el producto y se avisa.
- Cuentas y saldos bancarios (`account-service`).
- Evaluación crediticia, aprobación, cuotas ni intereses (fuera de alcance del demo).
- Debitar una cuenta al pagar: el pago se registra directamente sobre el producto.

## 3. Modelo de dominio (DDD)

### 3.1 Aggregates y entidades
| Elemento | Tipo | Descripción |
|---|---|---|
| `Credit` | Aggregate root | Crédito con monto, saldo pendiente y fecha de vencimiento. Protege reglas de pago y vencimiento |
| `CreditCard` | Aggregate root | Tarjeta con línea, monto usado, fecha de pago y estado. Protege reglas de consumo y pago |

Son dos aggregates independientes: no comparten transacciones y solo se relacionan por `customerId`.

Atributos de `Credit`: `id`, `customerId`, `type`, `principalAmount`, `outstandingBalance`, `dueDate`, `status`, `version`, `createdAt`, `updatedAt`.
Comportamiento: `open(...)`, `registerPayment(amount)`, `markOverdueIfDue(asOf)`, `reschedule(newDueDate)`, `close()`.

Atributos de `CreditCard`: `id`, `customerId`, `type`, `cardNumber`, `creditLimit`, `usedAmount`, `paymentDueDate` (opcional), `status`, `version`, `createdAt`, `updatedAt`.
Comportamiento: `issue(...)`, `charge(amount, date)`, `registerPayment(amount)`, `markOverdueIfDue(asOf)`, `changeLimit(newLimit)`, `close()`. Derivado: `availableCredit = creditLimit − usedAmount`.

### 3.2 Value objects
| VO | Campos | Validaciones |
|---|---|---|
| `CreditId`, `CreditCardId`, `CustomerId`, `OperationId` | `value` | No vacío |
| `Money` | `amount` (BigDecimal, 2 decimales), `currency` | No nulo. Solo `PEN` en el demo. `plus`, `minus`, `isGreaterThan` |
| `DueDate` | `value` (fecha) | `isPastDue(asOf)`. Al crear debe ser futura (salvo modo demo) |
| `CardNumber` | `value` | 16 dígitos ficticios generados; se expone enmascarado (`**** 1234`) |
| `PaymentResult` | `newBalance`, `status` | Resultado inmutable de un pago |
| `ChargeResult` | `usedAmount`, `availableCredit`, `paymentDueDate` | Resultado inmutable de un consumo |

### 3.3 Enums
| Enum | Valores |
|---|---|
| `OwnerType` | `PERSONAL`, `BUSINESS` (se deriva del tipo de cliente, no lo elige el usuario) |
| `CreditStatus` | `ACTIVE`, `OVERDUE`, `PAID`, `CLOSED` |
| `CardStatus` | `ACTIVE`, `OVERDUE`, `CLOSED` |
| `OperationType` | `PAYMENT`, `CHARGE` |

### 3.4 Reglas de negocio e invariantes
| # | Regla | Dónde se aplica |
|---|---|---|
| 1 | El cliente debe existir y estar `ACTIVE` | `AcquisitionPolicy` (dato vía `CustomerLookupPort`) |
| 2 | El tipo del producto es el del cliente: crédito y tarjeta personal para `PERSONAL`, empresarial para `BUSINESS` | `AcquisitionPolicy` |
| 3 | `PERSONAL`: un solo crédito no pagado (activo o vencido). Uno ya pagado no cuenta. `BUSINESS`: varios | `AcquisitionPolicy` + índice parcial contra carreras |
| 4 | No se exige cuenta bancaria | — (no hay validación) |
| 5 | Cliente con **deuda vencida** no puede adquirir crédito ni tarjeta (P3) | `AcquisitionPolicy` (dato propio: `existsOverdueByCustomer`) |
| 6 | Crédito: monto > 0 y vencimiento futuro (con modo demo puede ser pasado) | `Credit.open` |
| 7 | Tarjeta: línea > 0. Sin límite de tarjetas por cliente | `CreditCard.issue` |
| 8 | Pago: monto > 0 y no puede superar el saldo pendiente | `registerPayment` |
| 9 | Cualquier cliente puede pagar productos de terceros; se registra quién pagó | Caso de uso (`payerCustomerId`) |
| 10 | Crédito con saldo 0 pasa a `PAID`; tarjeta con saldo 0 limpia su fecha de pago | `registerPayment` |
| 11 | Consumo: monto > 0 y ≤ crédito disponible | `CreditCard.charge` |
| 12 | Al primer consumo con saldo 0, la fecha de pago = fecha del consumo + plazo (30 días, configurable) | `CreditCard.charge` |
| 13 | **Vencido** = fecha de pago pasada y saldo pendiente > 0 | `markOverdueIfDue` |
| 14 | Un producto vencido vuelve a normal al pagarse por completo (o al reprogramar el crédito a fecha futura) | `registerPayment` / `reschedule` |
| 15 | La deuda vencida no bloquea pagos ni consumos: solo la adquisición de productos nuevos | Decisión de negocio |
| 16 | Cierre (baja lógica) solo con saldo 0 | `close` |
| 17 | Cambiar la línea: nueva línea ≥ monto usado | `changeLimit` |
| 18 | Idempotencia: una misma `operationId` no se aplica dos veces | Caso de uso + `OperationLogPort` |

### 3.5 Domain services
| Servicio | Responsabilidad |
|---|---|
| `AcquisitionPolicy` | Valida las reglas 1, 2, 3 y 5. Puro: recibe los datos ya obtenidos (cliente, cantidad de créditos no pagados, deuda vencida). Cadena de validaciones (**Chain of Responsibility**) |
| `CardNumberGenerator` | Genera el número ficticio de tarjeta |

### 3.6 Eventos de dominio
| Evento | Cuándo | Datos mínimos |
|---|---|---|
| `CreditOpened`, `CreditUpdated`, `CreditClosed` | Ciclo de vida del crédito | Estado completo: `creditId`, `customerId`, `ownerType`, `status`, `principalAmount`, `outstandingBalance`, `dueDate`, `updatedAt` |
| `CreditCardIssued`, `CreditCardUpdated`, `CreditCardClosed` | Ciclo de vida de la tarjeta | Estado completo: `cardId`, `customerId`, `ownerType`, `maskedNumber`, `status`, `creditLimit`, `usedAmount`, `paymentDueDate` (opcional), `updatedAt` |
| `PaymentRegistered` | Pago aplicado | `operationId`, `productType`, `productId`, `customerId` (dueño), `payerCustomerId`, `amount`, `resultingBalance` (saldo pendiente o monto usado), `occurredAt` |
| `ChargeRegistered` | Consumo aplicado | `operationId`, `cardId`, `customerId`, `amount`, `resultingBalance` (monto usado), `availableCredit`, `description`, `occurredAt` |

| `ProductBecameOverdue` | El producto pasa a vencido | `customerId`, `productType`, `productId`, `dueDate`, `outstandingAmount`, `occurredAt` |
| `CustomerOverdueCleared` | Al cliente ya **no le queda** ningún producto vencido | `customerId`, `occurredAt` |

Campos, tópicos y claves de todos estos mensajes: `contracts/events/kafka-contract.md`. **Todo cambio de estado** de un crédito o tarjeta (pago, consumo, reprogramación, cambio de línea, vencido) publica su evento `*Updated` con el estado completo.

## 4. Casos de uso y puertos

### 4.1 Puertos de entrada (casos de uso)
| Caso de uso | Retorno | Descripción |
|---|---|---|
| `OpenCreditUseCase` | `Single<Credit>` | Reúne datos por puertos, aplica `AcquisitionPolicy`, crea y guarda |
| `FindCreditUseCase` / `FindCreditsUseCase` | `Single<Credit>` / `Flowable<Credit>` | Por id; lista con filtros `customerId`, `type`, `status` |
| `UpdateCreditUseCase` | `Single<Credit>` | Reprogramar el vencimiento |
| `CloseCreditUseCase` | `Completable` | Baja lógica con saldo 0 |
| `PayCreditUseCase` | `Single<PaymentResult>` | Pago propio o de tercero, idempotente |
| `IssueCreditCardUseCase` | `Single<CreditCard>` | Emite tarjeta aplicando `AcquisitionPolicy` |
| `FindCreditCardUseCase` / `FindCreditCardsUseCase` | `Single` / `Flowable` | Por id; lista con filtros |
| `UpdateCreditCardUseCase` | `Single<CreditCard>` | Cambiar la línea |
| `CloseCreditCardUseCase` | `Completable` | Baja lógica con saldo 0 |
| `ChargeCreditCardUseCase` | `Single<ChargeResult>` | Consumo con control de línea, idempotente |
| `PayCreditCardUseCase` | `Single<PaymentResult>` | Pago propio o de tercero, idempotente |
| `GetCreditCardBalanceUseCase` | `Single<CardBalanceView>` | Línea, usado, disponible, fecha de pago, estado (RF-31) |
| `GetPaymentInfoUseCase` | `Single<PaymentInfoView>` | Vista limitada para pagar a terceros: saldo pendiente, vencimiento, estado |
| `RecoverUnrecordedOperationsUseCase` | `Single<RecoveryResult>` | P1/P2: reintenta registrar en `transaction-service` los pagos y consumos ya aplicados que no se pudieron registrar |
| `CheckOverdueUseCase` | `Single<OverdueCheckResult>` | Marca vencidos a `asOf` (hoy si no se indica); publica eventos. Resultado: `{ creditsMarked, cardsMarked, customersAffected }` |

### 4.2 Puertos de salida
| Puerto | Métodos | Adaptador (fase) |
|---|---|---|
| `CreditRepositoryPort` | `save`, `findById`, `findAll(filters)`, `countUnpaidByCustomer`, `findActiveDueBefore(date)` | Mongo (P1) |
| `CreditCardRepositoryPort` | `save`, `findById`, `findAll(filters)`, `findActiveDueBefore(date)` | Mongo (P1) |
| `OverdueQueryPort` | `existsOverdueByCustomer(customerId)` | Mongo sobre ambas colecciones (P1) |
| `OperationLogPort` | `find(operationId)`, `save(operation)`, `findUnrecordedOlderThan(instant)` | Mongo `credit_operations` (P1) |
| `UnitOfWorkPort` | `inTransaction(Single<T>)` | Transacción de Mongo (P1): producto y operación se guardan juntos |
| `CustomerLookupPort` | `findById` → `CustomerSnapshot` (tipo, estado) | REST + circuit breaker (P1/P2) → read model (P3) |
| `MovementRecorderPort` | `record(movement)` | REST a `transaction-service` (P1/P2) → no-op en P3 (lo cubren los eventos) |
| `CreditEventPublisherPort` | `publish(event)` | No-op (P1/P2) → Kafka (P3) |

## 5. API (contrato OpenAPI)

Base: `/api/v1`. Roles aplican cuando `security.enabled=true`.

**Créditos**
| Método | Ruta | Descripción | Roles | Éxito | Errores |
|---|---|---|---|---|---|
| `POST` | `/credits` | Otorgar crédito (`customerId`, `amount`, `dueDate`) | `ADMIN`, `TELLER`, `CUSTOMER` (para sí) | 201 | 400, 404, 422, 503 |
| `GET` | `/credits` | Listar (`customerId`, `type`, `status`) | `ADMIN`, `TELLER`, `CUSTOMER` (los suyos) | 200 | — |
| `GET` | `/credits/{id}` | Obtener | `ADMIN`, `TELLER`, `CUSTOMER` (suyo) | 200 | 404 |
| `PUT` | `/credits/{id}` | Reprogramar vencimiento | `ADMIN`, `TELLER` | 200 | 400, 404, 422 |
| `DELETE` | `/credits/{id}` | Baja lógica (saldo 0) | `ADMIN`, `TELLER` | 204 | 404, 422 |
| `POST` | `/credits/{id}/payments` | Pagar (`operationId`, `amount`, `payerCustomerId` opcional) | `ADMIN`, `TELLER`, `CUSTOMER` (propios y de terceros) | 200 | 404, 422 |
| `GET` | `/credits/{id}/payment-info` | Vista limitada para pagar a terceros | `ADMIN`, `TELLER`, `CUSTOMER` | 200 | 404 |

**Tarjetas de crédito**
| Método | Ruta | Descripción | Roles | Éxito | Errores |
|---|---|---|---|---|---|
| `POST` | `/credit-cards` | Emitir tarjeta (`customerId`, `creditLimit`) | `ADMIN`, `TELLER`, `CUSTOMER` (para sí) | 201 | 400, 404, 422, 503 |
| `GET` | `/credit-cards` | Listar (`customerId`, `status`) | `ADMIN`, `TELLER`, `CUSTOMER` (las suyas) | 200 | — |
| `GET` | `/credit-cards/{id}` | Obtener | `ADMIN`, `TELLER`, `CUSTOMER` (suya) | 200 | 404 |
| `PUT` | `/credit-cards/{id}` | Cambiar la línea | `ADMIN`, `TELLER` | 200 | 400, 404, 422 |
| `DELETE` | `/credit-cards/{id}` | Baja lógica (saldo 0) | `ADMIN`, `TELLER` | 204 | 404, 422 |
| `GET` | `/credit-cards/{id}/balance` | Saldo disponible | `ADMIN`, `TELLER`, `CUSTOMER` (suya) | 200 | 404 |
| `POST` | `/credit-cards/{id}/charges` | Consumo (`operationId`, `amount`, `description`) | `ADMIN`, `TELLER`, `CUSTOMER` (suya) | 200 | 404, 422 |
| `POST` | `/credit-cards/{id}/payments` | Pagar (`operationId`, `amount`, `payerCustomerId` opcional) | `ADMIN`, `TELLER`, `CUSTOMER` (propias y de terceros) | 200 | 404, 422 |
| `GET` | `/credit-cards/{id}/payment-info` | Vista limitada para pagar a terceros | `ADMIN`, `TELLER`, `CUSTOMER` | 200 | 404 |

**Deuda vencida**
| Método | Ruta | Descripción | Roles | Éxito | Errores |
|---|---|---|---|---|---|
| `POST` | `/overdue-checks` | Ejecutar la revisión ahora; `asOf` opcional (parámetro de consulta) para simular otra fecha | `ADMIN` | 200 | 400 |
| `POST` | `/credit-recovery-runs` | Reintentar el registro pendiente de pagos y consumos en el historial (P1/P2; `olderThanMinutes` opcional) | `ADMIN` | 200 | 400 |

Notas:
- `account-service` usa `GET /credit-cards?customerId=&status=ACTIVE` en P2 para verificar que el cliente tenga tarjeta.
- Códigos 422: `PERSONAL_CREDIT_LIMIT_REACHED`, `CUSTOMER_INACTIVE`, `OVERDUE_DEBT`, `OVERPAYMENT`, `CREDIT_LIMIT_EXCEEDED`, `INVALID_DUE_DATE`, `NOT_CLOSABLE`, `INVALID_STATE`. 503 si `customer-service` o `transaction-service` no responden en 2 s.
- Cuerpo de error estándar: `{ timestamp, status, code, message, path }`.
- Otros códigos: 404 `CUSTOMER_NOT_FOUND`, `CREDIT_NOT_FOUND`, `CREDIT_CARD_NOT_FOUND`; 409 `CONCURRENT_MODIFICATION`, `OPERATION_ID_REUSED`; 422 `LIMIT_BELOW_USED_AMOUNT`.
- **Contrato exacto** (campos, tipos, ejemplos, validaciones, reglas de cálculo, documentos Mongo y datos de demo): `contracts/credit-service/openapi.yaml` y `data-model.md`. Si difieren de esta ficha, el contrato manda.

## 6. Persistencia y caché
- **`credits` y `credit_cards`:** un documento por producto. Índices: (`customerId`, `status`); (`status`, `dueDate`) y (`status`, `paymentDueDate`) para el barrido de vencidos.
- **Concurrencia:** control optimista con `version`; ante conflicto se reintenta.
- **Regla 3 sin carreras:** índice único parcial sobre `customerId` con filtro `unpaidPersonal = true` (campo derivado que se mantiene para créditos personales `ACTIVE`/`OVERDUE`).
- **`credit_operations`:** registro de pagos y consumos (`_id` = `operationId`). El producto y la operación se guardan **en una transacción de Mongo**; si llega el mismo `operationId`, se devuelve el resultado guardado sin volver a aplicarlo. Guarda tipo, producto, monto, quien pagó, resultado y si ya se registró en el historial (`recorded`). Solo se guardan las operaciones **aplicadas**.
- **Read model (P3):** `customer_snapshots` (id, tipo, estado), alimentado por `customer.*`.
- **Caché:** no aplica. El servicio no tiene datos maestros propios.

## 7. Mensajería (Kafka) — P3
| Publica | Consume |
|---|---|
| `credit.created`, `credit.updated`, `credit.closed`, `credit.card.created`, `credit.card.updated`, `credit.card.closed`, `credit.payment.registered`, `credit.card.charge.registered`, `credit.overdue.detected`, `credit.overdue.cleared` | `customer.created/updated/deleted` |

- `credit.overdue.detected` se emite cuando **un producto** pasa a vencido (incluye `dueDate` y `outstandingAmount`). `credit.overdue.cleared` solo cuando al **cliente** no le queda ningún producto vencido.
- `credit.card.updated` se publica también cuando la tarjeta **cambia de estado** (`ACTIVE` ↔ `OVERDUE`): `account-service` solo considera "activa" a una tarjeta `ACTIVE`.
- Ambos tipos viajan por **un solo tópico físico `credit.overdue`**, con clave `customerId` y compactación, para conservar el orden por cliente. Los consumidores guardan `overdue` + `updatedAt` por cliente y aplican el evento solo si `occurredAt` es igual o más reciente (ver `flows/04-overdue-debt.md`).
- `transaction-service` consume los eventos de pago y consumo para el historial.

**Rol en sagas:** hoy ninguno. Las operaciones son locales al servicio y se publican como eventos. Sería participante si se agrega el pago de un producto **desde una cuenta bancaria** (retiro en `account-service` + pago aquí, con reversa si falla). Se decide al diseñar los flujos.

## 8. Filesystem

```
credit-service/
├── pom.xml
├── Dockerfile
├── checkstyle.xml
├── README.md
├── docs/
│   ├── sequence/
│   └── uml/
└── src/
    ├── main/
    │   ├── java/com/bank/credit/
    │   │   ├── CreditServiceApplication.java
    │   │   ├── domain/
    │   │   │   ├── model/
    │   │   │   │   ├── Credit.java, CreditCard.java        (aggregate roots)
    │   │   │   │   ├── CreditId.java, CreditCardId.java, CustomerId.java, OperationId.java
    │   │   │   │   ├── Money.java, DueDate.java, CardNumber.java
    │   │   │   │   ├── PaymentResult.java, ChargeResult.java
    │   │   │   │   ├── CustomerSnapshot.java               (dato de lectura)
    │   │   │   │   └── OwnerType.java, CreditStatus.java, CardStatus.java, OperationType.java
    │   │   │   ├── service/
    │   │   │   │   ├── AcquisitionPolicy.java
    │   │   │   │   ├── validation/                         (eslabones de la cadena)
    │   │   │   │   └── CardNumberGenerator.java
    │   │   │   ├── event/                                  (eventos de dominio)
    │   │   │   └── exception/                              (ProductNotFoundException, BusinessRuleViolationException con code, ...)
    │   │   ├── application/
    │   │   │   ├── command/
    │   │   │   ├── port/
    │   │   │   │   ├── in/                                 (casos de uso)
    │   │   │   │   └── out/                                (8 puertos)
    │   │   │   └── usecase/
    │   │   └── infrastructure/
    │   │       ├── adapter/
    │   │       │   ├── in/rest/                            (CreditController, CreditCardController, OverdueCheckController, GlobalExceptionHandler)
    │   │       │   ├── in/scheduler/                       (OverdueCheckScheduler: dispara CheckOverdueUseCase)
    │   │       │   ├── in/kafka/                           (CustomerEventsConsumer; P3)
    │   │       │   ├── out/persistence/                    (documentos, repositorios, adaptadores, OperationLog)
    │   │       │   ├── out/rest/                           (CustomerRestAdapter, MovementRecorderRestAdapter; P1/P2 con circuit breaker)
    │   │       │   ├── out/readmodel/                      (adaptador de snapshots; P3)
    │   │       │   └── out/kafka/                          (CreditEventKafkaPublisher; P3)
    │   │       ├── mapper/
    │   │       └── config/                                 (beans, Clock, Mongo, Resilience4j, Kafka, seguridad)
    │   └── resources/
    │       ├── openapi/credit-service/openapi.yaml   (+ openapi/common/common-schemas.yaml)
    │       ├── application.yml                     (solo nombre y config.import; el resto viene del Config Server)
    │       └── logback-spring.xml
    └── test/java/com/bank/credit/
```

El scheduler es un adaptador de **entrada**: solo dispara el caso de uso, igual que el endpoint manual. Los DTOs REST se generan desde el contrato.

## 9. Stack y configuración

**Base común:** Java 17, Spring Boot 3.x, Maven, WebFlux, RxJava 3, Lombok, MapStruct, Logback.

| Función | Dependencia |
|---|---|
| Web y cliente HTTP | `spring-boot-starter-webflux` (WebClient) |
| Persistencia | `spring-boot-starter-data-mongodb-reactive` (`RxJava3CrudRepository`) |
| Resiliencia | `resilience4j-spring-boot3` + `resilience4j-reactor` (circuit breaker y time limiter de 2 s en llamadas salientes) |
| Proceso diario | `@Scheduled` de Spring con `java.time.Clock` inyectado |
| Contrato | `openapi-generator-maven-plugin` |
| Config / Discovery | `spring-cloud-starter-config`, `spring-cloud-starter-netflix-eureka-client` |
| Eventos y seguridad (P3) | `reactor-kafka`/`spring-kafka`, `spring-boot-starter-oauth2-resource-server` |
| Calidad y pruebas | Checkstyle, Jacoco, JUnit 5, Mockito, `reactor-test`, RxJava `TestObserver`, WebTestClient |

**Propiedades en Config Server:** puerto, Mongo, URLs de `customer-service` y `transaction-service`, timeouts y umbrales del circuit breaker, plazo de pago de la tarjeta (30 días), cron del proceso diario (zona `America/Lima`), modo demo (permitir vencimientos pasados), Kafka, `security.enabled`.

**Resiliencia:** circuit breaker con timeout de 2 s en las llamadas a `customer-service` (al otorgar productos) y a `transaction-service` (al registrar el historial). Los pagos y consumos no dependen de otros servicios para validarse.

## 10. Estrategia de pruebas
| Capa | Qué se prueba | Herramientas |
|---|---|---|
| Dominio: `Credit` | Pago parcial y total, sobrepago, vencimiento, reprogramación, cierre | JUnit 5 (sin Spring) |
| Dominio: `CreditCard` | Consumo dentro y fuera de línea, fecha de pago al primer consumo, pago, vencimiento, cambio de línea | JUnit 5, `Clock` fijo |
| Dominio: políticas | `AcquisitionPolicy`: 1 crédito personal, varios empresariales, deuda vencida, tipo de cliente | JUnit 5 parametrizado |
| Casos de uso | Flujos con puertos simulados; idempotencia; `CheckOverdueUseCase` (`detected` y `cleared` con varios productos vencidos); reintento por versión | Mockito + `TestObserver` |
| Adaptadores REST salientes | Timeout de 2 s y circuit breaker | WireMock o `MockWebServer` |
| Persistencia | Índices, reserva de `operationId` | Testcontainers *(opcional)* |
| Controllers y scheduler | Contrato, códigos de estado, disparo del caso de uso | WebTestClient |
| Cobertura | Reporte de todo el código | Jacoco |

## 11. Diagramas a elaborar
- [ ] Secuencia: otorgar crédito (validaciones y consulta a `customer-service`)
- [ ] Secuencia: pago de crédito de tercero (idempotencia, evento, historial)
- [ ] Secuencia: consumo de tarjeta con control de línea
- [ ] Secuencia: revisión de deuda vencida (`detected` y `cleared`)
- [ ] UML del dominio (`Credit`, `CreditCard` y VO)

## 12. Decisiones y pendientes
- **Decidido:**
  - Este servicio es la **fuente de verdad de la deuda vencida** y la puerta de entrada de pagos y consumos; `transaction-service` guarda el historial.
  - Crédito con un solo vencimiento y saldo pendiente, sin cuotas ni intereses.
  - Personal: un solo crédito **no pagado**; uno ya pagado permite pedir otro.
  - Pago de terceros: se indica `payerCustomerId` y se consulta el saldo con `payment-info` (vista limitada).
  - Vencimiento de tarjeta: se fija al primer consumo con saldo 0 (+30 días).
  - Detección con proceso diario y ejecución manual con `asOf` para probar en Postman. También se permiten vencimientos pasados con el modo demo (`credit.demo-mode`, propiedad en Config Server).
  - Flujo completo (detección, limpieza, bloqueo y consistencia): `flows/04-overdue-debt.md`.
  - El pago no debita ninguna cuenta.
  - Sin límite de tarjetas por cliente; solo moneda `PEN`.
  - Contrato detallado en `contracts/credit-service/`: transacción de Mongo para producto y operación; solo se guardan operaciones aplicadas; `unpaidPersonal` para el índice parcial; todo cambio de estado publica su `*.updated`; una tarjeta pagada vuelve a `ACTIVE` (no hay `PAID` en tarjetas).
- **Pendiente:**
  - Consumo con una tarjeta vencida: por la decisión de negocio está permitido; si se prefiere bloquearlo es una regla adicional.
  - Pago desde cuenta bancaria (saga con `account-service`).
  - ~~En P1/P2, qué pasa si falla el registro del historial~~ **Definido:** la respuesta sigue siendo 200; la operación queda `recorded = false` y se reintenta (programado, `POST /credit-recovery-runs` o al repetir el `operationId`).
  - Si se despliegan varias instancias, evitar que todas ejecuten el proceso diario a la vez.

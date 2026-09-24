# `credit-service` — Modelo de datos

> Complementa la ficha (`services/credit-service.md`), el flujo de deuda vencida (`flows/04-overdue-debt.md`) y el contrato REST (`openapi.yaml`, en esta carpeta) con los **tipos exactos**, los documentos de MongoDB, los índices, las reglas de cálculo, los mapeos y los datos de ejemplo. Si algo difiere, el `openapi.yaml` manda para la API y `contracts/events/kafka-contract.md` para los eventos.

## 1. Entidades de dominio

### 1.1 Aggregate `Credit`

| Campo | Tipo (dominio) | Req. | Regla | Mutable |
|---|---|---|---|---|
| `id` | `CreditId` (`String`, UUID v4) | ✓ | Lo genera el servicio | No |
| `customerId` | `String` | ✓ | Solo referencia | No |
| `ownerType` | `OwnerType` | ✓ | Se deriva del tipo del cliente al otorgar | No |
| `principalAmount` | `Money` | ✓ | > 0, máx. 999 999 999.99 | No |
| `outstandingBalance` | `Money` | ✓ | Inicia = `principalAmount`. Nunca negativo | Sí (pagos) |
| `dueDate` | `LocalDate` | ✓ | Futura al otorgar y al reprogramar, salvo modo demo | Sí (`reschedule`) |
| `status` | `CreditStatus` | ✓ | Ver 1.4 | Sí |
| `version` | `Long` | ✓ | Control optimista. No se expone | Automático |
| `createdAt` / `updatedAt` | `Instant` | ✓ | Reloj inyectado | `updatedAt` sí |

Comportamiento: `open(...)`, `registerPayment(amount)` → `PaymentResult`, `markOverdueIfDue(asOf)` → `boolean`, `reschedule(newDueDate, today, demoMode)`, `close()`.

### 1.2 Aggregate `CreditCard`

| Campo | Tipo | Req. | Regla | Mutable |
|---|---|---|---|---|
| `id` | `CreditCardId` (UUID v4) | ✓ | | No |
| `customerId` | `String` | ✓ | | No |
| `ownerType` | `OwnerType` | ✓ | Derivado del cliente | No |
| `cardNumber` | `CardNumber` | ✓ | 16 dígitos ficticios, único. Por REST y eventos solo sale enmascarado (`maskedNumber`, `**** NNNN`); no se registra en logs | No |
| `creditLimit` | `Money` | ✓ | > 0 | Sí (`changeLimit`) |
| `usedAmount` | `Money` | ✓ | 0 ≤ usado ≤ línea | Sí |
| `paymentDueDate` | `LocalDate?` | – | Solo con `usedAmount` > 0 | Sí |
| `status` | `CardStatus` | ✓ | Ver 1.4 | Sí |
| `version` | `Long` | ✓ | No se expone | Automático |
| `createdAt` / `updatedAt` | `Instant` | ✓ | | `updatedAt` sí |

Comportamiento: `issue(...)`, `charge(amount, today, termDays)` → `ChargeResult`, `registerPayment(amount)` → `PaymentResult`, `markOverdueIfDue(asOf)` → `boolean`, `changeLimit(newLimit)`, `close()`. Derivado: `availableCredit = creditLimit − usedAmount`.

### 1.3 Value objects

| VO | Campos | Validación |
|---|---|---|
| `Money` | `amount: BigDecimal` (escala 2), `currency` (`PEN`) | No nulo. Operaciones `plus`, `minus`, `isGreaterThan`, `isZero` |
| `DueDate` | `value: LocalDate` | `isPastDue(asOf)` = `value < asOf` |
| `CardNumber` | `value: String` | 16 dígitos, prefijo `400000` (rango de pruebas), 9 al azar y dígito de control Luhn. `masked()` → `**** 4821` |
| `PaymentResult` | `operationId`, `productType`, `productId`, `amount`, `resultingBalance`, `status`, `payerCustomerId?` | Inmutable |
| `ChargeResult` | `operationId`, `cardId`, `amount`, `usedAmount`, `availableCredit`, `paymentDueDate?`, `status` | Inmutable |
| `OverdueCheckResult` | `asOf`, `ranAt`, `creditsMarked`, `cardsMarked`, `customersAffected` | Inmutable |
| `CustomerSnapshot` | `customerId`, `type`, `status` | Dato de lectura (`CustomerLookupPort`) |

### 1.4 Enums y estados

| Enum | Valores |
|---|---|
| `OwnerType` | `PERSONAL`, `BUSINESS` |
| `CreditStatus` | `ACTIVE`, `OVERDUE`, `PAID`, `CLOSED` |
| `CardStatus` | `ACTIVE`, `OVERDUE`, `CLOSED` |
| `ProductType` | `CREDIT`, `CREDIT_CARD` |
| `OperationType` | `PAYMENT`, `CHARGE` |

| Producto | De → A | Cuándo |
|---|---|---|
| `Credit` | — → `ACTIVE` | Otorgar |
| | `ACTIVE` → `OVERDUE` | `dueDate < asOf` y saldo > 0 (revisión) |
| | `OVERDUE` → `ACTIVE` | Reprogramar a `dueDate ≥ hoy` |
| | `ACTIVE`/`OVERDUE` → `PAID` | Pago total |
| | `PAID` → `CLOSED` | `DELETE` (saldo 0) |
| `CreditCard` | — → `ACTIVE` | Emitir |
| | `ACTIVE` → `OVERDUE` | `paymentDueDate < asOf` y usado > 0 (revisión) |
| | `OVERDUE` → `ACTIVE` | Pago total (usado 0; se borra `paymentDueDate`) |
| | `ACTIVE` → `CLOSED` | `DELETE` (usado 0) |

Una tarjeta `OVERDUE` con usado 0 no existe (el pago total la devuelve a `ACTIVE`). Una tarjeta pagada por completo está `ACTIVE` y sin fecha de pago; **no** hay estado `PAID` en tarjetas.

## 2. Reglas de cálculo

"Hoy" = fecha de `Clock` en la zona `bank.zone` (propuesta `America/Lima`).

### 2.1 Adquirir un producto (`AcquisitionPolicy`, cadena)

Orden (la primera que falla corta). Los datos llegan ya obtenidos.

| # | Eslabón | Aplica a | Código |
|---|---|---|---|
| 1 | Cliente existe | Ambos | 404 `CUSTOMER_NOT_FOUND` |
| 2 | Cliente `ACTIVE` | Ambos | `CUSTOMER_INACTIVE` |
| 3 | `ownerType` = tipo del cliente (no se elige) | Ambos | — |
| 4 | `PERSONAL`: sin otro crédito **no pagado** (`ACTIVE` u `OVERDUE`). Un `PAID` o `CLOSED` no cuenta. `BUSINESS`: sin tope | Solo crédito | `PERSONAL_CREDIT_LIMIT_REACHED` |
| 5 | Sin deuda vencida (crédito o tarjeta `OVERDUE` del cliente, consulta propia) | Ambos | `OVERDUE_DEBT` |
| 6 | Crédito: `dueDate > hoy` (o cualquier fecha en modo demo) | Solo crédito | `INVALID_DUE_DATE` |

El eslabón 4 se comprueba con `existsByCustomerIdAndStatusIn` para dar un error claro; el índice único parcial (3.1) cubre la carrera entre dos altas simultáneas.

### 2.2 Pago (`registerPayment`), crédito y tarjeta

| Paso | Regla | Falla con |
|---|---|---|
| 1 | Idempotencia: si existe la operación (mismo producto, tipo y monto) → devuelve su resultado guardado (200). Con otros datos → 409 | `OPERATION_ID_REUSED` |
| 2 | Producto no `CLOSED`, y crédito no `PAID` | 422 `INVALID_STATE` |
| 3 | `amount ≤ saldo pendiente` (crédito) o `≤ usado` (tarjeta) | 422 `OVERPAYMENT` |
| 4 | Baja el saldo (o el usado) | — |
| 5 | Crédito: si el saldo llega a 0 → `PAID`. Tarjeta: si el usado llega a 0 → `paymentDueDate` se borra y, si estaba `OVERDUE`, pasa a `ACTIVE` | — |
| 6 | Un pago parcial **no** cambia el estado (un producto `OVERDUE` sigue `OVERDUE`) | — |
| 7 | `payerCustomerId`: se guarda **solo si es distinto del dueño** (pago de tercero); si es igual o falta, queda vacío | — |
| 8 | Se guarda el producto y la operación **en una transacción de Mongo** | — |
| 9 | **Después de guardar:** si el producto **salió de `OVERDUE`** (a `PAID` o `ACTIVE`), `existsOverdueByCustomer` sobre ambas colecciones; si no queda ninguno → publica `credit.overdue.cleared` | — |
| 10 | Registra el movimiento en `transaction-service` (P1/P2) o publica los eventos (P3) | — |

`resultingBalance` del `PaymentResult` = saldo pendiente del crédito o usado de la tarjeta tras el pago; `status` = estado del producto tras el pago.

### 2.3 Consumo de tarjeta (`charge`)

| Paso | Regla | Falla con |
|---|---|---|
| 1 | Idempotencia (igual que en el pago) | 409 `OPERATION_ID_REUSED` |
| 2 | Tarjeta no `CLOSED` (una `OVERDUE` **sí** puede consumir) | 422 `INVALID_STATE` |
| 3 | `amount ≤ availableCredit` | 422 `CREDIT_LIMIT_EXCEEDED` |
| 4 | Si `usedAmount = 0` → `paymentDueDate = hoy + credit.card.payment-term-days` (30). Si no, no se mueve | — |
| 5 | `usedAmount += amount` | — |
| 6 | Guarda tarjeta y operación (transacción); registra el movimiento / publica eventos | — |

### 2.4 Otras operaciones

| Operación | Regla | Falla con |
|---|---|---|
| Reprogramar crédito | Crédito `ACTIVE` u `OVERDUE`; `newDueDate > hoy` (o cualquiera en modo demo). Si estaba `OVERDUE` y `newDueDate ≥ hoy` → `ACTIVE` y limpieza como en el paso 9 del pago | 422 `INVALID_STATE`, `INVALID_DUE_DATE` |
| Cambiar la línea | Tarjeta no `CLOSED`; `newLimit ≥ usedAmount` | 422 `INVALID_STATE`, `LIMIT_BELOW_USED_AMOUNT` |
| Cerrar crédito | Saldo 0 (`PAID`). Ya `CLOSED` → 204 | 422 `NOT_CLOSABLE` |
| Cerrar tarjeta | Usado 0. Ya `CLOSED` → 204 | 422 `NOT_CLOSABLE` |

### 2.5 Revisión de deuda vencida (`CheckOverdueUseCase`)

Entrada: `asOf` (por defecto hoy).

1. Créditos candidatos: `findByStatusAndDueDateBefore(ACTIVE, asOf)`. Tarjetas: `findByStatusAndPaymentDueDateBefore(ACTIVE, asOf)`. Ambas usan los índices (`status`, fecha); el saldo > 0 se comprueba en el dominio (`markOverdueIfDue`).
2. Por cada producto, de forma **aislada** (un fallo no detiene a los demás):
   1. `markOverdueIfDue(asOf)`: si no aplica (saldo 0 o fecha no vencida) no hace nada.
   2. Guarda con control de versión. Si hay conflicto: recarga y reevalúa (máx. 3 veces); si el producto ya no es candidato, lo omite.
   3. Publica `credit.updated` o `credit.card.updated` (cambio de estado) y `credit.overdue.detected` (un aviso **por producto**).
3. Resultado: `creditsMarked`, `cardsMarked` y `customersAffected` (clientes distintos con al menos un producto marcado **en esta corrida**).

Idempotente: un producto `OVERDUE` no es candidato (solo se buscan `ACTIVE`), así que repetir la revisión no cambia ni publica nada. Se ejecuta con `@Scheduled` (`credit.overdue.cron`, zona `bank.zone`) y con `POST /overdue-checks`.

### 2.6 Registro en el historial (P1/P2) y recuperación

En P1/P2 el pago o consumo se **aplica y guarda primero**; después `MovementRecorderPort` llama a `POST /transactions/records` de `transaction-service` (circuit breaker, 2 s).

| Resultado del registro | Qué pasa |
|---|---|
| Éxito (201 o 200 por duplicado) | `credit_operations.recorded = true`, `recordedAt` |
| Error o tiempo agotado | La respuesta al cliente **sigue siendo 200** (el pago ya se aplicó). La operación queda con `recorded = false` |
| El cliente repite el `operationId` | Devuelve el resultado guardado y **reintenta** el registro si `recorded = false` (sin esperar el resultado) |
| Proceso programado / `POST /credit-recovery-runs` | Busca operaciones `recorded = false` con `createdAt` anterior a N minutos y reintenta el registro (idempotente en `transaction-service` por `operationId`) |

En P3 el adaptador es un no-op que marca `recorded = true`: el historial se alimenta con los eventos `credit.payment.registered` y `credit.card.charge.registered`.

## 3. Documentos MongoDB

Dinero como **`Decimal128`**. Fechas `LocalDate` como texto `yyyy-MM-dd` (comparan bien como texto; ver conversiones globales en `contracts/README.md`). `Instant` como `date` UTC. Índices con `auto-index-creation=true`.

### 3.1 `credits` — `CreditDocument`

| Campo Mongo | Tipo | Req. | Notas |
|---|---|---|---|
| `_id` | string | ✓ | UUID v4 |
| `customerId` | string | ✓ | |
| `ownerType` | string | ✓ | |
| `principalAmount` | decimal128 | ✓ | |
| `outstandingBalance` | decimal128 | ✓ | |
| `dueDate` | string | ✓ | `yyyy-MM-dd` |
| `status` | string | ✓ | |
| `unpaidPersonal` | bool | – | `true` solo si `ownerType = PERSONAL` y `status ∈ {ACTIVE, OVERDUE}`; se **omite** en los demás casos. Campo derivado que el mapper mantiene; sirve al índice parcial |
| `version` | long | ✓ | `@Version` |
| `createdAt` / `updatedAt` | date | ✓ | |

| Índice | Campos | Opciones | Para qué |
|---|---|---|---|
| `ix_credit_customer_status` | `customerId` (1), `status` (1) | — | Listados y `existsOverdueByCustomer` |
| `ix_credit_status_due` | `status` (1), `dueDate` (1) | — | Barrido de vencidos |
| `uk_credit_personal_unpaid` | `customerId` (1) | **Único parcial**: `{ unpaidPersonal: true }` | Regla 3 contra carreras: un solo crédito personal no pagado |

Un `DuplicateKeyException` sobre `uk_credit_personal_unpaid` se traduce a 422 `PERSONAL_CREDIT_LIMIT_REACHED`. El filtro parcial usa solo una igualdad (válido en cualquier versión de Mongo).

### 3.2 `credit_cards` — `CreditCardDocument`

| Campo Mongo | Tipo | Req. | Notas |
|---|---|---|---|
| `_id` | string | ✓ | |
| `customerId` | string | ✓ | |
| `ownerType` | string | ✓ | |
| `cardNumber` | string | ✓ | 16 dígitos. Único |
| `creditLimit` | decimal128 | ✓ | |
| `usedAmount` | decimal128 | ✓ | |
| `paymentDueDate` | string | – | Se omite si no hay |
| `status` | string | ✓ | |
| `version` | long | ✓ | |
| `createdAt` / `updatedAt` | date | ✓ | |

| Índice | Campos | Opciones |
|---|---|---|
| `uk_card_number` | `cardNumber` (1) | **Único** (ante colisión, se genera otro número; máx. 3 intentos) |
| `ix_card_customer_status` | `customerId` (1), `status` (1) | — |
| `ix_card_status_due` | `status` (1), `paymentDueDate` (1) | — |

### 3.3 `credit_operations` — `CreditOperationDocument`

Registro de pagos y consumos **aplicados**. Da la idempotencia y el estado del registro en el historial.

| Campo Mongo | Tipo | Req. | Notas |
|---|---|---|---|
| `_id` | string | ✓ | **El `operationId`** (natural) |
| `type` | string | ✓ | `PAYMENT` / `CHARGE` |
| `productType` | string | ✓ | `CREDIT` / `CREDIT_CARD` |
| `productId` | string | ✓ | |
| `customerId` | string | ✓ | Dueño del producto |
| `payerCustomerId` | string | – | Solo pago de tercero |
| `amount` | decimal128 | ✓ | |
| `description` | string | – | Solo consumos |
| `resultingBalance` | decimal128 | ✓ | Saldo pendiente o usado tras la operación |
| `status` | string | ✓ | Estado del producto tras la operación |
| `availableCredit` | decimal128 | – | Solo consumos |
| `paymentDueDate` | string | – | Solo consumos |
| `recorded` | bool | ✓ | `true` cuando se registró en el historial (P1/P2) o siempre en P3 |
| `recordedAt` | date | – | |
| `occurredAt` | date | ✓ | Cuando se aplicó |
| `createdAt` | date | ✓ | |

Índice: `ix_op_recorded_created` sobre (`recorded` (1), `createdAt` (1)) para la recuperación.

- **Solo se guardan las operaciones aplicadas.** Un rechazo (`OVERPAYMENT`, `CREDIT_LIMIT_EXCEEDED`…) no deja registro: repetir el `operationId` se evalúa de nuevo (a diferencia de `account-service`, aquí no hay una saga que dependa de recibir siempre la misma respuesta).
- Mismo `operationId` con otro producto, tipo o monto → 409 `OPERATION_ID_REUSED`.
- El registro y el producto se escriben en **una transacción de Mongo** (`UnitOfWorkPort`, ver `contracts/account-service/data-model.md` 2.4). Reemplaza el "reservar primero (`claim`/`complete`)" de la ficha.

### 3.4 Read model (P3)

`customer_snapshots`: `_id` = `customerId`; `type`, `status`, `updatedAt`. Se actualiza con `customer.*` si el `updatedAt` del evento es ≥ al guardado. En P1/P2 el adaptador REST reemplaza al read model (`GET customer-service /customers/{id}`).

## 4. Consultas (sin `@Query`)

| Necesidad | Método (derivado) |
|---|---|
| Por id | `findById` |
| Listar créditos / tarjetas | `findByCustomerId` (más filtros en memoria) o `findByCustomerIdAndStatus`; sin `customerId`: `findAll` (los filtros se aplican en memoria) |
| ¿Otro crédito no pagado? | `existsByCustomerIdAndStatusIn(id, [ACTIVE, OVERDUE])` |
| ¿Deuda vencida? (`OverdueQueryPort`) | `existsByCustomerIdAndStatus(id, OVERDUE)` en `credits` **y** en `credit_cards` |
| Candidatos a vencer | `findByStatusAndDueDateBefore(ACTIVE, asOf)` / `findByStatusAndPaymentDueDateBefore(ACTIVE, asOf)` |
| Operación | `findById` |
| Registros pendientes | `findByRecordedFalseAndCreatedAtBefore(t)` |

## 5. Mapeos

| Dominio | Documento Mongo | REST | Evento |
|---|---|---|---|
| `Credit.id` / `CreditCard.id` | `_id` | `id` | `creditId` / `cardId` |
| `customerId` | `customerId` | `customerId` | `customerId` |
| `ownerType` | `ownerType` | `ownerType` | `ownerType` |
| `principalAmount` | `principalAmount` | `principalAmount` | `principalAmount` |
| `outstandingBalance` | `outstandingBalance` | `outstandingBalance` | `outstandingBalance` |
| `dueDate` | `dueDate` | `dueDate` | `dueDate` |
| `cardNumber` | `cardNumber` | *(solo `maskedNumber`)* | `maskedNumber` |
| `creditLimit` / `usedAmount` | igual | igual (+ `availableCredit` calculado) | `creditLimit` / `usedAmount` |
| `paymentDueDate` | `paymentDueDate` | `paymentDueDate` | `paymentDueDate` |
| `status` | `status` | `status` | `status` |
| — (derivado) | `unpaidPersonal` | *(no viaja)* | *(no viaja)* |
| `version` | `version` | *(no viaja)* | *(no viaja)* |
| `createdAt` | `createdAt` | `createdAt` | *(no viaja)* |
| `updatedAt` | `updatedAt` | `updatedAt` | `updatedAt` |

MapStruct para REST ↔ dominio, dominio ↔ documento y dominio ↔ evento. `availableCredit` y `maskedNumber` los calcula el mapper.

## 6. Requests: validaciones y errores

Formato → **400** `VALIDATION_ERROR` (lo declara el `openapi.yaml`). Negocio → 403 / 404 / 409 / 422.

| Operación | Campo | Formato (400) | Negocio |
|---|---|---|---|
| `POST /credits` | `customerId` | Obligatorio | 404 `CUSTOMER_NOT_FOUND`; 403 cliente ajeno (`CUSTOMER`); 422 `CUSTOMER_INACTIVE`, `PERSONAL_CREDIT_LIMIT_REACHED`, `OVERDUE_DEBT` |
| | `amount` | Obligatorio, 0.01 – 999 999 999.99, 2 decimales | — |
| | `dueDate` | Obligatorio, `yyyy-MM-dd` | 422 `INVALID_DUE_DATE` |
| `PUT /credits/{id}` | `dueDate` | Obligatorio, fecha | 404 `CREDIT_NOT_FOUND`; 422 `INVALID_DUE_DATE`, `INVALID_STATE`; 409 `CONCURRENT_MODIFICATION` |
| `DELETE /credits/{id}` | — | — | 404; 422 `NOT_CLOSABLE` |
| `POST /credits/{id}/payments` | `operationId` | Obligatorio, 8–64 | 409 `OPERATION_ID_REUSED` |
| | `amount` | Obligatorio, 0.01 – 999 999 999.99 | 422 `OVERPAYMENT`, `INVALID_STATE` |
| | `payerCustomerId` | Opcional | 403 si un `CUSTOMER` envía uno que no es el suyo |
| `POST /credit-cards` | `customerId`, `creditLimit` | Obligatorios; línea 0.01 – 999 999 999.99 | Como el crédito, sin `PERSONAL_CREDIT_LIMIT_REACHED` ni `INVALID_DUE_DATE` |
| `PUT /credit-cards/{id}` | `creditLimit` | Obligatorio | 404 `CREDIT_CARD_NOT_FOUND`; 422 `LIMIT_BELOW_USED_AMOUNT`, `INVALID_STATE` |
| `DELETE /credit-cards/{id}` | — | — | 404; 422 `NOT_CLOSABLE` |
| `POST /credit-cards/{id}/charges` | `operationId`, `amount` | Como en el pago | 422 `CREDIT_LIMIT_EXCEEDED`, `INVALID_STATE`; 409 `OPERATION_ID_REUSED` |
| | `description` | Opcional, máx. 200 | — |
| `POST /credit-cards/{id}/payments` | Igual que el pago de crédito | | 422 `OVERPAYMENT`, `INVALID_STATE` |
| `GET /credits`, `/credit-cards` | `customerId`, `ownerType`, `status` | Opcionales, enum | 403 si un `CUSTOMER` pide otro cliente |
| `GET …/balance`, `…/payment-info` | `id` | Obligatorio | 404 |
| `POST /overdue-checks` | `asOf` | Opcional, `yyyy-MM-dd` | — |
| `POST /credit-recovery-runs` | `olderThanMinutes` | Opcional, 0–1440 | — |

**Códigos de error del servicio**

| Estado | Códigos |
|---|---|
| 400 | `VALIDATION_ERROR` |
| 401 / 403 | `UNAUTHORIZED`, `FORBIDDEN` |
| 404 | `CUSTOMER_NOT_FOUND`, `CREDIT_NOT_FOUND`, `CREDIT_CARD_NOT_FOUND` |
| 409 | `CONCURRENT_MODIFICATION`, `OPERATION_ID_REUSED` |
| 422 | `PERSONAL_CREDIT_LIMIT_REACHED`, `CUSTOMER_INACTIVE`, `OVERDUE_DEBT`, `OVERPAYMENT`, `CREDIT_LIMIT_EXCEEDED`, `LIMIT_BELOW_USED_AMOUNT`, `INVALID_DUE_DATE`, `NOT_CLOSABLE`, `INVALID_STATE` |
| 503 | `SERVICE_UNAVAILABLE` (`customer-service`, P1/P2) |

## 7. Eventos (P3)

### 7.1 Publica

| Tópico (clave) | Tipo | Cuándo | Payload (`kafka-contract.md`) |
|---|---|---|---|
| `credit` (`creditId`) | `credit.created` | Al otorgar | 6.5, estado completo |
| | `credit.updated` | **Cualquier cambio de estado del crédito**: pago (baja el saldo), reprogramación, paso a `OVERDUE`/`ACTIVE`/`PAID` | 6.5 |
| | `credit.closed` | Primera baja (repetir no publica) | 6.5 con `status = CLOSED` |
| `credit-card` (`cardId`) | `credit.card.created` | Al emitir | 6.6 |
| | `credit.card.updated` | **Cualquier cambio de estado de la tarjeta**: consumo, pago, cambio de línea, paso a `OVERDUE`/`ACTIVE` | 6.6 |
| | `credit.card.closed` | Primera baja | 6.6 con `status = CLOSED` |
| `credit.operation` (`productId`) | `credit.payment.registered` | Pago aplicado (crédito o tarjeta) | 6.8 |
| | `credit.card.charge.registered` | Consumo aplicado | 6.8 |
| `credit.overdue` (`customerId`) | `credit.overdue.detected` | Un producto pasa a vencido | 6.7 |
| | `credit.overdue.cleared` | Al cliente ya no le queda ningún producto vencido | 6.7 |

Un pago o consumo publica **dos** mensajes: el de operación (`credit.operation`, para el historial) y el de estado (`credit` o `credit-card`, para las copias locales). El estado se publica primero. `occurredAt` de `credit.overdue.*` = `Clock` al momento de la detección o limpieza.

### 7.2 Consume

| Tópico | Tipo | Efecto |
|---|---|---|
| `customer` | `customer.created/updated/deleted` | Upsert en `customer_snapshots` |

## 8. Propiedades (Config Server)

| Propiedad | Propuesta | Uso |
|---|---|---|
| `bank.zone` | `America/Lima` | "Hoy" y zona del cron |
| `credit.demo-mode` | `false` (`true` en el entorno de demo) | Permite vencimientos pasados al otorgar o reprogramar |
| `credit.card.payment-term-days` | `30` | Plazo de pago desde el primer consumo |
| `credit.overdue.cron` | `0 0 1 * * *` (01:00) | Revisión diaria |
| `credit.recorder.pending-minutes` | `2` | Antigüedad para reintentar registros pendientes (P1/P2) |
| `credit.recorder.cron` | cada minuto | Reintento de registros pendientes |
| `resilience4j.*` | 2 s, umbrales | Llamadas a `customer-service` y `transaction-service` |

## 9. Datos de ejemplo para la demo

Con los clientes de `customer-service` (A, B, C, V, E). `hoy + N` = fecha relativa al día de la demo.

| Alias | Cliente | Producto | Datos | Papel |
|---|---|---|---|---|
| CR-A | A (Ana, personal) | Crédito | 5000.00, vence `hoy + 90` | Un segundo crédito → `PERSONAL_CREDIT_LIMIT_REACHED` |
| CC-A | A | Tarjeta | Línea 2000.00 | Consumo 500 (fecha de pago = hoy + 30); consumo de 2000 → `CREDIT_LIMIT_EXCEEDED` |
| CC-V | V (Víctor, VIP) | Tarjeta | Línea 3000.00 | Habilita la cuenta de ahorro VIP |
| CC-E | E (Bodega, PYME) | Tarjeta | Línea 10000.00 | Habilita la cuenta corriente PYME |
| CR-E1, CR-E2 | E | Créditos | 20000.00 y 8000.00 | Empresa: varios créditos permitidos |
| CR-C | C (Carla) | Crédito | 800.00, vence `hoy − 10` (**solo con modo demo**) | Deuda vencida |

Escenarios que cubre:
- **Pago de tercero (P3):** B consulta `payment-info` de CR-A y paga 300.00 con `payerCustomerId` de B → `resultingBalance` 4700.00; la operación guarda `payerCustomerId`.
- **Idempotencia:** repetir el `operationId` del pago → 200 con el mismo `resultingBalance` (no baja dos veces).
- **Sobrepago:** pagar más que el saldo → 422 `OVERPAYMENT`. Pago total de CR-A → `PAID`; ahora A puede pedir otro crédito.
- **Deuda vencida:** `POST /overdue-checks` → CR-C `OVERDUE`, resultado `{ creditsMarked: 1, cardsMarked: 0, customersAffected: 1 }`. Repetirlo → todo en 0. C intenta crédito, tarjeta de crédito, cuenta o tarjeta de débito → 422 `OVERDUE_DEBT`. C paga el total de CR-C → `PAID` y se publica `cleared`; poco después ya puede adquirir.
- **Simulación:** `POST /overdue-checks?asOf=<hoy + 40>` marca vencida la tarjeta CC-A (con saldo usado) — se deshace pagándola por completo.

## 10. Decisiones y pendientes

**Decidido (nuevo en este contrato)**
- Producto y operación se guardan en **una transacción de Mongo** (`UnitOfWorkPort`), sin el esquema de reservar primero (`claim`/`complete`). `OperationLogPort` queda con `find` y `save`.
- Solo se guardan las operaciones **aplicadas**; los rechazos no dejan registro.
- Se añade el campo derivado `unpaidPersonal` para que un solo índice parcial de igualdad cubra "crédito personal `ACTIVE` u `OVERDUE`".
- `payerCustomerId` solo se guarda si es un tercero; no se valida contra `customer-service`. Un `CUSTOMER` solo puede ser el pagador él mismo.
- **Cada cambio de estado** del crédito o de la tarjeta publica su evento `*.updated` (con el estado completo), no solo el paso a `OVERDUE`.
- Cierra el pendiente de la ficha "qué pasa si falla el registro del historial (P1/P2)": la respuesta sigue siendo 200, la operación queda `recorded = false` y se reintenta (programado, con `POST /credit-recovery-runs`, o al repetir el `operationId`).
- Una tarjeta **no** tiene estado `PAID`: pagada por completo vuelve a `ACTIVE`.
- Reprogramar: `OVERDUE` → `ACTIVE` solo si la nueva fecha es ≥ hoy; en modo demo se puede reprogramar a una fecha pasada y el crédito conserva su estado.
- El número de tarjeta es ficticio (`400000` + Luhn) y nunca sale completo por REST ni por eventos.
- Créditos y tarjetas: monto máximo 999 999 999.99 (por cordura, no por negocio).
- Se elimina el rol `INTERNAL` en la consulta de tarjetas de `account-service`: la llamada solo existe en P2 (sin seguridad).

**Pendiente**
- Nada bloqueante para empezar a programar este servicio.
- Instancias múltiples: el proceso diario correría en todas; en el demo hay una sola.
- Consumo con tarjeta vencida: permitido por decisión de negocio; si se prefiere bloquearlo es una regla adicional.
- Pago desde cuenta bancaria (saga con `account-service`): fuera de alcance.

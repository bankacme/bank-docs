# `transaction-service` — Modelo de datos

> Complementa la ficha (`services/transaction-service.md`), los flujos (`flows/01-transfer.md`, `02`, `03`) y el contrato REST (`openapi.yaml`, en esta carpeta) con los **tipos exactos**, los documentos de MongoDB, los índices, las reglas de la saga, los mapeos y los datos de ejemplo. Si algo difiere, el `openapi.yaml` manda para la API y `contracts/events/kafka-contract.md` para los eventos.

## 1. Entidades de dominio

### 1.1 Aggregate `Transaction`

Un movimiento sobre **un solo producto**. Es el registro del historial.

| Campo | Tipo (dominio) | Req. | Regla | Mutable |
|---|---|---|---|---|
| `id` | `TransactionId` (`String`, UUID v4) | ✓ | Lo genera el servicio | No |
| `operationId` | `OperationId` (`String`) | ✓ | **Único.** Ver 3.1 para los sufijos | No |
| `product` | `ProductRef` (`productId`, `productType`) | ✓ | Cuenta, crédito o tarjeta de crédito | No |
| `customerId` | `String` | ✓ | Dueño del producto (de `AccountSnapshot` en cuentas; viene en el registro para crédito y tarjeta) | No |
| `type` | `TransactionType` | ✓ | | No |
| `amount` | `Money` | ✓ | > 0. Sin comisión | No |
| `resultingBalance` | `Money?` | – | Saldo de la cuenta, saldo pendiente del crédito o monto usado de la tarjeta. Se fija al completar | Una vez |
| `status` | `TransactionStatus` | ✓ | Ver 1.4 | Sí |
| `failureReason` | `FailureReason?` | – | Solo `FAILED` | Una vez |
| `transferId` | `String?` | – | Patas de transferencia y sus comisiones | No |
| `parentTransactionId` | `String?` | – | Solo `FEE` | No |
| `payerCustomerId` | `String?` | – | Pagos de terceros | No |
| `description` | `String?` | – | Máx. 200 | Sí |
| `reversal` | `ReversalInfo?` | – | Reversa pedida sobre este movimiento (ver 1.3) | Sí |
| `occurredAt` | `Instant` | ✓ | Cuándo se **aceptó** la operación. No cambia. Ordena y filtra el historial | No |
| `createdAt` / `updatedAt` | `Instant` | ✓ | Reloj inyectado | `updatedAt` sí |

Comportamiento: `pending(...)` y `record(...)` (factories), `complete(resultingBalance)`, `fail(reason)`, `requestReversal(reversalOperationId)`, `markReversed()`, `failReversal(reasonCode)`, `updateDescription(text)`, `discard()`.

### 1.2 Aggregate `Transfer`

| Campo | Tipo | Req. | Regla | Mutable |
|---|---|---|---|---|
| `id` | `TransferId` (UUID v4) | ✓ | | No |
| `operationId` | `String` | ✓ | **Único** (`<op>`) | No |
| `sourceAccountId` / `targetAccountId` | `String` | ✓ | Distintas | No |
| `sourceCustomerId` / `targetCustomerId` | `String` | ✓ | De los `AccountSnapshot`. **No se exponen por REST** (sirven para el alcance de `CUSTOMER` y para reportes) | No |
| `amount` | `Money` | ✓ | > 0 | No |
| `kind` | `TransferKind` | ✓ | `OWN` si los dos clientes son el mismo | No |
| `status` | `TransferStatus` | ✓ | Ver 1.4 | Sí |
| `failureReason` | `FailureReason?` | – | Motivo de la cuenta que rechazó | Sí |
| `debitTransactionId` | `String?` | – | `TRANSFER_OUT`; se fija al crear la pata | Una vez |
| `creditTransactionId` | `String?` | – | `TRANSFER_IN`; se fija al pedir el depósito | Una vez |
| `compensationAttempts` | `int` | ✓ | Solo crece en `COMPENSATING`. Máx. `transfer.compensation.max-attempts` | Sí |
| `description` | `String?` | – | | No |
| `requestedBy` | `String` | ✓ | `sub` del token; `anonymous` sin seguridad | No |
| `version` | `Long` | ✓ | Control optimista. **No** se expone | Automático |
| `createdAt` / `updatedAt` | `Instant` | ✓ | | `updatedAt` sí |

Transiciones (`Transfer`): `sourceDebited(txId)`, `completed(txId)`, `failed(reason)`, `startCompensation(reason)`, `compensated()`, `compensationFailed()`. Una transición que no corresponde al estado actual se **ignora** (mensaje duplicado o tardío), no lanza error hacia el cliente.

### 1.3 Value objects

| VO | Campos | Validación |
|---|---|---|
| `ProductRef` | `productId`, `productType` | No vacíos |
| `Money` | `amount: BigDecimal` (escala 2), `currency` (`PEN`) | > 0 al operar |
| `FailureReason` | `code`, `message?` | `code` no vacío |
| `ReversalInfo` | `operationId` (`<op>-REV`), `outcome` (`PENDING`/`REVERSED`/`FAILED`), `reasonCode?` | |
| `DateRange` | `from: LocalDate?`, `to: LocalDate?` | `from ≤ to` (si no, 400 `INVALID_DATE_RANGE`). Se convierte a `[from 00:00, to+1 00:00)` en la zona `bank.zone` |
| `MovementOutcome` | `Applied(fee, newBalance, movementNumber)` o `Rejected(reasonCode, message?)` | Resultado de `account-service` |
| `AccountSnapshot` | `accountId`, `customerId`, `type`, `status` | Dato de lectura (`AccountLookupPort`) |
| `PageView<T>` | `content`, `page`, `size`, `totalElements`, `totalPages` | |
| `RecoveryResult` | Ver el esquema `RecoveryResult` del `openapi.yaml` | |

### 1.4 Enums y estados

| Enum | Valores |
|---|---|
| `ProductType` | `ACCOUNT`, `CREDIT`, `CREDIT_CARD` |
| `TransactionType` | `DEPOSIT`, `WITHDRAWAL`, `TRANSFER_OUT`, `TRANSFER_IN`, `FEE`, `CREDIT_PAYMENT`, `CARD_PAYMENT`, `CARD_CHARGE`, `DEBIT_PAYMENT`, `YANKI_PAYMENT_OUT`, `YANKI_PAYMENT_IN` |
| `TransactionStatus` | `PENDING`, `COMPLETED`, `FAILED`, `REVERSED`, `DISCARDED` |
| `TransferKind` | `OWN`, `THIRD_PARTY` |
| `TransferStatus` | `STARTED`, `SOURCE_DEBITED`, `COMPLETED`, `FAILED`, `COMPENSATING`, `COMPENSATED`, `COMPENSATION_FAILED` |

Transiciones de `Transaction`:

| De | A | Cuándo |
|---|---|---|
| — | `PENDING` | Se acepta la operación y se pide a la cuenta |
| — | `COMPLETED` | Registro externo (crédito/tarjeta) y comisiones `FEE` |
| `PENDING` | `COMPLETED` | La cuenta aplicó el movimiento |
| `PENDING` | `FAILED` | La cuenta lo rechazó |
| `COMPLETED` | `REVERSED` | La cuenta aplicó la reversa. **Sus comisiones `FEE` enlazadas (`parentTransactionId`) pasan también a `REVERSED`**, en la misma transacción de Mongo (la cuenta devolvió monto y comisión) |
| `FAILED` | `DISCARDED` | `DELETE /transactions/{id}` |

Un movimiento `COMPLETED` cuya reversa fue rechazada **sigue `COMPLETED`**, con `reversal.outcome = FAILED` (el dinero no se devolvió).

**Efecto de cada tipo sobre el producto** (informativo; el signo no se guarda):

| Tipo | Producto | Efecto |
|---|---|---|
| `DEPOSIT`, `TRANSFER_IN`, `YANKI_PAYMENT_IN` | Cuenta | Suma al saldo |
| `WITHDRAWAL`, `TRANSFER_OUT`, `DEBIT_PAYMENT`, `YANKI_PAYMENT_OUT`, `FEE` | Cuenta | Resta del saldo |
| `CREDIT_PAYMENT` | Crédito | Baja el saldo pendiente |
| `CARD_CHARGE` | Tarjeta | Sube el monto usado |
| `CARD_PAYMENT` | Tarjeta | Baja el monto usado |

## 2. Reglas de la saga y de los movimientos

### 2.1 Identificadores derivados

Ninguno de los sufijos se guarda como campo aparte: van dentro de `operationId`.

| Operación | `Transaction.operationId` | `operationId` que se envía a `account-service` |
|---|---|---|
| Depósito / retiro | `<op>` | `<op>` |
| Comisión de un depósito / retiro | `<op>-FEE` | — (no se envía) |
| Pata de retiro de una transferencia | `<op>-OUT` | `<op>-OUT` |
| Pata de depósito de una transferencia | `<op>-IN` | `<op>-IN` |
| Comisiones de las patas | `<op>-OUT-FEE`, `<op>-IN-FEE` | — |
| Reversa de un retiro | (marca `reversal.operationId = <op>-REV`) | `<op>-OUT` (la operación **original**) |
| Pago con débito | `<op>` tal cual (P3) | `<op>` |
| Pata de pago Yanki | `<legId>` (`<op>-OUT` / `<op>-IN`) (P3) | `<legId>` |

El cliente envía `operationId` de **máximo 56** caracteres (`RequestOperationId`): el sufijo más largo (`-OUT-FEE`) mide 8 y todo debe caber en los 64 del `OperationId` común. `debit-service` y `yanki-service` deben respetar el mismo límite al generar sus ids.

### 2.2 Depósito y retiro

1. **Validar:** existe la cuenta (`AccountLookupPort`; si no, 404 `ACCOUNT_NOT_FOUND`). `CUSTOMER` solo en cuenta propia (403). La cuenta inactiva **no** se valida aquí: la decide `account-service`.
2. **Idempotencia:** buscar `Transaction` por `operationId`. Si existe con la misma cuenta, tipo y monto → devolver su estado actual (`COMPLETED` → 200; `FAILED` → mismo 422; `PENDING` → reintentar el pedido a la cuenta). Con otros datos → 409 `OPERATION_ID_REUSED`.
3. **Guardar** `Transaction` `PENDING` (`occurredAt` = ahora).
4. **Pedir** el movimiento a la cuenta (`date` = **hoy** en `bank.zone` en cada intento; la fecha no forma parte de la comparación de idempotencia de `account-service`).
5. **Aplicado:** `COMPLETED` con `resultingBalance = newBalance`. Si `fee > 0`, crear el movimiento `FEE` (`<op>-FEE`, `amount = fee`, `resultingBalance = newBalance`, `parentTransactionId`, **`occurredAt` = el del movimiento padre**) ya `COMPLETED`. Ambos se guardan **en una transacción de Mongo** (`UnitOfWorkPort`).
6. **Rechazado:** `FAILED` con `failureReason` (el `reasonCode` de la cuenta) → 422.
7. **Sin respuesta en 2 s:** queda `PENDING` → 202 (P1/P2 con el circuit breaker abierto o el tiempo agotado; P3 sin resultado en el tiempo de espera). **No** se marca `FAILED`.

### 2.3 Transferencia

| Paso | Qué hace | Se guarda |
|---|---|---|
| 0 | Validar (`TransferPolicy`): monto > 0 (400), ambas cuentas existen (404), origen ≠ destino (422 `SAME_ACCOUNT`), ambas `ACTIVE` (422 `ACCOUNT_INACTIVE`), `CUSTOMER` es dueño del origen (403), calcular `kind` | — |
| 1 | Crear | `Transfer` `STARTED` + `Transaction` `TRANSFER_OUT` `PENDING` (`<op>-OUT`), juntos |
| 2 | Pedir el retiro `<op>-OUT` | — |
| 3a | Aplicado | `TRANSFER_OUT` `COMPLETED` (+ `FEE` `<op>-OUT-FEE`); `Transfer` `SOURCE_DEBITED`; crear `TRANSFER_IN` `PENDING` (`<op>-IN`) y fijar `creditTransactionId`, juntos |
| 3b | Rechazado | `TRANSFER_OUT` `FAILED`; `Transfer` `FAILED` con motivo |
| 4 | Pedir el depósito `<op>-IN` | — |
| 5a | Aplicado | `TRANSFER_IN` `COMPLETED` (+ `FEE` `<op>-IN-FEE`); `Transfer` `COMPLETED` |
| 5b | Rechazado | `TRANSFER_IN` `FAILED`; `Transfer` `COMPENSATING` (con motivo) y `TRANSFER_OUT.reversal = { <op>-REV, PENDING }`, juntos |
| 6 | Pedir la reversa de `<op>-OUT` | `compensationAttempts + 1` |
| 7a | Aplicada | `TRANSFER_OUT` `REVERSED` (y su comisión `<op>-OUT-FEE` también); `Transfer` `COMPENSATED`. En P3 se publica `transaction.reversed` |
| 7b | Rechazada o intentos agotados | `TRANSFER_OUT.reversal.outcome = FAILED`; `Transfer` `COMPENSATION_FAILED` |

- **El estado se guarda antes de cada llamada y después de cada respuesta.**
- **Nunca se compensa por un tiempo agotado.** Solo un rechazo explícito del depósito lleva a `COMPENSATING`.
- Una respuesta de rechazo de la reversa (`OPERATION_NOT_FOUND`, `OPERATION_NOT_APPLIED`, `INSUFFICIENT_FUNDS`…) es definitiva → `COMPENSATION_FAILED` sin esperar los reintentos. Solo la **falta de respuesta** consume intentos.
- `transfer.compensation.max-attempts` propuesto: 5.
- Código de error hacia el cliente en 422: el `reasonCode` de la cuenta que rechazó (`INSUFFICIENT_FUNDS` si falló el retiro; `NOT_ALLOWED_DAY`, `ACCOUNT_INACTIVE`… si falló el depósito). Con el estado en `transferStatus`.

### 2.4 Registro de crédito y tarjeta (`RecordExternalMovementUseCase`)

`POST /transactions/records` (P1/P2) y los eventos `credit.payment.registered` / `credit.card.charge.registered` (P3) hacen lo mismo:

| Origen | `productType` | `type` |
|---|---|---|
| Pago de crédito | `CREDIT` | `CREDIT_PAYMENT` |
| Pago de tarjeta | `CREDIT_CARD` | `CARD_PAYMENT` |
| Consumo de tarjeta | `CREDIT_CARD` | `CARD_CHARGE` |

Nace `COMPLETED` (no se valida nada: el efecto ya ocurrió). `resultingBalance` viene en el mensaje. `occurredAt` = el del mensaje. Un `operationId` repetido con los mismos datos devuelve el registro existente sin crear otro; con otros datos, 409 `OPERATION_ID_REUSED` (por Kafka se registra en el log y se ignora). Se publica `transaction.registered`.

### 2.5 Reversas pedidas (`yanki.movement.reversal.requested`, P3)

| Situación | Resultado |
|---|---|
| El movimiento `<op>-OUT` no existe | Publica `transaction.reversal.failed` con `OPERATION_NOT_FOUND` |
| Existe pero no está `COMPLETED` ni `REVERSED` | `reversal.failed` con `OPERATION_NOT_APPLIED` |
| `COMPLETED` sin reversa | Guarda `reversal = { <op>-REV, PENDING }`, envía `transaction.movement.reversal.requested` a la cuenta |
| Ya tenía reversa `REVERSED` | **Reemite** `transaction.reversed` |
| Ya tenía reversa `FAILED` | **Reemite** `transaction.reversal.failed` |
| Ya tenía reversa `PENDING` | Reenvía el comando a la cuenta (idempotente) |
| Llega `account.movement.reversed` | El movimiento (y sus comisiones) pasa a `REVERSED` y **siempre** se publica `transaction.reversed`; además, si es de una transferencia avanza la saga (7a) |
| Llega `account.movement.reversal.rejected` | `reversal.outcome = FAILED`; si es de una transferencia avanza la saga (7b); si no, publica `transaction.reversal.failed` |

### 2.6 Recuperación (`RecoverPendingOperationsUseCase`)

Se ejecuta con `@Scheduled` (`transaction.recovery.cron`) y con `POST /transaction-recovery-runs`.

| Qué busca | Qué hace |
|---|---|
| `Transaction` `PENDING` sin `transferId` con `createdAt` anterior a N minutos | Repite el pedido a la cuenta con el **mismo** `operationId` |
| `Transfer` `STARTED` / `SOURCE_DEBITED` / `COMPENSATING` con `updatedAt` anterior a N minutos | Continúa desde ese estado con la misma función de avance. En `STARTED` sin `Transaction` `<op>-OUT` (caída entre guardados), la crea |
| `Transaction` con `reversal.outcome = PENDING` sin `transferId` | Reenvía la reversa |

N = `transaction.recovery.pending-minutes` (propuesta: 2). Cada elemento se procesa aislado: un error no detiene la corrida.

### 2.7 P2 y P3

| Tema | P1/P2 | P3 |
|---|---|---|
| Pedido a la cuenta | REST síncrono (`POST /accounts/{id}/movements`), con circuit breaker y 2 s | Comando en `account.command`; el resultado llega en `account.movement` |
| Quién avanza la saga | La propia petición, tras cada respuesta REST | Solo `HandleMovementResultUseCase`. La petición espera hasta `payment.await-timeout` (1,5 s) el estado terminal y, si no llega, responde 202 |
| Lookup de cuentas | REST `GET /accounts/{id}` | Read model `account_snapshots` |
| Registro de crédito/tarjeta | `POST /transactions/records` | Eventos `credit.operation` |

## 3. Documentos MongoDB

Dinero como **`Decimal128`** (configuración global). `Instant` como `date` UTC. Los índices se crean al arrancar (`auto-index-creation=true`).

### 3.1 `transactions` — `TransactionDocument`

| Campo Mongo | Tipo | Req. | Notas |
|---|---|---|---|
| `_id` | string | ✓ | UUID v4 |
| `operationId` | string | ✓ | Único |
| `productId` | string | ✓ | |
| `productType` | string | ✓ | |
| `customerId` | string | ✓ | |
| `type` | string | ✓ | |
| `amount` | decimal128 | ✓ | |
| `resultingBalance` | decimal128 | – | |
| `status` | string | ✓ | |
| `failureReason.code` / `.message` | string | – | Solo `FAILED` |
| `transferId` | string | – | |
| `parentTransactionId` | string | – | |
| `payerCustomerId` | string | – | |
| `description` | string | – | |
| `reversal.operationId` / `.outcome` / `.reasonCode` | string | – | Solo si se pidió una reversa |
| `occurredAt` | date | ✓ | |
| `createdAt` / `updatedAt` | date | ✓ | |

**Índices**

| Nombre | Campos | Opciones | Para qué |
|---|---|---|---|
| `uk_tx_operation_id` | `operationId` (1) | **Único** | Idempotencia y carreras |
| `ix_tx_product_occurred` | `productId` (1), `occurredAt` (-1) | — | Historial de un producto |
| `ix_tx_customer_occurred` | `customerId` (1), `occurredAt` (-1) | — | Consulta por cliente |
| `ix_tx_status_created` | `status` (1), `createdAt` (1) | — | Recuperación de `PENDING` |
| `ix_tx_transfer` | `transferId` (1) | Parcial: `transferId` existe | Movimientos de una transferencia |

Un `DuplicateKeyException` en `uk_tx_operation_id` (carrera entre dos peticiones iguales) se resuelve releyendo el existente y aplicando la regla de idempotencia (2.2, paso 2).

### 3.2 `transfers` — `TransferDocument`

| Campo Mongo | Tipo | Req. | Notas |
|---|---|---|---|
| `_id` | string | ✓ | |
| `operationId` | string | ✓ | Único |
| `sourceAccountId` / `targetAccountId` | string | ✓ | |
| `sourceCustomerId` / `targetCustomerId` | string | ✓ | |
| `amount` | decimal128 | ✓ | |
| `kind` | string | ✓ | |
| `status` | string | ✓ | |
| `failureReason.code` / `.message` | string | – | |
| `debitTransactionId` / `creditTransactionId` | string | – | |
| `compensationAttempts` | int | ✓ | |
| `description` | string | – | |
| `requestedBy` | string | ✓ | |
| `version` | long | ✓ | `@Version` |
| `createdAt` / `updatedAt` | date | ✓ | |

**Índices:** `uk_transfer_operation_id` (`operationId`, único); `ix_transfer_status_updated` (`status`, `updatedAt`); `ix_transfer_source_created` (`sourceAccountId`, `createdAt` -1); `ix_transfer_target_created` (`targetAccountId`, `createdAt` -1).

### 3.3 Read model (P3)

`account_snapshots`: `_id` = `accountId`; `customerId`, `type`, `status`, `updatedAt`. Se actualiza con `account.created/updated/deleted` si el `updatedAt` del evento es ≥ al guardado. Sin índices adicionales.

### 3.4 Atomicidad

Igual que en `account-service` (`contracts/account-service/data-model.md`, 2.4): los cambios que tocan **dos documentos** (`Transfer` + `Transaction`, o `Transaction` + su `FEE`) van en una transacción de Mongo con `UnitOfWorkPort.inTransaction(...)`; ante `WriteConflict` se reintenta el caso completo hasta 3 veces. El `Transfer` usa además `version` (una transición concurrente duplicada se ignora).

## 4. Consultas (sin `@Query`, sin consultas dinámicas)

El historial admite filtros opcionales `type` y `status` con paginación; como no se arman consultas dinámicas, el repositorio declara **una consulta derivada por combinación** (con su `count`):

| # | `type` | `status` | Método derivado (patrón) |
|---|---|---|---|
| 1 | no | no | `findByProductIdAndStatusNotAndOccurredAtGreaterThanEqualAndOccurredAtLessThan(..., DISCARDED, from, to, pageable)` |
| 2 | sí | no | `findByProductIdAndTypeAndStatusNotAnd…` |
| 3 | no | sí | `findByProductIdAndStatusAndOccurredAtGreaterThanEqualAnd…` |
| 4 | sí | sí | `findByProductIdAndTypeAndStatusAnd…` |

Igual para `customerId` en la consulta administrativa (otras 4). Cada una con su `countBy…` para el total. Sin `from`/`to`, el rango es `Instant.EPOCH` hasta `Instant.MAX` (o un límite lejano). `Sort` fijo: `occurredAt` descendente.

| Otra necesidad | Método |
|---|---|
| Por id | `findById` |
| Por operación | `findByOperationId` |
| Recuperación | `findByStatusAndCreatedAtBefore(PENDING, t)`; `findByReversal_OutcomeAndUpdatedAtBefore(PENDING, t)` |
| Transferencias por operación / cuenta / estado | `findByOperationId`; `findBySourceAccountIdOrTargetAccountId`; `findByStatus` (sobre el resultado se aplican en memoria los otros filtros y `take(200)`) |
| Transferencias en curso | `findByStatusInAndUpdatedAtBefore([STARTED, SOURCE_DEBITED, COMPENSATING], t)` |

**Alcance de `CUSTOMER`:** en el historial de un producto no se conoce al dueño de antemano (crédito y tarjeta no están en un read model). Se lee la página y, si algún movimiento tiene `customerId` distinto del token, 403. Una página vacía es válida (producto sin movimientos o inexistente: no hay 404).

## 5. Mapeos

| Dominio (`Transaction`) | Documento | REST | Evento `transaction.registered` |
|---|---|---|---|
| `id` | `_id` | `id` | `transactionId` |
| `operationId` | `operationId` | `operationId` | `operationId` |
| `product.productId` / `.productType` | `productId` / `productType` | `productId` / `productType` | `productId` / `productType` |
| `customerId` | `customerId` | `customerId` | `customerId` |
| `type` / `amount` | igual | igual | `type` / `amount` |
| `resultingBalance` | `resultingBalance` | `resultingBalance` | `resultingBalance` |
| `status` | `status` | `status` | *(implícito: solo se publica `COMPLETED`)* |
| `failureReason` | `failureReason` | `failureReason` | *(en `transaction.failed`: `reasonCode`, `message`)* |
| `transferId` / `parentTransactionId` / `payerCustomerId` / `description` | igual | igual | igual |
| — (comisión del movimiento) | movimiento `FEE` aparte | `feeTransaction` (solo en la respuesta de depósito/retiro) | `fee` (en el evento del movimiento padre) |
| `reversal` | `reversal` | `reversal` | *(en `transaction.reversed` / `reversal.failed`)* |
| `occurredAt` | `occurredAt` | `occurredAt` | `occurredAt` |
| `createdAt` / `updatedAt` | igual | igual | *(no viajan)* |

| `Transfer` | Documento | REST | Evento `transfer.*` |
|---|---|---|---|
| `id` | `_id` | `id` | `transferId` |
| `status` | `status` | `status` | `status` |
| `failureReason.code` | `failureReason.code` | `failureReason.code` | `reasonCode` |
| `sourceCustomerId` / `targetCustomerId` / `version` | igual | *(no viajan)* | *(no viajan)* |

MapStruct para REST ↔ dominio, dominio ↔ documento y dominio ↔ evento. El evento `transaction.registered` del movimiento padre lleva `fee`; **además** se publica uno propio para el movimiento `FEE`.

## 6. Requests: validaciones y errores

Formato → **400** `VALIDATION_ERROR` (lo declara el `openapi.yaml`). Negocio → 403 / 404 / 409 / 422.

| Operación | Campo | Formato (400) | Negocio |
|---|---|---|---|
| `POST /deposits`, `/withdrawals` | `operationId` | Obligatorio, 8–56 | 409 `OPERATION_ID_REUSED` |
| | `accountId` | Obligatorio | 404 `ACCOUNT_NOT_FOUND`; 403 cuenta ajena (`CUSTOMER`) |
| | `amount` | Obligatorio, ≥ 0.01, 2 decimales | `INVALID_AMOUNT` (solo por Kafka) |
| | `description` | Opcional, máx. 200 | — |
| | *(resultado de la cuenta)* | — | 422 `ACCOUNT_INACTIVE`, `MONTHLY_LIMIT_EXCEEDED`, `NOT_ALLOWED_DAY`, `INSUFFICIENT_FUNDS`, `INVALID_DATE` |
| `POST /transfers` | `operationId`, `amount`, `description` | Igual | 409 `OPERATION_ID_REUSED` |
| | `sourceAccountId`, `targetAccountId` | Obligatorios | 404 `ACCOUNT_NOT_FOUND`; 403 origen ajeno; 422 `SAME_ACCOUNT`, `ACCOUNT_INACTIVE` |
| | *(resultado de la saga)* | — | 422 con el motivo de la cuenta y `transferStatus` |
| `GET /transfers` | `operationId`, `accountId`, `status` | Opcionales | — |
| `GET /transfers/{id}` | `id` | Obligatorio | 404 `TRANSFER_NOT_FOUND`; 403 origen ajeno |
| `GET /products/{productId}/transactions` | `from`, `to`, `type`, `status`, `page`, `size` | Fechas `yyyy-MM-dd`; `size` 1–100 | 400 `INVALID_DATE_RANGE` (`from` > `to`); 403 producto ajeno |
| `GET /transactions` | `customerId` | **Obligatorio** | Igual que arriba |
| `GET /transactions/{id}` | `id` | Obligatorio | 404 `TRANSACTION_NOT_FOUND`; 403 ajeno |
| `PUT /transactions/{id}` | `description` | Obligatorio, máx. 200 | 404; 422 `TRANSACTION_DISCARDED` |
| `DELETE /transactions/{id}` | — | — | 404; 422 `NOT_DISCARDABLE` (no es `FAILED`). Repetir sobre `DISCARDED` → 204 |
| `POST /transactions/records` | Todos los del esquema | Ver `RecordExternalMovementRequest` | 409 `OPERATION_ID_REUSED`; 422 `INVALID_TYPE_FOR_PRODUCT` |
| `POST /transaction-recovery-runs` | `olderThanMinutes` | Opcional, 0–1440 | — |

**Códigos de error del servicio**

| Estado | Códigos |
|---|---|
| 400 | `VALIDATION_ERROR`, `INVALID_DATE_RANGE` |
| 401 / 403 | `UNAUTHORIZED`, `FORBIDDEN` |
| 404 | `ACCOUNT_NOT_FOUND`, `TRANSFER_NOT_FOUND`, `TRANSACTION_NOT_FOUND` |
| 409 | `OPERATION_ID_REUSED` |
| 422 | `SAME_ACCOUNT`, `ACCOUNT_INACTIVE`, `INVALID_AMOUNT`, `INSUFFICIENT_FUNDS`, `MONTHLY_LIMIT_EXCEEDED`, `NOT_ALLOWED_DAY`, `INVALID_DATE`, `NOT_DISCARDABLE`, `TRANSACTION_DISCARDED`, `INVALID_TYPE_FOR_PRODUCT` |
| 503 | `SERVICE_UNAVAILABLE` (no se pudo consultar la cuenta; no se creó nada) |

Los códigos de rechazo de movimiento (`INSUFFICIENT_FUNDS`, `MONTHLY_LIMIT_EXCEEDED`, `NOT_ALLOWED_DAY`, `ACCOUNT_INACTIVE`, `INVALID_DATE`) son **los mismos** que devuelve `account-service`; aquí solo se propagan.

## 7. Eventos y comandos (P3)

### 7.1 Publica

| Tópico (clave) | Tipo | Cuándo | Payload (`kafka-contract.md`) |
|---|---|---|---|
| `account.command` (`accountId`) | `transaction.movement.requested` | Pedir depósito/retiro | 6.3 (`operationId`, `accountId`, `type`, `amount`, `date`) |
| | `transaction.movement.reversal.requested` | Pedir reversa | 6.3 (`operationId` original, `accountId`) |
| `transaction` (`productId`) | `transaction.registered` | Un movimiento queda `COMPLETED` (también `FEE`) | 6.10 |
| | `transaction.failed` | Un movimiento queda `FAILED` | 6.10 |
| | `transaction.reversed` | **Toda** reversa aplicada: la pedida por `yanki-service` y la compensación de una transferencia. `yanki-service` solo procesa las suyas (por el sufijo `-REV` de un pago propio); `report-service` marca el movimiento original y sus comisiones como revertidos | 6.10 |
| | `transaction.reversal.failed` | Reversa pedida por `yanki-service` rechazada (la de una transferencia termina en `transfer.failed` con `COMPENSATION_FAILED`) | 6.10 |
| `transfer` (`transferId`) | `transfer.completed` | `COMPLETED` | 6.11 |
| | `transfer.failed` | `FAILED`, `COMPENSATED` o `COMPENSATION_FAILED` | 6.11 |

Los eventos `transaction.registered/failed` se publican para **todos** los movimientos (depósitos y retiros REST incluidos), no solo los pedidos por Kafka. Publicar es después de guardar; sin outbox en el demo.

### 7.2 Consume

| Tópico | Tipo | Efecto |
|---|---|---|
| `account.movement` | `applied`, `rejected` | `HandleMovementResultUseCase`: se busca el `Transaction` por `operationId` (la pata); se completa o falla; si tiene `transferId` avanza la `Transfer` |
| | `reversed`, `reversal.rejected` | Ver 2.5 (el `operationId` del mensaje es el **original**) |
| `account` | `account.created/updated/deleted` | Upsert en `account_snapshots` |
| `credit.operation` | `credit.payment.registered`, `credit.card.charge.registered` | Registro externo (2.4) |
| `transaction.command` | `debit.payment.requested` | `RecordRequestedMovementUseCase`: `Transaction` `DEBIT_PAYMENT` `PENDING` (`operationId` tal cual), retiro a la cuenta; responde `transaction.registered/failed` |
| | `yanki.movement.requested` | Igual con `YANKI_PAYMENT_OUT` (retiro) o `YANKI_PAYMENT_IN` (depósito) |
| | `yanki.movement.reversal.requested` | Ver 2.5 |

- **`customerId` del movimiento pedido por Kafka:** el pedido de Yanki no lo trae; se toma del `AccountSnapshot` de la cuenta. Si la cuenta no está en el read model → se rechaza con `ACCOUNT_NOT_FOUND` (`transaction.failed`).
- **Idempotencia con reemisión:** ante un `operationId` conocido no se crea otro registro; si ya terminó, se vuelve a publicar `transaction.registered/failed` (nuevo `eventId`, mismos campos).
- **Rechazo por reuso:** un pedido con el mismo `operationId` pero otros datos se ignora y se registra en el log.
- Un resultado de `account.movement` sin `Transaction` conocida (otro servicio, o un duplicado tardío) se ignora y se registra.
- Fallos técnicos: 3 reintentos y `.DLT` (ver 7 del contrato de Kafka).

## 8. Propiedades (Config Server)

| Propiedad | Propuesta | Uso |
|---|---|---|
| `bank.zone` | `America/Lima` | "Hoy" para la fecha del movimiento y para los filtros `from`/`to` |
| `payment.await-timeout` | `PT1.5S` | P3: espera del estado terminal antes de responder 202 (mismo nombre que en `debit` y `yanki`; menor que el timeout de 2 s del Gateway) |
| `transfer.compensation.max-attempts` | `5` | Intentos de reversa sin respuesta |
| `transaction.recovery.pending-minutes` | `2` | Antigüedad para considerar algo pendiente |
| `transaction.recovery.cron` | cada minuto | Proceso de recuperación |
| `transaction.page.max-size` | `100` | Tope de `size` (coincide con el contrato) |
| `resilience4j.*` | 2 s, umbrales | Llamadas a `account-service` (P1/P2) |

## 9. Datos de ejemplo para la demo

Con las cuentas de `contracts/account-service/data-model.md` (A1 ahorro, A2 corriente, A3 plazo fijo, B1 corriente, B2 plazo fijo con día no permitido).

| # | Operación | Resultado |
|---|---|---|
| 1 | `POST /deposits` A1, 250.00 | 201. `DEPOSIT` `COMPLETED`, `resultingBalance` = saldo |
| 2 | Repetir el mismo `operationId` | 200, mismo movimiento |
| 3 | `POST /withdrawals` A1, 2000.00 | 422 `INSUFFICIENT_FUNDS`; movimiento `FAILED` (luego `DELETE /transactions/{id}` → 204) |
| 4 | Sexto movimiento del mes en A1 | 201 con `feeTransaction` (`FEE` 2.00, `parentTransactionId`) |
| 5 | `POST /transfers` A1 → A2, 150.00 | 201, `kind = OWN`, `COMPLETED` |
| 6 | `POST /transfers` A1 → B1, 100.00 | 201, `kind = THIRD_PARTY` |
| 7 | `POST /transfers` A1 → B2 (día no permitido) | 422 `NOT_ALLOWED_DAY`, `transferStatus = COMPENSATED`; `TRANSFER_OUT` `REVERSED`, `TRANSFER_IN` `FAILED`; el saldo de A1 vuelve al inicial |
| 8 | `POST /transfers` con la misma cuenta | 422 `SAME_ACCOUNT` |
| 9 | `GET /products/{A1}/transactions?size=10` | Últimos 10, más recientes primero |
| 10 | `POST /transaction-recovery-runs?olderThanMinutes=0` | `RecoveryResult` (con todo terminal: ceros y `stillInProgress = 0`) |

Documento de una transferencia compensada (paso 7):

```json
{
  "_id": "9d8c7b6a-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
  "operationId": "b7f0c2a4-19d2-4c0a-8d6e-3a9c1f7e5b20",
  "sourceAccountId": "3f1c9a7e-5b2d-4e8a-9c6f-1a2b3c4d5e6f",
  "targetAccountId": "7e2a1c9b-4d3f-4b6a-9e8d-2c1b0a9f8e7d",
  "sourceCustomerId": "c0a1b2c3-d4e5-4f60-8a9b-0c1d2e3f4a5b",
  "targetCustomerId": "d1b2c3d4-e5f6-4a70-9b0c-1d2e3f4a5b6c",
  "amount": { "$numberDecimal": "150.00" },
  "kind": "THIRD_PARTY",
  "status": "COMPENSATED",
  "failureReason": { "code": "NOT_ALLOWED_DAY", "message": "Hoy no es el día permitido del plazo fijo." },
  "debitTransactionId": "11111111-2222-4333-8444-555555555555",
  "creditTransactionId": "66666666-7777-4888-9999-000000000000",
  "compensationAttempts": 1,
  "description": "Pago de alquiler",
  "requestedBy": "anonymous",
  "version": { "$numberLong": "5" },
  "createdAt": { "$date": "2026-09-24T15:24:00Z" },
  "updatedAt": { "$date": "2026-09-24T15:24:01Z" }
}
```

## 10. Decisiones y pendientes

**Decidido (nuevo en este contrato)**
- **Sin respuesta definitiva de la cuenta → 202** en depósitos y retiros (antes la ficha decía 503), igual que en transferencias. El 503 queda para cuando no se pudo consultar la cuenta y no se creó nada.
- `operationId` de cliente: máximo **56** caracteres, para que los sufijos derivados quepan en 64.
- Mismo `operationId` con otros datos → 409 `OPERATION_ID_REUSED`. La `date` enviada a la cuenta es "hoy" en cada intento y no se compara.
- Errores 422 de movimiento y de transferencia llevan `transactionId` / `transferId` + `transferStatus` en el cuerpo.
- `occurredAt` es la fecha de aceptación y no cambia; se agrega `updatedAt` a `Transaction`.
- `Transaction.reversal` embebido (`<op>-REV`, `outcome`): permite correlacionar la reversa, reemitir resultados y recuperar reversas pendientes.
- **`transaction.reversed` se publica para toda reversa** (también la compensación de una transferencia) y al revertir un movimiento **se revierten sus comisiones `FEE`**: si no, `report-service` seguiría mostrando el retiro y la comisión que la cuenta ya devolvió (cambio pedido por el contrato de `report-service`). La comisión hereda el `occurredAt` de su movimiento padre.
- Un movimiento con reversa rechazada **sigue `COMPLETED`**; la saga pasa a `COMPENSATION_FAILED`. Cierra el pendiente de la reversa de un depósito sin saldo (lo rechaza `account-service` con `INSUFFICIENT_FUNDS` y aquí termina en `COMPENSATION_FAILED`).
- Dos aggregates escritos juntos con **transacción de Mongo** (`UnitOfWorkPort`), como en `account-service`.
- `Transfer` guarda `sourceCustomerId` y `targetCustomerId` (no se exponen) para el alcance de `CUSTOMER`.
- Historial sin filtros dinámicos: 4 consultas derivadas por producto y 4 por cliente. La consulta administrativa **exige `customerId`**.
- Sin 404 en el historial de un producto: un producto desconocido devuelve una página vacía.
- `GET /transfers` sin paginación (máximo 200).
- Con seguridad desactivada (P1/P2) no se aplica el alcance de `CUSTOMER`; el paso 15 del guion de demo ("B transfiere desde una cuenta de A") pasa a ser solo P3.

**Pendiente**
- **Verificar al implementar las consultas por rango de fechas:** el patrón `…GreaterThanEqualAnd…LessThan` usa dos condiciones sobre el mismo campo; Spring Data MongoDB puede rechazarlo (`InvalidMongoDbApiUsageException`, "you can't add a second expression"). Si pasa, usar `Between` con `Range<Instant>` (cerrado en el inicio, abierto en el fin) o filtrar el rango en memoria. No cambia el contrato REST.
- Nada bloqueante para empezar a programar este servicio.
- **CRUD del historial** (versión restringida): confirmar con el instructor.
- Publicación confiable de eventos (outbox): fuera de alcance.

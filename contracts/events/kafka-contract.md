# Contrato de Kafka

> Fuente única para **quién publica qué, en qué tópico, con qué clave y con qué campos**. Consolida las fichas de `services/` y los cinco flujos de `flows/`. Si hay una diferencia, **este documento prevalece** para todo lo que viaja por Kafka.
> Alcance: P3. En P1/P2 los servicios usan REST y los puertos de eventos son no-op; los campos de este contrato son los mismos que llevarán los adaptadores Kafka.
> Los servicios que nacen en P3 (`auth`, `debit`, `yanki`) solo usan Kafka entre servicios.

## 1. Convenciones

| Tema | Regla |
|---|---|
| Formato | JSON (UTF-8), serializado con Jackson. Sin schema registry; el contrato es este documento |
| Nombres de campos | `camelCase`, en inglés |
| Identificadores | Cadenas UUID v4 generadas por el servicio dueño (se guardan como `_id` de texto en Mongo). Ejemplos de este documento abreviados: `cus-…` |
| Dinero | Número decimal JSON con **2 decimales** (`150.00`); se lee como `BigDecimal`. Solo moneda `PEN`, implícita |
| Fechas | `date` = `yyyy-MM-dd`. `instant` = ISO-8601 en UTC (`2026-09-24T15:24:00Z`) |
| Enums | Cadenas en mayúsculas con guion bajo |
| Nulos | Un campo opcional **se omite** cuando no aplica (no se envía `null`) |
| Datos personales | Los números de tarjeta y celular viajan **enmascarados** (`**** 1234`, `*** *** 321`). El documento del cliente viaja completo solo en el tópico `customer` (lo necesitan `auth` y `yanki`) |
| Lector tolerante | Los consumidores **ignoran campos desconocidos**. Agregar un campo opcional no rompe a nadie |
| Un tópico por aggregate | El tipo de evento va dentro del mensaje (`eventType`); la clave es el id del aggregate. Así se conserva el orden por entidad |

## 2. Sobre común

Todos los mensajes (eventos y comandos) llevan el mismo sobre:

| Campo | Tipo | Req. | Descripción |
|---|---|---|---|
| `eventId` | uuid | ✓ | Identifica el mensaje. Sirve para deduplicar |
| `eventType` | string | ✓ | Tipo (por ejemplo `customer.created`). Es lo que decide el consumidor |
| `eventVersion` | int | ✓ | Versión del esquema del payload. Hoy `1` |
| `occurredAt` | instant | ✓ | Cuándo ocurrió el hecho (no cuándo se publicó) |
| `producer` | string | ✓ | Servicio emisor (`customer-service`, …) |
| `correlationId` | string | ✓ | Para comandos y respuestas: el `operationId` de la operación. Para el resto, el `eventId` |
| `payload` | objeto | ✓ | Datos del tipo de evento (sección 6) |

```json
{
  "eventId": "5c0e7f2a-3d1b-4a7e-9b1c-0d6f5a1e2c11",
  "eventType": "account.movement.applied",
  "eventVersion": 1,
  "occurredAt": "2026-09-24T15:24:00Z",
  "producer": "account-service",
  "correlationId": "b7f0c2a4-19d2-4c0a-8d6e-3a9c1f7e5b20-OUT",
  "payload": { }
}
```

## 3. Catálogo de tópicos

Configuración común del demo: **3 particiones**, factor de replicación 1, `retention.ms` de 7 días salvo los compactados. La clave del mensaje es obligatoria.

| # | Tópico | Tipo | Clave | Compactado | Productor | Consumidores |
|---|---|---|---|---|---|---|
| 1 | `customer` | Estado | `customerId` | Sí | customer | account, credit, debit, yanki, auth |
| 2 | `account` | Estado | `accountId` | Sí | account | transaction, debit, report |
| 3 | `account.command` | Comando | `accountId` | No | transaction | account |
| 4 | `account.movement` | Respuesta | `accountId` | No | account | transaction |
| 5 | `credit` | Estado | `creditId` | Sí | credit | report |
| 6 | `credit-card` | Estado | `cardId` | Sí | credit | account, report |
| 7 | `credit.overdue` | Estado | `customerId` | Sí | credit | account, debit |
| 8 | `credit.operation` | Evento | `productId` | No | credit | transaction |
| 9 | `transaction.command` | Comando | `accountId` | No | debit, yanki | transaction |
| 10 | `transaction` | Evento y respuesta | `productId` | No | transaction | debit, yanki, report |
| 11 | `transfer` | Evento | `transferId` | No | transaction | *(trazabilidad)* |
| 12 | `debit-card` | Estado | `cardId` | Sí | debit | yanki, report |
| 13 | `debit.payment` | Evento | `cardId` | No | debit | report |
| 14 | `wallet` | Estado | `walletId` | Sí | yanki | *(trazabilidad)* |
| 15 | `wallet.payment` | Evento | `paymentId` | No | yanki | *(trazabilidad)* |

- **Estado:** el mensaje lleva el **estado completo** de la entidad. Con compactación, el último mensaje por clave reconstruye la copia local de cualquier consumidor.
- **Comando:** pide una acción a otro servicio. El tópico lo nombra **el receptor**. Siempre lleva `operationId`.
- **Respuesta:** resultado de un comando, correlacionado por `operationId`.
- **Evento:** hecho ocurrido (sin estado completo).
- Los tópicos con consumidor "trazabilidad" no los lee ningún servicio de negocio; sirven para auditoría y para la demo.
- Cada tópico tiene su cola de mensajes fallidos `<tópico>.DLT` (ver sección 7).

### 3.1 Tipos de evento por tópico

| Tópico | `eventType` |
|---|---|
| `customer` | `customer.created`, `customer.updated`, `customer.deleted` |
| `account` | `account.created`, `account.updated`, `account.deleted` |
| `account.command` | `transaction.movement.requested`, `transaction.movement.reversal.requested` |
| `account.movement` | `account.movement.applied`, `account.movement.rejected`, `account.movement.reversed`, `account.movement.reversal.rejected` |
| `credit` | `credit.created`, `credit.updated`, `credit.closed` |
| `credit-card` | `credit.card.created`, `credit.card.updated`, `credit.card.closed` |
| `credit.overdue` | `credit.overdue.detected`, `credit.overdue.cleared` |
| `credit.operation` | `credit.payment.registered`, `credit.card.charge.registered` |
| `transaction.command` | `debit.payment.requested`, `yanki.movement.requested`, `yanki.movement.reversal.requested` |
| `transaction` | `transaction.registered`, `transaction.failed`, `transaction.reversed`, `transaction.reversal.failed` |
| `transfer` | `transfer.completed`, `transfer.failed` |
| `debit-card` | `debit.card.created`, `debit.card.updated`, `debit.card.closed` |
| `debit.payment` | `debit.payment.completed`, `debit.payment.failed` |
| `wallet` | `yanki.wallet.created`, `yanki.wallet.updated`, `yanki.wallet.closed`, `yanki.wallet.card.linked`, `yanki.wallet.card.unlinked` |
| `wallet.payment` | `yanki.payment.completed`, `yanki.payment.failed` |

## 4. Matriz por servicio

| Servicio | Publica | Consume |
|---|---|---|
| `customer-service` | `customer` | — |
| `account-service` | `account`, `account.movement` | `customer`, `credit-card`, `credit.overdue`, `account.command` |
| `credit-service` | `credit`, `credit-card`, `credit.overdue`, `credit.operation` | `customer` |
| `transaction-service` | `account.command`, `transaction`, `transfer` | `account`, `account.movement`, `credit.operation`, `transaction.command` |
| `report-service` | — | `account`, `credit`, `credit-card`, `debit-card`, `transaction`, `debit.payment` |
| `auth-service` | — | `customer` |
| `debit-service` | `debit-card`, `debit.payment`, `transaction.command` | `customer`, `account`, `credit.overdue`, `transaction` |
| `yanki-service` | `wallet`, `wallet.payment`, `transaction.command` | `customer`, `debit-card`, `transaction` |

`credit-service` no consume `credit.overdue`: usa sus propios datos.

## 5. Valores de enums usados en los payloads

| Enum | Valores |
|---|---|
| `customerType` | `PERSONAL`, `BUSINESS` |
| `customerProfile` | `STANDARD`, `VIP`, `PYME` |
| `customerStatus` | `ACTIVE`, `INACTIVE` |
| `documentType` | `DNI`, `CEX`, `PASSPORT`, `RUC` |
| `accountType` | `SAVINGS`, `CHECKING`, `FIXED_TERM` |
| `accountStatus` | `ACTIVE`, `INACTIVE` (baja lógica) |
| `creditStatus` | `ACTIVE`, `OVERDUE`, `PAID`, `CLOSED` |
| `cardStatus` (crédito) | `ACTIVE`, `OVERDUE`, `CLOSED` |
| `debitCardStatus` | `ACTIVE`, `CLOSED` |
| `walletStatus` | `ACTIVE`, `CLOSED` |
| `productType` | `ACCOUNT`, `CREDIT`, `CREDIT_CARD` (en `transaction`, `credit.operation`) |
| `transactionType` | `DEPOSIT`, `WITHDRAWAL`, `TRANSFER_OUT`, `TRANSFER_IN`, `FEE`, `CREDIT_PAYMENT`, `CARD_PAYMENT`, `CARD_CHARGE`, `DEBIT_PAYMENT`, `YANKI_PAYMENT_OUT`, `YANKI_PAYMENT_IN` |
| `movementType` (en comandos a cuentas) | `DEPOSIT`, `WITHDRAWAL` |
| `transferStatus` | `COMPLETED`, `FAILED`, `COMPENSATED`, `COMPENSATION_FAILED` |
| `walletPaymentStatus` | `COMPLETED`, `FAILED`, `COMPENSATED`, `COMPENSATION_FAILED` |
| `reasonCode` | `INSUFFICIENT_FUNDS`, `MONTHLY_LIMIT_EXCEEDED`, `NOT_ALLOWED_DAY`, `ACCOUNT_INACTIVE`, `INVALID_AMOUNT`, `OPERATION_NOT_FOUND`, `WALLET_INACTIVE`, `RECEIVER_NOT_FOUND`, `INVALID_DATE`, `OPERATION_ID_REUSED`, `OPERATION_NOT_APPLIED`, `ACCOUNT_NOT_FOUND` y los demás códigos de negocio del servicio que rechaza |

## 6. Payloads

Notación de la columna **Req.**: ✓ siempre presente; `–` opcional (se omite si no aplica).
Todos los ejemplos muestran solo el `payload`; el sobre es el de la sección 2.

### 6.1 `customer` — estado, clave `customerId`
Tipos: `customer.created`, `customer.updated`, `customer.deleted` (con `status=INACTIVE`). **Los tres llevan el mismo estado completo.**

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `customerId` | uuid | ✓ | |
| `type` | customerType | ✓ | |
| `profile` | customerProfile | ✓ | |
| `status` | customerStatus | ✓ | |
| `document` | `{ type: documentType, number: string }` | ✓ | No cambia nunca |
| `name` | string | ✓ | |
| `updatedAt` | instant | ✓ | Para aplicar solo el evento más reciente |

```json
{ "customerId": "cus-1", "type": "PERSONAL", "profile": "VIP", "status": "ACTIVE",
  "document": { "type": "DNI", "number": "12345678" }, "name": "Ana Torres",
  "updatedAt": "2026-09-24T15:24:00Z" }
```

### 6.2 `account` — estado, clave `accountId`
Tipos: `account.created`, `account.updated`, `account.deleted` (con `status=INACTIVE`). No incluye el saldo (cambia con cada movimiento).

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `accountId` | uuid | ✓ | |
| `maskedNumber` | string | ✓ | `**** 4821` |
| `customerId` | uuid | ✓ | |
| `type` | accountType | ✓ | |
| `status` | accountStatus | ✓ | |
| `updatedAt` | instant | ✓ | |

```json
{ "accountId": "acc-1", "maskedNumber": "**** 4821", "customerId": "cus-1",
  "type": "SAVINGS", "status": "ACTIVE", "updatedAt": "2026-09-24T15:24:00Z" }
```

### 6.3 `account.command` — comandos `transaction` → `account`, clave `accountId`

`transaction.movement.requested`

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `operationId` | string | ✓ | Id de la pata (`<op>-OUT`, `<op>-IN`, o el `operationId` de un depósito o retiro). Idempotente |
| `accountId` | uuid | ✓ | |
| `type` | movementType | ✓ | `DEPOSIT` o `WITHDRAWAL` |
| `amount` | decimal | ✓ | > 0 |
| `date` | date | ✓ | Fecha del movimiento (regla del día del plazo fijo) |

`transaction.movement.reversal.requested`

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `operationId` | string | ✓ | El id de la **operación original** a revertir (`<op>-OUT`) |
| `accountId` | uuid | ✓ | |

`correlationId` del sobre = `operationId`.

### 6.4 `account.movement` — respuestas `account` → `transaction`, clave `accountId`

`account.movement.applied`

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `operationId` | string | ✓ | El de la operación |
| `accountId` | uuid | ✓ | |
| `type` | movementType | ✓ | |
| `amount` | decimal | ✓ | |
| `fee` | decimal | ✓ | `0.00` si no hubo comisión |
| `newBalance` | decimal | ✓ | Saldo tras aplicar (ya descuenta la comisión) |
| `movementNumber` | int | ✓ | Número de movimiento del mes en la cuenta |

`account.movement.rejected`: `operationId`, `accountId`, `reasonCode` (todos ✓), `message` (–).
`account.movement.reversed`: `operationId` (el original), `accountId`, `newBalance` (todos ✓).
`account.movement.reversal.rejected`: `operationId` (el original), `accountId`, `reasonCode` (por ejemplo `OPERATION_NOT_FOUND`, `ACCOUNT_INACTIVE`) (todos ✓).

```json
{ "operationId": "b7f0c2a4-…-OUT", "accountId": "acc-1", "type": "WITHDRAWAL",
  "amount": 150.00, "fee": 2.00, "newBalance": 848.00, "movementNumber": 6 }
```

### 6.5 `credit` — estado, clave `creditId`
Tipos: `credit.created`, `credit.updated`, `credit.closed`.

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `creditId` | uuid | ✓ | |
| `customerId` | uuid | ✓ | |
| `ownerType` | `PERSONAL` / `BUSINESS` | ✓ | |
| `status` | creditStatus | ✓ | |
| `principalAmount` | decimal | ✓ | |
| `outstandingBalance` | decimal | ✓ | |
| `dueDate` | date | ✓ | |
| `updatedAt` | instant | ✓ | |

### 6.6 `credit-card` — estado, clave `cardId`
Tipos: `credit.card.created`, `credit.card.updated`, `credit.card.closed`.

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `cardId` | uuid | ✓ | |
| `customerId` | uuid | ✓ | |
| `ownerType` | `PERSONAL` / `BUSINESS` | ✓ | |
| `maskedNumber` | string | ✓ | |
| `status` | cardStatus | ✓ | |
| `creditLimit` | decimal | ✓ | |
| `usedAmount` | decimal | ✓ | |
| `paymentDueDate` | date | – | Solo con saldo usado |
| `updatedAt` | instant | ✓ | |

`account-service` cuenta la tarjeta como "activa" **solo** cuando `status = ACTIVE`: una tarjeta `OVERDUE` o `CLOSED` no cumple el requisito VIP/PYME. Por eso `credit.card.updated` se publica **también cuando la tarjeta cambia de estado** (`ACTIVE` ↔ `OVERDUE`), y `account-service` consume los tres tipos (`created`, `updated`, `closed`).

### 6.7 `credit.overdue` — estado, clave `customerId`

`credit.overdue.detected`

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `customerId` | uuid | ✓ | |
| `productType` | `CREDIT` / `CREDIT_CARD` | ✓ | |
| `productId` | uuid | ✓ | |
| `dueDate` | date | ✓ | |
| `outstandingAmount` | decimal | ✓ | |
| `occurredAt` | instant | ✓ | Se usa para la regla "última escritura gana" |

`credit.overdue.cleared`: `customerId`, `occurredAt` (✓).

Consumidor: guarda `overdue` (`true` con `detected`, `false` con `cleared`) y `updatedAt = occurredAt`, y aplica el evento solo si su `occurredAt` es igual o más reciente.

### 6.8 `credit.operation` — evento, clave `productId`

`credit.payment.registered`

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `operationId` | string | ✓ | |
| `productType` | `CREDIT` / `CREDIT_CARD` | ✓ | |
| `productId` | uuid | ✓ | |
| `customerId` | uuid | ✓ | Dueño del producto |
| `payerCustomerId` | uuid | – | Si pagó un tercero |
| `amount` | decimal | ✓ | |
| `resultingBalance` | decimal | ✓ | Saldo pendiente (crédito) o monto usado (tarjeta) tras el pago |
| `occurredAt` | instant | ✓ | |

`credit.card.charge.registered`: `operationId`, `cardId` (`productId` en la clave), `customerId`, `amount`, `resultingBalance` (monto usado), `availableCredit`, `description` (–), `occurredAt`.

`transaction-service` los convierte en `Transaction`: crédito + pago → `CREDIT_PAYMENT`; tarjeta + pago → `CARD_PAYMENT`; tarjeta + consumo → `CARD_CHARGE`.

### 6.9 `transaction.command` — comandos hacia `transaction`, clave `accountId`

`debit.payment.requested` (de `debit-service`)

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `operationId` | string | ✓ | El del pago (tal cual, sin sufijo; máximo 56 caracteres porque `transaction-service` agrega `-FEE` a la comisión). También se publica de nuevo, igual, en cada reenvío de la recuperación |
| `paymentId` | uuid | ✓ | |
| `cardId` | uuid | ✓ | |
| `customerId` | uuid | ✓ | |
| `accountId` | uuid | ✓ | Cuenta de cargo |
| `amount` | decimal | ✓ | |
| `description` | string | – | |
| `requestedAt` | instant | ✓ | |

`yanki.movement.requested` (de `yanki-service`)

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `operationId` | string | ✓ | `legId`: `<op>-OUT` o `<op>-IN` |
| `paymentId` | uuid | ✓ | |
| `accountId` | uuid | ✓ | Cuenta principal de la tarjeta |
| `type` | `YANKI_PAYMENT_OUT` / `YANKI_PAYMENT_IN` | ✓ | OUT = retiro, IN = depósito |
| `amount` | decimal | ✓ | |
| `description` | string | – | |

`yanki.movement.reversal.requested`

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `operationId` | string | ✓ | `<op>-REV` |
| `originalOperationId` | string | ✓ | `<op>-OUT` |
| `paymentId` | uuid | ✓ | |
| `accountId` | uuid | ✓ | |

### 6.10 `transaction` — eventos y respuestas, clave `productId`

`transaction.registered` (un movimiento quedó `COMPLETED`; también las comisiones `FEE`)

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `transactionId` | uuid | ✓ | |
| `operationId` | string | ✓ | Correlación con quien lo pidió |
| `productId` | uuid | ✓ | |
| `productType` | productType | ✓ | |
| `customerId` | uuid | ✓ | Dueño del producto |
| `type` | transactionType | ✓ | |
| `amount` | decimal | ✓ | |
| `fee` | decimal | – | Comisión aplicada por la cuenta |
| `resultingBalance` | decimal | ✓ | Saldo de la cuenta, saldo pendiente o monto usado, según el producto |
| `description` | string | – | |
| `transferId` | uuid | – | Si es pata de una transferencia |
| `parentTransactionId` | uuid | – | En las comisiones `FEE` |
| `payerCustomerId` | uuid | – | En pagos de terceros |
| `occurredAt` | instant | ✓ | |

`transaction.failed`: `transactionId`, `operationId`, `productId`, `productType`, `type`, `reasonCode` (✓), `message` (–).
`transaction.reversed`: `operationId` (el de la reversa, `<op>-REV`), `originalOperationId`, `productId`, `amount`, `resultingBalance`, `occurredAt` (✓). Se publica para **toda** reversa aplicada: la pedida por `yanki-service` y la compensación de una transferencia. Las comisiones `FEE` del movimiento revertido se revierten con él (el consumidor las enlaza por `parentTransactionId`).
`transaction.reversal.failed`: `operationId`, `originalOperationId`, `reasonCode` (✓).

Reglas de correlación:
- `debit-service` procesa solo `transaction.registered/failed` de tipo `DEBIT_PAYMENT` cuyo `operationId` sea de un pago suyo.
- `yanki-service` procesa solo tipos `YANKI_PAYMENT_*` y las reversas cuyo `operationId` termine en `-OUT`, `-IN` o `-REV` de un pago suyo.
- `report-service` proyecta `transaction.registered` como movimiento y `transaction.reversed` como exclusión del movimiento original (por `originalOperationId`) y de sus comisiones (por `parentTransactionId`).
- **Reemisión:** ante un comando repetido cuyo resultado ya existe, `transaction-service` vuelve a publicar el mismo resultado (mismos campos; nuevo `eventId`).

### 6.11 `transfer` — evento, clave `transferId`

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `transferId` | uuid | ✓ | |
| `operationId` | string | ✓ | |
| `status` | transferStatus | ✓ | `transfer.completed` → `COMPLETED`; `transfer.failed` → `FAILED`, `COMPENSATED` o `COMPENSATION_FAILED` |
| `sourceAccountId` | uuid | ✓ | |
| `targetAccountId` | uuid | ✓ | |
| `amount` | decimal | ✓ | |
| `kind` | `OWN` / `THIRD_PARTY` | ✓ | |
| `reasonCode` | string | – | Solo en `transfer.failed` |

### 6.12 `debit-card` — estado, clave `cardId`
Tipos: `debit.card.created`, `debit.card.updated`, `debit.card.closed` (con `status=CLOSED`).

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `cardId` | uuid | ✓ | |
| `customerId` | uuid | ✓ | |
| `maskedNumber` | string | ✓ | |
| `accountIds` | uuid[] | ✓ | Cuentas asociadas |
| `mainAccountId` | uuid | ✓ | Cuenta principal |
| `expiryDate` | `yyyy-MM` | ✓ | |
| `status` | debitCardStatus | ✓ | |
| `updatedAt` | instant | ✓ | |

### 6.13 `debit.payment` — evento, clave `cardId`

`debit.payment.completed`

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `paymentId` | uuid | ✓ | |
| `cardId` | uuid | ✓ | |
| `customerId` | uuid | ✓ | |
| `accountId` | uuid | ✓ | |
| `amount` | decimal | ✓ | |
| `fee` | decimal | – | |
| `description` | string | – | |
| `resultingBalance` | decimal | ✓ | Saldo de la cuenta tras el pago |
| `occurredAt` | instant | ✓ | |

`debit.payment.failed`: `paymentId`, `cardId`, `reasonCode` (✓).

### 6.14 `wallet` — estado, clave `walletId`
Tipos: `yanki.wallet.created`, `yanki.wallet.updated`, `yanki.wallet.closed` (con `status=CLOSED`).

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `walletId` | uuid | ✓ | |
| `maskedPhone` | string | ✓ | |
| `status` | walletStatus | ✓ | |
| `linkedCardId` | uuid | – | Presente si está asociado a una tarjeta |
| `updatedAt` | instant | ✓ | |

`yanki.wallet.card.linked` / `yanki.wallet.card.unlinked`: **el mismo estado completo de arriba** (`walletId`, `maskedPhone`, `status`, `linkedCardId?`, `updatedAt`) **más** `cardId` (✓; la tarjeta asociada, o la que se quitó). Como el tópico está compactado, cada mensaje reemplaza al anterior de ese `walletId`; un payload con solo `walletId` y `cardId` borraría `status` y `maskedPhone`. `linkedCardId` está presente en `linked` y ausente en `unlinked`.

### 6.15 `wallet.payment` — evento, clave `paymentId`

`yanki.payment.completed`: `paymentId`, `senderWalletId`, `receiverWalletId`, `amount` (✓).
`yanki.payment.failed`: `paymentId`, `status` (`FAILED`, `COMPENSATED` o `COMPENSATION_FAILED`), `reasonCode` (✓).

## 7. Reglas de consumo y entrega

| Tema | Regla |
|---|---|
| Entrega | Al menos una vez. Los consumidores son **idempotentes** |
| Confirmación | Se confirma (commit del offset) **después** de guardar el resultado. Sin `enable.auto.commit` |
| Grupo de consumo | Uno por servicio (`groupId` = nombre del servicio). Varias instancias comparten el grupo |
| Inicio de lectura | `auto.offset.reset=earliest`: un servicio nuevo, o con la base vacía, reconstruye sus copias leyendo desde el inicio |
| Orden | Garantizado solo por clave dentro de una partición; por eso la clave es el id del aggregate |
| Copias locales | Se aplica un evento **solo si su fecha** (`updatedAt` u `occurredAt`) es igual o más reciente que la guardada. Los duplicados y los desordenados se ignoran |
| Comandos repetidos | Se responde con el resultado original (misma `operationId`). Si ya terminó, se **vuelve a publicar** la respuesta |
| Reintentos | 3 intentos con espera creciente (1 s, 2 s, 4 s). Si sigue fallando, el mensaje va a `<tópico>.DLT` y se continúa con el siguiente |
| Mensajes inválidos | Un mensaje que no se puede leer (JSON o campos requeridos ausentes) va directo a `.DLT`, sin reintentos |
| Tipo desconocido | Se ignora y se registra en el log (un productor nuevo no debe romper a un consumidor viejo) |
| Versiones | `eventVersion` sube solo ante un cambio **incompatible** (quitar o cambiar el tipo de un campo). Agregar campos opcionales no cambia la versión |
| Publicación | Después de guardar el cambio en la base. **Sin outbox en el demo:** si el servicio cae entre guardar y publicar, el evento se pierde (pendiente 3) |
| Compactación | Solo en tópicos de **estado**; el mensaje siempre lleva el estado completo |
| Datos sensibles | No se registran payloads completos en logs; los campos personales se enmascaran |

## 8. Correlación de operaciones (resumen)

| Operación | Comando | Respuesta | Correlación |
|---|---|---|---|
| Retiro o depósito de una transferencia | `transaction.movement.requested` (`<op>-OUT`, `<op>-IN`) | `account.movement.applied` / `rejected` | `operationId` de la pata |
| Compensación de una transferencia | `transaction.movement.reversal.requested` (`operationId` = `<op>-OUT`) | `account.movement.reversed` / `reversal.rejected` | `operationId` original |
| Pago con débito | `debit.payment.requested` (`<op>`) | `transaction.registered` / `failed` (`DEBIT_PAYMENT`) | `<op>` |
| Pago Yanki, pata remota | `yanki.movement.requested` (`<op>-OUT`, `<op>-IN`) | `transaction.registered` / `failed` (`YANKI_PAYMENT_*`) | `<legId>` |
| Compensación Yanki remota | `yanki.movement.reversal.requested` (`<op>-REV`, original `<op>-OUT`) | `transaction.reversed` / `reversal.failed` | `<op>-REV` |

En el pago con débito y en los pagos Yanki, `transaction-service` usa el `operationId` recibido **tal cual** como `operationId` del movimiento de cuenta que pide a `account-service`.

## 9. Cambios que este contrato pide a las fichas (aplicados)

| Ficha | Cambio |
|---|---|
| `account-service` | Nuevo evento `account.movement.reversal.rejected`; `AccountCreated/Updated/Deleted` llevan `maskedNumber`, `type`, `status` y `updatedAt` (sin saldo); `MovementApplied` incluye `movementNumber` |
| `transaction-service` | `TransactionRegistered` incluye `description`, `transferId`, `parentTransactionId` y `payerCustomerId`; al recibir un rechazo de reversa publica `transaction.reversal.failed` |
| `debit-service` | Los eventos de tarjeta llevan `maskedNumber` y `expiryDate` |
| `credit-service` | Los eventos de producto llevan el estado completo con `updatedAt`; `credit.payment.registered` y `credit.card.charge.registered` incluyen `resultingBalance` |
| `flows/README.md` | Las columnas "tópico" de los flujos muestran el **tipo de evento**; el tópico físico está en la sección 3 de este documento |

## 10. Pendientes

1. **Esquemas formales** (JSON Schema o AsyncAPI) generados desde estas tablas: opcional, útil para probar consumidores y productores.
2. ~~Tarjeta `OVERDUE` como "tarjeta activa"~~ **Decidido:** no cuenta (solo `ACTIVE`).
3. **Outbox** para publicar sin perder eventos: fuera de alcance en el demo.
4. Prefijo de los nombres de tópicos por entorno (`dev.`, `test.`): fuera de alcance.
5. Cantidad de particiones y retención definitivas: se revisan al levantar el `docker-compose`.
6. Tamaño máximo de mensaje y compresión: valores por defecto de Kafka.

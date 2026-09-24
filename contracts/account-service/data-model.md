# `account-service` — Modelo de datos

> Complementa la ficha (`services/account-service.md`) y el contrato REST (`openapi.yaml`, en esta carpeta) con los **tipos exactos** de cada campo, los documentos de MongoDB, los índices, las reglas de cálculo, los mapeos entre capas y los datos de ejemplo. Si algo difiere, el `openapi.yaml` manda para la API y `contracts/events/kafka-contract.md` para los eventos.

## 1. Entidades de dominio

### 1.1 Aggregate `Account`

| Campo | Tipo (dominio) | Req. | Regla | Mutable |
|---|---|---|---|---|
| `id` | `AccountId` (`String`, UUID v4) | ✓ | Lo genera el servicio | No |
| `accountNumber` | `AccountNumber` (`String`) | ✓ | 14 dígitos, único. Lo genera `AccountNumberGenerator` | No |
| `customerId` | `String` | ✓ | Solo referencia; el cliente vive en `customer-service` | No |
| `customerType` | `CustomerType` | ✓ | Foto al abrir | No |
| `customerProfile` | `CustomerProfile` | ✓ | Foto al abrir. No se actualiza si el cliente cambia de perfil | No |
| `type` | `AccountType` | ✓ | `SAVINGS`, `CHECKING`, `FIXED_TERM` | No |
| `alias` | `String?` | – | Máximo 60 | Sí |
| `balance` | `Money` | ✓ | ≥ 0 siempre. Solo cambia con movimientos y reversas | Sí (por movimiento) |
| `currency` | `String` | ✓ | Siempre `PEN` | No |
| `conditions` | `AccountConditions` | ✓ | Copia del catálogo tomada al abrir | No |
| `movementDayOfMonth` | `Integer?` | – | 1–28. Obligatorio solo en `FIXED_TERM` y prohibido en los demás | No |
| `holders` | `List<AccountParty>` | ✓ | Empresarial: ≥ 1. Personal: vacío | Sí |
| `signers` | `List<AccountParty>` | ✓ | Empresarial: 0..n. Personal: vacío | Sí |
| `monthlyActivity` | `MonthlyActivity` | ✓ | Se reinicia al cambiar de mes | Sí |
| `balanceTracker` | `BalanceTracker` | ✓ | Acumulador del promedio diario | Sí |
| `status` | `AccountStatus` | ✓ | `ACTIVE` al abrir; `INACTIVE` tras la baja | Sí |
| `version` | `Long` | ✓ | Control optimista. **No** se expone por REST ni por eventos | Sí (automático) |
| `createdAt` | `Instant` | ✓ | Reloj inyectado | No |
| `updatedAt` | `Instant` | ✓ | Cada cambio | Sí |

Comportamiento: `open(...)`, `updateParties(alias, holders, signers)`, `applyMovement(operationId, type, amount, date, now)` → `MovementResult`, `reverseMovement(operation, now)` → `ReversalResult`, `close()`.

### 1.2 Aggregate `AccountProduct` (catálogo)

| Campo | Tipo | Req. | Regla |
|---|---|---|---|
| `id` | `ProductId` (`String`) | ✓ | **Id natural** `<TIPO>_<PERFIL>` (`SAVINGS_VIP`). Es la única excepción a "id UUID": dato maestro sembrado al arrancar |
| `accountType` | `AccountType` | ✓ | |
| `profile` | `CustomerProfile` | ✓ | La pareja (`accountType`, `profile`) es única |
| `conditions` | `AccountConditions` | ✓ | Editable por `ADMIN` |
| `updatedAt` | `Instant` | ✓ | |

### 1.3 Value objects

| VO | Campos | Validación |
|---|---|---|
| `AccountId` | `value` | No vacío |
| `AccountNumber` | `value` | 14 dígitos |
| `Money` | `amount: BigDecimal` (escala 2, `HALF_EVEN` al normalizar), `currency` | No nulo. Solo `PEN`. `plus`, `minus`, `isGreaterThan`, `isZero` |
| `AccountParty` | `documentType`, `documentNumber`, `fullName` | Formato del documento (igual que `customer-service`); nombre 1–150 |
| `AccountConditions` | `maintenanceFee: Money`, `minimumOpeningAmount: Money`, `monthlyMovementLimit: Integer?`, `freeTransactionsLimit: int`, `transactionFee: Money`, `minimumDailyAverage: Money?`, `requiresCreditCard: boolean` | Montos ≥ 0. `monthlyMovementLimit` ≥ 1 si existe. `freeTransactionsLimit` ≥ 0 |
| `MonthlyActivity` | `yearMonth: YearMonth`, `movementCount: int` | ≥ 0 |
| `BalanceTracker` | `yearMonth: YearMonth`, `accumulatedBalanceDays: BigDecimal`, `lastBalance: Money`, `lastChangeDate: LocalDate` | Ver 3.3 |
| `MovementResult` | `operationId`, `accountId`, `type`, `amount`, `fee`, `newBalance`, `movementNumber` | Inmutable |
| `ReversalResult` | `operationId`, `accountId`, `newBalance` | Inmutable |
| `AccountOperation` | Ver 2.3 | Registro de una operación (entidad interna, no aggregate) |
| `CustomerSnapshot` | `customerId`, `type`, `profile`, `status` | Dato de lectura (`CustomerLookupPort`) |

### 1.4 Enums

| Enum | Valores |
|---|---|
| `AccountType` | `SAVINGS`, `CHECKING`, `FIXED_TERM` |
| `AccountStatus` | `ACTIVE`, `INACTIVE` |
| `MovementType` | `DEPOSIT`, `WITHDRAWAL` |
| `OperationStatus` | `APPLIED`, `REJECTED`, `REVERSED` |
| `CustomerType` / `CustomerProfile` | Como en `customer-service` |
| `DocumentType` | `DNI`, `CEX`, `PASSPORT`, `RUC` |

## 2. Documentos MongoDB

Dinero como **`Decimal128`**. Spring Data MongoDB guarda `BigDecimal` como texto por defecto; hay que configurar `MongoCustomConversions` con `BigDecimalRepresentation.DECIMAL128`. Sin eso las comparaciones y sumas en Mongo no sirven. Fechas `LocalDate` como texto `yyyy-MM-dd`; `YearMonth` como texto `yyyy-MM`; `Instant` como `date` UTC. Los índices se crean al arrancar (`spring.data.mongodb.auto-index-creation=true`).

### 2.1 `accounts` — `AccountDocument`

| Campo Mongo | Tipo Mongo | Req. | Notas |
|---|---|---|---|
| `_id` | string | ✓ | UUID v4 |
| `accountNumber` | string | ✓ | 14 dígitos |
| `customerId` | string | ✓ | |
| `customerType` | string | ✓ | |
| `customerProfile` | string | ✓ | |
| `type` | string | ✓ | |
| `alias` | string | – | Se omite si no hay |
| `balance` | decimal128 | ✓ | |
| `currency` | string | ✓ | `PEN` |
| `conditions.maintenanceFee` | decimal128 | ✓ | |
| `conditions.minimumOpeningAmount` | decimal128 | ✓ | |
| `conditions.monthlyMovementLimit` | int | – | |
| `conditions.freeTransactionsLimit` | int | ✓ | |
| `conditions.transactionFee` | decimal128 | ✓ | |
| `conditions.minimumDailyAverage` | decimal128 | – | |
| `conditions.requiresCreditCard` | bool | ✓ | |
| `movementDayOfMonth` | int | – | Solo plazo fijo |
| `holders[]` | array | ✓ | `{ document: { type, number }, fullName }`. Vacío si no hay |
| `signers[]` | array | ✓ | Igual |
| `monthlyActivity.yearMonth` | string | ✓ | `2026-09` |
| `monthlyActivity.movementCount` | int | ✓ | |
| `balanceTracker.yearMonth` | string | ✓ | |
| `balanceTracker.accumulatedBalanceDays` | decimal128 | ✓ | |
| `balanceTracker.lastBalance` | decimal128 | ✓ | |
| `balanceTracker.lastChangeDate` | string | ✓ | `yyyy-MM-dd` |
| `status` | string | ✓ | |
| `version` | long | ✓ | `@Version` |
| `createdAt` / `updatedAt` | date | ✓ | UTC |

```json
{
  "_id": "3f1c9a7e-5b2d-4e8a-9c6f-1a2b3c4d5e6f",
  "accountNumber": "00120260924871",
  "customerId": "c0a1b2c3-d4e5-4f60-8a9b-0c1d2e3f4a5b",
  "customerType": "PERSONAL",
  "customerProfile": "VIP",
  "type": "SAVINGS",
  "alias": "Ahorros del viaje",
  "balance": { "$numberDecimal": "848.00" },
  "currency": "PEN",
  "conditions": {
    "maintenanceFee": { "$numberDecimal": "0.00" },
    "minimumOpeningAmount": { "$numberDecimal": "0.00" },
    "monthlyMovementLimit": 10,
    "freeTransactionsLimit": 5,
    "transactionFee": { "$numberDecimal": "2.00" },
    "minimumDailyAverage": { "$numberDecimal": "500.00" },
    "requiresCreditCard": true
  },
  "holders": [],
  "signers": [],
  "monthlyActivity": { "yearMonth": "2026-09", "movementCount": 6 },
  "balanceTracker": {
    "yearMonth": "2026-09",
    "accumulatedBalanceDays": { "$numberDecimal": "16000.00" },
    "lastBalance": { "$numberDecimal": "848.00" },
    "lastChangeDate": "2026-09-24"
  },
  "status": "ACTIVE",
  "version": { "$numberLong": "8" },
  "createdAt": { "$date": "2026-09-01T14:00:00Z" },
  "updatedAt": { "$date": "2026-09-24T15:24:00Z" }
}
```

**Índices**

| Nombre | Campos | Opciones | Para qué |
|---|---|---|---|
| `uk_account_number` | `accountNumber` (1) | **Único** | Número único |
| `ix_account_customer_type_status` | `customerId` (1), `type` (1), `status` (1) | — | Listados y conteo por cliente |
| `uk_personal_savings` | `customerId` (1) | **Único parcial**: `{ customerType: "PERSONAL", type: "SAVINGS", status: "ACTIVE" }` | Regla 3 contra carreras: 1 ahorro activo por cliente personal |
| `uk_personal_checking` | `customerId` (1) | **Único parcial**: `{ customerType: "PERSONAL", type: "CHECKING", status: "ACTIVE" }` | Ídem para corriente |

Los filtros parciales usan solo igualdades (los admite cualquier versión de Mongo). Se usan **dos** índices, uno por tipo, en vez de uno con `$in`. Cuando un alta choca con `uk_personal_savings` o `uk_personal_checking`, el adaptador traduce el `DuplicateKeyException` a 422 `SAVINGS_LIMIT_REACHED` o `CHECKING_LIMIT_REACHED` según el nombre del índice; con `uk_account_number` regenera el número y reintenta (máx. 3).

### 2.2 `account_products` — `AccountProductDocument`

| Campo Mongo | Tipo | Req. | Notas |
|---|---|---|---|
| `_id` | string | ✓ | `SAVINGS_VIP` |
| `accountType` | string | ✓ | |
| `profile` | string | ✓ | |
| `conditions` | objeto | ✓ | Mismos subcampos que en `accounts` |
| `updatedAt` | date | ✓ | |

Índice: `uk_product_type_profile` único sobre (`accountType`, `profile`).

**Sembrado (`AccountProductSeeder`):** al arrancar inserta cada entrada del catálogo inicial **solo si no existe** (nunca pisa ediciones). Catálogo inicial:

| `_id` | Mant. | Apertura mín. | Tope mensual | Libres | Comisión | Prom. mín. | Exige tarjeta |
|---|---|---|---|---|---|---|---|
| `SAVINGS_STANDARD` | 0.00 | 0.00 | 10 | 5 | 2.00 | — | false |
| `SAVINGS_VIP` | 0.00 | 0.00 | 10 | 5 | 2.00 | 500.00 | true |
| `CHECKING_STANDARD` | 15.00 | 0.00 | — | 5 | 2.00 | — | false |
| `CHECKING_PYME` | 0.00 | 0.00 | — | 5 | 2.00 | — | true |
| `FIXED_TERM_STANDARD` | 0.00 | 100.00 | 1 | 5 | 2.00 | — | false |

**Elección de la condición al abrir:** buscar `<TIPO>_<perfil del cliente>`; si no existe, `<TIPO>_STANDARD`. Ejemplos: cliente `VIP` abre corriente → `CHECKING_STANDARD`; cliente `PYME` abre corriente → `CHECKING_PYME`; cliente `VIP` abre plazo fijo → `FIXED_TERM_STANDARD`.

### 2.3 `account_operations` — `AccountOperationDocument`

Registro de cada movimiento pedido. Da la idempotencia y los datos para revertir.

| Campo Mongo | Tipo | Req. | Notas |
|---|---|---|---|
| `_id` | string | ✓ | **El `operationId`** (natural). Garantiza que una operación no se registre dos veces |
| `accountId` | string | ✓ | |
| `type` | string | ✓ | `DEPOSIT` / `WITHDRAWAL` |
| `amount` | decimal128 | ✓ | |
| `date` | string | ✓ | Fecha del movimiento (`yyyy-MM-dd`) |
| `yearMonth` | string | ✓ | Mes de `date` |
| `status` | string | ✓ | `APPLIED` / `REJECTED` / `REVERSED` |
| `fee` | decimal128 | – | Solo si `APPLIED`/`REVERSED` |
| `newBalance` | decimal128 | – | Saldo tras aplicar |
| `movementNumber` | int | – | |
| `reasonCode` | string | – | Solo `REJECTED` |
| `reversedBalance` | decimal128 | – | Saldo tras la reversa (para responder repetidas) |
| `createdAt` / `updatedAt` | date | ✓ | |

Índice: `ix_operation_account_created` sobre (`accountId` (1), `createdAt` (-1)).

Reglas:
- Repetir un `operationId` con la misma `accountId`, `type` y `amount` devuelve lo guardado: `APPLIED` → el `MovementResult`; `REJECTED` → el **mismo** 422 (o el mismo `account.movement.rejected`); `REVERSED` → el `MovementResult` original (la reversa se consulta con su propio endpoint).
- Repetir con otra cuenta, tipo o monto → 409 `OPERATION_ID_REUSED` (en Kafka: `account.movement.rejected` con ese `reasonCode`).
- Se guardan también los **rechazos** de negocio, para que un reintento de la saga reciba la misma respuesta.

### 2.4 Atomicidad del movimiento

Aplicar un movimiento escribe **dos documentos** (`accounts` y `account_operations`). Para que no queden a medias:

- Ambas escrituras van en **una transacción de MongoDB** (`ReactiveMongoTransactionManager`). Exige que Mongo corra como *replica set* (en el `docker-compose`, un solo nodo con `--replSet rs0`).
- El caso de uso usa el puerto `UnitOfWorkPort.inTransaction(Single<T>)`; su adaptador de Mongo abre la transacción. Ante `WriteConflict` (control de versión) o `TransientTransactionError` se **reintenta el caso completo** hasta 3 veces; si se agotan → 409 `CONCURRENT_MODIFICATION`.
- El rechazo de negocio solo escribe `account_operations` (no toca el saldo).

### 2.5 Read models (P3)

| Colección | `_id` | Campos | Índice | Se actualiza con |
|---|---|---|---|---|
| `customer_snapshots` | `customerId` | `type`, `profile`, `status`, `updatedAt` | — | `customer.*` (si `updatedAt` del evento ≥ guardado) |
| `credit_card_snapshots` | `cardId` | `customerId`, `status`, `updatedAt` | (`customerId`, `status`) | `credit.card.created/updated/closed` (si `updatedAt` ≥ guardado) |
| `overdue_customers` | `customerId` | `overdue`, `updatedAt` | — | `credit.overdue.detected/cleared` (si `occurredAt` ≥ `updatedAt`) |

`hasActiveCreditCard(customerId)` = existe un snapshot con `customerId` y `status = ACTIVE`. Consultas derivadas: `existsByCustomerIdAndStatus`, `findById`. En P1/P2 los adaptadores REST reemplazan estos:

| Puerto | Llamada en P1/P2 |
|---|---|
| `CustomerLookupPort.findById` | `GET customer-service /customers/{id}` (404 → `CUSTOMER_NOT_FOUND`) |
| `CreditCardLookupPort.hasActiveCreditCard` | `GET credit-service /credit-cards?customerId={id}&status=ACTIVE` (¿lista no vacía?) |
| `OverdueDebtPort.hasOverdueDebt` | No-op: siempre `false` |

## 3. Reglas de cálculo

### 3.1 Aplicar un movimiento (`Account.applyMovement`)

Entrada: `operationId`, `type`, `amount`, `date` (por defecto hoy en la zona `bank.zone`, propuesta `America/Lima`), `now`.

| Paso | Regla | Falla con |
|---|---|---|
| 1 | La cuenta está `ACTIVE` | `ACCOUNT_INACTIVE` |
| 2 | `amount > 0` y 2 decimales | `INVALID_AMOUNT` |
| 3 | `date` no es futura ni anterior a `balanceTracker.lastChangeDate` | `INVALID_DATE` |
| 4 | **Plazo fijo:** `date.dayOfMonth == movementDayOfMonth` | `NOT_ALLOWED_DAY` |
| 5 | Si `date` está en otro mes que `monthlyActivity.yearMonth`, se reinicia el contador (`movementCount = 0`, mes nuevo) | — |
| 6 | Si hay `monthlyMovementLimit`: `movementCount < límite` | `MONTHLY_LIMIT_EXCEEDED` |
| 7 | `movementNumber = movementCount + 1`. `fee = transactionFee` si `movementNumber > freeTransactionsLimit`; si no, `0.00` | — |
| 8 | Nuevo saldo: retiro `balance − amount − fee`; depósito `balance + amount − fee`. Si es negativo | `INSUFFICIENT_FUNDS` |
| 9 | Guarda `movementCount = movementNumber`, actualiza el `balanceTracker` con el saldo nuevo y `date` | — |

`MovementResult = { operationId, accountId, type, amount, fee, newBalance, movementNumber }`.

Notas: el plazo fijo tiene `monthlyMovementLimit = 1` y día fijo; el paso 4 va antes del 6 para que un día equivocado responda `NOT_ALLOWED_DAY`. Un movimiento rechazado no cuenta ni cambia el saldo.

### 3.2 Revertir (`Account.reverseMovement`)

| Paso | Regla | Falla con |
|---|---|---|
| 1 | Existe la operación en esa cuenta | 404 `OPERATION_NOT_FOUND` |
| 2 | Estado `REVERSED` → devuelve el resultado guardado (idempotente) | — |
| 3 | Estado `REJECTED` → nada que revertir | 422 `OPERATION_NOT_APPLIED` |
| 4 | Retiro: `balance + amount + fee`. Depósito: `balance − amount + fee`; si queda negativo | 422 `INSUFFICIENT_FUNDS` |
| 5 | Si `yearMonth` de la operación es el mes actual del contador: `movementCount − 1` (mínimo 0). Si no, no se toca | — |
| 6 | El `balanceTracker` registra el cambio con la fecha de hoy. Marca la operación `REVERSED` y guarda `reversedBalance` | — |

La reversa **no exige cuenta `ACTIVE`** (ficha, sección 12).

### 3.3 Promedio diario (solo si `minimumDailyAverage` existe)

`balanceTracker` guarda: `accumulatedBalanceDays` (suma de saldo × días de los días **anteriores** a `lastChangeDate`, dentro del mes), `lastBalance` (saldo vigente desde `lastChangeDate`) y `lastChangeDate`.

- **Al cambiar el saldo** en la fecha `d` con saldo previo `b0` y último cambio `l`: `accumulatedBalanceDays += b0 × (d − l)` en días; `lastBalance = saldo nuevo`; `lastChangeDate = d`.
- **Cambio de mes:** si `d` cae en otro mes que `yearMonth`, se reinicia: `accumulatedBalanceDays = 0`, `yearMonth = mes de d`, `lastBalance = saldo vigente`, `lastChangeDate = día 1` del mes nuevo, y luego se aplica el cambio como arriba.
- **Cálculo a la fecha `x`** (se hace al consultar, sin guardar): `inicio = max(fecha de apertura, día 1 del mes)`; `días = x − inicio + 1`; `promedio = (accumulatedBalanceDays + lastBalance × (x − lastChangeDate + 1)) / días`, redondeado a 2 decimales `HALF_EVEN`. Si el mes consultado no es el de `yearMonth`, se calcula como si hubiera un reinicio en `x`.
- `meetsMinimum = promedio ≥ minimumDailyAverage`. Solo informa: no bloquea nada.

Ejemplo (VIP, mínimo 500): abre el 1 con 1000; el día 11 retira 600 (saldo 400). El día 21: acumulado = 1000 × 10 = 10 000; promedio = (10 000 + 400 × 11) / 21 = **685.71** → cumple.

### 3.4 Validaciones de apertura (`AccountOpeningPolicy`, cadena)

Orden (la primera que falla corta). Los datos llegan ya obtenidos.

| # | Eslabón | Regla ficha | Código |
|---|---|---|---|
| 1 | Cliente existe | 1 | 404 `CUSTOMER_NOT_FOUND` |
| 2 | Cliente `ACTIVE` | 1 | `CUSTOMER_INACTIVE` |
| 3 | Sin deuda vencida (P3) | 2 | `OVERDUE_DEBT` |
| 4 | Tipo permitido: `BUSINESS` solo `CHECKING` | 4 | `ACCOUNT_TYPE_NOT_ALLOWED` |
| 5 | `PERSONAL`: sin ahorro/corriente activo del mismo tipo | 3 | `SAVINGS_LIMIT_REACHED` / `CHECKING_LIMIT_REACHED` |
| 6 | Titulares y firmantes según el tipo de cliente | 5 | `PARTIES_NOT_ALLOWED` / `HOLDER_REQUIRED` |
| 7 | `movementDayOfMonth`: obligatorio en plazo fijo y prohibido en otros | 8 | `MOVEMENT_DAY_REQUIRED` / `MOVEMENT_DAY_NOT_ALLOWED` |
| 8 | Tarjeta de crédito `ACTIVE` si la condición la exige | 7 | `CREDIT_CARD_REQUIRED` |
| 9 | `openingAmount ≥ minimumOpeningAmount` | 6 | `INSUFFICIENT_OPENING_AMOUNT` |

El paso 5 se comprueba con `countActiveByCustomerAndType` para dar un error claro; el índice único parcial cubre la carrera entre dos altas simultáneas.

## 4. Consultas (sin `@Query`)

| Necesidad | Método (derivado) |
|---|---|
| Por id | `findById` |
| Listar por cliente | `findByCustomerId` (los demás filtros, con `Flowable.filter`) |
| Listar todo | `findAll` (los filtros `type`/`status` se aplican en memoria) |
| Contar cuentas activas de un cliente por tipo | `countByCustomerIdAndTypeAndStatus` |
| Catálogo | `findAll`, `findByAccountTypeAndProfile` |
| Operación | `findById` (el `_id` es el `operationId`) |

## 5. Mapeos

| Dominio | Documento Mongo | REST | Evento |
|---|---|---|---|
| `id` | `_id` | `id` | `accountId` |
| `accountNumber` | `accountNumber` | `accountNumber` | `maskedNumber` (`**** ` + últimos 4) |
| `customerId` | `customerId` | `customerId` | `customerId` |
| `customerType` / `customerProfile` | igual | igual | *(no viaja)* |
| `type` / `status` | igual | igual | `type` / `status` |
| `alias` | `alias` | `alias` | *(no viaja)* |
| `balance` | `balance` | `balance` | *(no viaja en `account`; sí `newBalance` en `account.movement.*`)* |
| `conditions` | `conditions` | `conditions` | *(no viaja)* |
| `holders` / `signers` | igual | igual (`document` + `fullName`) | *(no viajan)* |
| `monthlyActivity` | `monthlyActivity` | `monthlyActivity` | *(no viaja)* |
| `balanceTracker` | `balanceTracker` | *(se expone calculado en `BalanceView.dailyAverage`)* | *(no viaja)* |
| `version` | `version` | *(no viaja)* | *(no viaja)* |
| `updatedAt` | `updatedAt` | `updatedAt` | `updatedAt` |

Los mapeos se hacen con **MapStruct**; los VO se convierten a tipos simples en el borde. Los `Money` salen como número con 2 decimales.

## 6. Requests: validaciones y errores

Formato → **400** `VALIDATION_ERROR` (lo declara el `openapi.yaml`). Negocio → 404 / 409 / 422.

| Operación | Campo | Formato (400) | Negocio |
|---|---|---|---|
| `POST /accounts` | `customerId` | Obligatorio | 404 `CUSTOMER_NOT_FOUND` |
| | `type` | Obligatorio, enum | Ver 3.4 |
| | `alias` | Opcional, máx. 60 | — |
| | `openingAmount` | Opcional, ≥ 0, 2 decimales. Por defecto 0 | `INSUFFICIENT_OPENING_AMOUNT` |
| | `movementDayOfMonth` | Opcional, 1–28 | `MOVEMENT_DAY_REQUIRED` / `_NOT_ALLOWED` |
| | `holders`, `signers` | Opcionales, máx. 10 cada uno; `document` válido, `fullName` 1–150 | `PARTIES_NOT_ALLOWED`, `HOLDER_REQUIRED` |
| `GET /accounts` | `customerId`, `type`, `status` | Opcionales | 403 si un `CUSTOMER` pide otro `customerId` |
| `GET /accounts/{id}` y `/balance` | `id` | Obligatorio | 404 `ACCOUNT_NOT_FOUND` |
| `PUT /accounts/{id}` | `alias`, `holders`, `signers` | Igual que arriba | 404; 422 `ACCOUNT_INACTIVE`, `PARTIES_NOT_ALLOWED`, `HOLDER_REQUIRED`; 409 `CONCURRENT_MODIFICATION` |
| `DELETE /accounts/{id}` | — | — | 404; 422 `BALANCE_NOT_ZERO`. Repetir responde 204 |
| `GET /account-conditions` | `accountType`, `profile` | Opcionales, enum | — |
| `PUT /account-conditions/{id}` | `AccountConditions` completo | Ver esquema (montos ≥ 0, 2 decimales; límites ≥ 0/1) | 404 `CONDITIONS_NOT_FOUND` |
| `POST /accounts/{id}/movements` | `operationId` | Obligatorio, 8–64 | 409 `OPERATION_ID_REUSED` |
| | `type` | Obligatorio, enum | — |
| | `amount` | Obligatorio, ≥ 0.01, 2 decimales | `INVALID_AMOUNT` (solo llega por Kafka) |
| | `date` | Opcional, `yyyy-MM-dd` | `INVALID_DATE`, `NOT_ALLOWED_DAY` |
| `POST …/reversal` | `operationId` (ruta) | Obligatorio | 404 `OPERATION_NOT_FOUND`; 422 `OPERATION_NOT_APPLIED`, `INSUFFICIENT_FUNDS` |

**Códigos de error del servicio**

| Estado | Códigos |
|---|---|
| 400 | `VALIDATION_ERROR` |
| 401 / 403 | `UNAUTHORIZED`, `FORBIDDEN` |
| 404 | `ACCOUNT_NOT_FOUND`, `CUSTOMER_NOT_FOUND`, `CONDITIONS_NOT_FOUND`, `OPERATION_NOT_FOUND` |
| 409 | `CONCURRENT_MODIFICATION`, `OPERATION_ID_REUSED` |
| 422 apertura | `CUSTOMER_INACTIVE`, `OVERDUE_DEBT`, `ACCOUNT_TYPE_NOT_ALLOWED`, `SAVINGS_LIMIT_REACHED`, `CHECKING_LIMIT_REACHED`, `PARTIES_NOT_ALLOWED`, `HOLDER_REQUIRED`, `MOVEMENT_DAY_REQUIRED`, `MOVEMENT_DAY_NOT_ALLOWED`, `CREDIT_CARD_REQUIRED`, `INSUFFICIENT_OPENING_AMOUNT` |
| 422 cuenta y movimientos | `ACCOUNT_INACTIVE`, `BALANCE_NOT_ZERO`, `INVALID_AMOUNT`, `INVALID_DATE`, `NOT_ALLOWED_DAY`, `MONTHLY_LIMIT_EXCEEDED`, `INSUFFICIENT_FUNDS`, `OPERATION_NOT_APPLIED` |
| 503 | `SERVICE_UNAVAILABLE` (`customer-service` / `credit-service`, P1/P2) |

## 7. Caché (P3)

| Clave Redis | Valor | TTL | Se elimina cuando |
|---|---|---|---|
| `account-product:{id}` | JSON de `AccountProduct` | Configurable (propuesta 1 h) | Se edita esa entrada (`PUT /account-conditions/{id}`) |
| `account-products:all` | JSON de la lista completa | Igual | Se edita cualquier entrada |

Cache-aside en `findByAccountTypeAndProfile`/`findAll`. Si Redis falla, se lee de Mongo.

## 8. Eventos y comandos (P3)

### 8.1 Publica

| Tópico (clave) | Tipo | Cuándo | Payload (`kafka-contract.md`) |
|---|---|---|---|
| `account` (`accountId`) | `account.created` | Al abrir | 6.2, estado sin saldo |
| | `account.updated` | Al cambiar alias, titulares o firmantes | 6.2 |
| | `account.deleted` | Primera baja (repetir no publica) | 6.2 con `status = INACTIVE` |
| `account.movement` (`accountId`) | `account.movement.applied` | Movimiento aplicado | 6.4 = `MovementResult` |
| | `account.movement.rejected` | Movimiento rechazado | `operationId`, `accountId`, `reasonCode`, `message?` |
| | `account.movement.reversed` | Reversa aplicada | `operationId`, `accountId`, `newBalance` = `ReversalResult` |
| | `account.movement.reversal.rejected` | Reversa rechazada | `operationId`, `accountId`, `reasonCode` |

`correlationId` del sobre = `operationId`. El `reasonCode` es **el mismo código** que devuelve el REST (`INSUFFICIENT_FUNDS`, `ACCOUNT_INACTIVE`, `MONTHLY_LIMIT_EXCEEDED`, `NOT_ALLOWED_DAY`, `INVALID_AMOUNT`, `INVALID_DATE`, `OPERATION_ID_REUSED`, `OPERATION_NOT_FOUND`, `OPERATION_NOT_APPLIED`, `ACCOUNT_NOT_FOUND`).

### 8.2 Consume

| Tópico | Tipo | Efecto |
|---|---|---|
| `account.command` | `transaction.movement.requested` | `ApplyMovementUseCase` → publica `applied` o `rejected` |
| | `transaction.movement.reversal.requested` | `ReverseMovementUseCase` → publica `reversed` o `reversal.rejected` |
| `customer` | `customer.created/updated/deleted` | Upsert en `customer_snapshots` |
| `credit-card` | `credit.card.created/updated/closed` | Upsert en `credit_card_snapshots` |
| `credit.overdue` | `credit.overdue.detected/cleared` | Upsert en `overdue_customers` |

Un comando que falla por un error **técnico** (Mongo caído, conflicto agotado) no publica respuesta: se reintenta y, si se agota, va a `account.command.DLT`. Un comando con datos inválidos (por ejemplo cuenta inexistente) sí responde con `rejected` y el `reasonCode`. Un comando ya conocido (mismo `operationId`) **reemite** su respuesta guardada.

## 9. Datos de ejemplo para la demo

Con los clientes de `customer-service` (A, B, C, V, E, D). Números de cuenta ilustrativos.

| Alias | Cliente | Tipo | Condición | Apertura | Notas |
|---|---|---|---|---|---|
| A1 | A (Ana) | `SAVINGS` | `SAVINGS_STANDARD` | 1000.00 | Cuenta principal de la demo. Un segundo ahorro → `SAVINGS_LIMIT_REACHED` |
| A2 | A | `CHECKING` | `CHECKING_STANDARD` | 500.00 | Corriente (mantenimiento 15) |
| A3 | A | `FIXED_TERM` | `FIXED_TERM_STANDARD` | 500.00 | `movementDayOfMonth` = **hoy** (o 15 con `Clock` fijo) |
| B1 | B (Beto) | `CHECKING` | `CHECKING_STANDARD` | 200.00 | Recibe transferencias |
| B2 | B | `FIXED_TERM` | `FIXED_TERM_STANDARD` | 100.00 | `movementDayOfMonth` **distinto** de hoy: la transferencia de A se rechaza y se compensa (paso 14) |
| V1 | V (Víctor, VIP) | `SAVINGS` | `SAVINGS_VIP` | 1000.00 | Solo se abre con tarjeta de crédito `ACTIVE`. Promedio mínimo 500 |
| E1 | E (Bodega, PYME) | `CHECKING` | `CHECKING_PYME` | 0.00 | Solo con tarjeta empresarial `ACTIVE`. Titular: Rosa Salas Peña (DNI 40123456); firmante: Luis Vega Ramos (DNI 40987654) |

Escenarios que cubre:
- **Comisión:** 6 movimientos sobre A1 → el sexto trae `fee = 2.00` (`movementNumber = 6`).
- **Tope de ahorro:** 10 movimientos en A1 → el undécimo → `MONTHLY_LIMIT_EXCEEDED`.
- **Plazo fijo:** A3 el día correcto: un movimiento OK y el segundo `MONTHLY_LIMIT_EXCEEDED`; B2 otro día → `NOT_ALLOWED_DAY`.
- **Idempotencia:** repetir el `operationId` del retiro → 200 con el mismo `newBalance` (sin descuento doble).
- **Empresa:** ahorro → `ACCOUNT_TYPE_NOT_ALLOWED`; corriente sin titular → `HOLDER_REQUIRED`.

## 10. Decisiones y pendientes

**Decidido (nuevo en este contrato)**
- Atomicidad de `accounts` + `account_operations` con **transacción de Mongo** (replica set de un nodo). Puerto nuevo `UnitOfWorkPort`. Aplica también al resto de servicios que escriban dos colecciones.
- `BigDecimal` → `Decimal128` en Mongo (configuración global del proyecto).
- El catálogo usa id natural `<TIPO>_<PERFIL>`.
- Dos índices únicos parciales (uno por tipo) en vez de uno con `$in`; dos códigos `SAVINGS_LIMIT_REACHED` / `CHECKING_LIMIT_REACHED`.
- Se guardan los rechazos en `account_operations`; repetir devuelve lo mismo. Mismo `operationId` con otros datos → 409 `OPERATION_ID_REUSED`.
- `date` del movimiento: opcional, ni futura ni anterior al último cambio (`INVALID_DATE`); zona horaria `bank.zone` (propuesta `America/Lima`) para "hoy".
- Movimientos con comisión también en depósitos; el saldo nunca queda negativo (un depósito menor que la comisión se rechaza con `INSUFFICIENT_FUNDS`).
- Reversa de depósito con saldo insuficiente → 422 `INSUFFICIENT_FUNDS` (cierra ese pendiente de la ficha); en la práctica las sagas solo revierten retiros.
- Listado de cuentas sin paginación.
- `customerProfile` de la cuenta es una foto al abrir y no cambia.

**Pendiente**
- Nada bloqueante para empezar a programar este servicio.
- Opcional: cobro mensual de la comisión de mantenimiento (job).
- `credit-service` debe exponer `GET /credit-cards?customerId=&status=` (ya está en su ficha; se confirma en su contrato).

# `debit-service` — Modelo de datos

> Complementa la ficha (`services/debit-service.md`), el flujo del pago (`flows/02-debit-payment.md`) y el contrato REST (`openapi.yaml`, en esta carpeta) con los **tipos exactos**, los documentos de MongoDB, los índices, los algoritmos del pago y de la recuperación, y el tratamiento de cada evento. Si algo difiere, el `openapi.yaml` manda para la API y `contracts/events/kafka-contract.md` para los eventos.
>
> Servicio de P3: **sin REST hacia otros servicios**. Valida con copias locales y pide el retiro a `transaction-service` por Kafka.

## 1. Entidades de dominio

### 1.1 Aggregate `DebitCard`

| Campo | Tipo (dominio) | Req. | Regla | Mutable |
|---|---|---|---|---|
| `id` | `DebitCardId` (`String`, UUID v4) | ✓ | Lo genera el servicio | No |
| `customerId` | `CustomerId` | ✓ | Dueño. No cambia | No |
| `cardNumber` | `CardNumber` | ✓ | 16 dígitos ficticios, únicos. Solo sale enmascarado | No |
| `expiryDate` | `ExpiryDate` (`YearMonth`) | ✓ | Mes de emisión + 5 años | No |
| `linkedAccounts` | `LinkedAccounts` | ✓ | 1 a 10 cuentas; la principal está entre ellas | Sí |
| `status` | `DebitCardStatus` | ✓ | `ACTIVE` al emitir; `CLOSED` tras el cierre | Sí (`close`) |
| `version` | `long` | ✓ | Control optimista (`@Version`). **Interno**: no viaja por REST | Automático |
| `createdAt` / `updatedAt` | `Instant` | ✓ | Reloj inyectado (`Clock`) | `updatedAt` sí |

Comportamiento: `issue(...)`, `replaceAccounts(accounts, main)`, `linkAccount(accountId)`, `unlinkAccount(accountId)`, `changeMainAccount(accountId)`, `onAccountClosed(accountId)`, `close()`, `assertUsable(today)`, `isExpired(today)`.

- **Vencida** no es un estado: `isExpired(today)` es verdadero si el mes de `today` (en `bank.zone`) es **posterior** a `expiryDate`. La tarjeta sirve hasta el último día del mes de vencimiento.
- `assertUsable(today)`: `status = ACTIVE` y no vencida; si no, 422 `CARD_NOT_USABLE`. Todo cambio (asociar, desasociar, principal, reemplazar) y todo pago la exigen. **Cerrar** no la exige (se puede cerrar una tarjeta vencida; cerrar una ya cerrada es idempotente).

### 1.2 Aggregate `DebitPayment`

| Campo | Tipo (dominio) | Req. | Regla | Mutable |
|---|---|---|---|---|
| `id` | `DebitPaymentId` (`String`, UUID v4) | ✓ | Lo genera el servicio | No |
| `operationId` | `OperationId` | ✓ | El del cliente, **tal cual** (8 a 56 caracteres). Único | No |
| `cardId` | `DebitCardId` | ✓ | | No |
| `customerId` | `CustomerId` | ✓ | El de la tarjeta | No |
| `accountId` | `AccountId` | ✓ | Cuenta de cargo, ya resuelta (la indicada o la principal **en ese momento**) | No |
| `requestedAccountId` | `AccountId?` | – | Lo que envió el cliente (puede faltar). Solo sirve para comparar repeticiones | No |
| `amount` | `Money` | ✓ | > 0, 2 decimales | No |
| `description` | `String?` | – | Máximo 200 | No |
| `status` | `DebitPaymentStatus` | ✓ | `PENDING` al crear | Sí (`complete`, `fail`) |
| `failureReason` | `FailureReason?` | – | Solo si `FAILED` | Sí |
| `transactionId` | `String?` | – | Solo si `COMPLETED` | Sí |
| `fee` | `Money?` | – | Comisión de la cuenta. Solo si `COMPLETED` y fue mayor que 0 | Sí |
| `resultingBalance` | `Money?` | – | Solo si `COMPLETED` | Sí |
| `requestedAt` | `Instant` | ✓ | Reloj inyectado | No |
| `completedAt` | `Instant?` | – | El `occurredAt` del resultado (para que coincida con el movimiento del historial) | Sí |
| `recoveryAttempts` | `int` | ✓ | Reenvíos hechos por la recuperación. Empieza en 0 | Sí |
| `lastResentAt` | `Instant?` | – | Último reenvío (de la recuperación o de una repetición del cliente) | Sí |
| `version` | `long` | ✓ | Control optimista. Interno | Automático |
| `updatedAt` | `Instant` | ✓ | | Sí |

Comportamiento: `request(...)`, `complete(transactionId, fee, resultingBalance, occurredAt)`, `fail(reason, occurredAt)`. Solo `PENDING` admite `complete` y `fail`; sobre un pago ya terminal se **ignoran** (resultados repetidos o tardíos). No hay más transiciones.

### 1.3 Value objects

| VO | Campos | Validación |
|---|---|---|
| `DebitCardId`, `DebitPaymentId`, `CustomerId`, `AccountId` | `value: String` | No vacío |
| `OperationId` | `value: String` | 8 a 56 caracteres |
| `CardNumber` | `value: String` | 16 dígitos: prefijo `500000` + 9 dígitos aleatorios + dígito de control **Luhn**. `masked()` → `**** NNNN`. No se registra en logs |
| `ExpiryDate` | `yearMonth: YearMonth` | `issue` = mes de emisión + `debit.card.validity-years` |
| `LinkedAccounts` | `accountIds: List<AccountId>`, `mainAccountId` | 1 a 10, sin duplicados, en orden de asociación, la principal está incluida |
| `Money` | `amount: BigDecimal` (2 decimales) | Mayor que 0. Solo `PEN` |
| `FailureReason` | `code`, `message?` | Código no vacío |
| `AccountSnapshot` | `accountId`, `customerId`, `type`, `status`, `updatedAt` | Dato de lectura (2.3) |
| `CustomerSnapshot` | `customerId`, `status`, `updatedAt` | Dato de lectura (2.4). Ya no guarda el `type` que traía la ficha: no se usa |

### 1.4 Enums

| Enum | Valores |
|---|---|
| `DebitCardStatus` | `ACTIVE`, `CLOSED` |
| `DebitPaymentStatus` | `PENDING`, `COMPLETED`, `FAILED` |
| `AccountType` | `SAVINGS`, `CHECKING`, `FIXED_TERM` (solo en el snapshot) |
| `AccountStatus` | `ACTIVE`, `INACTIVE` (solo en el snapshot) |
| `CustomerStatus` | `ACTIVE`, `INACTIVE` (solo en el snapshot) |

## 2. Documentos MongoDB

Base propia del servicio. Dinero como `Decimal128`, `YearMonth` como texto `yyyy-MM`, `Instant` como `date` UTC (convenciones globales del README). Índices al arrancar (`auto-index-creation=true`).

### 2.1 `debit_cards` — `DebitCardDocument`

Clases de persistencia: `DebitCardDocument` (`@Document("debit_cards")`) con `LinkedAccountsData` embebido.

| Campo Mongo | Tipo Mongo | Tipo Java | Req. | Notas |
|---|---|---|---|---|
| `_id` | string | `String` | ✓ | UUID v4 |
| `customerId` | string | `String` | ✓ | |
| `cardNumber` | string | `String` | ✓ | 16 dígitos. Nunca sale del servicio en claro |
| `expiryDate` | string | `YearMonth` | ✓ | `yyyy-MM` |
| `linkedAccounts.accountIds` | array de string | `List<String>` | ✓ | Orden de asociación |
| `linkedAccounts.mainAccountId` | string | `String` | ✓ | |
| `status` | string | `String` (enum) | ✓ | |
| `version` | long | `Long` | ✓ | `@Version` |
| `createdAt` / `updatedAt` | date | `Instant` | ✓ | |

| Índice | Campos | Opciones | Para qué |
|---|---|---|---|
| `uk_card_number` | `cardNumber` (1) | **Único** | Ante colisión al emitir se genera otro número (máx. 3 intentos) |
| `ix_card_customer_status` | `customerId` (1), `status` (1) | — | Listado por cliente y estado |
| `ix_card_accounts` | `linkedAccounts.accountIds` (1) | Multiclave | Encontrar las tarjetas de una cuenta cuando llega el evento de cierre |

**Consultas (sin `@Query`, sin consultas dinámicas)**

| Necesidad | Método (derivado) |
|---|---|
| Por id | `findById` |
| Listado | Según los filtros: `findAll`, `findByCustomerId`, `findByStatus` o `findByCustomerIdAndStatus` (cuatro métodos, sin combinar dinámicamente). Orden en memoria: `createdAt` descendente y `id` |
| Tarjetas de una cuenta (evento de cierre) | `findByLinkedAccounts_AccountIdsContaining(accountId)`; se filtran en memoria las `ACTIVE` |

### 2.2 `debit_payments` — `DebitPaymentDocument`

| Campo Mongo | Tipo Mongo | Tipo Java | Req. | Notas |
|---|---|---|---|---|
| `_id` | string | `String` | ✓ | `paymentId` (UUID v4) |
| `operationId` | string | `String` | ✓ | Único |
| `cardId` | string | `String` | ✓ | |
| `customerId` | string | `String` | ✓ | |
| `accountId` | string | `String` | ✓ | |
| `requestedAccountId` | string | `String` | – | Se omite si el cliente no la indicó |
| `amount` | decimal128 | `BigDecimal` | ✓ | |
| `description` | string | `String` | – | |
| `status` | string | `String` (enum) | ✓ | |
| `failureReason.code` / `failureReason.message` | string | `String` | – | Solo `FAILED` |
| `transactionId` | string | `String` | – | Solo `COMPLETED` |
| `fee` | decimal128 | `BigDecimal` | – | Solo si hubo comisión |
| `resultingBalance` | decimal128 | `BigDecimal` | – | Solo `COMPLETED` |
| `requestedAt` | date | `Instant` | ✓ | |
| `completedAt` | date | `Instant` | – | |
| `recoveryAttempts` | int | `int` | ✓ | |
| `lastResentAt` | date | `Instant` | – | |
| `version` | long | `Long` | ✓ | |
| `updatedAt` | date | `Instant` | ✓ | |

| Índice | Campos | Opciones | Para qué |
|---|---|---|---|
| `uk_dp_operation_id` | `operationId` (1) | **Único** | Idempotencia y carreras de dos peticiones iguales (`DuplicateKeyException` → se trata como repetición) |
| `ix_dp_card_requested` | `cardId` (1), `requestedAt` (-1) | — | Historial de la tarjeta |
| `ix_dp_card_status_requested` | `cardId` (1), `status` (1), `requestedAt` (-1) | — | Historial filtrado por estado y "¿tiene pendientes?" |
| `ix_dp_status_requested` | `status` (1), `requestedAt` (1) | — | Recuperación |

**Consultas**

| Necesidad | Método (derivado) |
|---|---|
| Por id | `findById` |
| Idempotencia y correlación del resultado | `findByOperationId` |
| Historial sin `status` | `findByCardIdAndRequestedAtBetween(cardId, range, pageable)` + `countByCardIdAndRequestedAtBetween` |
| Historial con `status` | `findByCardIdAndStatusAndRequestedAtBetween(cardId, status, range, pageable)` + su `count` |
| Filtro `operationId` | `findByOperationId` y se comprueba que sea de la tarjeta (0 o 1 pago) |
| ¿Tiene pendientes? (cierre) | `existsByCardIdAndStatus(cardId, PENDING)` |
| Recuperación | `findByStatusAndRequestedAtBefore(PENDING, corte)`; se descartan en memoria los que tienen `lastResentAt` posterior al corte o `recoveryAttempts ≥ max-attempts` |

- **Rango de fechas:** `from`/`to` son fechas del banco (`bank.zone`), inclusivas. Se convierten a `Range.rightOpen([from 00:00, to+1 00:00))` en `Instant`. Sin `from`: `Instant.EPOCH`; sin `to`: un instante lejano (`9999-12-31T00:00:00Z`). Así solo hacen falta **dos** métodos (con y sin `status`).
- **A verificar al implementar:** que `Between` con `Range<Instant>` respete los extremos abierto y cerrado en la versión de Spring Data MongoDB elegida (si no, restar 1 ms al inicio). Alternativa: leer por `cardId` y filtrar el rango en memoria.
- **Orden:** `Sort` fijo `requestedAt` descendente, empate por `_id` descendente.

### 2.3 `account_snapshots` — `AccountSnapshotDocument`

Copia local de las cuentas, alimentada por el tópico `account`.

| Campo Mongo | Tipo Mongo | Req. | Notas |
|---|---|---|---|
| `_id` | string | ✓ | `accountId` |
| `customerId` | string | ✓ | Titular principal (el evento **no** trae cotitulares) |
| `type` | string | ✓ | `SAVINGS`, `CHECKING`, `FIXED_TERM` |
| `status` | string | ✓ | `ACTIVE` o `INACTIVE` |
| `updatedAt` | date | ✓ | Del evento |

Sin índices adicionales. El `maskedNumber` del evento no se guarda (no se usa).

### 2.4 `customer_snapshots` — `CustomerSnapshotDocument`

| Campo Mongo | Tipo Mongo | Req. | Notas |
|---|---|---|---|
| `_id` | string | ✓ | `customerId` |
| `status` | string | ✓ | `ACTIVE` o `INACTIVE` |
| `updatedAt` | date | ✓ | Del evento |

### 2.5 `overdue_customers` — `OverdueCustomerDocument`

| Campo Mongo | Tipo Mongo | Req. | Notas |
|---|---|---|---|
| `_id` | string | ✓ | `customerId` |
| `overdue` | bool | ✓ | `true` con `credit.overdue.detected`, `false` con `credit.overdue.cleared` |
| `updatedAt` | date | ✓ | El `occurredAt` del evento |

`OverdueDebtPort.hasOverdueDebt(customerId)`: `false` si no hay documento.

## 3. Reglas y algoritmos

### 3.1 Emisión (`IssueDebitCardUseCase` + `IssuancePolicy`)

Cadena de validaciones (**Chain of Responsibility**; la primera que falla responde). `IssuancePolicy` es puro: recibe los snapshots ya leídos.

| # | Eslabón | Error |
|---|---|---|
| 1 | Formato y alcance: un `CUSTOMER` solo emite para su `customerId` | 400 / 403 |
| 2 | `customer_snapshots[customerId]` existe | 422 `CUSTOMER_NOT_FOUND` |
| 3 | El cliente está `ACTIVE` | 422 `CUSTOMER_INACTIVE` |
| 4 | Sin deuda vencida (`overdue_customers`) | 422 `OVERDUE_DEBT` |
| 5 | Cada cuenta existe en `account_snapshots` | 422 `ACCOUNT_NOT_FOUND` |
| 6 | Cada cuenta es elegible: `customerId` igual al de la tarjeta, `ACTIVE`, `SAVINGS` o `CHECKING` | 422 `ACCOUNT_NOT_ELIGIBLE` |
| 7 | La principal indicada está entre las cuentas | 422 `MAIN_ACCOUNT_REQUIRED` |

Después: principal = la indicada o la **primera** cuenta de la lista; `CardNumber` nuevo (hasta 3 intentos ante colisión del índice único); `ExpiryDate`; se guarda; se publica `debit.card.created`.

`LinkedAccountsPolicy` aplica los eslabones 5 y 6 a cada cuenta que se agrega en `PUT /debit-cards/{id}` y en `POST /debit-cards/{id}/accounts`.

### 3.2 Cuentas de una tarjeta

| Operación | Regla | Error |
|---|---|---|
| Cualquier cambio | `assertUsable(today)` | 422 `CARD_NOT_USABLE` |
| `PUT /debit-cards/{id}` | Reemplaza todo el conjunto. Cada cuenta elegible. Principal indicada o la primera. Si el resultado es igual al actual, 200 sin evento | 422 `ACCOUNT_NOT_FOUND`, `ACCOUNT_NOT_ELIGIBLE`, `MAIN_ACCOUNT_REQUIRED` |
| `POST .../accounts` | Elegible y no asociada. No cambia la principal. Máximo 10 cuentas | 422 `ACCOUNT_ALREADY_LINKED`, `MAX_ACCOUNTS_REACHED` (ya son 10), `ACCOUNT_NOT_FOUND`, `ACCOUNT_NOT_ELIGIBLE` |
| `DELETE .../accounts/{accountId}` | Debe estar asociada (404 `ACCOUNT_NOT_LINKED`). Si es la única → `LAST_ACCOUNT`; si es la principal y hay otras → `MAIN_ACCOUNT_REQUIRED` | 404 / 422 |
| `PUT .../main-account` | Debe estar entre las asociadas. Si ya es la principal: 200 sin cambios ni evento | 422 `MAIN_ACCOUNT_REQUIRED` |

Cada cambio efectivo publica `debit.card.updated`. Un conflicto de versión se reintenta hasta 3 veces; si se agotan → 409 `CONCURRENT_MODIFICATION`.

### 3.3 Cierre

- **Manual** (`DELETE /debit-cards/{id}`): si ya está `CLOSED` → 204 sin publicar. Si tiene pagos `PENDING` → 422 `CARD_HAS_PENDING_PAYMENTS`. Si no, `CLOSED` y `debit.card.closed`.
- **Automático** (`HandleAccountClosedUseCase`): ver 4.2. No mira los pagos pendientes (esos pagos terminan igual: el retiro ya se pidió).

### 3.4 Pago (`PayWithDebitCardUseCase`)

1. **Tarjeta:** `findById` (404 `DEBIT_CARD_NOT_FOUND`); un `CUSTOMER` debe ser el dueño (403).
2. **Idempotencia:** `findByOperationId`.
   - Si existe y `cardId`, `amount` (con `compareTo`) y `requestedAccountId` **no** coinciden con lo pedido → 409 `OPERATION_ID_REUSED`. La `description` no se compara.
   - Si coincide → responde según el estado (3.4.1). Es lo primero que se evalúa después de la tarjeta: una repetición se responde aunque la tarjeta se haya cerrado o vencido después.
3. **Validaciones** (no crean nada si fallan): `assertUsable(today)` → 422 `CARD_NOT_USABLE`; cliente en `customer_snapshots` (422 `CUSTOMER_NOT_FOUND` si falta, 422 `CUSTOMER_INACTIVE` si está inactivo); cuenta de cargo = `accountId` indicado o la principal, y **debe estar asociada** a la tarjeta → 422 `ACCOUNT_NOT_ELIGIBLE`. La deuda vencida **no** se mira.
4. **Espera:** se registra la espera en memoria (`PaymentResultAwaiterPort.register(paymentId)`) **antes** de publicar, para que un resultado rapidísimo no se pierda.
5. **Guardar:** `DebitPayment` `PENDING` (`requestedAt` = ahora, `recoveryAttempts` = 0). Si el índice único de `operationId` salta (dos peticiones iguales a la vez) → se vuelve al paso 2.
6. **Pedir el retiro:** se publica `debit.payment.requested` (tópico `transaction.command`, clave `accountId`). Si Kafka falla, el pago queda `PENDING` y **se responde 202**: la recuperación lo reenviará.
7. **Esperar** hasta `payment.await-timeout` (1,5 s) el estado terminal. Con el resultado, se **relee** el pago de Mongo (el único que lo escribe es el consumidor, así que no hay carreras) y se responde: `COMPLETED` → **201**; `FAILED` → **422** con el `code` del motivo y el `paymentId`. Sin resultado → **202** con el pago `PENDING`. En todos los casos se retira la espera.

#### 3.4.1 Repeticiones (mismo `operationId`, mismos datos)

| Estado del pago | Respuesta |
|---|---|
| `COMPLETED` | **200** con el pago (nunca 201: 201 es solo para quien lo creó) |
| `FAILED` | **422** con el mismo `code` del motivo y el `paymentId` |
| `PENDING` | Se registra la espera, se relee el pago (pudo terminar entre tanto), y si sigue `PENDING` se **vuelve a publicar** `debit.payment.requested` (idempotente aguas abajo) y se espera otra vez hasta 1,5 s. Terminó → 200 o 422 según el caso; no terminó → **202** |

Solo la recuperación incrementa `recoveryAttempts`; el reenvío por una repetición del cliente solo actualiza `lastResentAt`.

### 3.5 Resultado del retiro (`HandleMovementResultUseCase`)

Consume el tópico `transaction`. Filtro: solo `eventType` `transaction.registered` o `transaction.failed` con `type = DEBIT_PAYMENT`. Todo lo demás (comisiones `FEE`, tipos `YANKI_*`, reversas) se descarta sin log de error.

1. `findByOperationId(payload.operationId)`. Si no existe → se descarta y se registra en el log (un pago de otro servicio o de una base reconstruida).
2. Si el pago no está `PENDING` → se ignora (resultado repetido o tardío).
3. `transaction.registered` → `complete(transactionId, fee, resultingBalance, occurredAt)`. El `fee` se guarda solo si viene y es mayor que 0. Se guarda con control de versión, y **después** se publica `debit.payment.completed` (`occurredAt` = el del resultado).
4. `transaction.failed` → `fail(FailureReason(reasonCode, message), occurredAt)`; se guarda y se publica `debit.payment.failed`.
5. Por último se avisa a la espera en memoria (`awaiter.complete(paymentId)`). Si nadie espera, no pasa nada.
6. Se confirma el offset **después** de guardar. Si la publicación de `debit.payment.*` falla **después** de guardar, se registra el error y no se reintenta el mensaje: sin *outbox*, ese evento se pierde (pendiente común de todo el sistema). El estado del pago sí queda correcto.

Motivos de rechazo que llegan de la cuenta: `INSUFFICIENT_FUNDS`, `MONTHLY_LIMIT_EXCEEDED`, `ACCOUNT_INACTIVE`, `ACCOUNT_NOT_FOUND`, `INVALID_AMOUNT`, `INVALID_DATE`, `OPERATION_ID_REUSED` (el `operationId` ya lo usó otro endpoint de dinero) y los demás códigos de negocio de la cuenta. Se guardan y se devuelven **tal cual**.

### 3.6 Recuperación (`RecoverPendingPaymentsUseCase`)

`@Scheduled` (`debit.recovery.cron`) y `POST /debit-payment-recovery-runs`. Con `corte = ahora − olderThanMinutes` (por defecto `debit.recovery.pending-minutes`, 2):

1. Busca pagos `PENDING` con `requestedAt < corte`.
2. Descarta los que se reenviaron hace menos que el corte (`lastResentAt ≥ corte`) y cuenta como `exhausted` los que ya llegaron a `debit.recovery.max-attempts` reenvíos (10); esos quedan `PENDING` para revisión manual y no se vuelven a reenviar.
3. Para el resto vuelve a publicar `debit.payment.requested` con el **mismo** `operationId`, suma 1 a `recoveryAttempts` y guarda `lastResentAt`.

Nunca cambia el estado del pago ni crea uno nuevo: el estado lo cambia el consumidor cuando `transaction-service` reemite el resultado. Con varias instancias, dos reenvíos iguales son inofensivos (idempotencia aguas abajo). El resultado se devuelve como `RecoveryResult` (`pendingFound`, `resent`, `exhausted`).

## 4. Eventos

### 4.1 Publica

| Tópico | `eventType` | Clave | Cuándo | Payload |
|---|---|---|---|---|
| `debit-card` | `debit.card.created` / `updated` / `closed` | `cardId` | Emisión; cambio de cuentas o principal; cierre (manual o automático) | Estado completo (contrato de Kafka, 6.12): `cardId`, `customerId`, `maskedNumber`, `accountIds`, `mainAccountId`, `expiryDate`, `status`, `updatedAt` |
| `transaction.command` | `debit.payment.requested` | `accountId` | Al crear el pago y en cada reenvío | 6.9: `operationId`, `paymentId`, `cardId`, `customerId`, `accountId`, `amount`, `description?`, `requestedAt` |
| `debit.payment` | `debit.payment.completed` | `cardId` | Pago `COMPLETED` | 6.13: `paymentId`, `cardId`, `customerId`, `accountId`, `amount`, `fee?`, `description?`, `resultingBalance`, `occurredAt` |
| `debit.payment` | `debit.payment.failed` | `cardId` | Pago `FAILED` | `paymentId`, `cardId`, `reasonCode` |

El `correlationId` del sobre es el `operationId` en `debit.payment.requested`; en el resto, el `eventId`. Los eventos de tarjeta llevan siempre la cuenta principal (la necesita `yanki-service`). Un `debit.card.updated` que no cambia nada no se publica.

### 4.2 Consume (grupo `debit-service`)

| Tópico | Eventos | Tratamiento |
|---|---|---|
| `customer` | `customer.created/updated/deleted` | Upsert de `customer_snapshots {status, updatedAt}` si el evento es igual o más reciente. No modifica tarjetas |
| `account` | `account.created/updated/deleted` | Upsert de `account_snapshots {customerId, type, status, updatedAt}` con la misma regla de fecha. **Si se aplicó y `status = INACTIVE`:** por cada tarjeta `ACTIVE` que la tenga asociada → `onAccountClosed(accountId)` (abajo) |
| `credit.overdue` | `credit.overdue.detected/cleared` | Upsert de `overdue_customers`; se aplica solo si `occurredAt ≥ updatedAt` |
| `transaction` | `transaction.registered/failed` (solo `DEBIT_PAYMENT`) | 3.5 |

**`onAccountClosed(accountId)`:** quita la cuenta de `accountIds`. Si era la principal, la nueva principal es la **primera** de las que quedan. Si no queda ninguna, la tarjeta pasa a `CLOSED`. Se guarda con control de versión y se publica `debit.card.updated` o `debit.card.closed`. Si dos cuentas de la misma tarjeta se cierran a la vez y hay conflicto de versión, el consumidor relee y repite; con 3 reintentos fallidos el mensaje va a `account.DLT`.

Reglas de consumo comunes (reintentos 3 veces con 1, 2 y 4 s, `.DLT`, confirmación tras guardar, tipo desconocido ignorado): contrato de Kafka, sección 7.

**Reconstrucción:** con la base vacía, `customer`, `account` y `credit.overdue` (compactados) se leen desde el inicio y recomponen las copias. Las tarjetas y los pagos **no** se pueden reconstruir desde Kafka (son la fuente de verdad de este servicio). Del tópico `transaction` (no compactado) solo queda lo de los últimos 7 días, y sin pagos guardados sus mensajes se descartan.

### 4.3 La espera en memoria (`InMemoryPaymentResultAwaiter`)

Un mapa `paymentId → AsyncSubject` (`register` crea o reutiliza; `complete` emite y lo retira; la espera con `timeout` lo retira si vence). Como el resultado siempre lo guarda el consumidor **antes** de avisar y la petición **relee** el pago, un aviso que llega antes de esperar (por eso se registra primero) o que no llega jamás (otra instancia, 202) no daña nada. **Con varias instancias**, el resultado puede caer en otra y el cliente verá 202 y consultará el pago (aceptado para el demo).

## 5. Mapeos

| Dominio | Documento Mongo | REST (`DebitCard`) | Evento `debit-card` |
|---|---|---|---|
| `id` | `_id` | `id` | `cardId` |
| `customerId` | `customerId` | `customerId` | `customerId` |
| `cardNumber` | `cardNumber` | *(solo `maskedNumber`)* | `maskedNumber` |
| `expiryDate` | `expiryDate` (`yyyy-MM`) | `expiryDate` | `expiryDate` |
| *(calculado)* | — | `expired` | — |
| `linkedAccounts.accountIds` | `linkedAccounts.accountIds` | `accountIds` | `accountIds` |
| `linkedAccounts.mainAccountId` | `linkedAccounts.mainAccountId` | `mainAccountId` | `mainAccountId` |
| `status` | `status` | `status` | `status` |
| `version` | `version` | *(interno)* | — |
| `createdAt` / `updatedAt` | `createdAt` / `updatedAt` | `createdAt` / `updatedAt` | `updatedAt` |

| Dominio (`DebitPayment`) | Documento Mongo | REST (`DebitPayment`) | Evento `debit.payment.completed` |
|---|---|---|---|
| `id` | `_id` | `id` | `paymentId` |
| `operationId` | `operationId` | `operationId` | *(en el sobre: `correlationId` del comando)* |
| `cardId` / `customerId` / `accountId` | igual | igual | igual |
| `requestedAccountId` | `requestedAccountId` | *(no viaja)* | — |
| `amount` / `description` | igual | igual | igual |
| `status` | `status` | `status` | *(implícito en el tipo de evento)* |
| `failureReason` | `failureReason.{code,message}` | `failureReason` | `reasonCode` (solo `failed`) |
| `transactionId` | `transactionId` | `transactionId` | *(no viaja)* |
| `fee` / `resultingBalance` | igual | igual | igual |
| `requestedAt` | `requestedAt` | `requestedAt` | — |
| `completedAt` | `completedAt` | `completedAt` | `occurredAt` |
| `recoveryAttempts`, `lastResentAt`, `version`, `updatedAt` | igual | *(internos)* | — |

Los mapeos se hacen con **MapStruct** (REST ↔ dominio, dominio ↔ documento, dominio ↔ evento). `maskedNumber` y `expired` los calcula el mapper (con el `Clock`). El número completo no tiene campo en ningún DTO ni evento.

## 6. Requests: validaciones y errores

Las validaciones de **formato** las declara el `openapi.yaml` (400 `VALIDATION_ERROR`). Las de **negocio** viven en el dominio (422, 404, 409).

| Operación | Campo | Formato (400) | Negocio |
|---|---|---|---|
| `POST /debit-cards` | `customerId` | Obligatorio | 403 si un `CUSTOMER` pide para otro; 422 `CUSTOMER_NOT_FOUND`, `CUSTOMER_INACTIVE`, `OVERDUE_DEBT` |
| | `accountIds` | Obligatorio, 1 a 10, sin repetidos | 422 `ACCOUNT_NOT_FOUND`, `ACCOUNT_NOT_ELIGIBLE` |
| | `mainAccountId` | Opcional | 422 `MAIN_ACCOUNT_REQUIRED` (no está en `accountIds`) |
| `GET /debit-cards` | `customerId`, `status` | Opcionales; enum válido | 403 si un `CUSTOMER` pide el de otro |
| `GET /debit-cards/{id}` | `id` | | 404 `DEBIT_CARD_NOT_FOUND`; 403 |
| `PUT /debit-cards/{id}` | `accountIds`, `mainAccountId` | Igual que al emitir | 404; 422 `CARD_NOT_USABLE`, `ACCOUNT_NOT_FOUND`, `ACCOUNT_NOT_ELIGIBLE`, `MAIN_ACCOUNT_REQUIRED`; 409 `CONCURRENT_MODIFICATION` |
| `DELETE /debit-cards/{id}` | — | — | 404; 422 `CARD_HAS_PENDING_PAYMENTS`. Repetir el cierre: 204 |
| `POST /debit-cards/{id}/accounts` | `accountId` | Obligatorio | 404; 422 `CARD_NOT_USABLE`, `ACCOUNT_ALREADY_LINKED`, `MAX_ACCOUNTS_REACHED`, `ACCOUNT_NOT_FOUND`, `ACCOUNT_NOT_ELIGIBLE` |
| `DELETE /debit-cards/{id}/accounts/{accountId}` | — | — | 404 `DEBIT_CARD_NOT_FOUND`, `ACCOUNT_NOT_LINKED`; 422 `CARD_NOT_USABLE`, `LAST_ACCOUNT`, `MAIN_ACCOUNT_REQUIRED` |
| `PUT /debit-cards/{id}/main-account` | `accountId` | Obligatorio | 404; 422 `CARD_NOT_USABLE`, `MAIN_ACCOUNT_REQUIRED` |
| `POST /debit-cards/{id}/payments` | `operationId` | Obligatorio, 8 a 56 | 409 `OPERATION_ID_REUSED` |
| | `amount` | Obligatorio, ≥ 0.01, 2 decimales | — |
| | `description` | Opcional, máximo 200 | — |
| | `accountId` | Opcional | 422 `ACCOUNT_NOT_ELIGIBLE` (no asociada); 422 `CARD_NOT_USABLE`, `CUSTOMER_NOT_FOUND`, `CUSTOMER_INACTIVE`; 404 `DEBIT_CARD_NOT_FOUND`; 403 |
| `GET /debit-cards/{id}/payments` | `from`, `to` | Opcionales, fecha | 400 `INVALID_DATE_RANGE` (`from` > `to`) |
| | `status`, `operationId` | Opcionales; enum / 8 a 56 | — |
| | `page`, `size` | Común (`size` máximo 100) | — |
| `GET /debit-cards/{id}/payments/{paymentId}` | — | — | 404 `DEBIT_PAYMENT_NOT_FOUND` |
| `POST /debit-payment-recovery-runs` | `olderThanMinutes` | Opcional, 0 a 1440 | — |

**Códigos de error del servicio**

| HTTP | Códigos |
|---|---|
| 400 | `VALIDATION_ERROR`, `INVALID_DATE_RANGE` |
| 401 | `UNAUTHORIZED` |
| 403 | `FORBIDDEN` |
| 404 | `DEBIT_CARD_NOT_FOUND`, `DEBIT_PAYMENT_NOT_FOUND`, `ACCOUNT_NOT_LINKED` |
| 409 | `OPERATION_ID_REUSED`, `CONCURRENT_MODIFICATION` |
| 422 (propios) | `CUSTOMER_NOT_FOUND`, `CUSTOMER_INACTIVE`, `OVERDUE_DEBT`, `ACCOUNT_NOT_FOUND`, `ACCOUNT_NOT_ELIGIBLE`, `ACCOUNT_ALREADY_LINKED`, `MAX_ACCOUNTS_REACHED`, `MAIN_ACCOUNT_REQUIRED`, `LAST_ACCOUNT`, `CARD_NOT_USABLE`, `CARD_HAS_PENDING_PAYMENTS` |
| 422 (de la cuenta, en un pago `FAILED`) | `INSUFFICIENT_FUNDS`, `MONTHLY_LIMIT_EXCEEDED`, `ACCOUNT_INACTIVE`, `ACCOUNT_NOT_FOUND`, `INVALID_AMOUNT`, `INVALID_DATE`, `OPERATION_ID_REUSED` y otros códigos de negocio de la cuenta |

`ACCOUNT_NOT_FOUND` puede salir de dos sitios: del snapshot local al emitir o asociar (422 sin pago) y de la cuenta en un pago `FAILED` (422 con `paymentId`). El `paymentId` distingue un caso del otro.

## 7. Propiedades (Config Server)

| Propiedad | Propuesta | Uso |
|---|---|---|
| `server.port` | `8087` | |
| `payment.await-timeout` | `PT1.5S` | Espera del resultado en el `POST` de un pago (común a los orquestadores) |
| `debit.card.validity-years` | `5` | Vigencia de la tarjeta |
| `debit.card.max-accounts` | `10` | Coincide con el contrato |
| `debit.recovery.pending-minutes` | `2` | Cuándo un pago `PENDING` se reenvía |
| `debit.recovery.max-attempts` | `10` | Reenvíos máximos por pago |
| `debit.recovery.cron` | `0 * * * * *` | Cada minuto |
| `debit.page.max-size` | `100` | Coincide con el contrato |
| `bank.zone` | `America/Lima` | "Hoy" para el vencimiento y las fechas `from`/`to` |
| `spring.kafka.*` | | Grupo `debit-service`, `auto.offset.reset=earliest`, sin `enable.auto.commit` |
| `security.enabled`, `security.jwt.issuer`, `security.jwt.public-key` | | Ver `auth-service` |

Para que los cuatro saltos del pago quepan en 1,5 s conviene configurar el productor con `linger.ms=0` y el consumidor con un `fetch.max.wait.ms` bajo (por ejemplo 100 ms). Si aun así se responde 202 con frecuencia en un entorno lento, es un comportamiento esperado, no un error.

## 8. Datos de ejemplo para la demo

Con el cliente A y sus cuentas del contrato de `account-service` (A1 ahorro `1000.00`, A2 corriente `500.00`, A3 plazo fijo).

| Paso | Petición | Resultado esperado |
|---|---|---|
| 1 | `POST /debit-cards` de A con `accountIds` `[A1, A2]` | 201; principal **A1** (la primera) |
| 2 | Lo mismo con A3 (plazo fijo) o con B1 (cuenta de B) | 422 `ACCOUNT_NOT_ELIGIBLE` |
| 3 | `POST .../payments` `45.90` | 201 (o 202 y luego consulta) → `COMPLETED`, `resultingBalance` `954.10`; A1 baja a `954.10` |
| 4 | Repetir el mismo `operationId` | 200, sin doble cargo. Con otro monto: 409 `OPERATION_ID_REUSED` |
| 5 | `PUT .../main-account` con A2 y pagar `100.00` | Se descuenta A2 |
| 6 | `POST .../payments` con `accountId` A1 | Se descuenta A1 aunque la principal sea A2 |
| 7 | `DELETE .../accounts/{A2}` (la principal) | 422 `MAIN_ACCOUNT_REQUIRED` |
| 8 | Pagar más que el saldo | 422 `INSUFFICIENT_FUNDS` con `paymentId`; el pago queda `FAILED` y aparece con `?operationId=` |
| 9 | Más de 5 movimientos en la cuenta | El pago informa `fee` y el historial tiene el `FEE` enlazado |
| 10 | `GET .../payments?status=COMPLETED&size=10` | Últimos 10 pagos completados |
| 11 | `POST /debit-cards` de C (con deuda vencida) | 422 `OVERDUE_DEBT`. Tras `credit.overdue.cleared`, con una cuenta de C ya abierta → 201 |
| 12 | Cerrar la única cuenta de una tarjeta (`DELETE /accounts/{id}` en `account-service`) | La tarjeta pasa a `CLOSED` sola; pagar → 422 `CARD_NOT_USABLE` |

## 9. Decisiones y pendientes

**Decidido (nuevo en este contrato)**
- **Un pago repetido con `PENDING` vuelve a pedir el retiro y espera otros 1,5 s** (no solo responde 202), para que "repetir con el mismo `operationId`" sea un camino real de resolución.
- **201 solo para quien crea el pago**; cualquier repetición de un `COMPLETED` responde 200.
- **Idempotencia antes que validación de la tarjeta**: repetir un pago devuelve el original aunque la tarjeta ya esté cerrada o vencida.
- **La comparación de una repetición usa tarjeta, monto y la cuenta que pidió el cliente** (no la resuelta), y no la descripción.
- **El `operationId` mide como máximo 56 caracteres** (`transaction-service` le suma `-FEE`).
- **Un cliente `INACTIVE` no puede pagar** con sus tarjetas (422 `CUSTOMER_INACTIVE` al pagar, sin crear el pago). La ficha solo lo pedía al emitir.
- **Solo se asocian cuentas de las que el cliente es titular principal**: el evento `account` no trae cotitulares.
- **Cierre automático sin mirar pagos pendientes**; el cierre manual sí los bloquea (`CARD_HAS_PENDING_PAYMENTS`).
- **Tope de reenvíos** de la recuperación (`debit.recovery.max-attempts`, 10): un pago que nunca recibe respuesta no se reenvía para siempre; queda `PENDING` para revisión manual.
- **`completedAt` y el `occurredAt` de `debit.payment.completed` son los del movimiento** de `transaction-service`, para que el reporte y el historial de la cuenta coincidan.
- `CustomerSnapshot` ya no guarda el `type` (no se usa).
- El número de tarjeta lleva prefijo `500000` y dígito de control Luhn (las de crédito usan `400000`).
- Sin transacciones de Mongo: cada operación toca un solo documento.

**Pendiente**
- **Cliente que pasa a `INACTIVE`:** hoy sus tarjetas siguen abiertas (solo se le impide pagar). Cerrarlas automáticamente por el evento `customer` sería lo simétrico a `auth-service`; queda fuera del demo.
- **Cuentas mancomunadas:** asociar una cuenta donde el cliente es solo cotitular exigiría que el evento `account` lleve los titulares.
- **Verificar `Between` con `Range<Instant>`** en la versión de Spring Data MongoDB elegida (2.2). Es el mismo patrón que se planteó para los historiales de `transaction-service` (que usa `GreaterThanEqual…And…LessThan` sobre `occurredAt`): comprobar allí también que la consulta derivada con dos condiciones sobre el mismo campo funciona, y si no, adoptar `Between`.
- Límite diario de pagos con débito: fuera de alcance (backlog del demo).
- Espera en memoria con varias instancias (202 más frecuente): aceptado.
- Outbox para `debit.card.*` y `debit.payment.*`: común a todos los servicios.
- Nada bloqueante para empezar a programar este servicio.

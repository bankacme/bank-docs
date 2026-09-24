# `yanki-service` — Modelo de datos

> Complementa la ficha (`services/yanki-service.md`), el flujo del pago (`flows/03-yanki-payment.md`) y el contrato REST (`openapi.yaml`, en esta carpeta) con los **tipos exactos**, los documentos de MongoDB, los índices, la saga de las cuatro rutas paso a paso, la recuperación y el tratamiento de cada evento. Si algo difiere, el `openapi.yaml` manda para la API y `contracts/events/kafka-contract.md` para los eventos.
>
> Servicio de P3: **sin REST hacia otros servicios**. Valida con copias locales y pide los movimientos de cuenta a `transaction-service` por Kafka. Es el **orquestador** de su propia saga.

## 1. Entidades de dominio

### 1.1 Aggregate `Wallet`

| Campo | Tipo (dominio) | Req. | Regla | Mutable |
|---|---|---|---|---|
| `id` | `WalletId` (`String`, UUID v4) | ✓ | Lo genera el servicio | No |
| `ownerUserId` | `UserId` | ✓ | El `sub` del token. Un monedero `ACTIVE` por usuario | No |
| `document` | `Document` | ✓ | El **del token**. Solo DNI, CEX o PASSPORT. Único entre monederos `ACTIVE` | No |
| `phoneNumber` | `PhoneNumber` | ✓ | 9 dígitos, empieza con 9. Único entre monederos `ACTIVE` | No |
| `imei` | `Imei` | ✓ | 15 dígitos. Nunca se devuelve completo | Sí (`updateContact`) |
| `email` | `Email` | ✓ | Formato válido, en minúsculas, máximo 254 | Sí (`updateContact`) |
| `balance` | `Money` | ✓ | ≥ 0. **Solo tiene sentido en modo `STANDALONE`**; en `LINKED` es siempre 0 | Sí (`debit`, `credit`, `refund`) |
| `linkedCardId` | `CardId?` | – | Presente si está asociado. Único entre monederos | Sí (`linkCard`, `unlinkCard`) |
| `appliedLegs` | `AppliedLegs` | ✓ | Los últimos `yanki.applied-legs.max` (50) `legId` aplicados al saldo | Sí |
| `status` | `WalletStatus` | ✓ | `ACTIVE` al abrir | Sí (`close`) |
| `version` | `long` | ✓ | Control optimista (`@Version`). **Interno** | Automático |
| `createdAt` / `updatedAt` | `Instant` | ✓ | Reloj inyectado (`Clock`) | `updatedAt` sí |

Comportamiento: `open(...)`, `updateContact(email, imei)`, `linkCard(cardId)`, `unlinkCard()`, `debit(amount, legId)`, `credit(amount, legId)`, `refund(amount, legId)`, `close()`. Derivado: `isLinked()`.

Las tres operaciones sobre el saldo son **idempotentes por `legId`** y devuelven `APPLIED` o `ALREADY_APPLIED`:

| Operación | Si el `legId` ya está en `appliedLegs` | Si no | Rechazos |
|---|---|---|---|
| `debit(amount, legId)` | `ALREADY_APPLIED` (se evalúa **primero**, sin mirar estado ni saldo) | Resta el monto y registra el `legId` | `WALLET_INACTIVE` (cerrado o `LINKED`); `INSUFFICIENT_FUNDS` (saldo menor que el monto) |
| `credit(amount, legId)` | ídem | Suma el monto y registra el `legId` | `WALLET_INACTIVE` (cerrado o `LINKED`) |
| `refund(amount, legId)` | ídem | Suma el monto y registra el `legId`, **sin importar estado ni modo** | Ninguno: devolver dinero al emisor nunca se rechaza |

`appliedLegs` descarta el más antiguo al pasar de 50. Basta porque solo importan las patas de pagos en curso.

### 1.2 Aggregate `WalletPayment`

| Campo | Tipo (dominio) | Req. | Regla | Mutable |
|---|---|---|---|---|
| `id` | `WalletPaymentId` (`String`, UUID v4) | ✓ | Lo genera el servicio | No |
| `operationId` | `OperationId` | ✓ | El del cliente (8 a 56 caracteres). Único. Las patas son `<op>-OUT`, `<op>-IN`, `<op>-REV` | No |
| `senderWalletId` / `receiverWalletId` | `WalletId` | ✓ | Distintos | No |
| `senderMaskedPhone` / `receiverMaskedPhone` | `String` | ✓ | `*** *** NNN`. Congelados al crear: el historial no necesita leer los monederos | No |
| `amount` | `Money` | ✓ | > 0, 2 decimales | No |
| `description` | `String?` | – | Máximo 200 | No |
| `route` | `PaymentRoute` | ✓ | Se resuelve al crear y **no cambia** | No |
| `status` | `WalletPaymentStatus` | ✓ | `STARTED` al crear | Sí |
| `failureReason` | `FailureReason?` | – | El **rechazo original** (del débito o del crédito) | Sí |
| `compensationFailure` | `FailureReason?` | – | Solo `COMPENSATION_FAILED`: por qué no se pudo devolver | Sí |
| `compensationAttempts` | `int` | ✓ | Pedidos de devolución remota enviados. Tope `yanki.compensation.max-attempts` (5) | Sí |
| `recoveryAttempts` | `int` | ✓ | Retomas hechas por la recuperación. Tope `yanki.recovery.max-attempts` (10) | Sí |
| `visibleTo` | `List<WalletId>` | ✓ | Monederos que ven el pago: el emisor desde el inicio; el receptor **solo al llegar a `COMPLETED`** | Sí |
| `requestedAt` | `Instant` | ✓ | | No |
| `completedAt` | `Instant?` | – | Al llegar a un estado terminal | Sí |
| `version` | `long` | ✓ | Control optimista. Interno | Automático |
| `updatedAt` | `Instant` | ✓ | Se actualiza en cada transición **y en cada reintento** (así la recuperación espera N minutos entre reintentos) | Sí |

Transiciones (cualquier otra se **ignora**): `STARTED → SOURCE_DEBITED | FAILED`; `SOURCE_DEBITED → COMPLETED | COMPENSATING`; `COMPENSATING → COMPENSATED | COMPENSATION_FAILED`. Métodos: `sourceDebited()`, `completed(now)`, `failed(reason, now)`, `startCompensation(reason)`, `compensated(now)`, `compensationFailed(reason, now)`.

### 1.3 Value objects

| VO | Campos | Validación |
|---|---|---|
| `WalletId`, `WalletPaymentId`, `UserId`, `CardId` | `value: String` | No vacío |
| `OperationId` | `value: String` | 8 a 56 caracteres |
| `Document` | `type`, `number` | DNI: 8 dígitos. CEX: 9 a 12 alfanuméricos. PASSPORT: 6 a 12 alfanuméricos. RUC no admitido (422 `DOCUMENT_TYPE_NOT_ALLOWED`) |
| `PhoneNumber` | `value` | 9 dígitos, empieza con 9. `masked()` → `*** *** NNN` |
| `Imei` | `value` | 15 dígitos. `masked()` → 11 asteriscos + los últimos 4 |
| `Email` | `value` | Formato válido, minúsculas |
| `Money` | `amount: BigDecimal` (2 decimales) | ≥ 0; > 0 al operar. Solo `PEN` |
| `AppliedLegs` | `legs: List<String>` (máx. 50, más reciente al final) | `contains(legId)`, `add(legId)` |
| `PaymentRoute` | `sourceKind`, `sourceAccountId?`, `targetKind`, `targetAccountId?` | `accountId` presente si y solo si el extremo es `ACCOUNT`. No cambia |
| `FailureReason` | `code`, `message?` | Código no vacío |
| `DebitCardSnapshot` | `cardId`, `customerId`, `maskedNumber`, `mainAccountId`, `expiryDate`, `status`, `updatedAt` | Dato de lectura (2.3) |
| `CustomerSnapshot` | `customerId`, `document`, `status`, `updatedAt` | Dato de lectura (2.4) |

### 1.4 Enums

| Enum | Valores |
|---|---|
| `DocumentType` | `DNI`, `CEX`, `PASSPORT` (`RUC` existe en el token, pero se rechaza aquí) |
| `WalletStatus` | `ACTIVE`, `CLOSED` |
| `WalletMode` | `STANDALONE`, `LINKED` (derivado de `linkedCardId`) |
| `FundingKind` | `WALLET` (saldo del monedero), `ACCOUNT` (cuenta principal de la tarjeta) |
| `WalletPaymentStatus` | `STARTED`, `SOURCE_DEBITED`, `COMPLETED`, `FAILED`, `COMPENSATING`, `COMPENSATED`, `COMPENSATION_FAILED` |
| `PaymentDirection` | `SENT`, `RECEIVED` (solo consultas) |

## 2. Documentos MongoDB

Base propia. Dinero como `Decimal128`, `YearMonth` como texto `yyyy-MM`, `Instant` como `date` UTC (convenciones globales del README). Índices al arrancar (`auto-index-creation=true`). **Sin transacciones de Mongo**: la consistencia entre el monedero y el pago se logra con pasos idempotentes por `legId` (3.6).

### 2.1 `wallets` — `WalletDocument`

| Campo Mongo | Tipo Mongo | Tipo Java | Req. | Notas |
|---|---|---|---|---|
| `_id` | string | `String` | ✓ | UUID v4 |
| `ownerUserId` | string | `String` | ✓ | |
| `document.type` / `document.number` | string | `String` | ✓ | |
| `phoneNumber` | string | `String` | ✓ | |
| `imei` | string | `String` | ✓ | No sale completo por ningún medio |
| `email` | string | `String` | ✓ | |
| `balance` | decimal128 | `BigDecimal` | ✓ | |
| `linkedCardId` | string | `String` | – | **Se omite** si no hay (nunca `null`) |
| `appliedLegs` | array de string | `List<String>` | ✓ | Máximo 50 |
| `status` | string | `String` (enum) | ✓ | |
| `version` | long | `Long` | ✓ | `@Version` |
| `createdAt` / `updatedAt` | date | `Instant` | ✓ | |

| Índice | Campos | Opciones | Para qué |
|---|---|---|---|
| `uk_wallet_document_active` | `document.type` (1), `document.number` (1) | **Único parcial**, filtro `{ status: "ACTIVE" }` | Un monedero activo por documento → 409 `DOCUMENT_ALREADY_REGISTERED` |
| `uk_wallet_phone_active` | `phoneNumber` (1) | Único parcial, `{ status: "ACTIVE" }` | 409 `PHONE_ALREADY_REGISTERED` y búsqueda del receptor |
| `uk_wallet_owner_active` | `ownerUserId` (1) | Único parcial, `{ status: "ACTIVE" }` | 409 `USER_ALREADY_HAS_WALLET` |
| `uk_wallet_card` | `linkedCardId` (1) | Único parcial, `{ linkedCardId: { $exists: true } }` | Una tarjeta, un monedero → 422 `CARD_ALREADY_LINKED` |
| `ix_wallet_status` | `status` (1) | — | Listado |

Los filtros parciales usan solo igualdad y `$exists` (no admiten `$in` ni `$ne`). La `DuplicateKeyException` se traduce por el **nombre del índice** que la causó. Al cerrar un monedero sale de los tres primeros índices: su documento, celular y usuario quedan libres.

**Consultas (sin `@Query`, sin consultas dinámicas)**

| Necesidad | Método (derivado) |
|---|---|
| Por id | `findById` |
| Mi monedero | `findByOwnerUserIdAndStatus(userId, ACTIVE)` |
| Receptor por celular | `findByPhoneNumberAndStatus(phone, ACTIVE)` |
| ¿Documento ya registrado? | `existsByDocument_TypeAndDocument_NumberAndStatus(type, number, ACTIVE)` |
| ¿Tarjeta ya asociada? / monedero de una tarjeta cerrada | `findByLinkedCardId(cardId)` |
| Listado | `findAll` o `findByStatus`. Orden en memoria: `createdAt` descendente y `id` |

### 2.2 `wallet_payments` — `WalletPaymentDocument`

| Campo Mongo | Tipo Mongo | Tipo Java | Req. | Notas |
|---|---|---|---|---|
| `_id` | string | `String` | ✓ | `paymentId` |
| `operationId` | string | `String` | ✓ | Único |
| `senderWalletId` / `receiverWalletId` | string | `String` | ✓ | |
| `senderMaskedPhone` / `receiverMaskedPhone` | string | `String` | ✓ | |
| `amount` | decimal128 | `BigDecimal` | ✓ | |
| `description` | string | `String` | – | |
| `route.sourceKind` / `route.targetKind` | string | `String` (enum) | ✓ | |
| `route.sourceAccountId` / `route.targetAccountId` | string | `String` | – | Solo si el extremo es `ACCOUNT` |
| `status` | string | `String` (enum) | ✓ | |
| `failureReason.code` / `.message` | string | `String` | – | |
| `compensationFailure.code` / `.message` | string | `String` | – | |
| `compensationAttempts` / `recoveryAttempts` | int | `int` | ✓ | |
| `visibleTo` | array de string | `List<String>` | ✓ | 1 o 2 elementos |
| `requestedAt` | date | `Instant` | ✓ | |
| `completedAt` | date | `Instant` | – | |
| `version` | long | `Long` | ✓ | |
| `updatedAt` | date | `Instant` | ✓ | |

| Índice | Campos | Opciones | Para qué |
|---|---|---|---|
| `uk_wp_operation_id` | `operationId` (1) | **Único** | Idempotencia y carreras de dos peticiones iguales (`DuplicateKeyException` → se trata como repetición) |
| `ix_wp_sender_requested` | `senderWalletId` (1), `requestedAt` (-1) | — | Enviados; "¿tiene pagos en curso como emisor?" |
| `ix_wp_receiver_status_requested` | `receiverWalletId` (1), `status` (1), `requestedAt` (-1) | — | Recibidos completados; "¿tiene pagos en curso como receptor?" |
| `ix_wp_visible_requested` | `visibleTo` (1), `requestedAt` (-1) | Multiclave | Historial de ambas direcciones |
| `ix_wp_status_updated` | `status` (1), `updatedAt` (1) | — | Recuperación |

**Consultas**

| Necesidad | Método (derivado) |
|---|---|
| Por id / por `operationId` | `findById` / `findByOperationId` |
| Historial `SENT` | `findBySenderWalletIdAndRequestedAtBetween(walletId, range, pageable)` + `countBy…` |
| Historial `RECEIVED` | `findByReceiverWalletIdAndStatusAndRequestedAtBetween(walletId, COMPLETED, range, pageable)` + `countBy…` |
| Historial sin `direction` | `findByVisibleToContainingAndRequestedAtBetween(walletId, range, pageable)` + `countBy…` |
| ¿Pagos en curso? (asociar, cerrar) | `existsBySenderWalletIdAndStatusIn(id, [STARTED, SOURCE_DEBITED, COMPENSATING])` **o** `existsByReceiverWalletIdAndStatusIn(id, …)` |
| Recuperación | `findByStatusInAndUpdatedAtBefore([STARTED, SOURCE_DEBITED, COMPENSATING], corte)` |

- **Por qué `visibleTo`:** "ambas direcciones" necesitaría un `OR` (emisor o receptor) combinado con el rango y con la condición "el receptor solo ve los `COMPLETED`"; una consulta derivada no expresa esa precedencia. `visibleTo` ya guarda a quién le corresponde ver cada pago y se resuelve con **una** consulta.
- **Rango de fechas:** igual que en `debit-service` (2.2): `Range.rightOpen([from 00:00, to+1 00:00))` en `bank.zone`, `Instant.EPOCH` y un instante lejano si faltan `from` o `to`. Mismo aviso de verificar `Between` con `Range` al implementar.
- **Orden:** `requestedAt` descendente, empate por `_id` descendente.

### 2.3 `debit_card_snapshots` — `DebitCardSnapshotDocument`

Copia local de las tarjetas, alimentada por el tópico `debit-card`.

| Campo Mongo | Tipo Mongo | Req. | Notas |
|---|---|---|---|
| `_id` | string | ✓ | `cardId` |
| `customerId` | string | ✓ | Titular de la tarjeta |
| `maskedNumber` | string | ✓ | `**** 4821` |
| `mainAccountId` | string | ✓ | Cuenta principal actual |
| `expiryDate` | string | ✓ | `yyyy-MM` |
| `status` | string | ✓ | `ACTIVE` o `CLOSED` |
| `updatedAt` | date | ✓ | Del evento |

### 2.4 `customer_snapshots` — `CustomerSnapshotDocument`

| Campo Mongo | Tipo Mongo | Req. | Notas |
|---|---|---|---|
| `_id` | string | ✓ | `customerId` |
| `document.type` / `document.number` | string | ✓ | Se compara con el documento del monedero al asociar una tarjeta |
| `status` | string | ✓ | `ACTIVE` o `INACTIVE` |
| `updatedAt` | date | ✓ | Del evento |

## 3. Reglas y algoritmos

### 3.1 Abrir el monedero (`OpenWalletUseCase`)

1. Formato (400). El token debe traer `sub`, `documentType` y `documentNumber` (si no, 401).
2. Documento del token distinto de RUC (422 `DOCUMENT_TYPE_NOT_ALLOWED`) y con el formato de su tipo.
3. Unicidad, en este orden: usuario (`findByOwnerUserIdAndStatus`) → 409 `USER_ALREADY_HAS_WALLET`; documento → 409 `DOCUMENT_ALREADY_REGISTERED`; celular → 409 `PHONE_ALREADY_REGISTERED`. Las comprobaciones dan el mensaje; la garantía real son los índices (3 parciales).
4. Se guarda `STANDALONE`, saldo 0, `appliedLegs` vacío. Se publica `yanki.wallet.created`.

### 3.2 Asociar y desasociar una tarjeta

**Asociar** (`LinkDebitCardUseCase` + `LinkCardPolicy`, puro; recibe el monedero, los snapshots y los resultados de las dos consultas). Dueño (403). La primera condición que falla responde:

| # | Comprobación | Error |
|---|---|---|
| 1 | Si ya está asociado a **esa** misma tarjeta → 200 sin cambios ni evento | — |
| 2 | Monedero `ACTIVE` | 422 `WALLET_INACTIVE` |
| 3 | No está asociado a otra tarjeta | 422 `WALLET_ALREADY_LINKED` |
| 4 | Saldo propio = 0 | 422 `WALLET_HAS_BALANCE` |
| 5 | Sin pagos en curso (emisor ni receptor) | 422 `WALLET_HAS_PAYMENTS_IN_PROGRESS` |
| 6 | La tarjeta existe en `debit_card_snapshots` y su titular en `customer_snapshots` | 422 `CARD_NOT_FOUND` |
| 7 | Tarjeta `ACTIVE`, **no vencida** (el mes actual en `bank.zone` no es posterior a `expiryDate`) y titular `ACTIVE` | 422 `CARD_NOT_ACTIVE` |
| 8 | El documento del titular **es igual** al del monedero (tipo y número) | 422 `CARD_OWNER_MISMATCH` |
| 9 | Ningún otro monedero tiene la tarjeta (`findByLinkedCardId`) | 422 `CARD_ALREADY_LINKED` |

Se guarda con `version`; el índice `uk_wallet_card` cubre la carrera del punto 9. Se publica `yanki.wallet.card.linked`. Un titular con RUC (empresa) nunca coincide con el documento de un monedero (DNI, CEX o PASSPORT): 422 `CARD_OWNER_MISMATCH`.

**Desasociar:** se permite siempre. Sin tarjeta → 200 sin cambios. Con tarjeta: `linkedCardId` se quita, el monedero queda `STANDALONE` con saldo 0 (ya lo era) y se publica `yanki.wallet.card.unlinked`. Los pagos en curso conservan su ruta.

**Tarjeta cerrada** (`HandleCardClosedUseCase`): ver 4.2.

### 3.3 Cerrar el monedero

`Wallet.close()` exige, en este orden: saldo 0 (`WALLET_HAS_BALANCE`), sin tarjeta (`WALLET_HAS_CARD`) y sin pagos en curso (`WALLET_HAS_PAYMENTS_IN_PROGRESS`). Si ya está `CLOSED` → 204 sin publicar. Publica `yanki.wallet.closed`.

### 3.4 Resolver la ruta (`PaymentRoutingPolicy`)

Puro: recibe los dos monederos y los snapshots de tarjeta ya leídos. Para cada extremo:

| Extremo | Si es `STANDALONE` | Si es `LINKED` |
|---|---|---|
| Emisor | `sourceKind = WALLET` | Tarjeta en el snapshot y `ACTIVE`, si no → 422 `CARD_NOT_ACTIVE`. `sourceKind = ACCOUNT`, `sourceAccountId = mainAccountId` **del snapshot en ese momento** |
| Receptor | `targetKind = WALLET` | Tarjeta en el snapshot y `ACTIVE`, si no → 422 `RECEIVER_NOT_FOUND` (no se le revela al emisor el motivo). `targetKind = ACCOUNT`, `targetAccountId = mainAccountId` |

La ruta se guarda en el pago y **no cambia** aunque luego se cambie la cuenta principal o se cierre la tarjeta.

| Ruta | Origen | Destino | Paso 1 (`-OUT`) | Paso 2 (`-IN`) | Devolución (`-REV`) |
|---|---|---|---|---|---|
| 1 | `WALLET` | `WALLET` | Local | Local | Local |
| 2 | `ACCOUNT` | `WALLET` | Remoto | Local | Remota |
| 3 | `WALLET` | `ACCOUNT` | Local | Remoto | Local |
| 4 | `ACCOUNT` | `ACCOUNT` | Remoto | Remoto | Remota |

### 3.5 Enviar un pago (`SendPaymentUseCase`)

1. **Monedero:** `findById` (404 `WALLET_NOT_FOUND`); debe ser del usuario del token (403). Solo `YANKI_USER` y `CUSTOMER` pueden pagar.
2. **Idempotencia:** `findByOperationId`. Si existe y **no** coincide el monedero emisor, el monto (`compareTo`) o el celular del receptor (se lee el monedero receptor guardado, por su id, y se compara su celular; el celular no cambia) → 409 `OPERATION_ID_REUSED`. La descripción no se compara. Si coincide → 3.5.1. Es lo primero después del monedero: una repetición se responde aunque el monedero ya esté cerrado.
3. **Validaciones** (no crean nada): emisor `ACTIVE` (`WALLET_INACTIVE`); receptor por celular entre los `ACTIVE` (`RECEIVER_NOT_FOUND`); emisor ≠ receptor (`SAME_WALLET`); rutas (3.4); emisor `STANDALONE` con saldo suficiente (`INSUFFICIENT_FUNDS`). La deuda vencida no se mira.
4. **Espera:** `PaymentResultAwaiterPort.register(paymentId)` **antes** de guardar y de publicar.
5. **Guardar** el pago `STARTED` (`visibleTo = [emisor]`, contadores en 0). Si `uk_wp_operation_id` salta (dos peticiones iguales a la vez) → se vuelve al paso 2.
6. **Avanzar** (`PaymentSagaCoordinator.advance`, 3.6): ejecuta los pasos **locales** que le toquen dentro de la petición y publica el **primer comando remoto** si lo hay. Si Kafka falla, el pago queda en curso y se responde 202 (lo retomará la recuperación).
7. **Esperar** hasta `payment.await-timeout` (1,5 s) un estado terminal. Con resultado se **relee** el pago y se responde según el estado (tabla 3.5.2). Sin resultado → 202. En todos los casos se retira la espera.

#### 3.5.1 Repeticiones (mismo `operationId`, mismos datos)

| Estado del pago | Respuesta |
|---|---|
| `COMPLETED` | **200** (201 es solo para quien lo creó) |
| `FAILED` / `COMPENSATED` | **422** con el mismo `code` (`failureReason`), `paymentId` y `paymentStatus` |
| `COMPENSATION_FAILED` | **202** con el pago |
| En curso (`STARTED`, `SOURCE_DEBITED`, `COMPENSATING`) | Se registra la espera, se relee el pago (pudo avanzar entre tanto) y, si sigue en curso, se llama a `advance` (repite el paso local o **vuelve a publicar** el comando remoto, idempotente aguas abajo) y se espera otra vez hasta 1,5 s → 200, 422 o 202 según termine |

#### 3.5.2 Respuesta según el estado final

| Estado | HTTP | Cuerpo |
|---|---|---|
| `COMPLETED` | 201 (200 si es repetición) | `WalletPayment` |
| `FAILED` | 422 | `code` = motivo del débito; `paymentId`; `paymentStatus = FAILED` |
| `COMPENSATED` | 422 | `code` = motivo del crédito; `paymentId`; `paymentStatus = COMPENSATED` |
| `COMPENSATION_FAILED` | 202 | `WalletPayment` con `status`, `failureReason` y `compensationFailure` |
| En curso | 202 | `WalletPayment` |

### 3.6 La saga: `advance(payment)`

Una sola función para la petición, el consumidor y la recuperación. Avanza en bucle hasta que **debe esperar un resultado remoto** o llega a un estado terminal. Cada transición se guarda con control de versión; si el guardado choca y el pago ya avanzó, se relee y se **ignora** (otro escritor ya lo hizo).

| Estado | Paso | Si el paso es **local** | Si el paso es **remoto** |
|---|---|---|---|
| `STARTED` | Débito `<op>-OUT` al emisor | `sender.debit(amount, "<op>-OUT")`: `APPLIED` o `ALREADY_APPLIED` → `SOURCE_DEBITED` y sigue; rechazado (`INSUFFICIENT_FUNDS`, `WALLET_INACTIVE`) → `FAILED` con ese motivo (fin) | Publica `yanki.movement.requested` (`operationId = <op>-OUT`, `type = YANKI_PAYMENT_OUT`, `accountId = sourceAccountId`) y **espera** |
| `SOURCE_DEBITED` | Crédito `<op>-IN` al receptor | `receiver.credit(amount, "<op>-IN")`: `APPLIED` o `ALREADY_APPLIED` → `COMPLETED` (fin); rechazado (`WALLET_INACTIVE`) → `COMPENSATING` con ese motivo y sigue | Publica `yanki.movement.requested` (`<op>-IN`, `YANKI_PAYMENT_IN`, `accountId = targetAccountId`) y **espera** |
| `COMPENSATING` | Devolución `<op>-REV` | `sender.refund(amount, "<op>-REV")` → `COMPENSATED` (fin). No puede fallar | `compensationAttempts + 1` y publica `yanki.movement.reversal.requested` (`operationId = <op>-REV`, `originalOperationId = <op>-OUT`, `accountId = sourceAccountId`); **espera** |

- **Regla de oro:** una respuesta perdida no es un rechazo. Nunca se compensa por un tiempo agotado; solo un rechazo explícito del paso 2 (`transaction.failed` de `<op>-IN`, o el rechazo local) lleva a `COMPENSATING`.
- **Solo se compensa el paso 1.** El motivo del rechazo original se guarda en `failureReason` y es el que ve el cliente aunque la devolución sea la que termine la saga.
- **Al llegar a un estado terminal:** `completedAt = ahora`, y **después de guardar** se publica el evento (`COMPLETED` → `yanki.payment.completed`; los otros tres → `yanki.payment.failed`) y se avisa a la espera en memoria. `COMPLETED` también agrega al receptor en `visibleTo` (en el mismo guardado).
- **Efecto observable del paso local:** el saldo del monedero cambia **antes** que el estado del pago. Por eso el orden de un paso local es: (1) el pago ya está guardado en el estado anterior; (2) se aplica la operación al monedero (idempotente por `legId`; conflicto de versión del monedero → se relee y se reintenta hasta 3 veces); (3) se guarda la transición. Si el servicio cae entre 2 y 3, la recuperación repite el paso: el monedero reconoce el `legId` (`ALREADY_APPLIED`) y solo falta guardar la transición. **Nunca se cobra dos veces.**
- **Ruta 1** ocurre entera dentro de la petición, sin Kafka.

### 3.7 Resultados remotos (`HandleMovementResultUseCase`)

Consume el tópico `transaction`. El pago se busca por `operationId` quitando **un** sufijo de la pata (`-OUT`, `-IN` o `-REV`): `findByOperationId(base)`. Si no existe, se descarta con un log (otro servicio o base reconstruida). En `transaction.reversed` se comprueba además que `originalOperationId` sea `<base>-OUT`.

| Mensaje | Tipo / pata | Condición para aplicarlo | Efecto |
|---|---|---|---|
| `transaction.registered` | `YANKI_PAYMENT_OUT`, `<op>-OUT` | `STARTED` y origen `ACCOUNT` | → `SOURCE_DEBITED`; `advance` (paso 2) |
| `transaction.failed` | `YANKI_PAYMENT_OUT`, `<op>-OUT` | `STARTED` y origen `ACCOUNT` | → `FAILED` con `reasonCode` y `message` |
| `transaction.registered` | `YANKI_PAYMENT_IN`, `<op>-IN` | `SOURCE_DEBITED` y destino `ACCOUNT` | → `COMPLETED` |
| `transaction.failed` | `YANKI_PAYMENT_IN`, `<op>-IN` | `SOURCE_DEBITED` y destino `ACCOUNT` | → `COMPENSATING` con el motivo; `advance` (devolución) |
| `transaction.reversed` | `<op>-REV` | `COMPENSATING` y origen `ACCOUNT` | → `COMPENSATED` |
| `transaction.reversal.failed` | `<op>-REV` | `COMPENSATING` y origen `ACCOUNT` | → `COMPENSATION_FAILED`, `compensationFailure = reasonCode`. Es **definitivo** (no se reintenta) |

Todo lo demás (comisiones `FEE`, tipos `DEBIT_PAYMENT`, `TRANSFER_*`, reversas de transferencias, mensajes en un estado que no corresponde) se ignora sin error. Se confirma el offset **después** de guardar. Un `advance` que vuelve a publicar tras guardar y falla se registra y **no** revierte lo guardado; la recuperación lo reenvía.

### 3.8 Recuperación (`RecoverPendingPaymentsUseCase`)

`@Scheduled` (`yanki.recovery.cron`) y `POST /wallet-payment-recovery-runs`. Con `corte = ahora − olderThanMinutes` (por defecto `yanki.recovery.pending-minutes`, 2):

1. `findByStatusInAndUpdatedAtBefore([STARTED, SOURCE_DEBITED, COMPENSATING], corte)`.
2. Para cada pago, en este orden:
   - `COMPENSATING` con devolución remota y `compensationAttempts ≥ yanki.compensation.max-attempts` → `COMPENSATION_FAILED` con `compensationFailure = NO_RESPONSE` (cuenta en `compensationFailed`).
   - `recoveryAttempts ≥ yanki.recovery.max-attempts` → no se reintenta (cuenta en `exhausted`); queda en curso para revisión manual.
   - Si no: `recoveryAttempts + 1`, se actualiza `updatedAt` y se llama a `advance` (cuenta en `resumed`).
3. `advance` repite el paso local (el monedero ignora el `legId` repetido) o reenvía el comando remoto con el **mismo** identificador de pata.

`transaction-service` es idempotente con reemisión: ante un comando repetido cuyo resultado ya existe, vuelve a publicarlo (mismos campos, nuevo `eventId`). Con varias instancias, dos reintentos iguales son inofensivos.

### 3.9 La espera en memoria (`InMemoryPaymentResultAwaiter`)

Igual que en `debit-service` (4.3): un mapa `paymentId → AsyncSubject`; se **registra antes** de guardar, `complete` emite y retira, y la espera con `timeout` lo retira si vence. El estado siempre lo guarda quien avanza la saga **antes** de avisar y la petición **relee**; un aviso perdido solo produce un 202. Con varias instancias, el resultado puede caer en otra y el cliente verá 202 y consultará (aceptado para el demo).

## 4. Eventos

### 4.1 Publica

| Tópico | `eventType` | Clave | Cuándo | Payload |
|---|---|---|---|---|
| `wallet` | `yanki.wallet.created` / `updated` / `closed` | `walletId` | Apertura; cambio de correo o IMEI; cierre | Estado (contrato de Kafka, 6.14): `walletId`, `maskedPhone`, `status`, `linkedCardId?`, `updatedAt` |
| `wallet` | `yanki.wallet.card.linked` / `unlinked` | `walletId` | Se asocia o desasocia (manual o por tarjeta cerrada) | **El mismo estado completo** (con `linkedCardId` presente al asociar y ausente al desasociar) **más** `cardId` (la tarjeta asociada, o la que se quitó) |
| `transaction.command` | `yanki.movement.requested` | `accountId` | Pasos remotos `-OUT` y `-IN`, y cada reenvío | 6.9: `operationId` (= pata), `paymentId`, `accountId`, `type`, `amount`, `description?` |
| `transaction.command` | `yanki.movement.reversal.requested` | `accountId` | Devolución remota y cada reenvío | 6.9: `operationId` (= `<op>-REV`), `originalOperationId` (= `<op>-OUT`), `paymentId`, `accountId` |
| `wallet.payment` | `yanki.payment.completed` | `paymentId` | Pago `COMPLETED` | `paymentId`, `senderWalletId`, `receiverWalletId`, `amount` |
| `wallet.payment` | `yanki.payment.failed` | `paymentId` | Pago `FAILED`, `COMPENSATED` o `COMPENSATION_FAILED` | `paymentId`, `status`, `reasonCode` (= `failureReason.code`) |

`correlationId` del sobre: la pata (`operationId`) en los comandos; el `eventId` en el resto. Ningún consumidor de negocio lee `wallet` ni `wallet.payment` (trazabilidad); **sí importa que `wallet` lleve siempre el estado completo**, porque está compactado.

### 4.2 Consume (grupo `yanki-service`)

| Tópico | Eventos | Tratamiento |
|---|---|---|
| `customer` | `customer.created/updated/deleted` | Upsert de `customer_snapshots {document, status, updatedAt}` si el evento es igual o más reciente. No cierra monederos (la baja de un cliente no toca su monedero) |
| `debit-card` | `debit.card.created/updated/closed` | Upsert de `debit_card_snapshots {customerId, maskedNumber, mainAccountId, expiryDate, status, updatedAt}` con la misma regla de fecha. **Si se aplicó y `status = CLOSED`:** `findByLinkedCardId(cardId)`; si hay monedero, se desasocia (`linkedCardId` fuera, `STANDALONE`, saldo 0) y se publica `yanki.wallet.card.unlinked`. Los pagos en curso siguen con su ruta |
| `transaction` | `transaction.registered/failed` (`YANKI_PAYMENT_*`), `transaction.reversed`, `transaction.reversal.failed` | 3.7 |

Reglas de consumo comunes (reintentos 3 veces con 1, 2 y 4 s, `.DLT`, confirmación tras guardar, tipo desconocido ignorado): contrato de Kafka, sección 7.

**Reconstrucción:** con la base vacía, `customer` y `debit-card` (compactados) se leen desde el inicio y recomponen las copias. Los monederos y los pagos son la fuente de verdad de este servicio y **no** se reconstruyen desde Kafka.

## 5. Mapeos

| Dominio (`Wallet`) | Documento Mongo | REST (`Wallet`) | Evento `wallet` |
|---|---|---|---|
| `id` | `_id` | `id` | `walletId` |
| `ownerUserId` | `ownerUserId` | `ownerUserId` | — |
| `document` | `document.{type,number}` | `document` | — |
| `phoneNumber` | `phoneNumber` | `phoneNumber` (al dueño y al personal) | `maskedPhone` |
| `imei` | `imei` | `maskedImei` (nunca completo) | — |
| `email` | `email` | `email` | — |
| `balance` | `balance` | *(solo en `GET .../balance`, modo `STANDALONE`)* | *(no viaja)* |
| `linkedCardId` | `linkedCardId` | `linkedCardId` y `mode` | `linkedCardId` |
| `appliedLegs`, `version` | igual | *(internos)* | — |
| `status` | `status` | `status` | `status` |
| `createdAt` / `updatedAt` | igual | igual | `updatedAt` |

| Dominio (`WalletPayment`) | Documento Mongo | REST (`WalletPayment`, visto desde `walletId`) |
|---|---|---|
| `id` | `_id` | `id` |
| `operationId` | `operationId` | `operationId` (solo `SENT`) |
| `senderWalletId` / `receiverWalletId` | igual | `walletId` = el monedero de la ruta; **el otro no se expone** |
| `senderMaskedPhone` / `receiverMaskedPhone` | igual | `counterpartMaskedPhone` = el del **otro** monedero |
| *(calculado)* | — | `direction`: `SENT` si `walletId` es el emisor; `RECEIVED` si es el receptor |
| `amount` / `description` / `status` | igual | igual |
| `failureReason` / `compensationFailure` | `failureReason.*` / `compensationFailure.*` | igual (nunca en `RECEIVED`, que solo ve `COMPLETED`) |
| `route`, `compensationAttempts`, `recoveryAttempts`, `visibleTo`, `version`, `updatedAt` | igual | *(internos)* |
| `requestedAt` / `completedAt` | igual | igual |

Los mapeos se hacen con **MapStruct**. `mode`, `maskedImei`, `direction` y `counterpartMaskedPhone` los calcula el mapper. Los datos personales no se escriben completos en los logs (documento, celular, IMEI y correo se enmascaran).

## 6. Requests: validaciones y errores

Las validaciones de **formato** las declara el `openapi.yaml` (400 `VALIDATION_ERROR`). Las de **negocio** viven en el dominio (422, 404, 409).

| Operación | Campo | Formato (400) | Negocio |
|---|---|---|---|
| `POST /wallets` | `phoneNumber` | Obligatorio, `^9[0-9]{8}$` | 409 `PHONE_ALREADY_REGISTERED` |
| | `imei` | Obligatorio, 15 dígitos | — |
| | `email` | Obligatorio, correo válido, máximo 254 | — |
| | *(token)* | `sub`, `documentType`, `documentNumber` | 422 `DOCUMENT_TYPE_NOT_ALLOWED`; 409 `USER_ALREADY_HAS_WALLET`, `DOCUMENT_ALREADY_REGISTERED` |
| `GET /wallets/me` | — | — | 404 `WALLET_NOT_FOUND` |
| `GET /wallets` | `status` | Opcional, enum | — |
| `GET /wallets/{id}` | `id` | | 404 `WALLET_NOT_FOUND`; 403 |
| `PUT /wallets/{id}` | `imei`, `email` | Obligatorios; formato de arriba | 404; 422 `WALLET_INACTIVE`; 409 `CONCURRENT_MODIFICATION` |
| `DELETE /wallets/{id}` | — | — | 404; 422 `WALLET_HAS_BALANCE`, `WALLET_HAS_CARD`, `WALLET_HAS_PAYMENTS_IN_PROGRESS`. Repetir: 204 |
| `GET /wallets/{id}/balance` | — | — | 404 |
| `PUT /wallets/{id}/debit-card` | `cardId` | Obligatorio | 404; 422 según 3.2 |
| `DELETE /wallets/{id}/debit-card` | — | — | 404 |
| `POST /wallets/{id}/payments` | `operationId` | Obligatorio, 8 a 56 | 409 `OPERATION_ID_REUSED` |
| | `receiverPhone` | Obligatorio, `^9[0-9]{8}$` | 422 `RECEIVER_NOT_FOUND`, `SAME_WALLET` |
| | `amount` | Obligatorio, ≥ 0.01, 2 decimales | 422 `INSUFFICIENT_FUNDS` (emisor independiente) |
| | `description` | Opcional, máximo 200 | — |
| | *(estado)* | — | 422 `WALLET_INACTIVE`, `CARD_NOT_ACTIVE`; 404 `WALLET_NOT_FOUND`; 403 |
| `GET /wallets/{id}/payments` | `direction`, `from`, `to`, `page`, `size` | Opcionales; `size` máximo 100 | 400 `INVALID_DATE_RANGE`; 404 |
| `GET /wallets/{id}/payments/{paymentId}` | — | — | 404 `WALLET_NOT_FOUND`, `WALLET_PAYMENT_NOT_FOUND` |
| `POST /wallet-payment-recovery-runs` | `olderThanMinutes` | Opcional, 0 a 1440 | — |

**Códigos de error del servicio**

| HTTP | Códigos |
|---|---|
| 400 | `VALIDATION_ERROR`, `INVALID_DATE_RANGE` |
| 401 | `UNAUTHORIZED` |
| 403 | `FORBIDDEN` |
| 404 | `WALLET_NOT_FOUND`, `WALLET_PAYMENT_NOT_FOUND` |
| 409 | `USER_ALREADY_HAS_WALLET`, `DOCUMENT_ALREADY_REGISTERED`, `PHONE_ALREADY_REGISTERED`, `OPERATION_ID_REUSED`, `CONCURRENT_MODIFICATION` |
| 422 (propios) | `DOCUMENT_TYPE_NOT_ALLOWED`, `WALLET_INACTIVE`, `WALLET_HAS_BALANCE`, `WALLET_HAS_CARD`, `WALLET_HAS_PAYMENTS_IN_PROGRESS`, `WALLET_ALREADY_LINKED`, `CARD_NOT_FOUND`, `CARD_NOT_ACTIVE`, `CARD_OWNER_MISMATCH`, `CARD_ALREADY_LINKED`, `RECEIVER_NOT_FOUND`, `SAME_WALLET`, `INSUFFICIENT_FUNDS` |
| 422 (de la cuenta, en un pago `FAILED` o `COMPENSATED`) | `INSUFFICIENT_FUNDS`, `MONTHLY_LIMIT_EXCEEDED`, `NOT_ALLOWED_DAY`, `ACCOUNT_INACTIVE`, `ACCOUNT_NOT_FOUND`, `INVALID_AMOUNT`, `INVALID_DATE`, `OPERATION_ID_REUSED` y otros códigos de negocio de la cuenta |

Los códigos de la cuenta y los propios se distinguen por el `paymentId` (solo lo llevan los rechazos de un pago ya creado).

## 7. Propiedades (Config Server)

| Propiedad | Propuesta | Uso |
|---|---|---|
| `server.port` | `8088` | |
| `payment.await-timeout` | `PT1.5S` | Espera del estado terminal en el `POST` de un pago (configurable, pero **siempre menor que el timeout de 2 s del Gateway**; en la ruta 4 son ocho saltos de Kafka y se responderá 202 a menudo) |
| `yanki.recovery.pending-minutes` | `2` | Cuándo un pago en curso se retoma |
| `yanki.recovery.max-attempts` | `10` | Retomas máximas por pago |
| `yanki.recovery.cron` | `0 * * * * *` | Cada minuto |
| `yanki.compensation.max-attempts` | `5` | Pedidos de devolución remota sin respuesta |
| `yanki.applied-legs.max` | `50` | Tamaño de `appliedLegs` |
| `yanki.page.max-size` | `100` | Coincide con el contrato |
| `bank.zone` | `America/Lima` | "Hoy" para el vencimiento de la tarjeta y las fechas `from`/`to` |
| `spring.kafka.*` | | Grupo `yanki-service`, `auto.offset.reset=earliest`, sin `enable.auto.commit`; `linger.ms=0` y `fetch.max.wait.ms` bajo para que los saltos quepan en la espera |
| `security.enabled`, `security.jwt.issuer`, `security.jwt.public-key` | | Ver `auth-service`. Este servicio necesita la identidad del token: fuera de las pruebas, va siempre activa |

## 8. Datos de ejemplo para la demo

Con los clientes y usuarios de `customer-service` y `auth-service`. Ana (A) y Diana (D); Beto (B) para la ruta 4.

| Alias | Documento | Celular | IMEI | Correo | Monedero |
|---|---|---|---|---|---|
| A (Ana) | DNI `12345678` (usuario `CUSTOMER`) | `987654321` | `356938035643809` | `ana.torres@example.com` | Se asocia a su tarjeta de débito (cuenta principal A1) → `LINKED` |
| D (Diana) | DNI `56789012` (usuario `YANKI_USER`) | `943210987` | `490154203237518` | `diana.flores@example.com` | Independiente |
| B (Beto) | DNI `23456789` (usuario `CUSTOMER`) | `976543210` | `353879234252633` | `beto.quispe@example.com` | Se asocia a una tarjeta con B1 → `LINKED` (ruta 4) |

| Paso | Petición | Resultado esperado |
|---|---|---|
| 1 | D: `POST /auth/register` → `POST /auth/login` → `POST /wallets` | 201; el documento es el **del token** (DNI `56789012`) |
| 2 | Usuario de E (RUC): `POST /wallets` | 422 `DOCUMENT_TYPE_NOT_ALLOWED` |
| 3 | A: `POST /wallets` → `PUT /wallets/{id}/debit-card` (su tarjeta) → `GET .../balance` | Modo `LINKED`, sin saldo; `mainAccountId` = A1 |
| 4 | Un monedero con saldo distinto de 0 intenta asociar | 422 `WALLET_HAS_BALANCE` |
| 5 | A → D: `POST /wallets/{A}/payments` `30.00`, celular de D (**ruta 2**) | 201 (o 202 y luego consulta). A1 baja `30.00` (`YANKI_PAYMENT_OUT` en su historial); D queda con saldo `30.00` |
| 6 | D → A: `10.00`, celular de A (**ruta 3**) | Saldo de D `20.00`; A1 sube `10.00` (`YANKI_PAYMENT_IN`) |
| 7 | Con A1 en su tope mensual (10 movimientos de ahorro): D → A | El depósito es rechazado (`MONTHLY_LIMIT_EXCEEDED`): 422 con `paymentStatus = COMPENSATED`; el saldo de D vuelve al inicial (devolución local) |
| 8 | A → B (ambos `LINKED`, **ruta 4**) | Ambos movimientos en los historiales de A1 y B1. Con B1 inactiva: 422 `ACCOUNT_INACTIVE`, `COMPENSATED`, y el retiro de A1 queda `REVERSED` (devolución remota) |
| 9 | Celular inexistente | 422 `RECEIVER_NOT_FOUND` |
| 10 | Pagar más que el saldo de D | 422 `INSUFFICIENT_FUNDS` (sin `paymentId`: se rechaza antes de crear el pago) |
| 11 | Repetir el `operationId` del paso 5 | 200, sin doble movimiento. Con otro monto: 409 `OPERATION_ID_REUSED` |
| 12 | `GET /wallets/{D}/payments?direction=RECEIVED` | Solo el pago `COMPLETED` del paso 5, con el celular enmascarado de A |
| 13 | `GET /wallets/{A}/payments?direction=SENT` | Los enviados de A en cualquier estado |
| 14 | Registrar Yanki con el documento de A | 409 `USER_ALREADY_EXISTS` (en `auth-service`): A usa el usuario que ya tiene |

## 9. Decisiones y pendientes

**Decidido (nuevo en este contrato)**
- **Los índices únicos de documento, celular y usuario son parciales sobre `status = ACTIVE`**: al cerrar un monedero, esa persona puede abrir otro. La ficha decía únicos a secas, lo que la dejaba sin poder volver a registrarse.
- **`yanki.wallet.card.linked/unlinked` llevan el estado completo** del monedero más `cardId`. La ficha y el contrato de Kafka solo tenían `walletId` y `cardId`, pero el tópico `wallet` está **compactado**: ese mensaje habría reemplazado al estado y borrado `status` y `maskedPhone`. Aplicado en `contracts/events/kafka-contract.md` (6.14).
- **`visibleTo`**: el receptor solo ve un pago cuando llega a `COMPLETED`; el emisor lo ve siempre. Permite el historial de ambas direcciones con una sola consulta derivada.
- **`GET .../balance` en modo `LINKED` devuelve el `mainAccountId`, no la cuenta enmascarada**: este servicio no consume el tópico `account`, así que no conoce su número enmascarado.
- **Ida y vuelta de la asociación:** asociar exige saldo 0; al desasociar (manual o por tarjeta cerrada) el saldo sigue en 0. `WALLET_ALREADY_LINKED` cuando ya tiene otra tarjeta (hay que desasociar primero).
- **Asociar también exige** tarjeta no vencida y titular `ACTIVE`; el titular con RUC nunca coincide con el documento del monedero (`CARD_OWNER_MISMATCH`).
- **Un receptor con tarjeta no activa se informa como `RECEIVER_NOT_FOUND`** (no se revela el motivo a un tercero).
- **La devolución local (`refund`) nunca se rechaza**, sin mirar estado ni modo del emisor.
- **`failureReason` guarda siempre el rechazo original**; si la devolución falla, su motivo va aparte en `compensationFailure`. Un rechazo de la reversa es definitivo; solo la falta de respuesta consume intentos (igual que la transferencia).
- **`COMPENSATION_FAILED` responde 202** (según el flujo 03), con el estado en el cuerpo.
- **Repetir un pago en curso lo retoma** (repite el paso local o reenvía el comando remoto) y espera otros 1,5 s; 201 solo para quien lo creó.
- **La idempotencia se evalúa antes** que el estado del monedero: una repetición se responde aunque el monedero ya se haya cerrado. La comparación usa monedero emisor, monto y celular del receptor (no la descripción).
- **`operationId` de 8 a 56 caracteres**: las patas y las comisiones de `transaction-service` suman hasta 8 de sufijo.
- **`updatedAt` se actualiza en cada reintento**, así la recuperación deja pasar N minutos entre reintentos sin un campo aparte.
- **Sin transacciones de Mongo**: monedero y pago se mantienen coherentes con pasos idempotentes por `legId`.
- El token debe traer `sub` y el documento: este servicio no funciona con la seguridad apagada fuera de las pruebas.

**Pendiente**
- **Cortes por `appliedLegs` (50):** si un pago quedara detenido mientras el mismo monedero aplica más de 50 patas, su `legId` podría salir del conjunto y una retoma tardía duplicaría el efecto. Improbable en el demo; `yanki.recovery.max-attempts` acota la ventana.
- **Un `operationId` reutilizado entre endpoints** (por ejemplo el de una transferencia con el de un pago Yanki) hace que `transaction-service` ignore el comando y el pago se quede en `STARTED` hasta agotar los reintentos. Se evita con un UUID nuevo por operación.
- **Carrera al asociar una tarjeta** justo cuando entra un pago al saldo: aceptado; el crédito local se rechaza (`WALLET_INACTIVE`) y se compensa.
- **Cliente `INACTIVE`:** su monedero no se cierra ni se bloquea (decisión del flujo 05).
- **Comisiones de cuenta** en un pago Yanki: quedan solo en el historial de la cuenta.
- **Monto máximo por pago**, espera en memoria con varias instancias y *outbox*: pendientes comunes.
- **Confirmar con el instructor** (definición general, 10.1): RF-60 y Yanki, y el alcance del saldo propio.
- **Verificar `Between` con `Range<Instant>`** (ver `debit-service`, 2.2).
- Nada bloqueante para empezar a programar este servicio.

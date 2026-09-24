# `report-service` — Modelo de datos

> Complementa la ficha (`services/report-service.md`) y el contrato REST (`openapi.yaml`, en esta carpeta) con los **tipos exactos**, el modelo de lectura, los documentos de MongoDB (P3), las fuentes de datos de P2, las reglas de cálculo y los mapeos. Si algo difiere, el `openapi.yaml` manda para la API y `contracts/events/kafka-contract.md` para los eventos.
>
> Es un servicio de **solo lectura**: no tiene aggregates con comportamiento de negocio ni publica eventos.

## 1. Modelo de lectura

### 1.1 `ReportProduct` (foto de un producto)

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `productId` | `String` | ✓ | Id en su servicio dueño (UUID) |
| `productType` | `ProductType` | ✓ | `ACCOUNT`, `CREDIT`, `CREDIT_CARD`, `DEBIT_CARD` |
| `category` | `ProductCategory` | ✓ | Ver 1.4 |
| `customerId` | `String` | ✓ | Dueño |
| `status` | `ProductStatus` | ✓ | Estado en su servicio |
| `attributes` | `ProductAttributes` | ✓ | Cabecera del reporte (todos los campos opcionales) |
| `updatedAt` | `Instant` | ✓ | Fecha del evento aplicado (o `now` en P2) |

`ProductAttributes`: `maskedNumber`, `creditLimit`, `principalAmount`, `dueDate`, `linkedAccountIds`, `mainAccountId`, `expiryDate`. (La ficha decía también `subtype`; no hace falta: el subtipo ya es la `category`.)

### 1.2 `ReportMovement` (un movimiento ya ocurrido)

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `movementId` | `String` | ✓ | `transactionId` (cuentas, créditos, tarjetas de crédito) o `paymentId` (tarjeta de débito) |
| `operationId` | `String?` | – | Solo movimientos que vienen de `transaction.registered`. Sirve para aplicar `transaction.reversed` |
| `productId` / `productType` | `String` / `ProductType` | ✓ | |
| `customerId` | `String` | ✓ | Dueño del producto |
| `type` | `MovementType` | ✓ | |
| `amount` | `Money` | ✓ | > 0; el `type` indica el sentido |
| `resultingBalance` | `Money?` | – | Saldo resultante |
| `status` | `MovementStatus` | ✓ | `COMPLETED` o `REVERSED` (solo se guardan estos dos) |
| `description` | `String?` | – | |
| `transferId` / `parentMovementId` | `String?` | – | Patas de transferencia / comisiones |
| `fee` / `accountId` | `Money?` / `String?` | – | Solo pagos de débito |
| `occurredAt` | `Instant` | ✓ | |

Inmutable, salvo `status` (`COMPLETED` → `REVERSED`).

### 1.3 Value objects y resultados

| VO | Campos | Validación |
|---|---|---|
| `DateRange` | `from: LocalDate`, `to: LocalDate` | `from ≤ to` y `to − from ≤ 365` días (366 inclusivos, `report.max-range-days`). Si no, 400 `INVALID_RANGE`. Se convierte a `[from 00:00, to+1 00:00)` en `bank.zone` |
| `PageRequest` | `page ≥ 0`, `size` 1–100 | |
| `TypeTotal` | `type`, `count`, `totalAmount` | |
| `ReportSummary` | `movementsCount`, `totalsByType`, `totalFees`, `openingBalance?`, `closingBalance?` | Inmutable |
| `ProductReport`, `LastMovementsReport`, `CustomerCardsReport`, `CategoryReport` | Ver `openapi.yaml` | Inmutables; todos llevan `generatedAt` |

### 1.4 Enums

| Enum | Valores |
|---|---|
| `ProductType` | `ACCOUNT`, `CREDIT`, `CREDIT_CARD`, `DEBIT_CARD` |
| `ProductCategory` | `SAVINGS`, `CHECKING`, `FIXED_TERM`, `PERSONAL_CREDIT`, `BUSINESS_CREDIT`, `CREDIT_CARD`, `DEBIT_CARD` |
| `ProductStatus` | `ACTIVE`, `INACTIVE`, `OVERDUE`, `PAID`, `CLOSED` |
| `MovementType` | Los 11 de `transaction-service` |
| `MovementStatus` | `COMPLETED`, `REVERSED` |
| Ruta `productType` | `accounts` → `ACCOUNT`, `credits` → `CREDIT`, `credit-cards` → `CREDIT_CARD`, `debit-cards` → `DEBIT_CARD` |

**Categoría de cada producto**

| Origen | Categoría |
|---|---|
| Cuenta | Su `type`: `SAVINGS`, `CHECKING`, `FIXED_TERM` |
| Crédito | `ownerType = PERSONAL` → `PERSONAL_CREDIT`; `BUSINESS` → `BUSINESS_CREDIT` |
| Tarjeta de crédito | `CREDIT_CARD` |
| Tarjeta de débito | `DEBIT_CARD` |

## 2. Reglas de cálculo

Todas son **puras** y usan Streams. Reciben los movimientos ya filtrados por `COMPLETED`.

### 2.1 Orden

Más reciente primero: `occurredAt` descendente y, en empate, `movementId` descendente (determinista). Una comisión y su movimiento padre comparten `occurredAt`; el orden entre ellos es el del id, no el de causa y efecto.

### 2.2 Resumen (`ProductReportCalculator`)

Entrada: **todos** los movimientos `COMPLETED` del intervalo (no solo la página) y el movimiento anterior al intervalo.

| Campo | Cálculo |
|---|---|
| `movementsCount` | Cantidad de movimientos (los `FEE` cuentan) |
| `totalsByType` | Agrupa por `type`: `count` y suma de `amount`; ordenado por `type` |
| `totalFees` | Suma de `amount` de los `FEE` (`0.00` si no hay) |
| `openingBalance` | `resultingBalance` del **último movimiento `COMPLETED` anterior a `from`** (por orden 2.1). Se omite si no existe |
| `closingBalance` | `resultingBalance` del **último movimiento del intervalo** (el primero de la lista ordenada). Se omite si el intervalo no tiene movimientos |

Limitaciones a tener presentes:
- La apertura de una cuenta con monto inicial **no es un movimiento**, así que una cuenta sin movimientos previos no tiene saldo inicial.
- Un movimiento `REVERSED` no cuenta: el saldo inicial y final salen del último movimiento `COMPLETED`; los saldos posteriores ya reflejan la reversa.
- En una tarjeta de débito, `resultingBalance` es el saldo de la cuenta que pagó, y con varias cuentas asociadas los saldos de pagos con cuentas distintas no son una serie continua.

### 2.3 Últimos movimientos (`LastMovementsSelector`)

Los primeros `limit` de la lista ordenada (por defecto 10, entre 1 y 50). Menos movimientos que `limit` → los que haya.

### 2.4 Reporte por categoría (`CategoryReportCalculator`, P3)

1. Productos de la categoría (`findByCategory`, cualquier estado) → `productsCount`.
2. Movimientos `COMPLETED` de esos `productId` en el intervalo (consulta derivada con `In` y rango).
3. Suma `movementsCount`, `totalsByType` y `totalFees` sobre todos; `productsWithMovements` = `productId` distintos.
4. Sin saldos inicial y final.

### 2.5 Tope de movimientos (`RANGE_TOO_LARGE`)

Si el intervalo tiene más de `report.max-movements` movimientos (propuesta **5000**), se responde 422 `RANGE_TOO_LARGE`. Se comprueba **antes** de traerlos (con `countBy…` en P3; con `totalElements` de la primera página en P2).

## 3. Fuente de datos P2 (`report.source=rest`)

Sin base de datos. Todas las llamadas con circuit breaker y time limiter de 2 s; si una falla, 503 `SERVICE_UNAVAILABLE` (no se entregan reportes incompletos).

### 3.1 Producto (`ProductQueryPort`)

| Ruta del reporte | Llamada | Campos que se toman |
|---|---|---|
| `accounts` | `account-service` `GET /accounts/{id}` | `id`, `customerId`, `type` (→ categoría), `status`, `accountNumber` (→ `maskedNumber`) |
| `credits` | `credit-service` `GET /credits/{id}` | `customerId`, `ownerType` (→ categoría), `status`, `principalAmount`, `dueDate` |
| `credit-cards` | `credit-service` `GET /credit-cards/{id}` | `customerId`, `status`, `maskedNumber`, `creditLimit` |
| `debit-cards` | — | **501 `NOT_AVAILABLE`** |

404 del servicio dueño → 404 `PRODUCT_NOT_FOUND`. Con `report.source=rest` la llamada usa una credencial de servicio (con seguridad activa; en P2 la seguridad está desactivada, así que no hay problema de permisos).

`GET /reports/customers/{id}/cards/last-movements` en P2: `credit-service` `GET /credit-cards?customerId={id}` (todas las tarjetas del cliente; sin filtro de estado). `debitCards` = `[]` y `debitCardsIncluded = false`.

### 3.2 Movimientos (`MovementQueryPort`)

Siempre a `transaction-service` `GET /products/{productId}/transactions` con `status=COMPLETED` (así se excluyen los `REVERSED`, `FAILED`, `PENDING` y `DISCARDED`):

| Necesidad | Parámetros |
|---|---|
| Página de movimientos del intervalo | `from`, `to`, `page`, `size` (los del reporte) |
| Todos los del intervalo, para el resumen | `from`, `to`, `page = 0..n`, `size = 100`, hasta cubrir `totalElements` (comprobando el tope de 2.5 con la primera página) |
| Movimiento anterior al intervalo (saldo inicial) | `to = from − 1 día`, `size = 1` (sin `from`: todo el historial anterior) |
| Últimos N | `size = N`, `page = 0` |

Si el `openapi.yaml` de `transaction-service` cambia estos parámetros, este documento se actualiza junto con él. Con la página del reporte se **reutilizan** las que ya se descargaron para el resumen cuando coinciden en `size`; si no, se pide aparte.

**Alcance de `CUSTOMER`:** se compara el `customerId` de la cabecera del producto con el del token (403 si difiere). No hace falta mirar los movimientos.

## 4. Fuente de datos P3 (`report.source=readmodel`)

Dinero como **`Decimal128`**; `Instant` como `date`; `LocalDate` y `YearMonth` como texto (ver `contracts/README.md`).

### 4.1 `report_products` — `ReportProductDocument`

| Campo Mongo | Tipo | Req. | Notas |
|---|---|---|---|
| `_id` | string | ✓ | El `productId` (UUID v4; único entre tipos, así que no hace falta la pareja como clave) |
| `productType` | string | ✓ | |
| `category` | string | ✓ | |
| `customerId` | string | ✓ | |
| `status` | string | ✓ | |
| `attributes.maskedNumber` | string | – | |
| `attributes.creditLimit` | decimal128 | – | |
| `attributes.principalAmount` | decimal128 | – | |
| `attributes.dueDate` | string | – | |
| `attributes.linkedAccountIds` | array de string | – | |
| `attributes.mainAccountId` | string | – | |
| `attributes.expiryDate` | string | – | `yyyy-MM` |
| `updatedAt` | date | ✓ | `updatedAt` del evento aplicado |

Índices: `ix_rp_customer_type` (`customerId` (1), `productType` (1)); `ix_rp_category_status` (`category` (1), `status` (1)).

### 4.2 `report_movements` — `ReportMovementDocument`

| Campo Mongo | Tipo | Req. | Notas |
|---|---|---|---|
| `_id` | string | ✓ | `movementId` |
| `operationId` | string | – | |
| `productId` / `productType` | string | ✓ | |
| `customerId` | string | ✓ | |
| `type` | string | ✓ | |
| `amount` | decimal128 | ✓ | |
| `resultingBalance` | decimal128 | – | |
| `status` | string | ✓ | `COMPLETED` / `REVERSED` |
| `description` | string | – | |
| `transferId` / `parentMovementId` | string | – | |
| `fee` / `accountId` | decimal128 / string | – | Solo débito |
| `occurredAt` | date | ✓ | |

| Índice | Campos | Opciones | Para qué |
|---|---|---|---|
| `ix_rm_product_status_occurred` | `productId` (1), `status` (1), `occurredAt` (-1) | — | Reporte, últimos N, saldo inicial |
| `uk_rm_operation_id` | `operationId` (1) | **Único parcial**: `operationId` existe | Aplicar `transaction.reversed` |
| `ix_rm_parent` | `parentMovementId` (1) | Parcial: existe | Marcar como revertidas las comisiones de un movimiento revertido |

### 4.3 Proyección de eventos

Consumidores idempotentes; commit después de guardar (contrato de Kafka, sección 7).

**Productos** (`ReportProduct`): se aplica el evento **solo si su `updatedAt` es ≥ al guardado** (o no hay documento).

| Tópico | Tipo | `category` | `attributes` |
|---|---|---|---|
| `account` | `account.created/updated/deleted` | `type` del evento | `maskedNumber` |
| `credit` | `credit.created/updated/closed` | `ownerType` → `PERSONAL_CREDIT` / `BUSINESS_CREDIT` | `principalAmount`, `dueDate` |
| `credit-card` | `credit.card.created/updated/closed` | `CREDIT_CARD` | `maskedNumber`, `creditLimit` |
| `debit-card` | `debit.card.created/updated/closed` | `DEBIT_CARD` | `maskedNumber`, `linkedAccountIds` (= `accountIds`), `mainAccountId`, `expiryDate` |

**Movimientos** (`ReportMovement`):

| Tópico | Tipo | Efecto |
|---|---|---|
| `transaction` | `transaction.registered` | Inserta si **no existe** un movimiento con ese `_id` (= `transactionId`); si existe, se ignora (es inmutable y puede ya estar `REVERSED`). Guarda `status = COMPLETED`. Cubre cuentas, créditos, tarjetas de crédito y las comisiones `FEE` (con `parentMovementId`) |
| `transaction` | `transaction.reversed` | Busca por `operationId = originalOperationId` (`uk_rm_operation_id`) y pasa a `REVERSED`; además pasa a `REVERSED` los movimientos con `parentMovementId` = el `_id` encontrado (sus comisiones, que la cuenta también devolvió). Si no encuentra el movimiento, lo registra en el log y lo ignora |
| `debit.payment` | `debit.payment.completed` | Inserta un movimiento `DEBIT_PAYMENT` con `productType = DEBIT_CARD`, `productId = cardId`, `_id = paymentId`, `accountId`, `fee` y `resultingBalance` |

No se consumen `transaction.failed`, `transaction.reversal.failed`, `debit.payment.failed`, ni los eventos de Yanki. Los tipos desconocidos se ignoran.

**Orden:** `transaction.registered` y `transaction.reversed` de un mismo producto llevan la misma clave (`productId`) y por tanto van en orden dentro de la partición. Un movimiento que llega antes que su producto es válido: el reporte responde 404 `PRODUCT_NOT_FOUND` hasta que llegue el evento del producto (consistencia eventual).

### 4.4 Consultas (sin `@Query`)

| Necesidad | Método (derivado) |
|---|---|
| Producto | `findById`; `findByCustomerId` (tarjetas: filtra por tipo en memoria o `findByCustomerIdAndProductType`); `findByCategory` |
| Movimientos del intervalo | `findByProductIdAndStatusAndOccurredAtGreaterThanEqualAndOccurredAtLessThan(id, COMPLETED, from, to, sort)`; su `countBy…` |
| Saldo inicial | `findFirstByProductIdAndStatusAndOccurredAtLessThanOrderByOccurredAtDescIdDesc(id, COMPLETED, from)` |
| Últimos N | `findByProductIdAndStatus(id, COMPLETED, PageRequest.of(0, n, Sort desc por occurredAt y _id))` |
| Categoría | `findByProductIdInAndStatusAndOccurredAtGreaterThanEqualAnd…LessThan` y su `countBy…` |
| Aplicar reversa | `findByOperationId`; `findByParentMovementId` |

La página del reporte es una porción en memoria de la lista ya ordenada (el tope de 5000 lo permite).

## 5. Mapeos

| Resultado / REST | Origen P2 (`transaction-service` `Transaction`) | Origen P3 (evento) |
|---|---|---|
| `movementId` | `id` | `transaction.registered.transactionId` / `debit.payment.completed.paymentId` |
| `type`, `amount`, `resultingBalance`, `description`, `transferId`, `occurredAt` | igual | igual |
| `parentMovementId` | `parentTransactionId` | `parentTransactionId` |
| `fee`, `accountId` | *(no aplica)* | `debit.payment.completed` |
| `ProductHeader.status` | `status` del producto | `status` del evento |
| `ProductHeader.maskedNumber` | `accountNumber` enmascarado (`**** ` + últimos 4) o `maskedNumber` | `maskedNumber` |

MapStruct para los mapeos; los cálculos no usan MapStruct.

## 6. Requests: validaciones y errores

| Operación | Parámetro | Formato (400) | Negocio |
|---|---|---|---|
| `GET /reports/products/{productType}/{productId}` | `productType` | Uno de los 4 valores | 501 `NOT_AVAILABLE` (`debit-cards` en P2) |
| | `productId` | Obligatorio | 404 `PRODUCT_NOT_FOUND`; 403 producto ajeno (`CUSTOMER`) |
| | `from`, `to` | **Obligatorios**, `yyyy-MM-dd` | 400 `INVALID_RANGE` (`from > to` o > 366 días); 422 `RANGE_TOO_LARGE` |
| | `page`, `size` | `page ≥ 0`, `size` 1–100 | — |
| `…/last-movements` | `limit` | 1–50 (por defecto 10) | 404; 403; 501 |
| `GET /reports/customers/{customerId}/cards/last-movements` | `customerId` | Obligatorio | 403 si un `CUSTOMER` pide otro cliente. Sin tarjetas: listas vacías |
| `GET /reports/product-categories/{category}` | `category` | Uno de los 7 valores | 501 `NOT_AVAILABLE` (P2) |
| | `from`, `to` | Obligatorios | 400 `INVALID_RANGE`; 422 `RANGE_TOO_LARGE` |

**Códigos de error del servicio**

| Estado | Códigos |
|---|---|
| 400 | `VALIDATION_ERROR`, `INVALID_RANGE` |
| 401 / 403 | `UNAUTHORIZED`, `FORBIDDEN` |
| 404 | `PRODUCT_NOT_FOUND` |
| 422 | `RANGE_TOO_LARGE` |
| 501 | `NOT_AVAILABLE` |
| 503 | `SERVICE_UNAVAILABLE` (P2) |

## 7. Propiedades (Config Server)

| Propiedad | Propuesta | Uso |
|---|---|---|
| `report.source` | `rest` (P2) / `readmodel` (P3) | Elige los adaptadores |
| `report.max-range-days` | `366` | Intervalo máximo |
| `report.max-movements` | `5000` | Tope por reporte |
| `report.page.max-size` | `100` | Coincide con el contrato |
| `report.last-movements.max` | `50` | Máximo de `limit` |
| `bank.zone` | `America/Lima` | Zona de las fechas `from`/`to` |
| `resilience4j.*` | 2 s, umbrales | Llamadas a las fuentes (P2) |

## 8. Datos de ejemplo para la demo

Con la cuenta de ahorro A1 de `contracts/account-service/data-model.md` y septiembre de 2026 (movimiento anterior: `2026-08-20 DEPOSIT 1000.00`, saldo 1000.00).

`GET /reports/products/accounts/{A1}?from=2026-09-01&to=2026-09-30&size=5`:

```json
{
  "product": { "productId": "3f1c9a7e-5b2d-4e8a-9c6f-1a2b3c4d5e6f", "productType": "ACCOUNT", "category": "SAVINGS",
               "customerId": "c0a1b2c3-d4e5-4f60-8a9b-0c1d2e3f4a5b", "status": "ACTIVE", "maskedNumber": "**** 4871" },
  "period": { "from": "2026-09-01", "to": "2026-09-30" },
  "summary": {
    "movementsCount": 7,
    "totalsByType": [
      { "type": "DEPOSIT",      "count": 2, "totalAmount": 350.00 },
      { "type": "FEE",          "count": 1, "totalAmount": 2.00 },
      { "type": "TRANSFER_IN",  "count": 1, "totalAmount": 50.00 },
      { "type": "TRANSFER_OUT", "count": 1, "totalAmount": 100.00 },
      { "type": "WITHDRAWAL",   "count": 2, "totalAmount": 298.00 }
    ],
    "totalFees": 2.00,
    "openingBalance": 1000.00,
    "closingBalance": 1000.00
  },
  "movements": {
    "page": 0, "size": 5, "totalElements": 7, "totalPages": 2,
    "content": [
      { "movementId": "…", "type": "FEE",          "amount": 2.00,   "resultingBalance": 1000.00, "parentMovementId": "…", "occurredAt": "2026-09-24T15:24:00Z" },
      { "movementId": "…", "type": "WITHDRAWAL",   "amount": 148.00, "resultingBalance": 1000.00, "occurredAt": "2026-09-24T15:24:00Z" },
      { "movementId": "…", "type": "TRANSFER_IN",  "amount": 50.00,  "resultingBalance": 1150.00, "occurredAt": "2026-09-15T16:02:00Z" },
      { "movementId": "…", "type": "TRANSFER_OUT", "amount": 100.00, "resultingBalance": 1100.00, "occurredAt": "2026-09-10T14:10:00Z" },
      { "movementId": "…", "type": "DEPOSIT",      "amount": 100.00, "resultingBalance": 1200.00, "occurredAt": "2026-09-08T13:45:00Z" }
    ]
  },
  "generatedAt": "2026-09-24T15:30:00Z"
}
```

Otros escenarios:
- **Últimos 10 de una tarjeta de crédito:** `GET /reports/products/credit-cards/{CC-A}/last-movements` tras consumos y pagos → hasta 10, más recientes primero.
- **Tarjetas de un cliente:** `GET /reports/customers/{A}/cards/last-movements` → `creditCards` con CC-A y sus movimientos; en P3, `debitCards` con la tarjeta de A y sus pagos.
- **Movimiento revertido:** la transferencia de A1 a B2 compensada (paso 14 del guion) **no** aparece en el reporte: ni el `TRANSFER_OUT` revertido ni su comisión; tampoco el `TRANSFER_IN` fallido.
- **Categoría (P3):** `GET /reports/product-categories/SAVINGS?from=…&to=…` → suma de todas las cuentas de ahorro.
- **Errores:** `to < from` → 400 `INVALID_RANGE`; intervalo de 400 días → 400 `INVALID_RANGE`; `debit-cards` en P2 → 501 `NOT_AVAILABLE`.

## 9. Decisiones y pendientes

**Decidido (nuevo en este contrato)**
- **`transaction-service` publica `transaction.reversed` para toda reversa** (compensación de una transferencia incluida, no solo las pedidas por `yanki-service`) y, al revertir un movimiento, **revierte también sus comisiones `FEE` enlazadas**. Sin esto, una transferencia compensada seguiría en los reportes y su comisión (que la cuenta devolvió) también. Ya está aplicado en el contrato de `transaction-service` y en el de Kafka.
- La proyección enlaza el movimiento revertido por `operationId` y sus comisiones por `parentMovementId`.
- `report_products` usa el `productId` como `_id` (no hay índice único de la pareja tipo-id).
- El resumen y los saldos inicial y final salen **solo de movimientos**: una cuenta sin movimientos previos no tiene saldo inicial (la apertura no es un movimiento).
- El reporte por categoría **no** incluye saldos inicial y final.
- Sin 404 para `customerId` en el reporte de tarjetas: el servicio no conoce clientes; un cliente sin tarjetas devuelve listas vacías.
- En P2, `debit-cards` y el reporte por categoría responden **501** `NOT_AVAILABLE`; el reporte de tarjetas de un cliente responde con `debitCardsIncluded = false`.
- Los códigos de indisponibilidad usan `SERVICE_UNAVAILABLE`, igual que el resto de servicios (la ficha decía `SOURCE_UNAVAILABLE`).
- `INVALID_RANGE` (400) cubre tanto `from > to` como más de 366 días; `RANGE_TOO_LARGE` (422) es por cantidad de movimientos.
- El orden de los movimientos empata por `movementId` descendente.

**Pendiente**
- **Verificar al implementar las consultas por rango de fechas:** el patrón `…GreaterThanEqualAnd…LessThan` usa dos condiciones sobre el mismo campo; Spring Data MongoDB puede rechazarlo (`InvalidMongoDbApiUsageException`, "you can't add a second expression"). Si pasa, usar `Between` con `Range<Instant>` (cerrado en el inicio, abierto en el fin) o filtrar el rango en memoria. No cambia el contrato REST.
- Nada bloqueante para empezar a programar este servicio.
- **Confirmar con el instructor** el alcance de "reporte por producto del banco" (un producto concreto o una categoría): se ofrecen ambos.
- Reconstruir el read model si se pierde: reprocesar los tópicos compactados desde el inicio; los tópicos de eventos (`transaction`, `debit.payment`) dependen de la retención de 7 días, así que el historial anterior se perdería. Para el demo se acepta.
- Exportar a CSV o PDF: opcional.

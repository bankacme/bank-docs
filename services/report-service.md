# `report-service`

## 1. Resumen

| Campo | Valor |
|---|---|
| Propósito | Generar reportes de solo lectura: reporte completo de un producto en un intervalo de fechas y últimos 10 movimientos de tarjetas de débito y crédito |
| Bounded context | Reportes y consultas |
| Fase | P2 (consulta por REST) → P3 (read model alimentado por eventos, reporte por categoría y de débito) |
| Puerto | 8085 |
| Base de datos | P2: ninguna. P3: MongoDB `report_products` y `report_movements` (read model) |
| Depende de | P2: `account-service`, `credit-service` y `transaction-service` por REST. P3: eventos de `account`, `credit`, `debit` y `transaction` |

## 2. Responsabilidades

**Hace:**
- **Reporte completo de un producto** (cuenta, crédito, tarjeta de crédito o de débito) en un intervalo dado por el usuario: datos del producto, resumen y movimientos.
- **Últimos 10 movimientos** de una tarjeta de crédito o de débito, y el conjunto de ambos por cliente.
- **Reporte por categoría de producto** (por ejemplo, todas las cuentas de ahorro del banco) en un intervalo. Desde P3.
- Mantener el read model a partir de eventos (P3).

**No hace:**
- Modificar datos de otros servicios ni publicar eventos: es solo lectura.
- Guardar el historial original (`transaction-service`) ni los datos maestros de productos.
- Generar archivos PDF o Excel (los reportes son JSON; la exportación es opcional).
- Analítica avanzada, gráficos o dashboards.

## 3. Modelo de dominio (DDD)

Al ser un servicio de consulta, el dominio es liviano: un modelo de lectura y **cálculos puros** sobre él.

### 3.1 Aggregates y entidades
| Elemento | Tipo | Descripción |
|---|---|---|
| `ReportProduct` | Aggregate root (modelo de lectura) | Foto de un producto: identidad, dueño, categoría, estado y datos de cabecera |
| `ReportMovement` | Aggregate root (registro inmutable) | Un movimiento ya ocurrido de un producto |

Atributos de `ReportProduct`: `productId`, `productType`, `category`, `customerId`, `status`, `attributes`, `updatedAt`.
Atributos de `ReportMovement`: `movementId`, `operationId`, `productId`, `productType`, `customerId`, `type`, `amount`, `resultingBalance`, `status` (`COMPLETED`/`REVERSED`), `description`, `transferId`, `parentMovementId`, `fee` y `accountId` (solo débito), `occurredAt`.

Ninguno tiene comportamiento de negocio; solo se **crean o reemplazan** (`upsert`) al procesar eventos.

### 3.2 Value objects
| VO | Campos | Validaciones |
|---|---|---|
| `ProductId`, `CustomerId`, `MovementId` | `value` | No vacío |
| `Money` | `amount` (BigDecimal, 2 decimales), `currency` | No nulo. Solo `PEN` |
| `DateRange` | `from`, `to` | `from` ≤ `to`; máximo 366 días (configurable) |
| `PageRequest` | `page`, `size` | `size` entre 1 y 100 |
| `ProductAttributes` | `maskedNumber`, `creditLimit`, `principalAmount`, `dueDate`, `linkedAccountIds`, `mainAccountId`, `expiryDate` | Solo para la cabecera del reporte; todos opcionales según el producto |
| `ReportSummary` | `movementsCount`, `totalsByType`, `totalFees`, `openingBalance`, `closingBalance` | Resultado inmutable calculado |
| `ProductReport` | cabecera + `period` + `summary` + página de movimientos + `generatedAt` | Resultado inmutable |
| `LastMovementsReport` | producto + hasta 10 movimientos + `generatedAt` | Resultado inmutable |
| `CustomerCardsReport` | tarjetas de crédito y de débito del cliente, cada una con sus últimos 10 movimientos | Resultado inmutable |
| `CategoryReport` | categoría + `period` + cantidad de productos + resumen agregado | Resultado inmutable |

### 3.3 Enums
| Enum | Valores |
|---|---|
| `ProductType` | `ACCOUNT`, `CREDIT`, `CREDIT_CARD`, `DEBIT_CARD` |
| `ProductCategory` | `SAVINGS`, `CHECKING`, `FIXED_TERM`, `PERSONAL_CREDIT`, `BUSINESS_CREDIT`, `CREDIT_CARD`, `DEBIT_CARD` |
| `MovementType` | `DEPOSIT`, `WITHDRAWAL`, `TRANSFER_OUT`, `TRANSFER_IN`, `FEE`, `CREDIT_PAYMENT`, `CARD_PAYMENT`, `CARD_CHARGE`, `DEBIT_PAYMENT`, `YANKI_PAYMENT_OUT`, `YANKI_PAYMENT_IN` |

### 3.4 Reglas de negocio e invariantes
| # | Regla | Dónde se aplica |
|---|---|---|
| 1 | El intervalo es obligatorio en el reporte de producto; `from` ≤ `to` y no supera 366 días | `DateRange` |
| 2 | Solo se listan y suman movimientos `COMPLETED`; los fallidos, pendientes y descartados no aparecen, y los revertidos tampoco (su efecto neto es cero) | Caso de uso / calculadoras |
| 3 | Los movimientos van del más reciente al más antiguo (desempate por id) | Consultas y calculadoras |
| 4 | El **resumen** se calcula sobre **todo** el intervalo, independiente de la página que se pida | `ProductReportCalculator` |
| 5 | El resumen incluye: cantidad, total por tipo de movimiento, total de comisiones, saldo inicial (saldo resultante del último movimiento anterior al intervalo) y saldo final (saldo resultante del último movimiento del intervalo). Si no hay datos, son nulos | `ProductReportCalculator` |
| 6 | "Saldo resultante" significa saldo de la cuenta, saldo pendiente del crédito o monto usado de la tarjeta, según el producto | Documentado en el contrato |
| 7 | Últimos movimientos: **10 por defecto**; `limit` opcional entre 1 y 50 | `LastMovementsSelector` |
| 8 | Movimientos de **tarjeta de crédito** = consumos y pagos (`CARD_CHARGE`, `CARD_PAYMENT`). Movimientos de **tarjeta de débito** = pagos hechos con la tarjeta (`debit-service`) | Fuentes de datos |
| 9 | `CUSTOMER` solo pide reportes de sus propios productos o de sí mismo; `ADMIN`/`TELLER` de cualquiera. El reporte por categoría es solo para `ADMIN`/`TELLER` | Caso de uso (por `customerId` del producto) |
| 10 | El producto debe existir; uno cerrado o dado de baja se puede reportar | Caso de uso |
| 11 | Un intervalo con demasiados movimientos se rechaza en lugar de devolver un reporte parcial | Caso de uso (`RANGE_TOO_LARGE`) |
| 12 | En P2, si una fuente no responde, el reporte falla (503): no se entregan reportes incompletos | Adaptadores REST |
| 13 | El read model (P3) se actualiza de forma **idempotente**: un evento repetido no duplica y uno desordenado no rompe (gana el más reciente por `updatedAt`) | Casos de uso de proyección |
| 14 | Montos en `PEN` con dos decimales | `Money` |

### 3.5 Domain services
| Servicio | Responsabilidad |
|---|---|
| `ProductReportCalculator` | Calcula el `ReportSummary` de una lista de movimientos con **Streams** (agrupar por tipo, sumar, saldos inicial y final) |
| `LastMovementsSelector` | Ordena y toma los últimos N movimientos |
| `CategoryReportCalculator` | Suma los resúmenes de los productos de una categoría (P3) |

### 3.6 Eventos de dominio
No aplica. El servicio no publica eventos.

## 4. Casos de uso y puertos

### 4.1 Puertos de entrada (casos de uso)
| Caso de uso | Retorno | Descripción |
|---|---|---|
| `GenerateProductReportUseCase` | `Single<ProductReport>` | Reporte completo de un producto en un intervalo |
| `GetLastMovementsUseCase` | `Single<LastMovementsReport>` | Últimos N movimientos de un producto (10 por defecto) |
| `GetCustomerCardsReportUseCase` | `Single<CustomerCardsReport>` | Últimos 10 movimientos de cada tarjeta de crédito y débito de un cliente |
| `GenerateCategoryReportUseCase` | `Single<CategoryReport>` | Reporte agregado por categoría (P3) |
| `ProjectProductEventUseCase` | `Completable` | Actualiza `report_products` desde un evento (P3) |
| `ProjectMovementEventUseCase` | `Completable` | Actualiza `report_movements` desde un evento (P3) |

### 4.2 Puertos de salida
| Puerto | Métodos | Adaptador (fase) |
|---|---|---|
| `ProductQueryPort` | `findById(type, id)`, `findByCustomer(customerId, types)`, `findByCategory(category)` | REST + circuit breaker (P2) → Mongo (P3) |
| `MovementQueryPort` | `findByProduct(type, id, range, page)`, `findLast(type, id, limit)`, `findByProducts(ids, range)` | REST + circuit breaker (P2) → Mongo (P3) |
| `ReadModelWritePort` | `upsertProduct`, `upsertMovement` | Mongo (P3) |

Un solo parámetro, `report.source` (`rest` en P2, `readmodel` en P3), elige qué adaptadores se activan; los casos de uso y el dominio no cambian.

## 5. API (contrato OpenAPI)

Base: `/api/v1`. Roles aplican cuando `security.enabled=true`. `productType` en la ruta: `accounts`, `credits`, `credit-cards` o `debit-cards`.

| Método | Ruta | Descripción | Roles | Éxito | Errores |
|---|---|---|---|---|---|
| `GET` | `/reports/products/{productType}/{productId}` | Reporte completo (`from`, `to` obligatorios; `page`, `size`) | `ADMIN`, `TELLER`, `CUSTOMER` (suyo) | 200 | 400, 404, 422, 503 |
| `GET` | `/reports/products/{productType}/{productId}/last-movements` | Últimos movimientos (`limit`, 10 por defecto) | `ADMIN`, `TELLER`, `CUSTOMER` (suyo) | 200 | 400, 404, 503 |
| `GET` | `/reports/customers/{customerId}/cards/last-movements` | Últimos 10 movimientos de cada tarjeta de crédito y de débito del cliente | `ADMIN`, `TELLER`, `CUSTOMER` (para sí) | 200 | 404, 503 |
| `GET` | `/reports/product-categories/{category}` | Reporte agregado por categoría (`from`, `to`). **Desde P3** | `ADMIN`, `TELLER` | 200 | 400, 422 |

Notas:
- Toda respuesta incluye `generatedAt`. En P3 los datos reflejan lo procesado hasta ese momento (consistencia eventual).
- `debit-cards` en cualquier reporte funciona **desde P3**, cuando existen las tarjetas de débito.
- Códigos: 400 `INVALID_RANGE` (`from > to` o más de 366 días), 404 `PRODUCT_NOT_FOUND`, 422 `RANGE_TOO_LARGE` (demasiados movimientos), 501 `NOT_AVAILABLE` (tarjeta de débito y reporte por categoría en P2), 503 `SERVICE_UNAVAILABLE` (P2). Cuerpo estándar: `{ timestamp, status, code, message, path }`.
- El reporte de tarjetas de un cliente no tiene 404 (un cliente sin tarjetas devuelve listas vacías) e informa `debitCardsIncluded` (`false` en P2).
- **Contrato exacto** (campos, tipos, ejemplos, fuentes de datos de P2 y P3, reglas de cálculo, documentos Mongo y datos de demo): `contracts/report-service/openapi.yaml` y `data-model.md`. Si difieren de esta ficha, el contrato manda.
- Este servicio no tiene CRUD: es solo consulta, no hay entidades de negocio propias que crear o eliminar.

## 6. Persistencia y caché
- **P2:** sin base de datos; todo se consulta por REST y se calcula en memoria con Streams. Para el resumen se recorren las páginas del historial (100 por página) hasta cubrir el intervalo, con un tope de movimientos (`RANGE_TOO_LARGE`).
- **P3 `report_products`:** un documento por producto (`_id` = `productId`). Índices: (`customerId`, `productType`); (`category`, `status`).
- **P3 `report_movements`:** un documento por movimiento. Índices: (`productId`, `status`, `occurredAt` desc); único parcial en `operationId`; parcial en `parentMovementId` (para revertir comisiones). El reporte por categoría busca primero los productos de la categoría y luego sus movimientos con una consulta derivada `In` + rango, sin `@Query`.
- **Caché:** no aplica.

## 7. Mensajería (Kafka) — P3
| Publica | Consume |
|---|---|
| Nada | `account.created/updated/deleted`, `credit.created/updated/closed`, `credit.card.created/updated/closed`, `debit.card.created/updated/closed`, `transaction.registered`, `transaction.reversed` (marca el movimiento original y sus comisiones como revertidos y los excluye de los reportes), `debit.payment.completed` |

- Los movimientos de cuentas, créditos y tarjetas de crédito llegan por `transaction.registered`; los de la tarjeta de débito, por `debit.payment.completed` (guardados con tipo `DEBIT_CARD`).
- No se consumen `transaction.failed` ni los eventos de Yanki.

**Rol en sagas:** ninguno.

## 8. Filesystem

```
report-service/
├── pom.xml
├── Dockerfile
├── checkstyle.xml
├── README.md
├── docs/
│   ├── sequence/
│   └── uml/
└── src/
    ├── main/
    │   ├── java/com/bank/report/
    │   │   ├── ReportServiceApplication.java
    │   │   ├── domain/
    │   │   │   ├── model/
    │   │   │   │   ├── ReportProduct.java, ReportMovement.java   (modelo de lectura)
    │   │   │   │   ├── ProductId.java, CustomerId.java, MovementId.java, Money.java
    │   │   │   │   ├── DateRange.java, PageRequest.java, ProductAttributes.java
    │   │   │   │   ├── ReportSummary.java, ProductReport.java, LastMovementsReport.java
    │   │   │   │   ├── CustomerCardsReport.java, CategoryReport.java
    │   │   │   │   └── ProductType.java, ProductCategory.java, MovementType.java
    │   │   │   ├── service/
    │   │   │   │   ├── ProductReportCalculator.java
    │   │   │   │   ├── LastMovementsSelector.java
    │   │   │   │   └── CategoryReportCalculator.java
    │   │   │   └── exception/                                  (ProductNotFoundException, InvalidRangeException, RangeTooLargeException, SourceUnavailableException)
    │   │   ├── application/
    │   │   │   ├── query/                                      (ProductReportQuery, LastMovementsQuery, ...)
    │   │   │   ├── view/                                       (PageView)
    │   │   │   ├── port/
    │   │   │   │   ├── in/                                     (casos de uso)
    │   │   │   │   └── out/                                    (3 puertos)
    │   │   │   └── usecase/
    │   │   └── infrastructure/
    │   │       ├── adapter/
    │   │       │   ├── in/rest/                                (ReportController, GlobalExceptionHandler)
    │   │       │   ├── in/kafka/                               (ProductEventsConsumer, MovementEventsConsumer; P3)
    │   │       │   ├── out/rest/                               (ProductRestAdapter, MovementRestAdapter; P2, activo con report.source=rest)
    │   │       │   └── out/persistence/                        (documentos, ReadModelWriteAdapter, ProductQueryMongoAdapter, MovementQueryMongoAdapter; P3, activo con report.source=readmodel)
    │   │       ├── mapper/
    │   │       └── config/                                     (beans, Resilience4j, Mongo, Kafka, seguridad, selección de adaptadores)
    │   └── resources/
    │       ├── openapi/report-service/openapi.yaml   (+ openapi/common/common-schemas.yaml)
    │       ├── application.yml                     (solo nombre y config.import; el resto viene del Config Server)
    │       └── logback-spring.xml
    └── test/java/com/bank/report/
```

Los DTOs REST se generan desde el contrato.

## 9. Stack y configuración

**Base común:** Java 17, Spring Boot 3.x, Maven, WebFlux, RxJava 3, Lombok, MapStruct, Logback.

| Función | Dependencia |
|---|---|
| Web y cliente HTTP (P2) | `spring-boot-starter-webflux` (WebClient) |
| Resiliencia (P2) | `resilience4j-spring-boot3` + `resilience4j-reactor`: circuit breaker y time limiter de 2 s |
| Persistencia (P3) | `spring-boot-starter-data-mongodb-reactive` (`RxJava3CrudRepository`) |
| Eventos (P3) | `reactor-kafka` o `spring-kafka` |
| Contrato | `openapi-generator-maven-plugin` |
| Config / Discovery | `spring-cloud-starter-config`, `spring-cloud-starter-netflix-eureka-client` |
| Seguridad | `spring-boot-starter-oauth2-resource-server` |
| Calidad y pruebas | Checkstyle, Jacoco, JUnit 5, Mockito, `reactor-test`, RxJava `TestObserver`, WebTestClient, WireMock |

**Propiedades en Config Server:** puerto, `report.source` (`rest`/`readmodel`), URLs o nombres Eureka de las fuentes (P2), timeouts y umbrales del circuit breaker, máximo de días del intervalo (366), máximo de movimientos por reporte, tamaño de página, Mongo y Kafka (P3), `security.enabled`.

**Resiliencia (P2):** las llamadas a `account-service`, `credit-service` y `transaction-service` llevan circuit breaker con **timeout de 2 s**; si falla una, el reporte responde 503. En P3 no hay llamadas salientes y la disponibilidad depende solo de su propia base.

## 10. Estrategia de pruebas
| Capa | Qué se prueba | Herramientas |
|---|---|---|
| Dominio: calculadoras | Resumen con y sin movimientos, totales por tipo, comisiones, saldo inicial y final, solo `COMPLETED`, orden, últimos N con menos de N movimientos | JUnit 5 (sin Spring), pruebas parametrizadas |
| Dominio: VO | `DateRange` (inválido, más de 366 días), `PageRequest` | JUnit 5 |
| Casos de uso | Reporte de producto, últimos movimientos, reporte por cliente; producto inexistente; producto ajeno (`CUSTOMER`); rango demasiado grande | Mockito + `TestObserver` |
| Adaptadores REST (P2) | Recorrido de varias páginas, timeout de 2 s, circuit breaker abierto, fuente caída | WireMock |
| Proyección (P3) | `upsert` idempotente, eventos repetidos y desordenados, movimiento antes del producto | Mockito + pruebas de consumidor |
| Consultas Mongo (P3) | Rango, orden, consulta `In` por categoría | Testcontainers *(opcional)* |
| Controllers | Contrato, roles, validación de parámetros, códigos 400/404/422/503 | WebTestClient |
| Cobertura | Reporte de todo el código | Jacoco |

## 11. Diagramas a elaborar
- [ ] Secuencia: reporte de producto en P2 (consulta por REST de producto y movimientos)
- [ ] Secuencia: reporte de producto en P3 (read model)
- [ ] Secuencia: últimos 10 movimientos de una tarjeta
- [ ] Secuencia: reporte de tarjetas de un cliente (crédito + débito)
- [ ] Secuencia: proyección de un evento al read model
- [ ] UML del modelo de lectura y los reportes

## 12. Decisiones y pendientes
- **Decidido:**
  - Servicio de solo lectura: sin eventos ni CRUD; los cálculos son puros y con Streams.
  - "Reporte completo por producto" se interpreta como **el reporte de un producto concreto** (tipo extracto) en un intervalo; funciona en P2 por REST. Además se ofrece en P3 la versión **por categoría de producto** con el read model.
  - "Últimos 10 movimientos": por producto y, además, el conjunto de tarjetas de un cliente.
  - Movimientos de la tarjeta de débito = pagos de `debit-service`; el reporte de débito se habilita en P3.
  - Solo movimientos `COMPLETED`; los revertidos quedan fuera.
  - P2 por REST con circuit breaker de 2 s; P3 con read model. Cambia solo el adaptador, según `report.source`.
  - Intervalo máximo 366 días, página máxima 100 y tope de movimientos por reporte.
  - Salida solo JSON, en `PEN`.
  - Contrato detallado en `contracts/report-service/`: `501 NOT_AVAILABLE` para lo que solo existe en P3; saldos inicial y final solo desde movimientos (la apertura no es un movimiento); el reporte por categoría no lleva saldos; `SERVICE_UNAVAILABLE` en lugar de `SOURCE_UNAVAILABLE`.
- **Pendiente:**
  - **Confirmar con el instructor** qué significa "reporte por producto del banco": un producto concreto (lo implementado en P2) o una categoría de productos del banco (lo ofrecido en P3).
  - ~~Reversas~~ **Resuelto:** `transaction-service` publica `transaction.reversed` para toda reversa y revierte las comisiones enlazadas; el read model marca ambos como `REVERSED`.
  - Cómo reconstruir el read model si se pierde: reprocesar los tópicos desde el inicio (depende de la retención de Kafka).
  - Exportar a CSV o PDF: opcional.
  - En P2, el costo de recorrer páginas por REST es aceptable con datos de demo; no escala.

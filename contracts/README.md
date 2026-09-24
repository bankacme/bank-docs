# Contratos

Todo lo que dos personas (o dos servicios) deben tener idéntico antes de programar.

| Carpeta | Contenido | Estado |
|---|---|---|
| `common/` | `common-schemas.yaml`: `Id`, `OperationId`, `Money`, `Document`, `PageMeta`, `ErrorResponse`, parámetros de paginación y fechas, respuestas de error y esquema de seguridad | Listo |
| `events/` | `kafka-contract.md`: tópicos, claves, sobre común, campos de cada mensaje y reglas de consumo | Listo |
| `customer-service/` | `openapi.yaml` (requests y responses) y `data-model.md` (entidad, documento Mongo, índices, mapeos, ejemplos) | Listo |
| `account-service/` | Ídem (incluye reglas de cálculo, catálogo de condiciones y datos de demo) | Listo |
| `transaction-service/` | Ídem (incluye la saga de transferencia, las reversas y la recuperación) | Listo |
| `credit-service/` | Ídem (incluye la revisión de deuda vencida, pagos de terceros y el registro en el historial) | Listo |
| `report-service/` | Ídem (incluye las fuentes de datos de P2 y P3 y las reglas de cálculo de los reportes) | Listo |
| `auth-service/` | Ídem (incluye el formato del token, el login con bloqueo y la copia local de clientes) | Listo |
| `debit-service/` | Ídem (incluye el pago con espera de 2 s, la recuperación y los read models) | Listo |
| `yanki-service/` | Ídem (incluye la saga de cuatro rutas con pasos locales y remotos, el historial de ambas direcciones y la asociación a tarjeta) | Listo |

Orden de trabajo: el de construcción (customer → account → transaction → credit → report → auth → debit → yanki).

## Cómo se usa cada pieza

| Pieza | En el repo del servicio |
|---|---|
| `openapi.yaml` | `src/main/resources/openapi/<servicio>/openapi.yaml`. Con `openapi-generator-maven-plugin` genera las interfaces de los controllers y los DTOs. Los controllers los implementan y **no** se editan los DTOs generados |
| `common-schemas.yaml` | `src/main/resources/openapi/common/common-schemas.yaml` (misma estructura que `contracts/`, para que `../common/...` resuelva) |
| `data-model.md` | Guía para las clases del dominio, `*Document` de Mongo, mappers e índices |
| `kafka-contract.md` | Guía para los DTOs de eventos y los consumidores; los servicios copian solo los mensajes que publican o consumen |

### Opciones sugeridas del generador (verificar con la versión del plugin)

| Opción | Valor | Motivo |
|---|---|---|
| `generatorName` | `spring` | |
| `interfaceOnly` | `true` | Solo interfaces y modelos; los controllers son propios |
| `reactive` | `true` | Firmas reactivas (WebFlux). El generador usa tipos Reactor; el controller los adapta a RxJava en el borde |
| `useSpringBoot3` | `true` | Jakarta |
| `useTags` | `true` | Una interfaz por etiqueta |
| `documentationProvider` | `none` | Sin anotaciones de Swagger |
| `openApiNullable` | `false` | Sin `JsonNullable` |
| `dateLibrary` | `java8` | `Instant` / `LocalDate` |
| `useBeanValidation` | `true` | Las validaciones del contrato se convierten en anotaciones |

## Convenciones del contrato REST

| Tema | Regla |
|---|---|
| Base | `/api/v1`; rutas y campos en inglés, plural para colecciones |
| Versión de OpenAPI | 3.0.3 |
| Roles | Extensión `x-roles` en cada operación (no la usa el generador; la usan la seguridad y las pruebas). `x-customer-scope` explica cuándo un `CUSTOMER` solo accede a lo suyo |
| Errores | Cuerpo `ErrorResponse`: `timestamp`, `status`, `code`, `message`, `path` (+ `details` en 400). 400 formato, 404 no existe, 409 duplicado, 422 regla de negocio, 503 otro servicio no responde |
| Idempotencia | `operationId` obligatorio en operaciones que mueven dinero (`OperationId`). Repetir devuelve el resultado original (200). Los ids que un servicio recibe del cliente y a los que agrega sufijos (`-OUT`, `-IN`, `-FEE`…) se limitan a **56** caracteres para que el derivado quepa en 64. Mismo id con otros datos: 409 `OPERATION_ID_REUSED` |
| Sin respuesta definitiva | Si otro servicio no responde a tiempo en una operación que mueve dinero, se responde **202** (queda pendiente y se reintenta con el mismo `operationId`); el 503 se reserva para cuando no se creó nada |
| Dinero | `Money`: número con 2 decimales, solo `PEN`. Se lee como `BigDecimal` |
| Fechas | `date` = `yyyy-MM-dd`; `date-time` en UTC |
| Ids | Cadena UUID v4 (`Id`); no se declara `format: uuid` para que el generador use `String` |
| Listados | `page` (desde 0) y `size` (máximo 100), más recientes primero; respuesta con `PageMeta` |
| Validación | El formato lo declara el contrato (400). Las reglas de negocio las aplica el dominio (422) |

## Convenciones de persistencia (todos los servicios)

| Tema | Regla |
|---|---|
| Dinero | `BigDecimal` guardado como `Decimal128` (`MongoCustomConversions` con `BigDecimalRepresentation.DECIMAL128`). Por defecto Spring Data lo guardaría como texto |
| Fechas | `Instant` como `date` UTC; `LocalDate` como texto `yyyy-MM-dd` y `YearMonth` como texto `yyyy-MM` (conversores en `MongoCustomConversions`, que también se aplican a los parámetros de las consultas derivadas; evita el desfase de zona horaria de la conversión por defecto); zona del banco `bank.zone` (propuesta `America/Lima`) para "hoy" |
| Ids | `_id` de texto (UUID v4). Excepciones: catálogo de `account-service` (`<TIPO>_<PERFIL>`) y `account_operations` (`_id` = `operationId`) |
| Índices | Se crean al arrancar (`spring.data.mongodb.auto-index-creation=true`) |
| Escrituras a dos documentos | Transacción de Mongo con `UnitOfWorkPort`. Exige Mongo como *replica set* (en el `docker-compose`, un solo nodo con `--replSet rs0`) |
| Consultas | Métodos derivados de Spring Data, sin `@Query` ni consultas dinámicas: una consulta por combinación de filtros o filtro en memoria si el volumen es pequeño |

## Validar los contratos

`python3 validate_oas.py <archivo.yaml>` con `openapi-spec-validator` (paquete de pip). Todos los `openapi.yaml` de esta carpeta se validan con esa herramienta antes de entregarse.

## Decisión pendiente

- **Dónde vive `contracts/`:** propuesta, un repositorio `bank-contracts` con esta carpeta, y cada servicio copia la versión que usa al tag indicado. Alternativa: copiarlo dentro de cada repo. Se decide al crear los repositorios.

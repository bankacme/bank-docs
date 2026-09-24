# `customer-service` — Modelo de datos

> Complementa la ficha (`services/customer-service.md`) y el contrato REST (`openapi.yaml`, en esta carpeta) con los **tipos exactos** de cada campo, el documento de MongoDB, los índices, los mapeos entre capas y los datos de ejemplo. Si algo difiere, el `openapi.yaml` manda para la API y `contracts/events/kafka-contract.md` para los eventos.

## 1. Entidad de dominio

### 1.1 Aggregate `Customer`

| Campo | Tipo (dominio) | Req. | Regla | Mutable |
|---|---|---|---|---|
| `id` | `CustomerId` (`String`, UUID v4) | ✓ | Lo genera el servicio al crear | No |
| `type` | `CustomerType` | ✓ | `PERSONAL` o `BUSINESS`. No cambia | No |
| `profile` | `CustomerProfile` | ✓ | `STANDARD` por defecto. `VIP` solo si `PERSONAL`; `PYME` solo si `BUSINESS` | Sí (`changeProfile`) |
| `name` | `CustomerName` | ✓ | 1 a 150 caracteres | Sí (`update`) |
| `document` | `Document` | ✓ | Único en el sistema. Tipo coherente con `type` | No |
| `contact` | `ContactInfo` | ✓ | Email y celular obligatorios; dirección opcional | Sí (`update`) |
| `status` | `CustomerStatus` | ✓ | `ACTIVE` al crear; `INACTIVE` tras la baja | Sí (`deactivate`) |
| `createdAt` | `Instant` | ✓ | Reloj inyectado (`Clock`) | No |
| `updatedAt` | `Instant` | ✓ | Se actualiza en cada cambio | Sí |

Comportamiento: `create(...)`, `update(name, contact)`, `changeProfile(profile)`, `deactivate()`. Un cliente `INACTIVE` no admite `update` ni `changeProfile`.

### 1.2 Value objects

| VO | Campos | Validación (en el constructor o factory) |
|---|---|---|
| `CustomerId` | `value: String` | No vacío |
| `CustomerName` | `value: String` | No vacío, máximo 150 caracteres, sin espacios sobrantes |
| `Document` | `type: DocumentType`, `number: String` | DNI: 8 dígitos. CEX: 9 a 12 alfanuméricos. PASSPORT: 6 a 12 alfanuméricos. RUC: 11 dígitos |
| `Email` | `value: String` | Formato válido, se guarda en minúsculas, máximo 254 |
| `PhoneNumber` | `value: String` | 9 dígitos y empieza con 9 |
| `ContactInfo` | `email: Email`, `phone: PhoneNumber`, `address: String?` | Compone los anteriores. Dirección: máximo 200 |

### 1.3 Enums

| Enum | Valores |
|---|---|
| `CustomerType` | `PERSONAL`, `BUSINESS` |
| `CustomerProfile` | `STANDARD`, `VIP`, `PYME` |
| `CustomerStatus` | `ACTIVE`, `INACTIVE` |
| `DocumentType` | `DNI`, `CEX`, `PASSPORT`, `RUC` |

Coherencias: `PERSONAL` usa `DNI`, `CEX` o `PASSPORT`; `BUSINESS` usa `RUC`.

## 2. Documento MongoDB

Colección **`customers`**. Clase de persistencia: `CustomerDocument` (`@Document("customers")`), con `DocumentData` y `ContactData` embebidos. **No** se usa el nombre `Document` para la clase embebida (choca con `org.bson.Document`).

| Campo Mongo | Tipo Mongo | Tipo Java | Req. | Notas |
|---|---|---|---|---|
| `_id` | string | `String` | ✓ | UUID v4 en texto |
| `type` | string | `String` (enum) | ✓ | |
| `profile` | string | `String` (enum) | ✓ | |
| `name` | string | `String` | ✓ | |
| `document.type` | string | `String` (enum) | ✓ | |
| `document.number` | string | `String` | ✓ | |
| `contact.email` | string | `String` | ✓ | En minúsculas |
| `contact.phone` | string | `String` | ✓ | |
| `contact.address` | string | `String` | – | Se omite si no hay |
| `status` | string | `String` (enum) | ✓ | |
| `createdAt` | date | `Instant` | ✓ | UTC |
| `updatedAt` | date | `Instant` | ✓ | UTC |

Ejemplo:

```json
{
  "_id": "3f1c9a7e-5b2d-4e8a-9c6f-1a2b3c4d5e6f",
  "type": "PERSONAL",
  "profile": "STANDARD",
  "name": "Ana Torres Ríos",
  "document": { "type": "DNI", "number": "12345678" },
  "contact": { "email": "ana.torres@example.com", "phone": "987654321", "address": "Av. Los Olivos 123, Lima" },
  "status": "ACTIVE",
  "createdAt": { "$date": "2026-09-24T15:24:00Z" },
  "updatedAt": { "$date": "2026-09-24T15:24:00Z" }
}
```

### 2.1 Índices

| Nombre | Campos | Opciones | Para qué |
|---|---|---|---|
| `uk_customer_document` | `document.type` (1), `document.number` (1) | **Único** | Regla 1 y carreras entre altas simultáneas |
| `ix_customer_status` | `status` (1) | — | Filtro de listados |
| `ix_customer_type_profile` | `type` (1), `profile` (1) | — | Filtros por tipo y perfil |

Un alta que choque con `uk_customer_document` lanza `DuplicateKeyException`, que el adaptador traduce a 409 `DOCUMENT_ALREADY_REGISTERED`.

### 2.2 Consultas (sin `@Query`)

| Necesidad | Método del repositorio (derivado) |
|---|---|
| Por id | `findById` |
| ¿Existe el documento? | `existsByDocument_TypeAndDocument_Number(type, number)` |
| Buscar por documento | `findByDocument_TypeAndDocument_Number(type, number)` |
| Listar con filtros | `findAll()` o `findByStatus`, `findByType`, `findByProfile`, y el resto de filtros se aplica con `Flowable.filter`. Es aceptable por el volumen del demo. No se construyen consultas dinámicas |

## 3. Mapeos

| Dominio | Documento Mongo | REST (`Customer`) | Evento (`customer`) |
|---|---|---|---|
| `id` | `_id` | `id` | `customerId` |
| `type` | `type` | `type` | `type` |
| `profile` | `profile` | `profile` | `profile` |
| `name` | `name` | `name` | `name` |
| `document.type` / `document.number` | `document.type` / `document.number` | `document.type` / `document.number` | `document.type` / `document.number` |
| `contact.email` | `contact.email` | `contact.email` | *(no viaja)* |
| `contact.phone` | `contact.phone` | `contact.phone` | *(no viaja)* |
| `contact.address` | `contact.address` | `contact.address` | *(no viaja)* |
| `status` | `status` | `status` | `status` |
| `createdAt` | `createdAt` | `createdAt` | *(no viaja)* |
| `updatedAt` | `updatedAt` | `updatedAt` | `updatedAt` |

Los mapeos se hacen con **MapStruct** (REST ↔ dominio, dominio ↔ documento, dominio ↔ evento). Los VO se convierten a tipos simples en el borde.

## 4. Requests: validaciones y errores

Las validaciones de **formato** las declara el `openapi.yaml` (el generador las convierte en Bean Validation) y responden **400** `VALIDATION_ERROR`. Las de **negocio** viven en el dominio y responden **422** (o 409 / 404).

| Operación | Campo | Formato (400) | Negocio |
|---|---|---|---|
| `POST /customers` | `type` | Obligatorio, enum | — |
| | `profile` | Opcional, enum. Por defecto `STANDARD` | 422 `PROFILE_NOT_ALLOWED` si `VIP` con `BUSINESS` o `PYME` con `PERSONAL` |
| | `name` | Obligatorio, 1 a 150 | — |
| | `document.type` / `document.number` | Obligatorios; número alfanumérico de 6 a 12 | 422 `DOCUMENT_TYPE_NOT_ALLOWED` (tipo incoherente con `type`); 422 `INVALID_DOCUMENT` (número no cumple el formato de su tipo); 409 `DOCUMENT_ALREADY_REGISTERED` |
| | `contact.email` | Obligatorio, correo válido | — |
| | `contact.phone` | Obligatorio, 9 dígitos que empiezan con 9 | — |
| | `contact.address` | Opcional, máximo 200 | — |
| `PUT /customers/{id}` | `name`, `contact` | Igual que arriba | 404 `CUSTOMER_NOT_FOUND`; 422 `CUSTOMER_INACTIVE` |
| `PATCH /customers/{id}/profile` | `profile` | Obligatorio, enum | 404; 422 `PROFILE_NOT_ALLOWED`, `CUSTOMER_INACTIVE` |
| `DELETE /customers/{id}` | — | — | 404 `CUSTOMER_NOT_FOUND`. Repetir la baja responde 204 |
| `GET /customers/by-document` | `documentType`, `documentNumber` | Obligatorios | 404 `CUSTOMER_NOT_FOUND` |
| `GET /customers` | `type`, `profile`, `status` | Opcionales, enum | — |

**Códigos de error del servicio:** `VALIDATION_ERROR`, `CUSTOMER_NOT_FOUND`, `DOCUMENT_ALREADY_REGISTERED`, `DOCUMENT_TYPE_NOT_ALLOWED`, `INVALID_DOCUMENT`, `PROFILE_NOT_ALLOWED`, `CUSTOMER_INACTIVE`, `UNAUTHORIZED`, `FORBIDDEN`.

## 5. Caché (P3)

| Clave Redis | Valor | TTL | Se elimina cuando |
|---|---|---|---|
| `customer:{id}` | JSON del `Customer` (mismo formato que la respuesta REST) | Configurable (propuesta: 10 min) | Se actualiza el cliente, se cambia su perfil o se da de baja |

Cache-aside solo en `findById`. Si Redis falla, se lee de Mongo (la caché no es obligatoria para responder).

## 6. Eventos

| Publica (tópico `customer`, clave `customerId`) | Cuándo | Contenido |
|---|---|---|
| `customer.created` | Al crear | Estado completo (ver contrato de Kafka, 6.1) |
| `customer.updated` | Al actualizar datos o cambiar perfil | Estado completo |
| `customer.deleted` | Al dar de baja (solo la **primera** vez; repetir la baja no publica) | Estado completo con `status = INACTIVE` |

Consume: nada.

## 7. Datos de ejemplo para la demo

Personas y empresa ficticias, con formatos válidos. Sirven para las pruebas y para la colección de Postman.

| Alias | Tipo | Perfil | Nombre | Documento | Celular | Papel en la demo |
|---|---|---|---|---|---|---|
| A | `PERSONAL` | `STANDARD` | Ana Torres Ríos | DNI `12345678` | `987654321` | Cliente principal: cuentas, créditos, transferencias, Yanki con tarjeta |
| B | `PERSONAL` | `STANDARD` | Beto Quispe Salas | DNI `23456789` | `976543210` | Tercero: recibe transferencias y paga el crédito de A |
| C | `PERSONAL` | `STANDARD` | Carla Mendoza Vega | DNI `34567890` | `965432109` | Cliente con deuda vencida |
| V | `PERSONAL` | `VIP` | Víctor Herrera Paz | DNI `45678901` | `954321098` | Cuenta de ahorro VIP (exige tarjeta de crédito) |
| E | `BUSINESS` | `PYME` | Bodega San Martín S.A.C. | RUC `20512345678` | `912345678` | Cuenta corriente PYME (exige tarjeta de crédito) |
| D | *(no es cliente)* | — | Diana Flores Cruz | DNI `56789012` | `943210987` | Solo Yanki: se registra con `/auth/register` |

## 8. Decisiones y pendientes

**Decidido**
- El documento y el tipo no cambian; por eso los eventos y los usuarios de `auth-service` pueden confiar en ellos.
- Sin `version` ni control optimista: dos actualizaciones simultáneas del mismo cliente se resuelven con "gana la última", y los consumidores usan `updatedAt` para aplicar el evento más reciente.
- Listado sin paginación (volumen pequeño); filtros combinados aplicados en memoria sobre un método derivado.
- La validación fina del documento por tipo (dígitos de DNI o RUC) es del dominio: el `openapi.yaml` solo exige 6 a 12 alfanuméricos.

**Pendiente**
- Nada bloqueante para empezar a programar este servicio.

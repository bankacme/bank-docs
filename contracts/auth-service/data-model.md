# `auth-service` — Modelo de datos

> Complementa la ficha (`services/auth-service.md`) y el contrato REST (`openapi.yaml`, en esta carpeta) con los **tipos exactos**, los documentos de MongoDB, los índices, el formato del token, los algoritmos de login y bloqueo, y el consumo del tópico `customer`. Si algo difiere, el `openapi.yaml` manda para la API y `contracts/events/kafka-contract.md` para los eventos.
>
> No llama a ningún servicio y **no publica eventos**. El flujo completo está en `flows/05-customer-onboarding-and-access.md`.

## 1. Entidad de dominio

### 1.1 Aggregate `User`

| Campo | Tipo (dominio) | Req. | Regla | Mutable |
|---|---|---|---|---|
| `id` | `UserId` (`String`, UUID v4) | ✓ | Lo genera el servicio | No |
| `document` | `Document` | ✓ | Único (tipo + número). No cambia | No |
| `passwordHash` | `PasswordHash` | ✓ | BCrypt. Nunca sale del servicio ni se registra | Sí (`changePassword`, `resetPassword`) |
| `roles` | `Set<Role>` | ✓ | Una combinación válida (1.4) | Sí (`changeRoles`, `linkCustomer`) |
| `customerId` | `CustomerId?` | – | Presente **si y solo si** `roles` contiene `CUSTOMER`. Único entre usuarios | Sí (`linkCustomer`, solo de vacío a un valor) |
| `status` | `UserStatus` | ✓ | `ACTIVE` al crear | Sí (`disable`, `enable`) |
| `mustChangePassword` | `boolean` | ✓ | `true` si lo creó el personal o se restableció la contraseña; `false` en el registro Yanki y en el `ADMIN` inicial | Sí |
| `failedAttempts` | `int` | ✓ | Fallos de login consecutivos. Empieza en 0 | Sí (`authenticate`) |
| `lockedUntil` | `Instant?` | – | Fin del bloqueo. Ausente si no está bloqueado | Sí |
| `lastLoginAt` | `Instant?` | – | Último login correcto | Sí |
| `version` | `long` | ✓ | Control optimista (`@Version`). **Interno**: no viaja por REST | Automático |
| `createdAt` | `Instant` | ✓ | Reloj inyectado (`Clock`) | No |
| `updatedAt` | `Instant` | ✓ | Se actualiza en cada cambio, incluidos los contadores de login | Sí |

Comportamiento: `create(...)`, `registerYanki(...)`, `authenticate(matches, now, maxAttempts, lockDuration)`, `changePassword(newHash)`, `resetPassword(tempHash)`, `linkCustomer(customerId)`, `changeRoles(roles)`, `disable()`, `enable()`, `isLocked(now)`, `isStaff()`.

`isStaff()` es verdadero si tiene `ADMIN` o `TELLER`. Se usa para el alcance del `TELLER` (1.5).

### 1.2 Value objects

| VO | Campos | Validación (en el constructor o factory) |
|---|---|---|
| `UserId` | `value: String` | No vacío |
| `Document` | `type: DocumentType`, `number: String` | DNI: 8 dígitos. CEX: 9 a 12 alfanuméricos. PASSPORT: 6 a 12 alfanuméricos. RUC: 11 dígitos (422 `INVALID_DOCUMENT`) |
| `PasswordHash` | `value: String` | Hash BCrypt (empieza con `$2a$`, `$2b$` o `$2y$`) |
| `RawPassword` | `value: String` | Solo en memoria: 8 a 64 caracteres, con al menos una letra y un número (422 `WEAK_PASSWORD`). Se descarta al hashear |
| `CustomerId` | `value: String` | No vacío |
| `AuthToken` | `accessToken`, `tokenType = "Bearer"`, `expiresIn` (segundos) | Resultado inmutable del login |
| `CustomerSnapshot` | `customerId`, `document`, `status`, `updatedAt` | Dato de lectura (sección 2.2), no es aggregate |

### 1.3 Enums

| Enum | Valores |
|---|---|
| `Role` | `ADMIN`, `TELLER`, `CUSTOMER`, `YANKI_USER` |
| `UserStatus` | `ACTIVE`, `DISABLED` |
| `DocumentType` | `DNI`, `CEX`, `PASSPORT`, `RUC` |

### 1.4 Combinaciones de roles válidas

| Roles | `customerId` | Documento permitido | Cómo nace |
|---|---|---|---|
| `[ADMIN]` | No | DNI, CEX, PASSPORT | `POST /auth/users` (`ADMIN`) o el arranque inicial |
| `[TELLER]` | No | DNI, CEX, PASSPORT | `POST /auth/users` (`ADMIN`) |
| `[CUSTOMER]` | Sí | Cualquiera (RUC para empresas) | `POST /auth/users` (`ADMIN` o `TELLER`) |
| `[YANKI_USER]` | No | DNI, CEX, PASSPORT | `POST /auth/register` |
| `[CUSTOMER, YANKI_USER]` | Sí | DNI, CEX, PASSPORT | `PUT /auth/users/{id}/customer` sobre un `YANKI_USER` |

Cualquier otra combinación responde 422 `INVALID_ROLE_COMBINATION`; un documento RUC fuera de `[CUSTOMER]` responde 422 `DOCUMENT_TYPE_NOT_ALLOWED`.

### 1.5 Quién puede hacer qué

| Operación | `ADMIN` | `TELLER` | `CUSTOMER` / `YANKI_USER` |
|---|---|---|---|
| `POST /auth/users` con rol `ADMIN` o `TELLER` | ✓ | 403 `ROLE_NOT_ALLOWED` | 403 `FORBIDDEN` |
| `POST /auth/users` con rol `CUSTOMER` | ✓ | ✓ | 403 `FORBIDDEN` |
| `GET /auth/users`, `GET /auth/users/{id}` | Todos | Solo usuarios **sin** rol `ADMIN` ni `TELLER` | 403 `FORBIDDEN` |
| `PUT /auth/users/{id}` | ✓ | 403 `FORBIDDEN` | 403 `FORBIDDEN` |
| `PUT /auth/users/{id}/customer` | ✓ | ✓, solo sobre usuarios sin rol de personal | 403 `FORBIDDEN` |
| `POST /auth/users/{id}/password-reset`, `DELETE /auth/users/{id}` | ✓ | 403 `FORBIDDEN` | 403 `FORBIDDEN` |
| `GET /auth/me`, `PUT /auth/password` | ✓ | ✓ | ✓ |

Para el `TELLER`, un usuario fuera de su alcance **no existe**: aparece como 404 `USER_NOT_FOUND` (no se le revela). El alcance incluye a los `YANKI_USER`, porque el `TELLER` es quien los vincula con su cliente.

Con `security.enabled=false` (pruebas locales) las rutas no exigen token y el actor se toma como `ADMIN`. Nunca se usa así fuera del desarrollo: cuando existe `auth-service` la seguridad va activa.

## 2. Documentos MongoDB

### 2.1 `users` — `UserDocument`

Clase de persistencia `UserDocument` (`@Document("users")`) con `DocumentData` embebido (no se llama `Document`: choca con `org.bson.Document`).

| Campo Mongo | Tipo Mongo | Tipo Java | Req. | Notas |
|---|---|---|---|---|
| `_id` | string | `String` | ✓ | UUID v4 en texto |
| `document.type` | string | `String` (enum) | ✓ | |
| `document.number` | string | `String` | ✓ | |
| `passwordHash` | string | `String` | ✓ | BCrypt |
| `roles` | array de string | `List<String>` | ✓ | Orden del enum `Role` |
| `customerId` | string | `String` | – | **Se omite** si no hay (nunca `null`), para que el índice parcial funcione |
| `status` | string | `String` (enum) | ✓ | |
| `mustChangePassword` | bool | `boolean` | ✓ | |
| `failedAttempts` | int | `int` | ✓ | |
| `lockedUntil` | date | `Instant` | – | Se omite si no hay |
| `lastLoginAt` | date | `Instant` | – | Se omite si nunca entró |
| `version` | long | `Long` | ✓ | `@Version` |
| `createdAt` | date | `Instant` | ✓ | |
| `updatedAt` | date | `Instant` | ✓ | |

**Índices**

| Nombre | Campos | Opciones | Para qué |
|---|---|---|---|
| `uk_user_document` | `document.type` (1), `document.number` (1) | **Único** | Regla "un usuario por documento" y carreras de altas simultáneas → 409 `USER_ALREADY_EXISTS` |
| `uk_user_customer` | `customerId` (1) | **Único parcial**, filtro `{ customerId: { $exists: true } }` | Un usuario por cliente. Red de seguridad: como el documento del usuario debe coincidir con el del cliente, normalmente salta antes `uk_user_document`. Si salta este → 422 `CUSTOMER_ALREADY_LINKED` |
| `ix_user_docnumber` | `document.number` (1) | — | Filtro `documentNumber` sin tipo |
| `ix_user_status` | `status` (1) | — | Filtro de listado y conteo de `ADMIN` activos |

El filtro parcial usa solo `$exists` (los índices parciales no admiten `$in` ni `$ne`).

**Consultas (sin `@Query`, sin consultas dinámicas)**

| Necesidad | Método del repositorio (derivado) |
|---|---|
| Por id | `findById` |
| Login y alta: por documento | `findByDocument_TypeAndDocument_Number(type, number)` |
| Filtro `documentNumber` sin tipo | `findByDocument_Number(number)` |
| ¿Ya hay usuario para el cliente? / usuario de un cliente (evento de baja) | `findByCustomerId(customerId)` |
| Cuántos `ADMIN` activos | `countByRolesContainingAndStatus(ADMIN, ACTIVE)` |
| Listado | Si viene `customerId` → `findByCustomerId`; si no, si viene `documentNumber` → la consulta por documento; si no, si viene `status` → `findByStatus`; si no → `findAll`. El resto de filtros (`role`, `status`, alcance del `TELLER`) se aplica con `Flowable.filter`; se ordena en memoria por `createdAt` descendente y `id`. Aceptable por el volumen del demo |

### 2.2 `customer_snapshots` — `CustomerSnapshotDocument`

Copia local del cliente, alimentada por el tópico `customer`. Solo guarda lo que este servicio necesita.

| Campo Mongo | Tipo Mongo | Tipo Java | Req. | Notas |
|---|---|---|---|---|
| `_id` | string | `String` | ✓ | `customerId` |
| `document.type` | string | `String` (enum) | ✓ | No cambia nunca |
| `document.number` | string | `String` | ✓ | |
| `status` | string | `String` (enum) | ✓ | `ACTIVE` o `INACTIVE` |
| `updatedAt` | date | `Instant` | ✓ | Del evento. Se aplica solo un evento igual o más reciente |

Sin índices adicionales: solo se consulta por `_id`. **Nunca se guarda ni se devuelve el nombre, tipo ni perfil del cliente**: no los necesita.

## 3. Reglas y algoritmos

### 3.1 Login (`LoginUseCase`)

1. Validación de formato (400). El número de documento del login **no** se valida por tipo: un formato raro simplemente no encuentra usuario.
2. Busca el usuario por documento. Si **no existe**: compara la contraseña contra un hash falso fijo (para que el tiempo de respuesta no delate si el documento existe) y responde 401 `INVALID_CREDENTIALS`.
3. Con `now` del `Clock`:
   - Si `lockedUntil` existe y `now < lockedUntil` → 423 `ACCOUNT_LOCKED` (**sin** comprobar la contraseña; no cambia nada).
   - Si `lockedUntil` existe y ya pasó → se limpia y `failedAttempts = 0` (se persiste con el guardado del paso 5 o 6).
4. Si el usuario está `DISABLED`: compara igual contra el hash (mismo costo) y responde 401 `INVALID_CREDENTIALS`. **No** cuenta como fallo.
5. Contraseña **incorrecta**: `failedAttempts + 1`. Si llega a `security.login.max-failed-attempts` (5) → `lockedUntil = now + 15 min` y responde 423 (con `retryAfter`); si no, 401. Se guarda con `version`.
6. Contraseña **correcta**: `failedAttempts = 0`, sin `lockedUntil`, `lastLoginAt = now`. Se guarda y se emite el token. La respuesta lleva `mustChangePassword` y `user`.
7. Si el guardado falla por `version` (dos logins simultáneos), se **relee y se repite** el caso hasta 3 veces; si se agotan → 409 `CONCURRENT_MODIFICATION`.

El hash BCrypt es trabajo de CPU: se ejecuta en un scheduler de RxJava dedicado y acotado (`observeOn(bcryptScheduler)`), no en el hilo reactivo. El costo es `security.password.bcrypt-strength` (10 por defecto).

### 3.2 Contraseñas

| Operación | Regla |
|---|---|
| Política (`PasswordPolicy`) | 8 a 64 caracteres, al menos una letra y un número. Falla → 422 `WEAK_PASSWORD` |
| `PUT /auth/password` | Comprueba `currentPassword` (si falla → 422 `INVALID_CURRENT_PASSWORD`, no cuenta para el bloqueo). La nueva debe cumplir la política y ser distinta de la actual (422 `PASSWORD_UNCHANGED`). Deja `mustChangePassword = false` |
| `POST /auth/users` | `temporaryPassword` cumple la política. Nace con `mustChangePassword = true` |
| `POST /auth/users/{id}/password-reset` | La temporal cumple la política. Deja `mustChangePassword = true`, `failedAttempts = 0` y sin `lockedUntil` (**desbloquea**) |
| `POST /auth/register` | La contraseña cumple la política. `mustChangePassword = false` |
| Registro en logs | Nunca: ni la contraseña, ni el hash. El documento se enmascara en logs (`****5678`) |

### 3.3 Alta de un usuario por el personal (`CreateUserUseCase`)

Orden de las validaciones (la primera que falle responde):

1. Formato (400). El actor puede crear ese rol (403 `ROLE_NOT_ALLOWED`).
2. Combinación de roles y `customerId` (422 `INVALID_ROLE_COMBINATION`): exactamente un rol de `ADMIN`, `TELLER` o `CUSTOMER`; `CUSTOMER` exige `customerId`; `ADMIN` y `TELLER` no lo admiten.
3. Documento: formato por tipo (422 `INVALID_DOCUMENT`); RUC solo para `CUSTOMER` (422 `DOCUMENT_TYPE_NOT_ALLOWED`).
4. Contraseña (422 `WEAK_PASSWORD`).
5. Solo `CUSTOMER`: `customer_snapshots[customerId]` existe (422 `CUSTOMER_NOT_FOUND`), está `ACTIVE` (422 `CUSTOMER_INACTIVE`), su documento coincide (422 `DOCUMENT_MISMATCH`), y ningún usuario tiene ese `customerId` (422 `CUSTOMER_ALREADY_LINKED`).
6. El documento no tiene usuario (409 `USER_ALREADY_EXISTS`). Es la última comprobación de lectura; la garantía real es `uk_user_document` (`DuplicateKeyException` → 409).

`CUSTOMER_ALREADY_LINKED` va **antes** de `USER_ALREADY_EXISTS` a propósito: si el documento ya tiene un usuario **ya vinculado a ese cliente**, el mensaje útil es "ya está vinculado". Si el documento tiene un `YANKI_USER` sin cliente, el paso 5 pasa y el 6 responde 409: la salida es vincular (`PUT /auth/users/{id}/customer`).

### 3.4 Vincular un cliente (`LinkCustomerUseCase`)

1. El usuario existe y está en el alcance del actor (404 `USER_NOT_FOUND`).
2. Si ya tiene **ese mismo** `customerId` → 200 sin cambios (idempotente).
3. `DISABLED` → 422 `USER_DISABLED`. Ya tiene **otro** `customerId` → 422 `USER_ALREADY_LINKED`. Tiene rol `ADMIN` o `TELLER` → 422 `INVALID_ROLE_COMBINATION`.
4. Mismas verificaciones del cliente que en 3.3.5.
5. Se asigna el `customerId` y se agrega `CUSTOMER` (queda `[CUSTOMER, YANKI_USER]`). El usuario conserva su contraseña. Se ve en el siguiente login.

### 3.5 Actualizar roles y estado (`UpdateUserUseCase`, `DisableUserUseCase`)

- `PUT /auth/users/{id}` recibe **roles y estado completos**. Los roles deben ser una combinación válida (1.4) y `CUSTOMER` presente **si y solo si** hay `customerId`; por aquí no se agrega ni se quita `CUSTOMER` (422 `INVALID_ROLE_COMBINATION`).
- Un usuario con documento RUC solo puede ser `[CUSTOMER]` (422 `DOCUMENT_TYPE_NOT_ALLOWED` si se intenta otra cosa).
- `DISABLED → ACTIVE`: si tiene `customerId`, el cliente debe estar `ACTIVE` en la copia local (422 `CUSTOMER_INACTIVE`).
- **Último `ADMIN`:** si el usuario es `ADMIN` activo y el cambio le quita el rol o lo deshabilita, se cuenta con `countByRolesContainingAndStatus(ADMIN, ACTIVE)`; si es 1 → 422 `LAST_ADMIN`. La comprobación no es atómica: dos `ADMIN` que se deshabilitan mutuamente a la vez podrían dejar el sistema sin `ADMIN`. Se acepta en el demo; el arranque vuelve a crear uno (3.6).
- `DELETE /auth/users/{id}` = `status → DISABLED`, con las mismas comprobaciones. Repetir → 204.
- Los cambios de roles, estado y cliente vinculado se ven en el **siguiente login**; el token vigente no se toca.

### 3.6 Arranque: `ADMIN` inicial (`SeedAdminUseCase`)

Al arrancar, si `countByRolesContainingAndStatus(ADMIN, ACTIVE) == 0`, crea un `ADMIN` con las variables de entorno `AUTH_ADMIN_DOCUMENT_TYPE`, `AUTH_ADMIN_DOCUMENT_NUMBER` y `AUTH_ADMIN_PASSWORD`, con `mustChangePassword = false`.

- Idempotente: si ya hay un `ADMIN` activo no hace nada; si dos instancias arrancan a la vez, la que pierde recibe `DuplicateKeyException` y lo ignora.
- Si no hay `ADMIN` y faltan las variables, o el documento ya lo usa otro usuario, **el arranque falla** con un mensaje claro (un sistema sin `ADMIN` no se puede administrar).

## 4. El token JWT

Firma **RS256**, formato compacto (`header.payload.firma`). Lo emite el adaptador `JwtTokenIssuerAdapter` con `nimbus-jose-jwt`; el dominio solo ve el puerto `TokenIssuerPort.issue(claims)`.

**Cabecera**

| Campo | Valor |
|---|---|
| `alg` | `RS256` |
| `typ` | `JWT` |
| `kid` | `security.jwt.key-id` (por ejemplo `bank-1`) |

**Claims**

| Claim | Tipo | Req. | Valor |
|---|---|---|---|
| `sub` | string | ✓ | `userId` |
| `roles` | array de string | ✓ | Por ejemplo `["CUSTOMER","YANKI_USER"]` |
| `customerId` | string | – | Solo si el usuario tiene cliente vinculado |
| `documentType` | string | ✓ | `DNI`, `CEX`, `PASSPORT` o `RUC` |
| `documentNumber` | string | ✓ | Con esto `yanki-service` fija el documento del monedero |
| `iss` | string | ✓ | `security.jwt.issuer` |
| `iat` | número (segundos) | ✓ | Reloj inyectado (`Clock`) |
| `exp` | número (segundos) | ✓ | `iat` + `security.jwt.access-token-ttl` |
| `jti` | string | ✓ | UUID v4 por token (auditoría; no hay lista de revocación) |

```json
{ "sub": "7c1e4b2a-93d0-4f6b-8a51-0d2e6f9b3c44",
  "roles": ["CUSTOMER"], "customerId": "3f1c9a7e-5b2d-4e8a-9c6f-1a2b3c4d5e6f",
  "documentType": "DNI", "documentNumber": "12345678",
  "iss": "bootcamp-bank", "iat": 1790263440, "exp": 1790265240,
  "jti": "0b6e6c0e-6a1f-4f6e-9f5e-2f1f5b1f0a11" }
```

**Cómo lo valida quien lo recibe** (Gateway y cada servicio)

| Comprobación | Regla |
|---|---|
| Firma | RS256 con la clave pública de `security.jwt.public-key` |
| Emisor | `iss` igual a `security.jwt.issuer` |
| Vigencia | `exp` no vencido (tolerancia de reloj: 30 s) |
| Autoridades | Cada elemento de `roles` se convierte en la autoridad `ROLE_<rol>` (por eso `hasRole('ADMIN')` funciona) |
| `CUSTOMER` sobre un recurso | El `customerId` del recurso debe ser el del token; si no → 403 |

Ni el Gateway ni los servicios llaman a `auth-service` para validar.

**Claves.** Un par RSA de 2048 bits. La privada va en PKCS#8 (variable de entorno o secreto montado, `security.jwt.private-key`); la pública en formato PEM en Config Server (`security.jwt.public-key`). Para generarlas:

```bash
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out private.pem
openssl rsa -in private.pem -pubout -out public.pem
```

`GET /.well-known/jwks.json` publica esa misma clave pública (`n`, `e`) con el `kid`. Con una sola clave, cambiarla implica reiniciar Gateway y servicios (pendiente).

## 5. Mapeos

| Dominio | Documento Mongo | REST (`User`) | Token |
|---|---|---|---|
| `id` | `_id` | `id` | `sub` |
| `document.type` / `document.number` | `document.type` / `document.number` | `document.type` / `document.number` | `documentType` / `documentNumber` |
| `passwordHash` | `passwordHash` | *(nunca)* | *(nunca)* |
| `roles` | `roles` | `roles` | `roles` |
| `customerId` | `customerId` | `customerId` | `customerId` |
| `status` | `status` | `status` | *(no viaja)* |
| `mustChangePassword` | `mustChangePassword` | `mustChangePassword` | *(no viaja)* |
| `failedAttempts` | `failedAttempts` | *(no viaja)* | — |
| `lockedUntil` | `lockedUntil` | `lockedUntil` (solo si sigue vigente) | — |
| `lastLoginAt` | `lastLoginAt` | `lastLoginAt` | — |
| `version` | `version` | *(interno)* | — |
| `createdAt` / `updatedAt` | `createdAt` / `updatedAt` | `createdAt` / `updatedAt` | `iat` (solo el instante de emisión) |

Los mapeos se hacen con **MapStruct** (REST ↔ dominio, dominio ↔ documento). Los VO se convierten a tipos simples en el borde. El `passwordHash` no tiene campo en ningún DTO REST.

## 6. Requests: validaciones y errores

Las validaciones de **formato** las declara el `openapi.yaml` (el generador las convierte en Bean Validation) y responden **400** `VALIDATION_ERROR`. Las de **negocio** viven en el dominio y responden **422** (o 401 / 403 / 404 / 409 / 423).

| Operación | Campo | Formato (400) | Negocio |
|---|---|---|---|
| `POST /auth/login` | `documentType`, `documentNumber`, `password` | Obligatorios; número alfanumérico de 6 a 12; contraseña de 1 a 64 | 401 `INVALID_CREDENTIALS`; 423 `ACCOUNT_LOCKED`; 409 `CONCURRENT_MODIFICATION` |
| `POST /auth/register` | `documentType`, `documentNumber` | Obligatorios; número alfanumérico de 6 a 12 | 422 `DOCUMENT_TYPE_NOT_ALLOWED` (RUC); 422 `INVALID_DOCUMENT`; 409 `USER_ALREADY_EXISTS` |
| | `password` | Obligatorio, 8 a 64 | 422 `WEAK_PASSWORD` |
| `PUT /auth/password` | `currentPassword`, `newPassword` | Obligatorios; 1 a 64 y 8 a 64 | 422 `INVALID_CURRENT_PASSWORD`, `WEAK_PASSWORD`, `PASSWORD_UNCHANGED` |
| `POST /auth/users` | `document`, `roles`, `temporaryPassword` | Obligatorios; `roles` con exactamente 1 elemento; contraseña de 8 a 64 | Ver 3.3 |
| | `customerId` | Opcional, no vacío | 422 `INVALID_ROLE_COMBINATION`, `CUSTOMER_NOT_FOUND`, `CUSTOMER_INACTIVE`, `DOCUMENT_MISMATCH`, `CUSTOMER_ALREADY_LINKED` |
| `GET /auth/users` | `role`, `status`, `customerId`, `documentType`, `documentNumber` | Opcionales; enums válidos | — |
| `GET /auth/users/{id}` | `id` | Obligatorio | 404 `USER_NOT_FOUND` (también fuera del alcance del `TELLER`) |
| `PUT /auth/users/{id}` | `roles`, `status` | Obligatorios; `roles` de 1 a 2 sin repetir | 404; 422 `INVALID_ROLE_COMBINATION`, `DOCUMENT_TYPE_NOT_ALLOWED`, `LAST_ADMIN`, `CUSTOMER_INACTIVE`; 409 `CONCURRENT_MODIFICATION` |
| `PUT /auth/users/{id}/customer` | `customerId` | Obligatorio | 404; 422 `USER_DISABLED`, `USER_ALREADY_LINKED`, `INVALID_ROLE_COMBINATION`, `CUSTOMER_NOT_FOUND`, `CUSTOMER_INACTIVE`, `DOCUMENT_MISMATCH`, `CUSTOMER_ALREADY_LINKED` |
| `POST /auth/users/{id}/password-reset` | `temporaryPassword` | Obligatorio, 8 a 64 | 404; 422 `WEAK_PASSWORD` |
| `DELETE /auth/users/{id}` | — | — | 404; 422 `LAST_ADMIN`. Repetir la baja responde 204 |

**Códigos de error del servicio**

| HTTP | Códigos |
|---|---|
| 400 | `VALIDATION_ERROR` |
| 401 | `UNAUTHORIZED` (sin token o token inválido), `INVALID_CREDENTIALS` |
| 403 | `FORBIDDEN`, `ROLE_NOT_ALLOWED` |
| 404 | `USER_NOT_FOUND` |
| 409 | `USER_ALREADY_EXISTS`, `CONCURRENT_MODIFICATION` |
| 422 | `WEAK_PASSWORD`, `INVALID_CURRENT_PASSWORD`, `PASSWORD_UNCHANGED`, `INVALID_DOCUMENT`, `DOCUMENT_TYPE_NOT_ALLOWED`, `INVALID_ROLE_COMBINATION`, `CUSTOMER_NOT_FOUND`, `CUSTOMER_INACTIVE`, `DOCUMENT_MISMATCH`, `CUSTOMER_ALREADY_LINKED`, `USER_ALREADY_LINKED`, `USER_DISABLED`, `LAST_ADMIN` |
| 423 | `ACCOUNT_LOCKED` |

## 7. Eventos

| Publica | Consume |
|---|---|
| Nada | Tópico `customer` (`customer.created`, `customer.updated`, `customer.deleted`), grupo `auth-service` |

**Procesamiento (`HandleCustomerEventUseCase`)**, con el estado completo de `kafka-contract.md` sección 6.1:

1. Lee el sobre y el `payload`. Un mensaje ilegible o sin campos requeridos va directo a `customer.DLT`; un fallo de proceso se reintenta 3 veces (1 s, 2 s, 4 s) y luego va a `customer.DLT` (contrato de Kafka, sección 7).
2. `snapshot = findById(customerId)`. Si **no existe** o `payload.updatedAt >= snapshot.updatedAt` → se guarda `{ document, status, updatedAt }` (upsert). Si es **más viejo** → se ignora y termina.
3. Si se aplicó y `status = INACTIVE`: busca el usuario con ese `customerId`; si está `ACTIVE` lo pasa a `DISABLED` (`updatedAt` = ahora). Si ya estaba `DISABLED` no hace nada. Esta baja **no** pasa por la regla `LAST_ADMIN`: los usuarios con cliente nunca son `ADMIN`.
4. No se toca el rol `CUSTOMER` ni el `customerId` del usuario. Un cliente no vuelve a `ACTIVE`, así que no hay reactivación automática; un `ADMIN` puede reactivar al usuario, pero mientras el cliente siga `INACTIVE` → 422 `CUSTOMER_INACTIVE`.

El evento es idempotente (repetirlo produce el mismo estado) y tolera desorden por `updatedAt`. Los eventos de un mismo cliente llegan por la misma partición (clave `customerId`), así que se procesan en orden y sin carreras entre ellos.

**Reconstrucción:** con la base vacía, el consumidor lee el tópico compactado `customer` desde el inicio y recompone `customer_snapshots`. Mientras se pone al día, las altas de usuarios `CUSTOMER` pueden responder 422 `CUSTOMER_NOT_FOUND`.

## 8. Propiedades (Config Server) y variables de entorno

| Propiedad | Propuesta | Uso |
|---|---|---|
| `server.port` | `8086` | |
| `security.enabled` | `false` (P1/P2 y desarrollo) / `true` (P3) | Ver 1.5 |
| `security.jwt.issuer` | `bootcamp-bank` | Claim `iss` |
| `security.jwt.access-token-ttl` | `PT30M` | Vigencia (`expiresIn` = segundos) |
| `security.jwt.key-id` | `bank-1` | `kid` |
| `security.jwt.public-key` | PEM | La consumen también Gateway y servicios |
| `security.login.max-failed-attempts` | `5` | Bloqueo |
| `security.login.lock-minutes` | `15` | Duración del bloqueo |
| `security.password.bcrypt-strength` | `10` | Costo BCrypt |
| `security.password.min-length` / `max-length` | `8` / `64` | `PasswordPolicy` (coincide con el contrato) |
| `bank.zone` | `America/Lima` | Zona horaria del banco (global) |
| `spring.kafka.*` | | Consumidor del tópico `customer` |

| Variable de entorno o secreto | Uso |
|---|---|
| `SECURITY_JWT_PRIVATE_KEY` | Clave privada PKCS#8. **Nunca** al repositorio ni al Config Server en claro |
| `AUTH_ADMIN_DOCUMENT_TYPE`, `AUTH_ADMIN_DOCUMENT_NUMBER`, `AUTH_ADMIN_PASSWORD` | `ADMIN` inicial (3.6) |

## 9. Datos de ejemplo para la demo

Credenciales **solo del demo**. Los clientes A, B, C, V y E son los de `customer-service/data-model.md`; D no es cliente.

| Alias | Documento | Roles | Cómo se crea | Contraseña inicial |
|---|---|---|---|---|
| Admin | DNI `10203040` | `ADMIN` | Arranque (`AUTH_ADMIN_*`) | `Admin2026x` |
| Cajero | DNI `40404040` | `TELLER` | `POST /auth/users` como `ADMIN` | `Teller2026x` |
| A | DNI `12345678` | `CUSTOMER` (`customerId` de A) | `POST /auth/users` como `TELLER` | `Temporal2026` (luego `Clave2026x`) |
| B | DNI `23456789` | `CUSTOMER` | Ídem | `Temporal2026` |
| C | DNI `34567890` | `CUSTOMER` | Ídem | `Temporal2026` |
| V | DNI `45678901` | `CUSTOMER` | Ídem | `Temporal2026` |
| E | RUC `20512345678` | `CUSTOMER` (empresa) | Ídem | `Temporal2026` |
| D | DNI `56789012` | `YANKI_USER` | `POST /auth/register` | `Yanki2026x` |

Escenarios que cubre el guion de Postman:
- **Login del `ADMIN`** → token; crear el `TELLER`; entrar con él.
- **`TELLER` crea a A justo tras crear el cliente**: si responde 422 `CUSTOMER_NOT_FOUND`, reintentar a los pocos instantes.
- **Documento distinto** (`23456789` con el `customerId` de A) → 422 `DOCUMENT_MISMATCH`.
- **Segundo usuario para A** (mismo `customerId`) → 422 `CUSTOMER_ALREADY_LINKED`. Con el documento de D (un `YANKI_USER` sin cliente) y el `customerId` de D → 409 `USER_ALREADY_EXISTS`: la salida es vincular.
- **`TELLER` crea un `TELLER`** → 403 `ROLE_NOT_ALLOWED`.
- **Registro Yanki de D**, login y `POST /wallets` con el token de D.
- **D se hace cliente**: el `TELLER` crea el cliente (DNI `56789012`), busca a D con `GET /auth/users?documentNumber=56789012` y llama `PUT /auth/users/{id}/customer` → roles `[CUSTOMER, YANKI_USER]`; el siguiente login trae `customerId`.
- **Baja del cliente C** → su usuario queda `DISABLED` y el login responde 401; su token anterior sigue valiendo hasta vencer.
- **Cinco contraseñas incorrectas** de A → 423 con `retryAfter` (y con la correcta, mientras dure, también 423); el `ADMIN` la desbloquea con `password-reset`.
- **`ADMIN` quiere deshabilitarse siendo el único** → 422 `LAST_ADMIN`.

## 10. Decisiones y pendientes

**Decidido (nuevo en este contrato)**
- **Alcance del `TELLER` = usuarios sin rol `ADMIN` ni `TELLER`** (la ficha decía "solo `CUSTOMER`"): sin incluir a los `YANKI_USER` no podría vincularlos. Fuera de su alcance, el usuario aparece como 404.
- **La contraseña llega a 64 caracteres como máximo** (BCrypt solo usa 72 bytes; con 64 no se trunca nada en silencio).
- **El quinto fallo ya responde 423** (no 401); durante el bloqueo responde 423 aunque la contraseña sea correcta y no se comprueba.
- **Bloqueo vencido:** el siguiente intento parte de cero.
- **El login contra un usuario inexistente o `DISABLED` cuesta lo mismo** que uno normal (hash contra un valor falso), para no revelar por tiempo si el documento existe.
- **`PUT /auth/users/{id}`** reemplaza `roles` y `status` completos; **no** agrega ni quita `CUSTOMER` (eso es `PUT .../customer`). Combinaciones de roles cerradas (1.4).
- **`POST /auth/users`** crea un solo rol de `ADMIN`, `TELLER` o `CUSTOMER`; los `YANKI_USER` nacen solo por el registro. Documento RUC solo para `[CUSTOMER]`.
- **`password-reset` también desbloquea** al usuario.
- **`PUT /auth/password`**: un `currentPassword` incorrecto no cuenta para el bloqueo; la nueva debe ser distinta.
- **Vincular es idempotente** con el mismo `customerId`. Otro cliente distinto → 422 `USER_ALREADY_LINKED`.
- **`ADMIN` inicial** con `mustChangePassword = false` y arranque que **falla** si no hay `ADMIN` y no hay variables.
- **`GET /auth/me` lee el usuario actual**, no el token.
- **Sin paginación** en el listado de usuarios (volumen del demo); el filtro se apoya en métodos derivados y `Flowable.filter`.
- **`version` es interno** (no se pide ni se devuelve): los conflictos se reintentan hasta 3 veces y luego 409 `CONCURRENT_MODIFICATION`.
- `security.enabled=false` es solo para desarrollo local; el actor se toma como `ADMIN`.

**Pendiente**
- **Registro sin prueba de identidad.** Quien conozca el documento de un cliente que aún no tiene usuario puede registrarse antes con ese documento; si luego el `TELLER` lo **vincula**, esa persona heredaría el acceso como `CUSTOMER`. Mitigación posible (fuera del demo): que la vinculación también restablezca la contraseña, o verificar el correo/celular. Ya se aceptaba que el registro revela si un documento tiene usuario.
- Forzar el cambio de `mustChangePassword` (hoy solo se informa).
- Revocación de tokens al deshabilitar (ventana de hasta 30 min), rotación de claves, recuperación de contraseña y doble factor: fuera de alcance.
- Límite de velocidad (rate limiting) sobre `/auth/login` y `/auth/register` por IP: el bloqueo por usuario no protege contra el barrido de documentos.
- Comprobación de `LAST_ADMIN` no atómica (3.5): aceptado.
- Nada bloqueante para empezar a programar este servicio.

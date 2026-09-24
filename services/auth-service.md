# `auth-service`

## 1. Resumen

| Campo | Valor |
|---|---|
| Propósito | Autenticación y autorización: usuarios, contraseñas, roles y emisión de tokens JWT |
| Bounded context | Identidad y acceso |
| Fase | P3 (obligatorio: el enunciado pide JWT) |
| Puerto | 8086 |
| Base de datos | MongoDB: `users` + read model `customer_snapshots` |
| Depende de | Solo eventos de `customer-service`, para verificar que el `customerId` de un usuario existe y coincide con su documento. **No llama a nadie por REST** |

## 2. Responsabilidades

**Hace:**
- **Login** con documento + contraseña y emisión del **JWT** (RS256).
- CRUD de usuarios: personal del banco (`ADMIN`, `TELLER`), clientes (`CUSTOMER`) y usuarios Yanki (`YANKI_USER`).
- **Registro público** de usuarios Yanki.
- Vincular un usuario `CUSTOMER` con su `customerId` verificando el documento.
- Cambio y restablecimiento de contraseña.
- Bloqueo temporal por intentos fallidos.
- Crear el `ADMIN` inicial al arrancar.
- Publicar la clave pública para que Gateway y servicios validen los tokens.

**No hace:**
- Validar el token en cada petición: lo hacen el **Gateway** y cada servicio con la clave pública.
- Datos del cliente ni del monedero (`customer-service`, `yanki-service`).
- Refresh tokens, revocación, recuperación de contraseña por correo ni doble factor (fuera de alcance del demo).

## 3. Modelo de dominio (DDD)

### 3.1 Aggregates y entidades
| Elemento | Tipo | Descripción |
|---|---|---|
| `User` | Aggregate root | Persona con acceso al sistema: documento, contraseña (hash), roles, cliente vinculado y estado de seguridad |

Atributos de `User`: `id`, `document`, `passwordHash`, `roles`, `customerId` (opcional), `status`, `mustChangePassword`, `failedAttempts`, `lockedUntil`, `lastLoginAt`, `version`, `createdAt`, `updatedAt`.
Comportamiento: `create(...)`, `registerYanki(...)`, `authenticate(matches, now)` (registra éxito o fallo), `changePassword(newHash)`, `resetPassword(tempHash)`, `linkCustomer(customerId)`, `changeRoles(roles)`, `disable()`, `enable()`, `isLocked(now)`.

### 3.2 Value objects
| VO | Campos | Validaciones *(simplificadas para el demo)* |
|---|---|---|
| `UserId` | `value` | No vacío |
| `Document` | `type`, `number` | DNI: 8 dígitos. CEX: 9–12 alfanuméricos. PASSPORT: 6–12 alfanuméricos. RUC: 11 dígitos |
| `PasswordHash` | `value` | Nunca se guarda ni se devuelve la contraseña en claro |
| `RawPassword` | `value` | Solo existe en memoria: de 8 a 64 caracteres (BCrypt solo usa 72 bytes), con al menos una letra y un número. Se descarta al hashear |
| `CustomerId` | `value` | No vacío |
| `AuthToken` | `accessToken`, `tokenType`, `expiresIn` | Resultado inmutable del login |
| `CustomerSnapshot` | `customerId`, `document`, `status` | Dato de lectura, no es aggregate |

### 3.3 Enums
| Enum | Valores |
|---|---|
| `Role` | `ADMIN`, `TELLER`, `CUSTOMER`, `YANKI_USER` |
| `UserStatus` | `ACTIVE`, `DISABLED` |
| `DocumentType` | `DNI`, `CEX`, `PASSPORT`, `RUC` |

### 3.4 Reglas de negocio e invariantes
| # | Regla | Dónde se aplica |
|---|---|---|
| 1 | El login usa **documento + contraseña**. Cualquier fallo (usuario inexistente, contraseña incorrecta, usuario deshabilitado) responde igual: `INVALID_CREDENTIALS` | `LoginUseCase` |
| 2 | Un usuario por documento (tipo + número) | Caso de uso + índice único |
| 3 | Contraseña: de 8 a 64 caracteres, una letra y un número. Se guarda solo como hash BCrypt y nunca se registra en logs | `RawPassword` + `PasswordHasherPort` |
| 4 | El registro público solo crea `YANKI_USER`, con documento DNI, CEX o PASSPORT | `RegisterYankiUserUseCase` |
| 5 | Solo `ADMIN` crea `ADMIN` y `TELLER`. `ADMIN` y `TELLER` crean usuarios `CUSTOMER` | `UserProvisioningPolicy` |
| 6 | `CUSTOMER` ⇔ tiene `customerId`. El cliente debe existir, estar `ACTIVE` y su **documento debe coincidir con el del usuario** (RUC para empresas). Un solo usuario por `customerId` | `UserProvisioningPolicy` (dato del read model) |
| 7 | Un `YANKI_USER` que luego es cliente del banco no crea otro usuario: se le **vincula** el `customerId` y se le agrega el rol `CUSTOMER` (mismas verificaciones de la regla 6) | `User.linkCustomer` |
| 8 | Los usuarios creados por el personal nacen con `mustChangePassword = true`; se limpia al cambiar la contraseña, que exige la contraseña actual | `User.changePassword` |
| 9 | Bloqueo: 5 fallos consecutivos bloquean 15 minutos (configurable); un login correcto reinicia el contador | `User.authenticate` |
| 10 | Un usuario `DISABLED` no puede entrar. Si el cliente vinculado pasa a `INACTIVE` (evento), su usuario se deshabilita | `User.disable` + consumidor |
| 11 | Siempre debe quedar al menos un `ADMIN` activo: no se puede deshabilitar, eliminar ni quitar el rol al último | `UserProvisioningPolicy` |
| 12 | Eliminar es **baja lógica** (`DISABLED`); solo `ADMIN` lo hace o lo revierte | `User.disable/enable` |
| 13 | Token: RS256, vigencia configurable (30 min por defecto), claims `sub` (userId), `roles`, `customerId` (si tiene), `documentType`, `documentNumber`, `iss`, `iat`, `exp`, `jti` (el documento permite a `yanki-service` fijar el del monedero sin confiar en el cuerpo de la petición). Los cambios de roles se ven en el siguiente login | `TokenIssuerPort` |
| 14 | Al arrancar, si no existe ningún `ADMIN`, se crea uno con las credenciales configuradas por variables de entorno | `SeedAdminUseCase` |
| 15 | Restablecer la contraseña de otro usuario: solo `ADMIN`; deja la contraseña temporal y `mustChangePassword = true` | `User.resetPassword` |

### 3.5 Domain services
| Servicio | Responsabilidad |
|---|---|
| `UserProvisioningPolicy` | Valida las reglas 5, 6, 7 y 11. Puro: recibe los roles de quien actúa, el usuario objetivo, el `CustomerSnapshot`, si ya existe un usuario para ese `customerId` y cuántos `ADMIN` activos hay |
| `PasswordPolicy` | Valida la regla 3 |

### 3.6 Eventos de dominio
| Evento | Cuándo | Datos mínimos |
|---|---|---|
| `UserCreated` | Al crear o registrar | `userId`, `roles`, `customerId` |
| `UserUpdated` | Cambian roles, estado o cliente vinculado | `userId`, `roles`, `status`, `customerId` |
| `LoginSucceeded` / `LoginFailed` | Cada intento (auditoría) | `userId` (si existe), `at`, `reason` |

Los eventos de dominio se usan internamente y para auditoría en logs; **no se publican a Kafka** salvo que se decida lo contrario (ver pendientes).

## 4. Casos de uso y puertos

### 4.1 Puertos de entrada (casos de uso)
| Caso de uso | Retorno | Descripción |
|---|---|---|
| `LoginUseCase` | `Single<AuthToken>` | Valida credenciales, aplica bloqueo y emite el token |
| `RegisterYankiUserUseCase` | `Single<User>` | Registro público con rol `YANKI_USER` |
| `CreateUserUseCase` | `Single<User>` | Alta por el personal, con `UserProvisioningPolicy` |
| `FindUserUseCase` / `FindUsersUseCase` | `Single` / `Flowable` | Por id; lista con `role`, `status`, `customerId`, documento |
| `GetCurrentUserUseCase` | `Single<User>` | Perfil del usuario del token |
| `UpdateUserUseCase` | `Single<User>` | Roles y estado (`ADMIN`) |
| `LinkCustomerUseCase` | `Single<User>` | Vincula `customerId` y agrega el rol `CUSTOMER` |
| `ChangePasswordUseCase` | `Completable` | Cambia la propia contraseña |
| `ResetPasswordUseCase` | `Completable` | Restablecimiento por `ADMIN` |
| `DisableUserUseCase` | `Completable` | Baja lógica |
| `SeedAdminUseCase` | `Completable` | Crea el `ADMIN` inicial si no existe |
| `HandleCustomerEventUseCase` | `Completable` | Deshabilita al usuario si su cliente pasa a `INACTIVE` |
| `GetPublicKeysUseCase` | `Single<KeySet>` | Clave pública en formato JWKS |

### 4.2 Puertos de salida
| Puerto | Métodos | Adaptador |
|---|---|---|
| `UserRepositoryPort` | `save`, `findById`, `findByDocument`, `findAll(filters)`, `existsByCustomerId`, `countActiveAdmins` | Mongo |
| `CustomerLookupPort` | `findById` → `CustomerSnapshot` | Read model (Mongo) |
| `PasswordHasherPort` | `hash(raw)`, `matches(raw, hash)` | BCrypt |
| `TokenIssuerPort` | `issue(claims)` → token firmado | JWT con RS256 |

Los adaptadores de BCrypt y JWT están **fuera del dominio**: cambiar de algoritmo o librería no toca las reglas.

## 5. API (contrato OpenAPI)

Base: `/api/v1`. Los roles aplican con `security.enabled=true`; las rutas públicas lo son siempre.

| Método | Ruta | Descripción | Roles | Éxito | Errores |
|---|---|---|---|---|---|
| `POST` | `/auth/login` | Login (`documentType`, `documentNumber`, `password`) → `accessToken`, `tokenType`, `expiresIn`, `mustChangePassword`, datos básicos del usuario | **Público** | 200 | 400, 401, 423 |
| `POST` | `/auth/register` | Registro Yanki (`documentType`, `documentNumber`, `password`) | **Público** | 201 | 400, 409, 422 |
| `GET` | `/auth/me` | Perfil del usuario del token | Autenticado | 200 | 401 |
| `PUT` | `/auth/password` | Cambiar mi contraseña (`currentPassword`, `newPassword`) | Autenticado | 204 | 400, 422 |
| `POST` | `/auth/users` | Crear usuario (`document`, `roles`, `customerId`, `temporaryPassword`) | `ADMIN` (cualquier rol), `TELLER` (solo `CUSTOMER`) | 201 | 400, 403, 409, 422 |
| `GET` | `/auth/users` | Listar (`role`, `status`, `customerId`, `documentType`, `documentNumber`) | `ADMIN`; `TELLER` solo usuarios de clientes (sin rol `ADMIN` ni `TELLER`) | 200 | — |
| `GET` | `/auth/users/{id}` | Obtener | `ADMIN`; `TELLER` solo usuarios de clientes (fuera de su alcance responde 404) | 200 | 404 |
| `PUT` | `/auth/users/{id}` | Actualizar roles y estado (ambos obligatorios) | `ADMIN` | 200 | 400, 404, 409, 422 |
| `PUT` | `/auth/users/{id}/customer` | Vincular un cliente existente (`customerId`) | `ADMIN`, `TELLER` | 200 | 404, 422 |
| `POST` | `/auth/users/{id}/password-reset` | Restablecer contraseña (`temporaryPassword`) | `ADMIN` | 204 | 404, 422 |
| `DELETE` | `/auth/users/{id}` | Baja lógica | `ADMIN` | 204 | 404, 422 |
| `GET` | `/.well-known/jwks.json` | Clave pública en JWKS (opcional, para validadores externos) | Público | 200 | — |

Notas:
- El registro Yanki pide **solo documento y contraseña**: el celular, IMEI y correo van al crear el monedero en `yanki-service`.
- **Login de una empresa:** usa el RUC del cliente empresarial y su contraseña.
- Errores: 401 `INVALID_CREDENTIALS`, 403 `ROLE_NOT_ALLOWED` (un `TELLER` creando `TELLER` o `ADMIN`), 423 `ACCOUNT_LOCKED` (con `retryAfter` y encabezado `Retry-After`), 409 `USER_ALREADY_EXISTS`, `CONCURRENT_MODIFICATION`, 422 `WEAK_PASSWORD`, `CUSTOMER_NOT_FOUND`, `CUSTOMER_INACTIVE`, `DOCUMENT_MISMATCH`, `CUSTOMER_ALREADY_LINKED`, `USER_ALREADY_LINKED`, `USER_DISABLED`, `LAST_ADMIN`, `INVALID_CURRENT_PASSWORD`, `PASSWORD_UNCHANGED`, `INVALID_ROLE_COMBINATION`, `DOCUMENT_TYPE_NOT_ALLOWED`, `INVALID_DOCUMENT`. Cuerpo estándar: `{ timestamp, status, code, message, path }`.
- **Contrato exacto** (campos, tipos, combinaciones de roles, algoritmo de login y bloqueo, formato del token, documentos Mongo, consumo del tópico `customer` y datos de demo): `contracts/auth-service/openapi.yaml` y `data-model.md`. Si difieren de esta ficha, el contrato manda.
- **Cambios respecto a la primera versión:** el `TELLER` ve y vincula a los usuarios de clientes (incluye `YANKI_USER`, no solo `CUSTOMER`); el quinto intento fallido ya responde 423; `PUT /auth/users/{id}/customer` es idempotente; `POST /auth/users/{id}/password-reset` también desbloquea; `PUT /auth/users/{id}` no agrega ni quita `CUSTOMER`.
- **Consistencia eventual:** si el `TELLER` crea el usuario justo después de crear el cliente y el evento aún no llegó, la respuesta es `CUSTOMER_NOT_FOUND`; se repite en unos instantes.
- El registro público revela si un documento ya tiene usuario (409); se acepta en el demo.

## 6. Persistencia y caché
- **`users`:** un documento por usuario. Índices: único (`document.type`, `document.number`); único parcial en `customerId`; índice en `status`. Control optimista con `version`, para que el contador de fallos no se pise.
- **`customer_snapshots`:** (id, documento, estado, `updatedAt`), alimentado por el tópico `customer` de forma idempotente: se aplica un evento solo si su `updatedAt` es igual o más reciente. Se reconstruye leyendo el tópico compactado desde el inicio.
- **Caché:** no aplica.
- **Claves:** la privada **nunca** va al repositorio ni al Config Server en claro (variable de entorno o secreto montado); la pública se distribuye por Config Server.

## 7. Mensajería (Kafka)
| Publica | Consume |
|---|---|
| Nada obligatorio | `customer.created`, `customer.updated`, `customer.deleted` |

**Rol en sagas:** ninguno. El flujo completo (alta de cliente y usuario, registro Yanki, baja, login y validación del token) está en `flows/05-customer-onboarding-and-access.md`.

## 8. Filesystem

```
auth-service/
├── pom.xml
├── Dockerfile
├── checkstyle.xml
├── README.md
├── docs/
│   ├── sequence/
│   └── uml/
└── src/
    ├── main/
    │   ├── java/com/bank/auth/
    │   │   ├── AuthServiceApplication.java
    │   │   ├── domain/
    │   │   │   ├── model/
    │   │   │   │   ├── User.java                          (aggregate root)
    │   │   │   │   ├── UserId.java, Document.java, PasswordHash.java, RawPassword.java, CustomerId.java
    │   │   │   │   ├── AuthToken.java, CustomerSnapshot.java
    │   │   │   │   └── Role.java, UserStatus.java, DocumentType.java
    │   │   │   ├── service/
    │   │   │   │   ├── UserProvisioningPolicy.java
    │   │   │   │   └── PasswordPolicy.java
    │   │   │   ├── event/                                  (UserCreated, UserUpdated, LoginSucceeded, LoginFailed)
    │   │   │   └── exception/                              (UserNotFoundException, InvalidCredentialsException, AccountLockedException, BusinessRuleViolationException con code, ...)
    │   │   ├── application/
    │   │   │   ├── command/
    │   │   │   ├── port/
    │   │   │   │   ├── in/                                 (casos de uso)
    │   │   │   │   └── out/                                (4 puertos)
    │   │   │   └── usecase/
    │   │   └── infrastructure/
    │   │       ├── adapter/
    │   │       │   ├── in/rest/                            (AuthController, UserController, JwksController, GlobalExceptionHandler)
    │   │       │   ├── in/kafka/                           (CustomerEventsConsumer)
    │   │       │   ├── in/startup/                         (AdminSeedRunner: dispara SeedAdminUseCase)
    │   │       │   ├── out/persistence/                    (documentos, repositorios, adaptadores)
    │   │       │   ├── out/readmodel/                      (snapshots de cliente)
    │   │       │   └── out/security/                       (BcryptPasswordHasherAdapter, JwtTokenIssuerAdapter, KeyLoader)
    │   │       ├── mapper/
    │   │       └── config/                                 (beans, Clock, Mongo, Kafka, seguridad, enmascarado de logs)
    │   └── resources/
    │       ├── openapi/auth-service.yaml
    │       ├── application.yml                     (solo nombre y config.import; el resto viene del Config Server)
    │       └── logback-spring.xml
    └── test/java/com/bank/auth/
```

Los DTOs REST se generan desde el contrato.

## 9. Stack y configuración

**Base común:** Java 17, Spring Boot 3.x, Maven, WebFlux, RxJava 3, Lombok, MapStruct, Logback.

| Función | Dependencia |
|---|---|
| Web | `spring-boot-starter-webflux` (sin cliente HTTP hacia otros servicios) |
| Persistencia | `spring-boot-starter-data-mongodb-reactive` (`RxJava3CrudRepository`) |
| Hash de contraseñas | `spring-security-crypto` (BCrypt). Es trabajo de CPU: se ejecuta en un scheduler de RxJava aparte para **no bloquear** el hilo reactivo |
| JWT | `nimbus-jose-jwt` para firmar (RS256) |
| Validar sus propias rutas | `spring-boot-starter-oauth2-resource-server` con la clave pública |
| Eventos | `reactor-kafka` o `spring-kafka` |
| Contrato | `openapi-generator-maven-plugin` |
| Config / Discovery | `spring-cloud-starter-config`, `spring-cloud-starter-netflix-eureka-client` |
| Calidad y pruebas | Checkstyle, Jacoco, JUnit 5, Mockito, `reactor-test`, RxJava `TestObserver`, WebTestClient |

**Propiedades en Config Server:** puerto, Mongo, Kafka, `security.enabled`, `security.jwt.issuer`, `security.jwt.access-token-ttl`, `security.jwt.public-key`, intentos y minutos de bloqueo, política de contraseña.
**Por variable de entorno o secreto:** clave privada, documento y contraseña del `ADMIN` inicial.

**Cómo consumen el token los demás:**
| Componente | Qué hace |
|---|---|
| **Gateway** | Rutas públicas: `/auth/login`, `/auth/register`, `/.well-known/jwks.json`. El resto exige token: valida firma, emisor y expiración con la **clave pública del Config Server** (sin llamar a `auth-service`) |
| **Cada servicio** | Vuelve a validar el token (defensa en profundidad) y autoriza por `roles`; para `CUSTOMER` compara `customerId` del token con el del recurso. El claim `roles` se convierte en autoridades de Spring |
| **Modo demo** | Con `security.enabled=false`, Gateway y servicios dejan pasar sin token |

**Resiliencia:** sin llamadas salientes no aplica circuit breaker. La protección aquí es el bloqueo por intentos, el hash fuera del hilo reactivo y no depender de nadie en tiempo de ejecución (la validación es local, por clave pública).

## 10. Estrategia de pruebas
| Capa | Qué se prueba | Herramientas |
|---|---|---|
| Dominio: `User` | Bloqueo y reinicio del contador, cambio y restablecimiento de contraseña, invariante `CUSTOMER` ⇔ `customerId`, roles | JUnit 5 (sin Spring), `Clock` fijo |
| Dominio: políticas | `UserProvisioningPolicy` (quién crea qué, cliente inexistente/inactivo, documento distinto, último `ADMIN`); `PasswordPolicy` | JUnit 5 parametrizado |
| Casos de uso | Login (éxito, contraseña incorrecta, inexistente, deshabilitado, bloqueado); registro duplicado; alta de cliente con documento distinto; vincular un `YANKI_USER`; cliente inactivo deshabilita usuario | Mockito + `TestObserver` |
| Adaptador de token | Claims, expiración y firma verificable con la clave pública (par de claves generado en la prueba) | JUnit 5 |
| Adaptador BCrypt | Hash distinto por cada llamada, `matches` correcto | JUnit 5 |
| Consumers y startup | Eventos duplicados; el seed del `ADMIN` es idempotente | Pruebas de consumidor |
| Controllers | Rutas públicas vs protegidas, 401/403/423, contrato | WebTestClient |
| Cobertura | Reporte de todo el código | Jacoco |

## 11. Diagramas a elaborar
- [ ] Secuencia: login exitoso y con bloqueo por intentos
- [ ] Secuencia: registro Yanki
- [ ] Secuencia: `TELLER` crea el usuario de un cliente (verificación con el read model)
- [ ] Secuencia: vincular un cliente a un `YANKI_USER` existente
- [ ] Secuencia: validación del token en Gateway y servicios (entre servicios)
- [ ] Secuencia: cliente inactivo → usuario deshabilitado
- [ ] UML del dominio

## 12. Decisiones y pendientes
- **Decidido:**
  - Login uniforme con documento + contraseña, usando el RUC para clientes empresariales.
  - El personal crea los usuarios de los clientes; el `customerId` se verifica contra un read model alimentado por eventos y su documento debe coincidir con el del usuario. **Esto corrige lo que se dijo antes:** `auth-service` no llama a nadie, pero sí consume eventos de clientes.
  - Un solo aggregate `User`; un usuario por documento y por cliente.
  - Un `YANKI_USER` que se vuelve cliente **conserva su usuario** y se le vincula el `customerId`.
  - Solo access token; vigencia corta (30 min). No hay refresh ni revocación.
  - La clave pública se distribuye por Config Server, por lo que nadie depende de `auth-service` para validar; la privada nunca sale de una variable o secreto.
  - Bloqueo de 5 intentos por 15 minutos; hash BCrypt fuera del hilo reactivo.
  - `ADMIN` inicial creado al arrancar; el último `ADMIN` activo no se puede quitar.
- **Pendiente:**
  - Registro sin prueba de identidad: quien conozca el documento de un cliente sin usuario puede registrarse primero y heredar el acceso si el `TELLER` lo vincula (detalle y mitigación en `contracts/auth-service/data-model.md`, sección 10).
  - Un usuario deshabilitado sigue con token válido hasta que expire (máximo 30 min). Aceptado para el demo; la alternativa sería una lista de tokens revocados.
  - `mustChangePassword` se informa en el login pero **no se fuerza**: el token igual sirve para todo. Decidir si se restringe.
  - Rotación de claves: por ahora una sola clave; cambiarla implica reiniciar Gateway y servicios.
  - Publicar eventos de auditoría a Kafka (por ejemplo, intentos fallidos): opcional.
  - Recuperación de contraseña por el propio usuario, doble factor y política de contraseñas más estricta: fuera de alcance.

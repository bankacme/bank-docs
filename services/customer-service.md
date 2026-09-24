# `customer-service`

## 1. Resumen

| Campo | Valor |
|---|---|
| Propósito | Dueño del maestro de clientes del banco (personas y empresas) y su perfil |
| Bounded context | Gestión de clientes |
| Fase | P1 (eventos y caché en P3) |
| Puerto | 8081 |
| Base de datos | MongoDB, colección `customers` |
| Depende de | Nadie. Es fuente de datos para los demás servicios |

## 2. Responsabilidades

**Hace:**
- CRUD de clientes (Create, FindAll, Update, Delete).
- Mantener el **tipo** (`PERSONAL` / `BUSINESS`) y el **perfil** (`STANDARD`, `VIP`, `PYME`).
- Garantizar documento único y perfil compatible con el tipo.
- Búsqueda por documento (la usa el personal para vincular usuarios).
- Publicar eventos de cambios (P3).

**No hace:**
- Autenticación ni usuarios (`auth-service`; el usuario solo referencia el `customerId`).
- Validar requisitos de VIP/PYME como tener tarjeta de crédito: eso se valida al **abrir la cuenta** en `account-service`.
- Titulares y firmantes de cuentas (`account-service`).
- Usuarios de Yanki: no son clientes (`yanki-service`).

## 3. Modelo de dominio (DDD)

### 3.1 Aggregates y entidades
| Elemento | Tipo | Descripción |
|---|---|---|
| `Customer` | Aggregate root | Único aggregate del servicio. Contiene identidad, tipo, perfil, nombre, documento, contacto y estado |

Atributos de `Customer`: `id` (CustomerId), `type`, `profile`, `name`, `document`, `contact`, `status`, `createdAt`, `updatedAt`.

Comportamiento del aggregate: `create(...)` (factory), `update(name, contact)`, `changeProfile(newProfile)`, `deactivate()`.

### 3.2 Value objects
| VO | Campos | Validaciones *(simplificadas para el demo)* |
|---|---|---|
| `CustomerId` | `value` (String) | No vacío |
| `CustomerName` | `value` | No vacío, máx. 150 caracteres. Nombre completo (personal) o razón social (empresa) |
| `Document` | `type`, `number` | DNI: 8 dígitos. CEX: 9–12 alfanuméricos. PASSPORT: 6–12 alfanuméricos. RUC: 11 dígitos |
| `Email` | `value` | Formato válido, en minúsculas |
| `PhoneNumber` | `value` | 9 dígitos, empieza con 9 |
| `ContactInfo` | `email`, `phone`, `address` (opcional) | Compone los VO anteriores |

Todos inmutables y con validación en el constructor/factory (no se puede crear un VO inválido).

### 3.3 Enums
| Enum | Valores |
|---|---|
| `CustomerType` | `PERSONAL`, `BUSINESS` |
| `CustomerProfile` | `STANDARD`, `VIP`, `PYME` |
| `CustomerStatus` | `ACTIVE`, `INACTIVE` |
| `DocumentType` | `DNI`, `CEX`, `PASSPORT`, `RUC` |

### 3.4 Reglas de negocio e invariantes
| # | Regla | Dónde se aplica |
|---|---|---|
| 1 | Documento único (tipo + número) | Caso de uso (consulta por puerto) + índice único en Mongo |
| 2 | `PERSONAL` usa DNI, CEX o PASSPORT; `BUSINESS` usa RUC | Aggregate (`create`) |
| 3 | `VIP` solo para `PERSONAL`; `PYME` solo para `BUSINESS`; `STANDARD` para ambos | Aggregate (`create`, `changeProfile`) |
| 4 | El **tipo** no puede cambiar después de creado | Aggregate (`update` no lo expone) |
| 5 | Cliente `INACTIVE` no se puede actualizar ni cambiar de perfil | Aggregate |
| 6 | Eliminar = **baja lógica** (`INACTIVE`), no borrado físico, porque otros servicios referencian el `customerId` | Aggregate (`deactivate`) |
| 7 | Email y teléfono con formato válido | Value objects |

### 3.5 Domain services
No aplica. Las reglas caben en el aggregate y los VO.

### 3.6 Eventos de dominio
| Evento | Cuándo | Datos mínimos |
|---|---|---|
| `CustomerCreated` | Al crear | Estado completo: `customerId`, `type`, `profile`, `status`, `document`, `name`, `updatedAt` |
| `CustomerUpdated` | Al actualizar datos o cambiar perfil | Estado completo (los mismos campos) |
| `CustomerDeleted` | Al dar de baja | Estado completo, con `status = INACTIVE` |

Los tres eventos llevan **el estado completo actual** del cliente (no solo lo que cambió), porque los demás servicios lo necesitan para su copia local (read model) y pueden recibir un `updated` sin haber visto el `created`. Los consumidores aplican un evento solo si su `updatedAt` es igual o más reciente que el guardado (ver `flows/05-customer-onboarding-and-access.md`).

## 4. Casos de uso y puertos

Los puertos devuelven tipos RxJava 3 (`Single`, `Flowable`, `Completable`). El caso "no encontrado" se propaga como error de dominio.

### 4.1 Puertos de entrada (casos de uso)
| Caso de uso | Retorno | Descripción |
|---|---|---|
| `CreateCustomerUseCase` | `Single<Customer>` | Valida documento único, crea y guarda |
| `FindCustomerByIdUseCase` | `Single<Customer>` | Error `CustomerNotFoundException` si no existe |
| `FindAllCustomersUseCase` | `Flowable<Customer>` | Lista con filtros opcionales (`type`, `profile`, `status`) |
| `FindCustomerByDocumentUseCase` | `Single<Customer>` | Búsqueda por tipo + número |
| `UpdateCustomerUseCase` | `Single<Customer>` | Actualiza nombre y contacto |
| `ChangeCustomerProfileUseCase` | `Single<Customer>` | Cambia el perfil respetando la regla 3 |
| `DeleteCustomerUseCase` | `Completable` | Baja lógica |

### 4.2 Puertos de salida
| Puerto | Métodos | Adaptador (fase) |
|---|---|---|
| `CustomerRepositoryPort` | `save`, `findById`, `findAll(filters)`, `findByDocument`, `existsByDocument` | Mongo (P1) |
| `CustomerEventPublisherPort` | `publish(event)` | No-op (P1/P2) → Kafka (P3) |
| `CustomerCachePort` | `get`, `put`, `evict` | No-op (P1/P2) → Redis (P3) |

Con los adaptadores no-op, el servicio funciona igual desde P1 y en P3 solo se cambia el adaptador, sin tocar el dominio.

## 5. API (contrato OpenAPI)

Base: `/api/v1`. Roles aplican cuando `security.enabled=true`.

| Método | Ruta | Descripción | Roles | Éxito | Errores |
|---|---|---|---|---|---|
| `POST` | `/customers` | Crear cliente | `ADMIN`, `TELLER` | 201 | 400, 409, 422 |
| `GET` | `/customers` | Listar (filtros `type`, `profile`, `status`) | `ADMIN`, `TELLER` | 200 | — |
| `GET` | `/customers/{id}` | Obtener por id | `ADMIN`, `TELLER`, `CUSTOMER` (solo el suyo) | 200 | 404 |
| `GET` | `/customers/by-document` | Buscar por `documentType` + `documentNumber` | `ADMIN`, `TELLER` | 200 | 404 |
| `PUT` | `/customers/{id}` | Actualizar nombre y contacto | `ADMIN`, `TELLER` | 200 | 400, 404, 422 |
| `PATCH` | `/customers/{id}/profile` | Cambiar perfil | `ADMIN`, `TELLER` | 200 | 404, 422 |
| `DELETE` | `/customers/{id}` | Baja lógica | `ADMIN`, `TELLER` | 204 | 404 |

Errores: 400 datos inválidos (formato), 404 no existe, 409 documento duplicado, 422 regla de negocio (`PROFILE_NOT_ALLOWED`, `DOCUMENT_TYPE_NOT_ALLOWED`, `INVALID_DOCUMENT`, `CUSTOMER_INACTIVE`). Contrato completo y modelo de datos: `contracts/customer-service/`. Cuerpo estándar: `{ timestamp, status, code, message, path }`.

## 6. Persistencia y caché
- **Colección `customers`** con documento embebido para `document` y `contact`.
- **Índices:** único sobre (`document.type`, `document.number`); índice sobre `type` y `profile` para los filtros.
- **Duplicados concurrentes:** además de la consulta previa, se captura el error del índice único y se traduce a 409.
- **Caché (P3):** cache-aside de `findById` en Redis (`customer:{id}`), con evicción al actualizar o dar de baja.

## 7. Mensajería (Kafka) — P3
| Publica | Consume |
|---|---|
| `customer.created`, `customer.updated`, `customer.deleted` | Nada |

Los tres tipos de evento viajan por **un solo tópico físico `customer`**, con clave `customerId` y compactación, para conservar el orden por cliente.

**Rol en sagas:** ninguno. Es fuente de datos; los demás servicios reaccionan a sus eventos.

## 8. Filesystem

```
customer-service/
├── pom.xml
├── Dockerfile
├── checkstyle.xml
├── README.md
├── docs/
│   ├── sequence/
│   └── uml/
└── src/
    ├── main/
    │   ├── java/com/bank/customer/
    │   │   ├── CustomerServiceApplication.java
    │   │   ├── domain/
    │   │   │   ├── model/
    │   │   │   │   ├── Customer.java              (aggregate root)
    │   │   │   │   ├── CustomerId.java
    │   │   │   │   ├── CustomerName.java
    │   │   │   │   ├── Document.java
    │   │   │   │   ├── Email.java
    │   │   │   │   ├── PhoneNumber.java
    │   │   │   │   ├── ContactInfo.java
    │   │   │   │   ├── CustomerType.java
    │   │   │   │   ├── CustomerProfile.java
    │   │   │   │   ├── CustomerStatus.java
    │   │   │   │   └── DocumentType.java
    │   │   │   ├── event/
    │   │   │   │   ├── CustomerCreated.java
    │   │   │   │   ├── CustomerUpdated.java
    │   │   │   │   └── CustomerDeleted.java
    │   │   │   └── exception/
    │   │   │       ├── CustomerNotFoundException.java
    │   │   │       ├── DuplicateDocumentException.java
    │   │   │       └── InvalidCustomerException.java
    │   │   ├── application/
    │   │   │   ├── command/                        (CreateCustomerCommand, UpdateCustomerCommand)
    │   │   │   ├── port/
    │   │   │   │   ├── in/                         (7 casos de uso)
    │   │   │   │   └── out/                        (CustomerRepositoryPort, CustomerEventPublisherPort, CustomerCachePort)
    │   │   │   └── usecase/                        (implementaciones)
    │   │   └── infrastructure/
    │   │       ├── adapter/
    │   │       │   ├── in/rest/                    (CustomerController, GlobalExceptionHandler)
    │   │       │   ├── in/kafka/                   (vacío: no consume)
    │   │       │   ├── out/persistence/            (CustomerDocument, CustomerMongoRepository, CustomerPersistenceAdapter)
    │   │       │   ├── out/kafka/                  (CustomerEventKafkaPublisher, no-op hasta P3)
    │   │       │   └── out/cache/                  (CustomerRedisCacheAdapter, no-op hasta P3)
    │   │       ├── mapper/                         (REST ↔ dominio, dominio ↔ documento, dominio ↔ evento)
    │   │       └── config/                         (beans, Mongo, Kafka, Redis, seguridad)
    │   └── resources/
    │       ├── openapi/customer-service.yaml       (contrato)
    │       ├── application.yml                     (solo nombre y config.import; el resto viene del Config Server)
    │       └── logback-spring.xml
    └── test/java/com/bank/customer/                (espejo de main)
```

Los DTOs REST se **generan** desde el contrato en `target/generated-sources` (paquete `...infrastructure.adapter.in.rest.dto`); no se escriben a mano.

## 9. Stack y configuración

**Base común:** Java 17, Spring Boot 3.x, Maven, WebFlux, RxJava 3, Lombok, MapStruct, Logback.

| Función | Dependencia |
|---|---|
| Web reactiva | `spring-boot-starter-webflux` |
| Persistencia | `spring-boot-starter-data-mongodb-reactive` (repositorio `RxJava3CrudRepository`) |
| Validación | `spring-boot-starter-validation` |
| Contrato | `openapi-generator-maven-plugin` (generador `spring`, solo interfaces y DTOs) |
| Config / Discovery | `spring-cloud-starter-config`, `spring-cloud-starter-netflix-eureka-client` |
| Eventos (P3) | `reactor-kafka` o `spring-kafka` |
| Caché (P3) | `spring-boot-starter-data-redis-reactive` |
| Seguridad (P3) | `spring-boot-starter-oauth2-resource-server` (validación de JWT) |
| Calidad | Checkstyle, Jacoco |
| Pruebas | JUnit 5, Mockito, `reactor-test`, RxJava `TestObserver`, WebTestClient |

**Propiedades en Config Server:** puerto, URI de Mongo, bootstrap de Kafka, host de Redis, URL de Eureka, `security.enabled`, TTL de caché.

**Resiliencia:** el servicio no tiene llamadas salientes, así que el circuit breaker y el timeout de 2 s se configuran en la **ruta del Gateway** hacia este servicio. Se agrega Resilience4j en el servicio solo si aparece alguna llamada externa.

## 10. Estrategia de pruebas
| Capa | Qué se prueba | Herramientas |
|---|---|---|
| Dominio | VO (formatos válidos e inválidos), reglas 2–5 y 7 del aggregate | JUnit 5 (sin Spring) |
| Casos de uso | Flujos con puertos simulados: duplicado, no encontrado, perfil incompatible | Mockito + `TestObserver` |
| Adaptador Mongo | Guardar, buscar, índice único | Testcontainers *(opcional)* |
| Controller | Contrato, códigos de estado, errores | WebTestClient |
| Cobertura | Reporte con Jacoco de todo el código | Jacoco |

## 11. Diagramas a elaborar
- [ ] Secuencia: crear cliente (validación, duplicado, guardado, evento)
- [ ] Secuencia: cambiar perfil
- [ ] UML del dominio (`Customer` + VO + enums)

## 12. Decisiones y pendientes
- **Decidido:** un solo aggregate; eliminar es baja lógica; empresa identificada por RUC; puertos con adaptadores no-op para Kafka y Redis hasta P3; validaciones de formato simplificadas.
- **Pendiente:** ninguno. Los titulares y firmantes de cuentas empresariales se resolvieron en `account-service` como datos propios (documento + nombre), sin cambios aquí.

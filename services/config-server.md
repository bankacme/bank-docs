# `config-server`

> Servicio de infraestructura: no tiene dominio, base de datos ni API de negocio. Por eso esta ficha no sigue la plantilla completa (sin DDD, puertos ni pruebas de dominio).

## 1. Resumen

| Campo | Valor |
|---|---|
| Propósito | Entregar a cada servicio sus propiedades desde un **repositorio Git de configuración**, para que no quede configuración en el código (RNF-04) |
| Fase | **P1** (es lo primero que se levanta) |
| Puerto | 8888 |
| Tecnología | Spring Cloud Config Server (`spring-cloud-config-server`), backend Git |
| Base de datos | Ninguna |
| Se registra en Eureka | **No.** Los clientes conocen su URL fija (`CONFIG_SERVER_URL`). Registrarlo obligaría a que Eureka estuviera antes que la configuración de la que depende, y Eureka mismo pide su configuración aquí |
| Lo consumen | Todos: los 8 servicios de negocio, `eureka-server` y `api-gateway` |

## 2. Responsabilidades

**Hace:**
- Servir `GET /{aplicación}/{perfil}` a partir del repositorio Git de configuración.
- Combinar en cada respuesta el archivo común (`application.yml`), el del perfil (`application-<perfil>.yml`) y el del servicio (`<servicio>.yml`). El más específico gana.
- Guardar en un solo lugar la **clave pública JWT** (`security.jwt.public-key`) y el interruptor `security.enabled`.

**No hace:**
- Guardar secretos. La clave privada JWT, las credenciales del administrador inicial y cualquier contraseña viajan por **variables de entorno** de cada servicio, nunca por el repositorio.
- Cifrar propiedades (`{cipher}`): en el demo no hay credenciales de base de datos. Queda como mejora.
- Recargar propiedades en caliente: cambiar una propiedad exige **reiniciar** el servicio (sin `/actuator/refresh` ni Spring Cloud Bus).
- Autenticación: solo es alcanzable dentro de la red del `docker-compose`. **No tiene ruta en el Gateway.**

## 3. Repositorio de configuración

Un repositorio aparte (`bank-config`) con un archivo por servicio. El nombre del archivo es el `spring.application.name` del servicio.

```
bank-config/
├── application.yml              (común a todos)
├── application-docker.yml       (perfil `docker`: hosts de la red del docker-compose)
├── eureka-server.yml
├── api-gateway.yml              (rutas, circuit breakers, seguridad; ver ficha del Gateway)
├── customer-service.yml
├── account-service.yml
├── credit-service.yml
├── transaction-service.yml
├── report-service.yml
├── auth-service.yml
├── debit-service.yml
└── yanki-service.yml
```

Precedencia (de menor a mayor): `application.yml` < `application-<perfil>.yml` < `<servicio>.yml` < `<servicio>-<perfil>.yml`. Una variable de entorno del proceso vence a todas.

### 3.1 Propiedades comunes (`application.yml`)

| Propiedad | Valor propuesto | Quién la usa |
|---|---|---|
| `bank.zone` | `America/Lima` | Todos: "hoy" para vencimientos, día del plazo fijo, rangos de fechas |
| `security.enabled` | `false` (P1/P2) → `true` (entrega) | Todos y el Gateway |
| `security.jwt.issuer` | `bank-auth` | `auth-service` (emite) y Gateway y servicios (validan) |
| `security.jwt.public-key` | Clave pública RSA en PEM | Gateway y servicios. La **privada nunca** está aquí |
| `eureka.client.service-url.defaultZone` | `http://localhost:8761/eureka` | Todos menos `config-server` (P2 en adelante) |
| `eureka.client.enabled` | `false` en P1, `true` desde P2 | Todos |
| `eureka.client.registry-fetch-interval-seconds`, `eureka.client.healthcheck.enabled`, `eureka.instance.*` (`prefer-ip-address`, `lease-renewal-interval-in-seconds`, `lease-expiration-duration-in-seconds`), `management.health.redis.enabled` | Ver `services/eureka-server.md`, sección 4.2 | Todos los clientes de Eureka |
| `spring.kafka.bootstrap-servers` | `localhost:9092` | Servicios con Kafka (P3) |
| `spring.data.redis.host` / `port` | `localhost` / `6379` | `customer` y `account` (P3) |
| `management.endpoints.web.exposure.include` | `health,info` | Todos. Sin exponer `env` ni `configprops` (podrían mostrar la configuración) |
| `management.endpoint.health.probes.enabled` | `true` | Todos (verificación de salud del `docker-compose`) |
| `logging.level.root` / `com.bank` | `INFO` / `DEBUG` en desarrollo | Todos |

### 3.2 Propiedades por servicio

Cada archivo `<servicio>.yml` define lo que sea propio: `server.port`, la URI de Mongo (`spring.data.mongodb.uri`, una base por servicio) y las propiedades que se listan en la sección 9 de su ficha (por ejemplo `payment.await-timeout`, `yanki.recovery.*`, `report.source`, umbrales del circuit breaker). Este documento no las repite.

| Servicio | Puerto | Base de datos |
|---|---|---|
| `eureka-server` | 8761 | — |
| `api-gateway` | 8080 | — |
| `customer-service` | 8081 | `bank_customer` |
| `account-service` | 8082 | `bank_account` |
| `credit-service` | 8083 | `bank_credit` |
| `transaction-service` | 8084 | `bank_transaction` |
| `report-service` | 8085 | `bank_report` (vacía en P2; en P3 guarda las copias) |
| `auth-service` | 8086 | `bank_auth` |
| `debit-service` | 8087 | `bank_debit` |
| `yanki-service` | 8088 | `bank_yanki` |

### 3.3 Perfiles

| Perfil | Cuándo | Efecto |
|---|---|---|
| *(sin perfil)* | Servicios ejecutados desde el IDE en la máquina local | Hosts `localhost`. Mongo con `directConnection=true` |
| `docker` | Servicios dentro del `docker-compose` | `application-docker.yml` cambia los hosts a los nombres de servicio de la red (`mongo`, `kafka`, `redis`, `eureka-server`, `config-server`) |

## 4. Cómo se conectan los clientes

**Decisión:** con Spring Boot 3 no se usa `bootstrap.yml`. Cada servicio tiene un `application.yml` **mínimo** con solo dos datos y todo lo demás llega del Config Server:

```yaml
spring:
  application:
    name: customer-service
  config:
    import: "configserver:${CONFIG_SERVER_URL:http://localhost:8888}"
```

- El `import` **no** lleva `optional:`: si el Config Server no está, el servicio **no arranca** (falla rápido), en vez de arrancar con valores por defecto peligrosos.
- El perfil se elige con `SPRING_PROFILES_ACTIVE` (`docker` en el `docker-compose`).
- Las fichas de los servicios mencionaban `bootstrap.yml` en su árbol de archivos; se reemplazó por este `application.yml`.
- Reintentos de conexión al arrancar: no se usan; el `docker-compose` espera a que el Config Server esté sano (`depends_on` con `condition: service_healthy`).

## 5. Interfaz

Solo la que expone Spring Cloud Config (no hay contrato OpenAPI propio):

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/{aplicación}/{perfil}` | Propiedades combinadas de un servicio, en JSON |
| `GET` | `/{aplicación}-{perfil}.yml` | Lo mismo como YAML |
| `GET` | `/actuator/health` | Salud (la usa el `docker-compose`) |

## 6. Configuración del propio Config Server

Su `application.yml` es local (no puede pedirse a sí mismo su configuración):

| Propiedad | Valor | Nota |
|---|---|---|
| `server.port` | `8888` | |
| `spring.cloud.config.server.git.uri` | `${CONFIG_GIT_URI}` | Un `file:///config-repo` montado como volumen en el `docker-compose`, o la URL del repositorio en GitHub |
| `spring.cloud.config.server.git.default-label` | `main` | |
| `spring.cloud.config.server.git.clone-on-start` | `true` | Falla al arrancar si el repositorio no se puede leer |
| `spring.cloud.config.server.git.force-pull` | `true` | Descarta cambios locales del clon |
| `spring.cloud.config.server.git.username` / `password` | `${CONFIG_GIT_USER}` / `${CONFIG_GIT_TOKEN}` | Solo si el repositorio es privado; por variable de entorno |

## 7. Filesystem

```
config-server/
├── pom.xml
├── Dockerfile
├── checkstyle.xml
├── README.md
└── src/
    ├── main/
    │   ├── java/com/bank/config/ConfigServerApplication.java     (@EnableConfigServer)
    │   └── resources/application.yml
    └── test/java/com/bank/config/ConfigServerApplicationTests.java
```

Sin capas hexagonales: es una clase de arranque y un YAML.

## 8. Stack y configuración

| Función | Dependencia |
|---|---|
| Servidor de configuración | `spring-cloud-config-server` |
| Salud | `spring-boot-starter-actuator` |
| Calidad y pruebas | Checkstyle, Jacoco, JUnit 5 |

**Base común:** Java 17, Spring Boot 3.x y la versión de Spring Cloud compatible (definir en la base técnica; es la misma para todos los servicios). Es un servidor servlet: usa `spring-boot-starter-web`, no WebFlux ni RxJava (no hay lógica reactiva).

**Resiliencia:** no aplica. Si cae, los servicios ya arrancados siguen funcionando con lo que cargaron; solo no se pueden arrancar servicios nuevos.

## 9. Estrategia de pruebas

| Capa | Qué se prueba | Herramientas |
|---|---|---|
| Arranque | El contexto levanta con un repositorio Git de prueba (o el modo `native` sobre una carpeta) | `@SpringBootTest` |
| Contenido del repositorio | Existe un archivo por cada servicio de la sección 3.2 con su `server.port` correcto, y sin duplicar puertos | Prueba que lee la carpeta de configuración |
| Secretos | Ningún archivo del repositorio contiene `private-key`, `password` ni `token` | Prueba de búsqueda de texto (evita subir un secreto por error) |
| Cliente | Un servicio de prueba con `spring.config.import` obtiene sus propiedades; sin Config Server, falla | `@SpringBootTest` con servidor embebido |

## 10. Diagramas a elaborar
- [ ] Diagrama de despliegue: orden de arranque (Mongo, Kafka, Redis → `config-server` → `eureka-server` → servicios → `api-gateway`) y qué lee cada uno
- [ ] Secuencia: arranque de un servicio (Config Server → propiedades → Eureka → Mongo)

## 11. Decisiones y pendientes

**Decidido**
- Backend Git, con un repositorio aparte (`bank-config`) y un archivo por servicio.
- Sin `bootstrap.yml`: `spring.config.import` obligatorio (falla rápido).
- El Config Server **no** se registra en Eureka; `eureka-server` sí es cliente del Config Server.
- Secretos por variables de entorno; el repositorio solo lleva la clave **pública**.
- Sin recarga en caliente ni cifrado de propiedades: para el demo se reinicia.
- Sin autenticación: solo accesible desde la red interna, sin ruta en el Gateway.

**Pendiente**
- **Dónde vive el repositorio de configuración:** carpeta local montada (más simple para el demo) o GitHub (más cercano a lo que pide el enunciado: "repo de configuración"). Se decide al crear los repositorios; la ficha sirve para ambos.
- **Versiones** de Spring Boot y Spring Cloud: ver la base técnica.
- **Rotación de la clave JWT:** cambiarla implica editar el repositorio y reiniciar Gateway y servicios (ya anotado en `auth-service`).

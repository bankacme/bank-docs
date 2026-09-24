# `api-gateway`

> Servicio de infraestructura: no tiene dominio ni base de datos. Es el único que usa **Reactor directamente** (Spring Cloud Gateway está construido sobre él); no hay puertos ni casos de uso, así que no se aplica la regla de RxJava en los puertos. Por eso esta ficha no sigue la plantilla completa.

## 1. Resumen

| Campo | Valor |
|---|---|
| Propósito | Entrada única al sistema: enruta por prefijo hacia cada servicio, corta lo lento con un circuit breaker de 2 s, valida el JWT y bloquea los endpoints internos (RNF-17, RNF-18, RNF-27) |
| Fase | **P2** (rutas y circuit breaker). La validación del JWT se activa en P3 con `security.enabled=true` |
| Puerto | 8080 |
| Tecnología | Spring Cloud Gateway (WebFlux), Resilience4j, Eureka client, Spring Security (resource server) |
| Base de datos | Ninguna |
| Depende de | `config-server` (todo su comportamiento está en `api-gateway.yml`), `eureka-server` (resuelve `lb://<nombre>`) |
| Se registra en Eureka | Sí (`api-gateway`), aunque nadie lo llama por nombre |

## 2. Responsabilidades

**Hace:**
- Enrutar `/api/v1/...` al servicio dueño de cada recurso, con `lb://<nombre-en-eureka>` (sección 3).
- Aplicar a cada ruta un **circuit breaker con timeout de 2 s** y responder 503 con el cuerpo estándar cuando el servicio no contesta.
- Con `security.enabled=true`: exigir un JWT válido en todo lo que no sea público (firma, emisor y expiración).
- Bloquear los endpoints internos entre servicios.
- Dar un identificador de solicitud (`X-Request-Id`) y registrar una línea por solicitud.

**No hace:**
- Autorizar por rol ni por `customerId`: eso lo hace cada servicio (`x-roles` y `x-customer-scope` de su contrato). El Gateway solo comprueba que el token sea auténtico.
- Llamar a `auth-service` (valida con la clave pública que recibe por configuración).
- Reintentos, límite de tasa, caché ni transformación de cuerpos.
- CORS (el cliente es Postman; ver pendientes).
- Ocultar `config-server` ni `eureka-server`: simplemente **no tienen ruta**.

## 3. Rutas

Todas mantienen la ruta completa: **no se quita el prefijo** (`/api/v1` lo sirven los propios servicios). Solo hay rutas explícitas: `spring.cloud.gateway.discovery.locator.enabled=false`, así que lo que no está listado responde 404.

Se evalúan por `order` (el menor primero): primero las de bloqueo y luego las generales.

### 3.1 Bloqueo de endpoints internos (`order` -10)

| Id | Predicado | Respuesta |
|---|---|---|
| `deny-account-movements` | `Path=/api/v1/accounts/*/movements/**` | 403 `FORBIDDEN` ("Endpoint interno") |
| `deny-transaction-records` | `Path=/api/v1/transactions/records` | 403 `FORBIDDEN` ("Endpoint interno") |

Son los endpoints `x-internal` de los contratos (`POST /accounts/{id}/movements`, `POST /accounts/{id}/movements/{operationId}/reversal` y `POST /transactions/records`). En P3 los servicios ya no se llaman por REST y estos endpoints se retiran; la regla queda como defensa adicional.

### 3.2 Rutas generales (`order` 10)

Todas con circuit breaker de **2 s**. El nombre de la ruta y del circuit breaker es el del servicio.

| Id / breaker | Predicado (`/api/v1/...`, cualquier método) | Destino |
|---|---|---|
| `auth-service` | `/auth/**` | `lb://auth-service` |
| `auth-jwks` | `/.well-known/**` (**fuera de `/api/v1`**) | `lb://auth-service` |
| `customer-service` | `/customers/**` | `lb://customer-service` |
| `account-service` | `/accounts/**`, `/account-conditions/**` | `lb://account-service` |
| `credit-service` | `/credits/**`, `/credit-cards/**`, `/overdue-checks/**`, `/credit-recovery-runs/**` | `lb://credit-service` |
| `transaction-service` | `/deposits/**`, `/withdrawals/**`, `/transfers/**`, `/transactions/**`, `/products/**`, `/transaction-recovery-runs/**` | `lb://transaction-service` |
| `report-service` | `/reports/**` | `lb://report-service` |
| `debit-service` | `/debit-cards/**`, `/debit-payment-recovery-runs/**` | `lb://debit-service` |
| `yanki-service` | `/wallets/**`, `/wallet-payment-recovery-runs/**` | `lb://yanki-service` |

- Los endpoints de recuperación (`*-recovery-runs`) llevan el nombre de su servicio para no chocar: el Gateway enruta por prefijo.
- `/credit-recovery-runs/**` faltaba en la tabla de la definición general (sección 3); ya se agregó.

## 4. Resiliencia

### 4.1 Circuit breaker (Resilience4j)

Uno **por servicio**, para que un servicio caído no abra el circuito de los demás. Configuración común propuesta (`resilience4j.circuitbreaker.configs.default`):

| Propiedad | Valor | Nota |
|---|---|---|
| `sliding-window-type` / `sliding-window-size` | `COUNT_BASED` / `10` | |
| `minimum-number-of-calls` | `5` | No abre con muy pocas llamadas |
| `failure-rate-threshold` | `50` | % de fallos para abrir |
| `wait-duration-in-open-state` | `10s` | |
| `permitted-number-of-calls-in-half-open-state` | `3` | |
| `automatic-transition-from-open-to-half-open-enabled` | `true` | |

**Qué cuenta como fallo:** el timeout, las excepciones de conexión, la falta de instancias en Eureka y las respuestas 500, 502, 503 y 504. **No** cuentan los 4xx ni el **202** (una operación en proceso es un resultado normal).

### 4.2 Timeouts

**Todas las rutas tienen 2 s, sin excepciones** (RNF-18).

| Nivel | Valor | Dónde |
|---|---|---|
| Tiempo límite de una llamada (TimeLimiter) | **2 s** | `resilience4j.timelimiter.configs.default.timeout-duration` |
| Conexión al servicio | 1 s | `spring.cloud.gateway.httpclient.connect-timeout` |
| Tope del cliente HTTP | 3 s | `spring.cloud.gateway.httpclient.response-timeout` (nunca antes que el TimeLimiter) |

**Regla de coherencia con la espera de las sagas.** Cinco operaciones esperan un resultado y después responden 201 o 202: `POST /deposits`, `/withdrawals`, `/transfers`, `/debit-cards/{id}/payments` y `/wallets/{id}/payments`. Su espera (`payment.await-timeout`, **1,5 s** por defecto) tiene que ser **siempre menor** que los 2 s del Gateway. Si fuera igual o mayor, el Gateway cortaría la petición justo antes de que el servicio conteste y el cliente vería un 503 en lugar de un 201 o 202. Regla para el futuro: una operación que espere un resultado antes de responder debe esperar menos que el timeout del Gateway.

**Caso conocido en P1/P2** (depósito, retiro y transferencia): `transaction-service` llama a `account-service` por REST con su propio timeout de 2 s. Si la cuenta tarda, ambos vencen casi a la vez: el cliente recibe el 202 del servicio o el 503 del Gateway, según cuál gane. Ambos significan "quedó pendiente, repita con el mismo `operationId`", así que se acepta. En P3 desaparece porque la espera es de 1,5 s.

### 4.3 Respuestas de error del Gateway

Cuerpo estándar `{ timestamp, status, code, message, path }`.

| Situación | HTTP | `code` | Mensaje |
|---|---|---|---|
| Timeout, circuito abierto o servicio sin instancias | 503 | `SERVICE_UNAVAILABLE` | "El servicio no respondió a tiempo." Si la petición era un `POST` que mueve dinero: "La operación puede haberse aplicado. Repita con el mismo `operationId`." |
| Sin token o token inválido (con seguridad activa) | 401 | `UNAUTHORIZED` | |
| Endpoint interno (3.1) | 403 | `FORBIDDEN` | "Endpoint interno." |
| Ruta que no existe | 404 | `NOT_FOUND` | |

El fallback es un controlador propio (`/fallback`) al que las rutas reenvían con `fallbackUri: forward:/fallback`. Distingue el mensaje por el método y la ruta de la petición original.

Con el circuito **abierto** las operaciones de dinero se rechazan **sin haber empezado**, así que reintentar es seguro. Tras un timeout, la operación puede seguir avanzando: el cliente repite con el **mismo** `operationId` (idempotente) o consulta su estado.

## 5. Seguridad (JWT)

| Modo | Comportamiento |
|---|---|
| `security.enabled=false` (P2 y desarrollo) | Todo pasa sin token. No se descifra nada |
| `security.enabled=true` | Se exige `Authorization: Bearer <JWT>` en todo lo que no sea público |

- **Públicas (sin token):** `POST /api/v1/auth/login`, `POST /api/v1/auth/register`, `GET /.well-known/jwks.json` y `GET /actuator/health`.
- **Validación:** firma **RS256** con la clave pública de `security.jwt.public-key`, emisor igual a `security.jwt.issuer` y expiración (`exp`). No se aceptan otros algoritmos. No llama a `auth-service`.
- **No decide roles:** un token válido pasa; el servicio de destino responde 403 si el rol no alcanza.
- El encabezado `Authorization` se **reenvía sin cambios**: los servicios vuelven a validar el token (doble validación) y leen `roles`, `customerId` y `sub` de él. El Gateway no agrega encabezados de identidad.
- La seguridad corre **antes** de las rutas: una petición sin token a un endpoint interno bloqueado recibe 401, no 403.
- Cambiar `security.jwt.public-key` exige reiniciar el Gateway (sin rotación; ver `auth-service`).

## 6. Otros comportamientos

| Tema | Decisión |
|---|---|
| Identificador de solicitud | Se conserva `X-Request-Id` si viene o se genera un UUID; se envía a los servicios y se devuelve en la respuesta |
| Registro de solicitudes | Una línea por solicitud: método, ruta (**sin** parámetros de consulta), estado, milisegundos y `X-Request-Id`. Nunca cuerpos ni tokens |
| Balanceo | `spring-cloud-starter-loadbalancer`; `spring.cloud.loadbalancer.cache.ttl=5s` para enterarse pronto de altas y bajas |
| Reintentos | Ninguno en el Gateway |
| Actuator | Solo `health` e `info`; el endpoint `gateway` de rutas **no** se expone |
| Descubrimiento automático de rutas | Desactivado |

## 7. Configuración (`api-gateway.yml` en el repositorio de configuración)

Las rutas y los circuit breakers van **en YAML, no en código** (RNF-04).

| Propiedad | Valor propuesto |
|---|---|
| `server.port` | `8080` |
| `spring.cloud.gateway.routes[*]` | Las de la sección 3, con `order` y filtro `CircuitBreaker` (`name`, `fallbackUri`) |
| `spring.cloud.gateway.discovery.locator.enabled` | `false` |
| `spring.cloud.gateway.httpclient.connect-timeout` / `response-timeout` | `1000` / `3s` |
| `resilience4j.circuitbreaker.configs.default.*` y `.instances.<nombre>` | Sección 4.1 |
| `resilience4j.timelimiter.configs.default.timeout-duration` | `2s` |
| `spring.cloud.loadbalancer.cache.ttl` | `5s` |
| `security.enabled`, `security.jwt.issuer`, `security.jwt.public-key` | Comunes (vienen de `application.yml`) |
| `gateway.public-paths` | Lista de la sección 5 |

## 8. Filesystem

```
api-gateway/
├── pom.xml
├── Dockerfile
├── checkstyle.xml
├── README.md
└── src/
    ├── main/
    │   ├── java/com/bank/gateway/
    │   │   ├── ApiGatewayApplication.java
    │   │   ├── config/                    (SecurityConfig, JwtDecoderConfig)
    │   │   ├── filter/                    (RequestIdFilter, AccessLogFilter)
    │   │   ├── fallback/                  (FallbackController)
    │   │   └── error/                     (GatewayErrorHandler: 401, 404, 500 con cuerpo estándar)
    │   └── resources/application.yml      (solo nombre y config.import)
    └── test/java/com/bank/gateway/
```

## 9. Stack y configuración

| Función | Dependencia |
|---|---|
| Gateway | `spring-cloud-starter-gateway` |
| Circuit breaker | `spring-cloud-starter-circuitbreaker-reactor-resilience4j` |
| Descubrimiento y balanceo | `spring-cloud-starter-netflix-eureka-client`, `spring-cloud-starter-loadbalancer` |
| Configuración | `spring-cloud-starter-config` (con `spring.config.import`) |
| Seguridad | `spring-boot-starter-oauth2-resource-server` (`NimbusReactiveJwtDecoder.withPublicKey`) |
| Salud | `spring-boot-starter-actuator` |
| Calidad y pruebas | Checkstyle, Jacoco, JUnit 5, `reactor-test`, WebTestClient, WireMock o `MockWebServer` |

Java 17, Spring Boot 3.x y la versión de Spring Cloud compatible (base técnica).

## 10. Estrategia de pruebas

| Capa | Qué se prueba | Herramientas |
|---|---|---|
| Rutas | **Cada prefijo de cada contrato llega a su servicio.** Una prueba recorre los `paths` de los 8 `openapi.yaml` y verifica que ninguno queda sin ruta (evita otro caso como `credit-recovery-runs`) | WebTestClient contra servidores simulados |
| Orden | Los endpoints internos se bloquean antes de la ruta general (`/accounts/1/movements` → 403, `/accounts/1/balance` → pasa) | WebTestClient |
| Circuit breaker | Servicio que tarda más de 2 s → 503 en 2 s; tras 5 fallos el circuito abre y responde de inmediato; el 202 no cuenta como fallo | Servidor simulado con retardo |
| Coherencia con las esperas | Un servicio simulado que responde 202 a los 1,6 s (espera por defecto de 1,5 s + margen) llega al cliente como **202**; uno que tarda 2,3 s → 503. Prueba de configuración: `payment.await-timeout` < timeout del Gateway | Servidor simulado con retardo |
| Sin instancias | Servicio ausente en Eureka → 503 (`SERVICE_UNAVAILABLE`) | Prueba con Eureka simulado |
| JWT | Token válido pasa; sin token, expirado, con otro emisor, con otra firma o con algoritmo `none`/HS256 → 401; las 4 rutas públicas pasan sin token; con `security.enabled=false` todo pasa | WebTestClient con un par de claves de prueba |
| Reenvío | `Authorization` llega intacto al servicio; `X-Request-Id` se genera o se conserva | Servidor simulado |
| Errores | 401, 403, 404 y 503 con el cuerpo estándar | WebTestClient |

## 11. Diagramas a elaborar
- [ ] Secuencia: una solicitud con token (Gateway → validación → Eureka → servicio)
- [ ] Secuencia: circuit breaker (timeout, apertura, media apertura, cierre)
- [ ] Diagrama de despliegue (junto con `config-server` y `eureka-server`)

## 12. Decisiones y pendientes

**Decidido**
- Rutas, breakers y timeouts en el YAML del repositorio de configuración; sin descubrimiento automático de rutas.
- Un circuit breaker por servicio, con **2 s en todas las rutas, sin excepciones** (el enunciado).
- La espera de resultado de las sagas (`payment.await-timeout`, 1,5 s) es siempre menor que el timeout del Gateway.
- El Gateway solo autentica; los roles y el alcance por cliente los aplica cada servicio, que además vuelve a validar el token.
- Endpoints internos bloqueados con 403 antes de las rutas generales.
- No hay ruta hacia `config-server` ni `eureka-server`.
- Sin reintentos en el Gateway (las operaciones de dinero se reintentan por `operationId`, decisión del cliente).

**Pendiente**
- **Verificar al implementar** que el TimeLimiter de Resilience4j toma el timeout por defecto en cada ruta con la versión de Spring Cloud que se use.
- **CORS:** sin configurar. Si se agregara un front-end, se define aquí.
- **Documentación agregada** de las APIs (Swagger unificado): fuera de alcance.
- **Un solo Gateway, una instancia** (demo).

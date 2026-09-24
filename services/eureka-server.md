# `eureka-server`

> Servicio de infraestructura: sin dominio, base de datos ni API de negocio. Por eso esta ficha no sigue la plantilla completa.

## 1. Resumen

| Campo | Valor |
|---|---|
| Propósito | Registro y descubrimiento de servicios, con panel de control (RNF-16) |
| Fase | **P2** (en P1 los servicios se llaman por URL configurada) |
| Puerto | 8761 |
| Tecnología | Spring Cloud Netflix Eureka Server |
| Base de datos | Ninguna (el registro vive en memoria) |
| Depende de | `config-server` (recibe su configuración de `eureka-server.yml`) |
| Lo usan | El Gateway (rutas `lb://<nombre>`) y, en P2, los clientes REST entre servicios. **En P3 ya no hay REST entre servicios**, así que el Gateway es el único que resuelve nombres; el registro sigue siendo obligatorio (RNF-16) y el panel muestra todos los servicios |

## 2. Responsabilidades

**Hace:**
- Recibir el registro y los latidos de cada instancia y publicar la lista de instancias vivas.
- Mostrar el **panel** en `http://localhost:8761/`: aplicaciones, instancias, estado.
- Sacar del registro a las instancias que dejan de enviar latidos.

**No hace:**
- Balanceo de carga (lo hace el cliente: `spring-cloud-starter-loadbalancer`).
- Registrarse a sí mismo ni replicarse: una sola instancia, sin pares (demo).
- Autenticación: panel y API sin protección; solo dentro de la red del `docker-compose` y **sin ruta en el Gateway**.

## 3. Nombres registrados

El nombre es el `spring.application.name` del servicio. Debe coincidir con el `lb://` de las rutas del Gateway (`services/api-gateway.md`).

| Nombre en Eureka | Puerto | Fase de registro |
|---|---|---|
| `api-gateway` | 8080 | P2 |
| `customer-service` | 8081 | P2 |
| `account-service` | 8082 | P2 |
| `credit-service` | 8083 | P2 |
| `transaction-service` | 8084 | P2 |
| `report-service` | 8085 | P2 |
| `auth-service` | 8086 | P3 |
| `debit-service` | 8087 | P3 |
| `yanki-service` | 8088 | P3 |

`config-server` y `eureka-server` **no** aparecen en el panel.

## 4. Configuración

### 4.1 Del servidor (`eureka-server.yml` en el repositorio de configuración)

| Propiedad | Valor propuesto | Motivo |
|---|---|---|
| `server.port` | `8761` | |
| `eureka.client.register-with-eureka` | `false` | No se registra a sí mismo |
| `eureka.client.fetch-registry` | `false` | No lee su propio registro |
| `eureka.server.enable-self-preservation` | `false` | Con una sola instancia y arranques y paradas frecuentes en el demo, el modo de autopreservación deja instancias muertas en el panel. **Solo para el demo** |
| `eureka.server.eviction-interval-timer-in-ms` | `5000` | Limpia instancias caídas cada 5 s |
| `eureka.server.response-cache-update-interval-ms` | `5000` | Los cambios se ven en 5 s y no en 30 s |
| `eureka.server.wait-time-in-ms-when-sync-empty` | `0` | Arranca sin esperar a otros pares |

### 4.2 De los clientes (van en `application.yml` común del repositorio de configuración)

| Propiedad | Valor propuesto | Motivo |
|---|---|---|
| `eureka.client.service-url.defaultZone` | `http://localhost:8761/eureka` (`docker`: `http://eureka-server:8761/eureka`) | |
| `eureka.client.enabled` | `false` en P1, `true` desde P2 | |
| `eureka.client.registry-fetch-interval-seconds` | `5` | El Gateway se entera pronto de altas y bajas |
| `eureka.client.healthcheck.enabled` | `true` | El estado en Eureka sigue al `/actuator/health` del servicio: si Mongo cae, la instancia pasa a `DOWN` y el Gateway deja de enviarle tráfico |
| `eureka.instance.prefer-ip-address` | `true` | En Docker el nombre de host del contenedor no siempre resuelve |
| `eureka.instance.instance-id` | `${spring.application.name}:${server.port}` | Identifica cada instancia en el panel |
| `eureka.instance.lease-renewal-interval-in-seconds` | `5` | Latido cada 5 s |
| `eureka.instance.lease-expiration-duration-in-seconds` | `15` | Si pasan 15 s sin latido, se retira |
| `management.health.redis.enabled` | `false` | Redis es solo caché: que caiga **no** debe sacar al servicio de Eureka. Mongo sí cuenta |

Con estos valores una instancia que cae desaparece del Gateway en unos 15 a 20 s. Con los valores por defecto serían unos 90 s, demasiado para una demo.

## 5. Interfaz

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/` | Panel de control |
| `GET` | `/eureka/apps` | Registro completo (XML o JSON) |
| `GET` | `/actuator/health` | Salud (la usa el `docker-compose`) |

## 6. Filesystem

```
eureka-server/
├── pom.xml
├── Dockerfile
├── checkstyle.xml
├── README.md
└── src/
    ├── main/
    │   ├── java/com/bank/eureka/EurekaServerApplication.java     (@EnableEurekaServer)
    │   └── resources/application.yml                              (solo nombre y config.import)
    └── test/java/com/bank/eureka/EurekaServerApplicationTests.java
```

## 7. Stack y configuración

| Función | Dependencia |
|---|---|
| Servidor de registro | `spring-cloud-starter-netflix-eureka-server` |
| Configuración | `spring-cloud-starter-config` (con `spring.config.import`, sin `bootstrap.yml`; ver `services/config-server.md`, sección 4) |
| Salud | `spring-boot-starter-actuator` |
| Calidad y pruebas | Checkstyle, Jacoco, JUnit 5 |

Es un servidor servlet (Tomcat): usa `spring-boot-starter-web`, no WebFlux ni RxJava (no hay lógica reactiva; el servidor de Eureka no funciona sobre WebFlux). Java 17 y Spring Boot 3.x como el resto.

**Resiliencia:** una sola instancia. Si cae, los clientes siguen con la última lista que descargaron y no se enteran de altas ni bajas hasta que vuelva. Para el demo es aceptable.

## 8. Estrategia de pruebas

| Capa | Qué se prueba | Herramientas |
|---|---|---|
| Arranque | El contexto levanta y `/eureka/apps` responde | `@SpringBootTest` (puerto aleatorio) |
| Registro | Un cliente de prueba se registra y aparece en `/eureka/apps`; al detenerlo desaparece | `@SpringBootTest` con un cliente embebido |
| Verificación manual del demo | Con todo arriba, el panel lista los 9 nombres de la sección 3 (los de P3 solo desde P3), todos `UP` | Panel de control |
| Salud | Con Mongo detenido, el servicio afectado pasa a `DOWN` en el panel; con Redis detenido, sigue `UP` | Manual |

## 9. Diagramas a elaborar
- [ ] Diagrama de despliegue (el mismo de `config-server`, con Eureka y sus clientes)
- [ ] Secuencia: un servicio se registra, envía latidos y el Gateway lo descubre

## 10. Decisiones y pendientes

**Decidido**
- Una sola instancia, sin pares, sin autopreservación (demo).
- El estado en Eureka sigue al de la salud del servicio (`healthcheck.enabled`), y solo Mongo cuenta: Redis y Kafka no deben sacarlo del registro.
- Latidos cada 5 s y expiración a los 15 s para que las caídas se noten rápido.
- `config-server` y `eureka-server` no se registran.
- Panel sin autenticación y sin ruta en el Gateway.

**Pendiente**
- **Kafka y el estado de salud:** Spring Boot no trae un indicador de salud de Kafka. Si se agregara uno propio, no debe hacer pasar el servicio a `DOWN` por sí solo.
- **Proteger el panel** con autenticación básica si se expone fuera del `docker-compose`. Fuera del alcance del demo.

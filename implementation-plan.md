# Plan de implementación

> Cómo se va a construir el sistema, en qué orden y cómo vamos a trabajar. Se apoya en `bootcamp-bank-microservices-definition.md` (visión), `services/*.md` (fichas), `flows/*.md` (sagas) y `contracts/` (OpenAPI, modelos de datos y contrato de Kafka). Si algo difiere, prevalecen las fichas y los contratos.
>
> **Estado:** borrador para revisión. Las decisiones que necesitan tu confirmación están en la sección 9.

---

## 1. Cómo vamos a trabajar (modo guiado)

Quieres **entender** la implementación, no solo recibirla. Por eso el plan está hecho de **pasos pequeños**, cada uno con un objetivo, algo que se aprende y una forma de comprobar que terminó.

**Reparto de papeles**
- **Tú** escribes el código en tu máquina, ejecutas los comandos y ves los resultados. Es lo que te hace entenderlo.
- **Yo** explico el concepto antes de cada paso, te doy el código o el esqueleto de lo que toca, reviso lo que escribes y te ayudo a leer los errores.
- **No puedo compilar ni ejecutar el proyecto aquí:** desde mi entorno no se descargan las dependencias de Maven. Toda verificación real ocurre en tu máquina. Cuando algo falle, me pegas la salida de la consola y trabajamos desde ahí. Por eso el primer paso técnico es un **spike** (sección 5, paso 0.6) que confirma las piezas dudosas antes de construir sobre ellas.

**Ciclo de cada paso**

| # | Qué pasa | Quién |
|---|---|---|
| 1 | Se abre el paso: objetivo, documentos de referencia y el concepto que se aprende (explicación corta) | Yo |
| 2 | Se escribe la prueba o el contrato del paso (cuando aplica: dominio y casos de uso se prueban primero) | Tú, con mi guía |
| 3 | Se escribe el código hasta que la prueba pase | Tú, con mi guía |
| 4 | Se ejecuta lo indicado en **"Hecho cuando"** (prueba, comando `curl`, paso de Postman) | Tú |
| 5 | Revisamos juntos: qué quedó, qué se hizo distinto a la ficha, qué se anota como decisión | Ambos |
| 6 | Commit con el mensaje acordado y, si cierra un servicio, etiqueta | Tú |

**Reglas**
- **Una rebanada vertical primero.** En el primer servicio se construye *un solo endpoint* de punta a punta (dominio → caso de uso → Mongo → controller) antes de agregar el resto. Así entiendes cómo encajan las capas y luego repetimos el patrón sin sorpresas.
- **Contract-first.** Se copia el `openapi.yaml` del servicio, se generan las interfaces y los DTOs, y el controller los implementa. No se editan los DTOs generados.
- **Dominio sin frameworks.** El dominio no importa Spring, Mongo, Kafka ni código generado. Se prueba con JUnit puro.
- **Pruebas junto con el código**, no al final: Jacoco debe reflejar todo lo desarrollado.
- **Todo lo que la ficha marca como "verificar al implementar"** se resuelve en el spike o en el paso que lo usa, y se anota el resultado en la ficha.
- **Si el código y la ficha discrepan**, no se parcha en silencio: se decide cuál manda y se actualiza el documento.
- **Cada sesión** termina con el repositorio compilando y las pruebas en verde, para poder retomar sin arrastrar roturas.

---

## 2. Decisiones técnicas de base (propuesta)

> Las versiones se **confirman en el paso 0.5/0.6** al resolver las dependencias en tu máquina; desde aquí no pude comprobarlas contra Maven Central.

| Tema | Propuesta | Notas |
|---|---|---|
| Java | **17** | Ya asumido en todas las fichas (records, `switch` con patrones simples, `Clock`) |
| Spring Boot | **3.5.x** | Las fichas y los contratos usan Boot 3 (`useSpringBoot3`, `resilience4j-spring-boot3`). Boot 4 ya existe pero cambia bastante (Spring Framework 7, Jackson 3) y el ecosistema (generador OpenAPI, Resilience4j, adaptadores RxJava) va detrás. **3.5 salió del soporte de código abierto en junio de 2026**: para un proyecto de bootcamp no importa, pero conviene saberlo. Ver decisión 9.1 |
| Spring Cloud | **2025.0.x** (la línea compatible con Boot 3.5) | Config Server, Eureka, Gateway, LoadBalancer, circuit breaker. Se fija con el BOM `spring-cloud-dependencies` |
| Construcción | Maven 3.9 | Un `pom.xml` por servicio (1 repositorio por microservicio, como pide el enunciado) |
| Reactivo | WebFlux + **RxJava 3** en puertos y controllers (decidido) | El generador produce tipos Reactor; se adaptan en el borde. Se prueba en el spike |
| Persistencia | `spring-boot-starter-data-mongodb-reactive`, `RxJava3CrudRepository`, **sin `@Query`** | Convenciones en `contracts/README.md` |
| Mongo | 7.x o 8.x, **un solo nodo como *replica set*** (`--replSet rs0`) | Necesario para transacciones (`UnitOfWorkPort`) |
| Kafka | Modo KRaft (sin ZooKeeper), un solo broker | Solo desde P3 |
| Redis | 7.x | Solo desde P3 |
| Mapeo y utilidades | Lombok, MapStruct (orden de procesadores: Lombok, `lombok-mapstruct-binding`, MapStruct) | |
| Contrato | `openapi-generator-maven-plugin`, opciones en `contracts/README.md` | |
| Resiliencia | Resilience4j (`resilience4j-spring-boot3`, `resilience4j-reactor`) | Desde P2 |
| Seguridad | `spring-boot-starter-oauth2-resource-server` | Desde P3 |
| Calidad | Checkstyle (plugin en el `pom.xml`, reglas propuestas: Google Style relajado) y Jacoco | Checkstyle desde el primer servicio, para no arrastrar deuda |
| Pruebas | JUnit 5, Mockito, `reactor-test` y RxJava `TestObserver`, WebTestClient, WireMock o `MockWebServer`, Testcontainers (Mongo y Kafka) *opcional* | |
| Contenedores | Docker + `docker-compose` | Dockerfile por servicio desde P2 |
| Paquete base | `com.bank.<servicio>` (`com.bank.customer`, `com.bank.yanki`, `com.bank.gateway`…) | Diseño hexagonal: `domain`, `application`, `infrastructure` (fichas, sección 8) |
| Identificadores | `groupId` `com.bank`, `artifactId` = nombre del servicio | |
| Configuración de cada servicio | Solo `spring.application.name` y `spring.config.import` (sin `bootstrap.yml`) | `services/config-server.md`, sección 4 |

### 2.1 Repositorios

Un repositorio por microservicio (entregables del enunciado), más los de apoyo.

| Repositorio | Contenido |
|---|---|
| `bank-docs` | Esta documentación y la carpeta `contracts/` (fuente única de los contratos) |
| `bank-config` | Repositorio de configuración del Config Server |
| `bank-platform` | `docker-compose` (infraestructura y servicios), scripts de arranque |
| `bank-postman` | Colección de Postman (RNF-29): una carpeta por bloque del guion |
| `bank-service-template` | Plantilla base (repositorio "template" de GitHub) para arrancar cada servicio |
| `config-server`, `eureka-server`, `api-gateway` | Infraestructura |
| `customer-service`, `account-service`, `credit-service`, `transaction-service`, `report-service`, `auth-service`, `debit-service`, `yanki-service` | Servicios de negocio |

**Los contratos** viven en `bank-docs/contracts/`. Cada servicio copia su `openapi.yaml` y `common-schemas.yaml` a `src/main/resources/openapi/` (con la misma estructura, para que `../common/...` resuelva) mediante un script sencillo de copia. Una prueba o paso de CI compara la copia con el original. La alternativa (un repositorio `bank-contracts` aparte) queda descartada por ahora para no sumar otro repositorio.

**Ramas y commits:** `main` siempre compila; una rama por paso (`feat/customer-create`); mensajes tipo *Conventional Commits* (`feat:`, `test:`, `docs:`); etiqueta al cerrar cada fase (`p1`, `p2`, `p3`) y cada servicio (`customer-v1`).

---

## 3. Cómo se construye cada servicio (receta)

Es el mismo camino para los 8 servicios de negocio. Los pasos de las fases lo citan como **R1 a R10**.

| Paso | Qué se hace | Qué se aprende | Hecho cuando |
|---|---|---|---|
| **R1. Esqueleto** | Crear el repositorio desde la plantilla, copiar el contrato, generar interfaces y DTOs, `application.yml` con `config.import`, entrada en `bank-config` | Contract-first; qué genera el plugin y qué no se toca | `mvn verify` compila y el servicio arranca vacío |
| **R2. Dominio** | Value objects, aggregate, enums, excepciones y reglas de la ficha (sección 3). Pruebas primero | DDD: invariantes dentro del aggregate; pruebas sin Spring | Pruebas de dominio en verde |
| **R3. Casos de uso y puertos** | Interfaces de entrada y salida (`Single`/`Maybe`/`Completable`/`Flowable`), casos de uso, adaptadores *no-op* o *en memoria* para las pruebas | Arquitectura hexagonal; RxJava en los puertos | Pruebas de casos de uso con `TestObserver` |
| **R4. Persistencia** | `*Document`, mappers MapStruct, repositorios, índices, `UnitOfWorkPort` si aplica | Mongo reactivo con RxJava; índices únicos y parciales; `Decimal128` | Pruebas de mapeo y, con Testcontainers o el Mongo del compose, de índices |
| **R5. Adaptadores de entrada** | Controllers que implementan la interfaz generada, adaptación Reactor ↔ RxJava, `GlobalExceptionHandler` con el cuerpo estándar | Cómo se traduce una regla de negocio a 4xx | Pruebas con WebTestClient |
| **R6. Configuración y arranque** | Propiedades en `bank-config`, conexión a Mongo, arranque real contra el Config Server | Configuración externa y perfiles | `curl` a los endpoints con datos reales |
| **R7. Clientes salientes** *(si los hay)* | Adaptadores REST con timeout (P1) y circuit breaker de 2 s (P2); en P3, eventos | Resilience4j; pruebas con servidor simulado | Prueba con servicio simulado lento y caído |
| **R8. Calidad** | Checkstyle sin errores, cobertura de Jacoco revisada, casos borde de la ficha (sección 10) | Qué significa "cubierto" | `mvn verify` limpio y reporte generado |
| **R9. Postman** | Carpeta de la colección con los pasos del guion que ya funcionan | Probar el servicio como lo verá el usuario | La carpeta corre completa |
| **R10. Cierre** | README del servicio, diagramas pendientes de su ficha (sección 11), commit y etiqueta | | Servicio etiquetado |

**Definición de terminado de un servicio:** contrato implementado sin desviaciones (o desviaciones anotadas en la ficha) · pruebas de dominio, casos de uso, adaptadores y controllers · Checkstyle limpio · Jacoco generado · carpeta de Postman · README · diagramas de su ficha · etiqueta.

---

## 4. Estimación de esfuerzo

Medida en **sesiones de trabajo** (unas 2 a 3 horas). Es gruesa: se recalibra al terminar `customer-service`, que es el más simple y donde se aprende el patrón.

| Fase | Contenido | Sesiones |
|---|---|---|
| 0 | Entorno, plantilla, Mongo, Config Server, spike | 5 a 7 |
| P1 | `customer`, `account` básico, `transaction`, `credit` | 22 a 30 |
| P2 | `eureka`, `api-gateway`, Resilience4j, VIP/PYME, transferencias, `report`, calidad y Docker | 18 a 25 |
| P3 | Kafka y Redis, migración de los servicios, `auth`, `debit`, `yanki`, deuda vencida, reportes por eventos, Postman final | 35 a 50 |

Las **fechas de entrega** de cada parte del bootcamp no las conozco: con ellas se pone calendario a las fases (sección 9.4).

---

## 5. Fase 0 — Base

| Paso | Qué se hace | Qué se aprende | Hecho cuando |
|---|---|---|---|
| **0.1 Entorno** | JDK 17, Maven 3.9, Docker Desktop, Git, IDE, Postman | — | `java -version`, `mvn -v`, `docker run hello-world`, `git --version` |
| **0.2 Repositorios** | Crear los repositorios de 2.1 (los de servicios pueden esperar), subir `bank-docs` | Convenciones de ramas y commits | `bank-docs`, `bank-config` y `bank-platform` en GitHub |
| **0.3 Mongo en Docker** | `docker-compose` con Mongo de un solo nodo como *replica set* y su inicialización, con *healthcheck* | Por qué transacciones exigen *replica set*; volúmenes | `rs.status()` responde con un nodo `PRIMARY` |
| **0.4 `config-server`** | Servicio Spring Cloud Config con backend Git; archivo `application.yml` común en `bank-config`. Ficha: `services/config-server.md` | Configuración externa, precedencia de archivos, `spring.config.import` | `curl localhost:8888/customer-service/default` devuelve las propiedades |
| **0.5 Plantilla de servicio** | `pom.xml` (BOM, Lombok, MapStruct, generador, Checkstyle, Jacoco, WebFlux, RxJava 3, Mongo reactivo), estructura de paquetes, `checkstyle.xml`, `application.yml`, `logback-spring.xml`, Dockerfile | Orden de los procesadores de anotaciones, opciones del generador, qué trae cada dependencia | Un servicio de ejemplo generado desde la plantilla compila y arranca |
| **0.6 Spike técnico** | Un servicio de juguete que confirma, con pruebas, las piezas dudosas (lista debajo) | Los límites reales de las herramientas, antes de depender de ellas | Informe de hallazgos y fichas corregidas donde haga falta |

**Qué confirma el spike (0.6)** — son los puntos marcados "verificar al implementar" en las fichas:

| # | Comprobación | Si falla |
|---|---|---|
| 1 | El generador, con `reactive=true`, produce interfaces con `Mono`/`Flux` y el controller las puede implementar devolviendo RxJava a través de un adaptador en el borde | Ajustar opciones del generador o el adaptador |
| 2 | `RxJava3CrudRepository`: métodos derivados con `Between` y `Range<Instant>`, dos condiciones sobre la misma propiedad, `existsBy…`, `findBy…In` | Filtrar en memoria o usar `Between` (alternativas anotadas en los modelos de datos de `transaction`, `report` y `debit`) |
| 3 | Índices únicos **parciales** (`partialFilterExpression`) creados desde las anotaciones de Spring Data | Crear los índices con un `IndexOperations` propio al arrancar |
| 4 | `BigDecimal` como `Decimal128`; `LocalDate` y `YearMonth` como texto, incluso en los parámetros de las consultas derivadas | Ajustar `MongoCustomConversions` |
| 5 | Transacción de dos documentos con `TransactionalOperator` y el *replica set* del compose | Revisar el URI (`replicaSet`, `directConnection`) |
| 6 | `Clock` inyectado y zona `America/Lima` | — |
| 7 | Resilience4j reactivo: circuit breaker y *time limiter* de 2 s sobre un `WebClient` | Ajustar la versión |
| 8 | (P3) productor y consumidor de Kafka con el sobre común y reintentos 1 s / 2 s / 4 s con `.DLT` | Se difiere al paso 3.1 |

**Hito 0:** `config-server` sirviendo propiedades, Mongo en *replica set*, plantilla lista y spike documentado.

---

## 6. Fase P1 — Base del sistema

Las cuatro entregas usan la receta R1–R10. Todos los servicios de P1 se construyen con **adaptadores REST y adaptadores *no-op*** para lo que llega en P3 (eventos, Redis). Así en P3 solo se cambian adaptadores y el dominio no se toca (definición, sección 2.1). `security.enabled=false` en todo.

| Paso | Servicio | Ficha y contratos | Qué tiene de particular (y qué se aprende) | Sesiones |
|---|---|---|---|---|
| **1.1** | `customer-service` | `services/customer-service.md`, `contracts/customer-service/` | **El servicio modelo.** Una sola rebanada vertical primero (`POST /customers`), luego el resto. Un aggregate, baja lógica, índice único de documento, cliente personal o empresa (RUC). Aprendes el patrón completo | 6 a 8 |
| **1.2** | `account-service` (reglas básicas) | `services/account-service.md`, `contracts/account-service/` | Reglas por tipo de cliente y de cuenta (1 ahorro, 1 corriente, N plazo fijo; empresas), catálogo de condiciones en Mongo, `Clock`, control optimista. **Primer cliente REST** saliente (a `customer-service`, con timeout). Endpoints internos de movimiento (`x-internal`). Sin VIP/PYME ni comisiones todavía | 7 a 9 |
| **1.3** | `transaction-service` (depósito, retiro, historial) | `services/transaction-service.md`, `contracts/transaction-service/` | Idempotencia por `operationId`, historial de movimientos, `UnitOfWorkPort` (transacción de Mongo), `POST /transactions/records`. Cliente REST a `account-service`. Sin transferencias todavía | 6 a 8 |
| **1.4** | `credit-service` | `services/credit-service.md`, `contracts/credit-service/` | Créditos, tarjetas de crédito, pagos y consumos; plazos con `Clock`; registro del historial en `transaction-service` (cliente REST). Sin deuda vencida ni pago de terceros todavía | 6 a 8 |

**Hito P1 — checklist de entrega**
- Los cuatro servicios con etiqueta y sus carpetas de Postman.
- Pasos del guion (`bootcamp-bank-microservices-definition.md`, sección 7) que ya deben funcionar: **3, 4, 7, 8, 9, 11, 12, 16, 17, 18**.
- Config Server en uso: ninguna propiedad en el código.
- Diagramas de cada ficha (sección 11) al menos en borrador.

---

## 7. Fase P2 — Robustez y ecosistema

| Paso | Qué se hace | Ficha | Qué se aprende | Sesiones |
|---|---|---|---|---|
| **2.1** | `eureka-server`; cada servicio se registra (`eureka.client.enabled=true`) | `services/eureka-server.md` | Registro y descubrimiento, latidos, panel | 2 a 3 |
| **2.2** | `api-gateway`: rutas por prefijo con `lb://`, circuit breaker de 2 s, endpoints internos bloqueados. Sin JWT todavía | `services/api-gateway.md` | Spring Cloud Gateway, orden de rutas, fallback, por qué la espera de las sagas debe ser menor que 2 s | 4 a 5 |
| **2.3** | Circuit breaker y timeout de 2 s en **todos los clientes REST** (`account`→`customer`, `credit`→`customer`/`transaction`, `transaction`→`account`) | Fichas, sección 9 | Resilience4j reactivo; pruebas con servidor simulado lento y caído | 3 a 4 |
| **2.4** | `account-service`: VIP y PYME, apertura mínima, comisiones, transacciones libres, promedio diario; cliente REST a `credit-service` (tarjeta activa) | `services/account-service.md`, `contracts/account-service/data-model.md` | Reglas de cálculo, catálogo de condiciones, dependencia entre servicios | 4 a 6 |
| **2.5** | `transaction-service`: **transferencias** con saga orquestada por REST, compensación y recuperación | `flows/01-transfer.md`, `contracts/transaction-service/` | Sagas, idempotencia con reemisión, qué no se compensa por un timeout | 5 a 7 |
| **2.6** | `report-service` por REST (`report.source=rest`) | `services/report-service.md`, `contracts/report-service/` | Consumir varias fuentes con paginación y agregar; 503 si una fuente cae | 3 a 4 |
| **2.7** | Calidad y contenedores: Checkstyle limpio en todos, Jacoco, Dockerfile por servicio, `docker-compose` de los servicios | — | Imágenes, redes de Docker, perfil `docker` | 3 a 4 |

**Hito P2 — checklist de entrega**
- Todos los servicios registrados en el panel de Eureka; todo se usa **a través del Gateway** (puerto 8080).
- Circuit breaker demostrable: detener un servicio y ver el 503 estándar en 2 s.
- Pasos del guion que ya deben funcionar (además de los de P1): **10, 13, 14, 20, 21, 36, 37**.
- Reportes de Jacoco y Checkstyle sin errores por servicio.
- `docker-compose` levanta el sistema completo con un comando.

---

## 8. Fase P3 — Arquitectura orientada a eventos

Orden pensado para no romper lo que ya funciona: primero la infraestructura, luego migrar los servicios existentes uno por uno (cada uno se puede probar solo), después los servicios nuevos y por último las reglas transversales.

| Paso | Qué se hace | Ficha y contratos | Qué se aprende | Sesiones |
|---|---|---|---|---|
| **3.1** | Kafka (KRaft) y Redis en el `docker-compose`; creación de tópicos según el contrato; código común de eventos (sobre, serialización, reintentos 1 s/2 s/4 s, `.DLT`) copiado a cada servicio | `contracts/events/kafka-contract.md` | Tópicos, claves, compactación, consumo *al menos una vez* | 4 a 6 |
| **3.2** | `customer-service`: publica `customer.*` y usa Redis (`customer:{id}`) | `services/customer-service.md` | Publicar eventos con estado completo; caché *cache-aside* | 3 a 4 |
| **3.3** | `account-service`: copias locales (cliente, tarjeta, deuda), consume comandos de movimiento y publica resultados y `account.*`; Redis para el catálogo | `services/account-service.md`, `contracts/account-service/data-model.md` | Read models, idempotencia de consumidores, último cambio gana por fecha | 6 a 8 |
| **3.4** | `transaction-service`: comandos y resultados por Kafka, copia de cuentas, consumo de eventos de crédito; la transferencia pasa a la saga por Kafka con espera de 1,5 s y 202 | `flows/01-transfer.md`, `contracts/transaction-service/` | Saga por mensajes, awaiter en memoria, el consumidor como único escritor | 6 a 8 |
| **3.5** | `credit-service`: publica eventos, copia de cliente, **deuda vencida** (proceso diario y `POST /overdue-checks`), pago de terceros (RF-26) | `flows/04-overdue-debt.md`, `contracts/credit-service/` | Procesos programados con `Clock`, eventos de estado (`overdue.detected/cleared`) | 5 a 7 |
| **3.6** | `auth-service` (JWT RS256, login con bloqueo, usuarios, JWKS) y JWT en el Gateway; `security.enabled=true` en todos | `services/auth-service.md`, `flows/05-customer-onboarding-and-access.md`, `contracts/auth-service/` | Firmas asimétricas, claims, autorización por rol y `customerId` | 6 a 8 |
| **3.7** | `debit-service` (tarjetas, cuentas asociadas, pagos con espera y recuperación) | `services/debit-service.md`, `flows/02-debit-payment.md`, `contracts/debit-service/` | Orquestador de un solo paso, read models, recuperación | 6 a 8 |
| **3.8** | `yanki-service` (monederos, saga de cuatro rutas, `visibleTo`, asociación a tarjeta) | `services/yanki-service.md`, `flows/03-yanki-payment.md`, `contracts/yanki-service/` | Pasos locales y remotos en una misma saga, idempotencia por `legId` | 8 a 10 |
| **3.9** | Deuda vencida aplicada en `account`, `credit` y `debit` (bloquea **adquirir** productos nuevos, no operar ni pagar) | `flows/04-overdue-debt.md` | Reglas que dependen de otro servicio sin llamarlo | 2 a 3 |
| **3.10** | `report-service` por eventos (`report.source=readmodel`), reportes por categoría y de la tarjeta de débito | `services/report-service.md`, `contracts/report-service/data-model.md` | Read model propio; excluir movimientos revertidos | 4 a 6 |
| **3.11** | Cierre: colección de Postman completa, guion de demo de punta a punta, diagramas y UML, README de cada repositorio | Definición, sección 7 | | 4 a 6 |

**Hito P3 — checklist de entrega**
- Los 11 servicios y la infraestructura levantan con `docker-compose`.
- **Los 42 pasos del guion de demo** corren en Postman contra el Gateway, con JWT activado.
- Los nuevos servicios (`auth`, `debit`, `yanki`) no llaman por REST a otros.
- Tópicos, `.DLT` y reintentos comprobados con un mensaje inválido.
- Repositorio de Postman, Jacoco y Checkstyle de todos los servicios, diagramas de secuencia, UML y draw.io.

---

## 9. Decisiones y preguntas abiertas

| # | Tema | Decisión (24-sep-2026) |
|---|---|---|
| **9.1** | Versión de Spring Boot | **Confirmado:** Boot 3.5.x con Spring Cloud 2025.0.x. Se verifica al resolver dependencias (paso 0.5) |
| **9.2** | Dónde viven los repositorios | **Una organización de GitHub** para mantenerlos ordenados, con repositorios **públicos** y visibles desde el perfil personal (membresía pública de la organización y repositorios principales fijados en el perfil). Nombre de la organización: por definir |
| **9.3** | Repositorio de configuración | `bank-config` en la organización (carpeta local con `file://` para trabajar sin red) |
| **9.4** | Fechas de entrega | **Se mantiene el modo guiado.** El domingo 26-sep-2026 es una **meta personal**, no la entrega oficial (que es posterior, sin fecha fijada). Se avanza en orden y las fases se calendarizan cuando se conozca la entrega oficial |
| **9.5** | IDE y sistema operativo | **IntelliJ IDEA, Docker Desktop, Windows.** Los comandos se dan para PowerShell (o Git Bash) |
| **9.6** | Checkstyle | Google Style relajado; el instructor no pidió reglas específicas |
| **9.7** | Cobertura | 80 % de líneas en dominio y casos de uso como meta propia |
| **9.8** | Código común de Kafka | Copiado a cada servicio (sin librería compartida) |

> **Nota sobre el plazo.** La estimación de la sección 4 suma unas 80 a 110 sesiones de 2 a 3 horas para el alcance completo con pruebas; terminar todo el 26-sep no es posible. Como el 26 es solo una meta personal, se conserva el modo guiado y se ve cuánto se avanza (objetivo razonable: la Fase 0 y `customer-service`, y empezar `account-service`).

---

## 10. Riesgos

| Riesgo | Por qué importa | Cómo se reduce |
|---|---|---|
| RxJava sobre WebFlux y el generador (Reactor) | Todos los controllers dependen de esa adaptación | Spike 0.6 (puntos 1 y 2) antes del primer servicio |
| Consultas derivadas de Spring Data con rangos de fechas | Reportes e historiales las usan | Spike 0.6 (punto 2); alternativa de filtrar en memoria ya anotada |
| Índices parciales y transacciones de Mongo | Yanki y transacciones dependen de ellos | Spike 0.6 (puntos 3 y 5) |
| Volumen: 11 servicios, 3 fases | Es mucho trabajo para una persona | Se construye por rebanadas, con plantilla y receta común; se recalibra con `customer-service` |
| Migración a Kafka de servicios que ya funcionan | Riesgo de romper P1/P2 | Puertos con adaptadores *no-op* desde P1; se migra un servicio por vez y las pruebas de los casos de uso no cambian |
| No puedo compilar desde mi entorno | Los errores aparecen en tu máquina | Pasos pequeños; me pegas la salida; el spike descubre incompatibilidades pronto |
| Servicios con espera de 1,5 s en un entorno lento | Verás muchos 202 en el demo | Es el comportamiento esperado; el cliente consulta o repite con el mismo `operationId`. Se ajustan `linger.ms` y `fetch.max.wait.ms` |

---

## 11. Siguiente paso

Con las decisiones de la sección 9 confirmadas, se empieza por **0.1 Entorno** y se avanza en orden. Cada paso se abre con su explicación y su verificación.

# Definición de Microservicios — Visión consolidada

> **Cómo leer la documentación**
> - `bootcamp-bank-microservices-guide.md`: enunciado, stack, prácticas, requisitos (RF/RNF) y plan general.
> - **Este documento:** índice consolidado, dependencias, rutas del Gateway, eventos, convenciones, seguridad, guion de demo y pendientes.
> - `services/<servicio>.md`: detalle de cada servicio (dominio, puertos, API, filesystem, stack, pruebas). **Si hay diferencias, prevalece la ficha del servicio.**
>
> **Estado (2026-09-24):** los 8 servicios de negocio y los 3 de infraestructura (`config-server`, `eureka-server`, `api-gateway`) están definidos, con sus contratos, y los flujos con sagas diseñados. Siguen el plan de implementación y la base técnica (ver sección 10).
> Los requisitos se verificaron contra el enunciado original ("Proyecto General vf").

---

## 1. Mapa de servicios

| Servicio | Puerto | Fase | Responsabilidad | Aggregates | Colecciones propias | Ficha |
|---|---|---|---|---|---|---|
| `config-server` | 8888 | P1 | Propiedades externalizadas (repo Git de configuración) | — | — | `services/config-server.md` |
| `eureka-server` | 8761 | P2 | Registro y descubrimiento, con panel | — | — | `services/eureka-server.md` |
| `api-gateway` | 8080 | P2 | Entrada única, rutas, circuit breaker 2 s, validación JWT | — | — | `services/api-gateway.md` |
| `customer-service` | 8081 | P1 | Clientes, tipo y perfil | `Customer` | `customers` | `services/customer-service.md` |
| `account-service` | 8082 | P1 (VIP/PYME y comisiones P2) | Cuentas, saldo y reglas de movimiento | `Account`, `AccountProduct` | `accounts`, `account_products`, `account_operations` | `services/account-service.md` |
| `credit-service` | 8083 | P1 | Créditos, tarjetas de crédito, pagos, consumos. **Fuente de la deuda vencida** | `Credit`, `CreditCard` | `credits`, `credit_cards`, `credit_operations` | `services/credit-service.md` |
| `transaction-service` | 8084 | P1 (transferencias P2) | Historial de movimientos. Puerta de entrada de depósitos, retiros y transferencias. Orquesta la saga de transferencias | `Transaction`, `Transfer` | `transactions`, `transfers` | `services/transaction-service.md` |
| `report-service` | 8085 | P2 | Reportes de solo lectura | `ReportProduct`, `ReportMovement` (lectura) | P2: ninguna. P3: `report_products`, `report_movements` | `services/report-service.md` |
| `auth-service` | 8086 | P3 | Usuarios, login y JWT | `User` | `users` | `services/auth-service.md` |
| `debit-service` | 8087 | P3 | Tarjetas de débito y pagos con ellas | `DebitCard`, `DebitPayment` | `debit_cards`, `debit_payments` | `services/debit-service.md` |
| `yanki-service` | 8088 | P3 | Monederos y pagos por celular | `Wallet`, `WalletPayment` | `wallets`, `wallet_payments` | `services/yanki-service.md` |

**Copias locales (read models, P3)** alimentadas por eventos:

| Servicio | Copias que mantiene |
|---|---|
| `account-service` | `customer_snapshots`, `credit_card_snapshots`, `overdue_customers` |
| `credit-service` | `customer_snapshots` |
| `transaction-service` | `account_snapshots` |
| `debit-service` | `customer_snapshots`, `account_snapshots`, `overdue_customers` |
| `yanki-service` | `customer_snapshots`, `debit_card_snapshots` |
| `auth-service` | `customer_snapshots` |

Estructura de carpetas, arquitectura hexagonal y plantilla: ver `services/_TEMPLATE.md` y la sección 8 de cada ficha.

---

## 2. Dependencias y orden de construcción

### 2.1 De REST (P1/P2) a eventos (P3)

Cada servicio existente cambia **solo sus adaptadores** en P3; el dominio y los casos de uso no se tocan.

| Servicio | Llama por REST en P1/P2 | En P3 |
|---|---|---|
| `customer-service` | — | Publica eventos y activa Redis (`customer:{id}`) |
| `account-service` | `customer-service` (cliente); `credit-service` (tarjeta activa, desde P2) | Read models de cliente, tarjeta y deuda. Los endpoints internos de movimiento pasan a comandos Kafka. Redis para el catálogo de condiciones |
| `credit-service` | `customer-service` (al otorgar); `transaction-service` (registrar historial) | Read model de cliente. El historial llega por eventos |
| `transaction-service` | `account-service` (aplicar/revertir movimiento y consultar cuenta) | Comandos y resultados por Kafka con correlación por `operationId`; read model de cuentas. Consume los eventos de crédito |
| `report-service` | `account-service`, `credit-service`, `transaction-service` | Read model propio: `report.source=readmodel` |
| `auth-service`, `debit-service`, `yanki-service` | Nacen en P3, **sin REST** hacia otros servicios | — |

Consecuencia para la seguridad: cuando se active JWT (P3), ya no hay llamadas REST entre servicios, así que **no hace falta propagar tokens entre ellos**. Los endpoints internos (`/accounts/{id}/movements`, `/transactions/records`) se retiran o quedan bloqueados en el Gateway.

### 2.2 Orden recomendado

> Corrige el orden anterior: `credit-service` registra su historial en `transaction-service`, así que este va antes.

| Fase | Pasos |
|---|---|
| **0. Base** | `config-server` + repo de configuración; `docker-compose` con MongoDB; plantilla base (pom con Lombok, Checkstyle, Jacoco, openapi-generator) |
| **P1** | `customer` → `account` (reglas básicas) → `transaction` (depósito, retiro, historial) → `credit` (créditos, tarjetas, pagos, consumos) |
| **P2** | `eureka` → `gateway` con circuit breaker de 2 s → `account` (VIP/PYME, apertura mínima, comisiones) → `transaction` (transferencias con saga por REST) → `report` (por REST) → Checkstyle, Jacoco, Docker |
| **P3** | Kafka + Redis → migrar los adaptadores de 2.1 → `auth` (JWT; activar `security.enabled`) → `debit` → `yanki` → deuda vencida en todos los servicios → pago de créditos de terceros (RF-26) → `report` por eventos y por categoría |

Siempre con `security.enabled=false` hasta que exista `auth-service`.

---

## 3. API Gateway: rutas

> Detalle completo (orden de las rutas, circuit breakers, JWT, errores y pruebas): `services/api-gateway.md`. Si hay diferencias, prevalece la ficha.

Todas con `lb://<nombre-en-eureka>`, **circuit breaker y timeout de 2 s** (Resilience4j) en cada ruta. Sin excepciones (RNF-18). Para que el Gateway no corte antes que el servicio, las operaciones que esperan un resultado y luego responden 201 o 202 (`POST /deposits`, `/withdrawals`, `/transfers`, `/debit-cards/{id}/payments` y `/wallets/{id}/payments`) esperan **1,5 s** por defecto (`payment.await-timeout`, siempre menor que 2 s). Con `security.enabled=true`, el Gateway valida firma, emisor y expiración del JWT con la **clave pública del Config Server** (no llama a `auth-service`).

| Ruta (`/api/v1/...`) | Destino |
|---|---|
| `/auth/**` y `/.well-known/**` (fuera de `/api/v1`) | `auth-service` |
| `/customers/**` | `customer-service` |
| `/accounts/**`, `/account-conditions/**` | `account-service` |
| `/credits/**`, `/credit-cards/**`, `/overdue-checks/**`, `/credit-recovery-runs/**` | `credit-service` |
| `/deposits/**`, `/withdrawals/**`, `/transfers/**`, `/transactions/**`, `/products/**`, `/transaction-recovery-runs/**` | `transaction-service` |
| `/reports/**` | `report-service` |
| `/debit-cards/**`, `/debit-payment-recovery-runs/**` | `debit-service` |
| `/wallets/**`, `/wallet-payment-recovery-runs/**` | `yanki-service` |

- **Públicas (sin token):** `POST /api/v1/auth/login`, `POST /api/v1/auth/register`, `GET /.well-known/jwks.json`.
- **Bloqueadas en el Gateway (internas)**, con reglas de denegación **antes** de las rutas generales: `/api/v1/accounts/*/movements/**` y `/api/v1/transactions/records`.
- Los endpoints de recuperación (`*-recovery-runs`) llevan el nombre del servicio para no chocar en el Gateway, que enruta por prefijo.

---

## 4. Eventos (P3)

> Esta tabla es un resumen por tipo de evento. **El contrato definitivo** (tópicos físicos, claves, sobre común, campos y reglas de consumo) está en `contracts/events/kafka-contract.md` y prevalece.

### 4.1 Eventos publicados

| Tópico | Productor | Consumidores | Para qué |
|---|---|---|---|
| `customer.created/updated/deleted` | customer | account, credit, debit, yanki, auth | Copia local de clientes (tipo, perfil, estado, documento). Cada evento lleva el **estado completo** y viajan por **un solo tópico `customer`** con clave `customerId`. `auth` deshabilita el usuario si el cliente pasa a `INACTIVE` (ver `flows/05-customer-onboarding-and-access.md`) |
| `account.created/updated/deleted` | account | transaction, debit, report | Copia de cuentas (cliente, tipo, estado); `debit` reacciona al cierre de una cuenta |
| `account.movement.applied/rejected/reversed` | account | transaction | Resultado de un comando de movimiento |
| `credit.created/updated/closed` | credit | report | Productos para reportes |
| `credit.card.created/updated/closed` | credit | account (created/closed), report | Tarjeta activa para VIP/PYME |
| `credit.payment.registered`, `credit.card.charge.registered` | credit | transaction | Historial de pagos y consumos |
| `credit.overdue.detected` | credit | account, debit | Un producto pasó a vencido: el cliente queda con deuda (`overdue=true`) |
| `credit.overdue.cleared` | credit | account, debit | Al cliente **ya no le queda** ningún producto vencido (`overdue=false`). Ambos tipos viajan por **un solo tópico `credit.overdue`** con clave `customerId` (ver `flows/04-overdue-debt.md`) |
| `debit.card.created/updated/closed` | debit | yanki, report | Cuenta principal de la tarjeta |
| `debit.payment.completed` | debit | report | Movimientos de la tarjeta de débito |
| `debit.payment.failed` | debit | — | Trazabilidad |
| `transaction.registered/failed` | transaction | debit (`DEBIT_PAYMENT`), yanki (`YANKI_PAYMENT_*`), report (solo `registered`) | Resultado correlacionado por `operationId` |
| `transaction.reversed`, `transaction.reversal.failed` | transaction | yanki (ambos), report (`reversed`) | Resultado de toda reversa (devolución pedida por `yanki-service` o compensación de una transferencia); `report` excluye el movimiento revertido |
| `transfer.completed/failed` | transaction | — | Trazabilidad |
| `yanki.wallet.created/updated/closed`, `yanki.wallet.card.linked/unlinked`, `yanki.payment.completed/failed` | yanki | — | Trazabilidad |

`auth-service` y `report-service` no publican eventos.

### 4.2 Comandos entre servicios

| Comando | Emisor → Receptor | Para qué |
|---|---|---|
| `transaction.movement.requested`, `transaction.movement.reversal.requested` | transaction → account | Aplicar o revertir un movimiento de saldo |
| `debit.payment.requested` | debit → transaction | Retiro por un pago con tarjeta |
| `yanki.movement.requested`, `yanki.movement.reversal.requested` | yanki → transaction | Depósito, retiro o devolución de una pata de un pago Yanki |

Cuentas bancarias solo se mueven a través de `transaction-service`, que las pide a `account-service`.

---

## 5. Convenciones comunes

- **API:** base `/api/v1`, rutas y nombres en inglés y en plural. Errores con cuerpo `{ timestamp, status, code, message, path }`: 400 formato, 404 no existe, 409 duplicado, 422 regla de negocio (con `code`), 503 fuente que no responde en 2 s.
- **Eliminar es baja lógica** en todas las entidades. En el historial de movimientos solo se descartan los `FAILED`.
- **Dinero:** `BigDecimal` con 2 decimales, solo `PEN`.
- **Idempotencia:** toda operación que mueve dinero lleva `operationId` (lo genera el cliente). Repetirla devuelve el resultado original (200). Códigos: 201 creado, 202 en proceso. Las patas de una saga usan `<id>-OUT`, `<id>-IN`, `<id>-REV`, `<id>-FEE`, y ese valor es el `operationId` de la pata remota (en `yanki-service` se llama `legId`).
- **Listados:** más recientes primero; `page` y `size` (máximo 100); fechas `from` y `to`.
- **Concurrencia:** campo `version` (control optimista) en los aggregates con saldo o estado.
- **Tiempo:** `java.time.Clock` inyectado, para probar fechas (deuda vencida, día del plazo fijo, meses).
- **Datos personales** (documento, celular, IMEI, tarjeta): enmascarados en respuestas a terceros y en logs.
- **Read models:** consistencia eventual. Un rechazo por un dato que aún no llegó (cliente o cuenta recién creados) es transitorio: se repite.
- **Puertos con adaptador no-op** (publicación de eventos, caché) hasta P3.
- **Demo en una sola instancia por servicio:** los procesos programados (deuda vencida, recuperación de pendientes) no se coordinan entre instancias.

---

## 6. Seguridad y acceso (JWT)

### 6.1 Reglas generales

**Un usuario = una persona, identificada por su documento** (DNI, CEX, pasaporte; RUC para empresas). Login para todos: **documento + contraseña** → JWT. El token va en `Authorization: Bearer`.

| Tema | Decisión |
|---|---|
| Emisión | `auth-service`, solo access token (30 min). Sin refresh ni revocación |
| Validación | Gateway y cada servicio validan con la **clave pública** (RS256); la privada nunca sale de `auth-service` |
| Claims | `sub` (userId), `roles`, `customerId` (solo clientes), `documentType`, `documentNumber`, `iss`, `iat`, `exp`, `jti` |
| Rutas públicas | `POST /auth/login`, `POST /auth/register` (solo Yanki), `GET /.well-known/jwks.json` |
| Dependencias | `auth-service` no llama a nadie; consume eventos de `customer` para verificar el `customerId` de sus usuarios |
| Bloqueo | 5 intentos fallidos → 15 minutos (423 `ACCOUNT_LOCKED`) |
| Flag | `security.enabled` en Config Server: `false` mientras se construye P1/P2; `true` en la entrega |

### 6.2 Roles

| Rol | Puede |
|---|---|
| `ADMIN` | Todo, incluido crear personal |
| `TELLER` | Crear clientes y sus usuarios; abrir productos y operar para **cualquier** cliente |
| `CUSTOMER` | Abrir productos y operar **solo con lo suyo** (`customerId` del token = el del recurso). Excepciones del enunciado: transferir a cuentas de terceros y pagar créditos de terceros. También crea su monedero Yanki |
| `YANKI_USER` | Solo crear y usar su monedero |

### 6.3 Cómo obtiene acceso cada persona

| Quién | Cómo |
|---|---|
| `ADMIN` | Usuario inicial creado al arrancar, con credenciales por variable de entorno |
| `TELLER` | Lo crea el `ADMIN` (`POST /auth/users`) |
| Cliente del banco | El personal crea el cliente (`POST /customers`) y luego su usuario (`POST /auth/users` con documento, rol `CUSTOMER`, `customerId` y contraseña temporal). El documento del usuario debe coincidir con el del cliente. El cliente cambia su contraseña al entrar |
| Usuario Yanki | `POST /auth/register` con **solo documento y contraseña**; luego, con su token, `POST /wallets` con celular, IMEI y correo (el documento se toma del token) |

- Si el documento ya tiene usuario (por ejemplo, es cliente), el registro Yanki responde 409: esa persona crea su monedero con el usuario que ya tiene. Nadie queda con dos usuarios. Un `YANKI_USER` que después es cliente conserva su usuario y se le vincula el `customerId` (`PUT /auth/users/{id}/customer`).
- Como el usuario del cliente se verifica con una copia local, si se crea justo después del cliente puede responder `CUSTOMER_NOT_FOUND` unos instantes. Se repite.
- `mustChangePassword` se informa en el login pero no se fuerza.

---

## 7. Guion de demo en Postman

Variables de la colección: `{{token}}`, `{{customerId}}`, `{{accountId}}`, etc., guardadas con un script de test tras cada respuesta. Cada operación de dinero lleva un `operationId` nuevo (`{{$guid}}`).

**Fases:** las etiquetas **[P1]**, **[P2]**, **[P3]** indican desde cuándo funciona cada paso. Sin JWT (P1/P2) se omiten los logins y el header `Authorization`.

**Datos de ejemplo:** cliente A y B (personales), C (personal con deuda), V (personal VIP), E (empresa PYME) y D (persona sin cuenta, para Yanki). Los valores de límites y comisiones son los del catálogo inicial de `account-service` (editables).

### A. Personal y clientes **[P3 para el login; los pasos de clientes son P1]**
1. `POST /auth/login` como `ADMIN` → guardar token.
2. `POST /auth/users` → crear un `TELLER` y entrar con él (opcional).
3. `POST /customers` → cliente A, B, C (personales) y una empresa (RUC). Guardar los `customerId`.
4. `PATCH /customers/{id}/profile` → V pasa a `VIP`; la empresa a `PYME`. Un perfil `VIP` para una empresa → 422.
5. `POST /auth/users` → usuario `CUSTOMER` para A y B (documento igual al del cliente, `customerId`, contraseña temporal). Con un documento distinto → 422 `DOCUMENT_MISMATCH`.
6. `POST /auth/login` como A → `PUT /auth/password` → volver a entrar.

### B. Cuentas y movimientos (como cliente A)
7. `POST /accounts` → ahorro con monto de apertura **[P1]**. Un segundo ahorro → 422 `SAVINGS_LIMIT_REACHED`.
8. `POST /accounts` → corriente **[P1]**. Empresa: ahorro → 422; corriente sin titulares → 422; con titular → 201.
9. `POST /deposits` y `POST /withdrawals` → `GET /accounts/{id}/balance` **[P1]**. Repetir el mismo `operationId` → 200 sin duplicar. Retiro mayor al saldo → 422 `INSUFFICIENT_FUNDS`.
10. Seguir moviendo la misma cuenta: pasadas las transacciones libres (5), la respuesta incluye un movimiento `FEE` **[P2]**. Al superar el tope mensual de ahorro (10) → 422 `MONTHLY_LIMIT_EXCEEDED`.
11. `POST /accounts` → plazo fijo con `movementDayOfMonth` distinto de hoy → depósito → 422 `NOT_ALLOWED_DAY`. Con el día de hoy → un movimiento OK y el segundo → 422 **[P1]**. Si hoy es 29–31, hay que fijar el `Clock` del entorno de demo (el día permitido va de 1 a 28).
12. `GET /products/{accountId}/transactions?from=&to=` → historial **[P1]**.

### C. Transferencias **[P2]**
13. `POST /transfers` entre dos cuentas de A → 201, tipo `OWN`. A una cuenta de B → tipo `THIRD_PARTY`. `GET /transfers/{id}` → estado.
14. **Compensación:** transferir a una cuenta a plazo fijo de B en un día no permitido. El depósito es rechazado y la transferencia termina `COMPENSATED`: el saldo del origen vuelve al valor inicial.
15. Como B intentar transferir **desde** una cuenta de A → 403 **[P3]** (sin seguridad no hay identidad del solicitante, así que la regla no se aplica en P2).

### D. Créditos y tarjetas **[P1]**
16. `POST /credits` (A) → 201. Un segundo crédito personal → 422 `PERSONAL_CREDIT_LIMIT_REACHED`. Empresa: dos créditos → OK.
17. `POST /credit-cards` → `POST /credit-cards/{id}/charges` dentro de la línea → `GET /credit-cards/{id}/balance`. Un consumo mayor a lo disponible → 422 `CREDIT_LIMIT_EXCEEDED`.
18. `POST /credits/{id}/payments` → pago parcial, luego total (`PAID`). Pago mayor al saldo → 422 `OVERPAYMENT`.
19. **[P3]** B consulta `GET /credits/{id}/payment-info` del crédito de A y lo paga con `payerCustomerId` de B.

### E. Perfiles VIP y PYME **[P2]**
20. V: `POST /accounts` ahorro sin tarjeta de crédito → 422 `CREDIT_CARD_REQUIRED`. Emitir una tarjeta y repetir → 201. `GET /accounts/{id}/balance` muestra el promedio diario y si cumple el mínimo (500).
21. Empresa PYME: corriente sin tarjeta → 422; con tarjeta empresarial → 201 y sin comisión de mantenimiento.

### F. Deuda vencida **[P3]**
22. Como `TELLER`/`ADMIN`: `POST /credits` para C con `dueDate` pasada (requiere el modo demo de `credit-service`).
23. `POST /overdue-checks` (opcional `asOf`) → el crédito pasa a `OVERDUE`.
24. Para C: `POST /accounts`, `POST /credits`, `POST /credit-cards`, `POST /debit-cards` → 422 `OVERDUE_DEBT`.
25. Comprobar que **sí puede** operar y pagar: `POST /credits/{id}/payments` total → `PAID`. Esperar el evento `credit.overdue.cleared` y repetir el paso 24 → ahora 201.

### G. Débito **[P3]**
26. `POST /debit-cards` (A: `accountIds` con el ahorro y la corriente) → cuenta principal por defecto: la primera.
27. `POST /debit-cards/{id}/payments` → 201 (o 202) → `GET /accounts/{principal}/balance` descontado; `GET /debit-cards/{id}/payments?size=10` → últimos 10.
28. `PUT /debit-cards/{id}/main-account` → otra cuenta; pagar de nuevo. `DELETE /debit-cards/{id}/accounts/{principal}` → 422 `MAIN_ACCOUNT_REQUIRED`.
29. Pago mayor al saldo de la cuenta → `INSUFFICIENT_FUNDS`. Repetir un `operationId` → 200 sin doble cargo.

### H. Yanki **[P3]**
30. D: `POST /auth/register` (sin token) → `POST /auth/login` → `POST /wallets` (celular, IMEI, correo; el documento sale del token).
31. A (cliente, con su propio usuario y token): `POST /wallets` (celular, IMEI, correo) → `PUT /wallets/{id}/debit-card` con su tarjeta → `GET /wallets/{id}/balance` en modo `LINKED`.
32. A → D: `POST /wallets/{id}/payments` con `receiverPhone` de D → la cuenta principal de A baja (movimiento `YANKI_PAYMENT_OUT`) y el saldo de D sube.
33. D → A: `POST /wallets/{id}/payments` con el celular de A → el monto se acredita en la cuenta principal de A (`YANKI_PAYMENT_IN`).
34. Negativos: celular inexistente → 422 `RECEIVER_NOT_FOUND`; registrar Yanki con el documento de A → 409; pagar más que el saldo → 422 `INSUFFICIENT_FUNDS`.
35. `GET /wallets/{id}/payments?direction=SENT`.

### I. Reportes **[P2; débito y categoría desde P3]**
36. `GET /reports/products/accounts/{id}?from=&to=` → resumen y movimientos.
37. `GET /reports/products/credit-cards/{id}/last-movements` → últimos 10.
38. `GET /reports/customers/{customerId}/cards/last-movements` → tarjetas de crédito y débito.
39. `GET /reports/product-categories/SAVINGS?from=&to=` como `ADMIN` **[P3]**.

### J. Seguridad **[P3]**
40. Sin token → 401. B pide una cuenta de A → 403. `YANKI_USER` llama `POST /accounts` → 403.
41. 5 contraseñas incorrectas → 423 con `retryAfter`.
42. `POST /api/v1/accounts/{id}/movements` por el Gateway → bloqueado.

---

## 8. Cobertura de requisitos

Verificado contra el enunciado (Partes I, II y III).

### 8.1 Requisitos funcionales

| Requisito | Servicio(s) |
|---|---|
| RF-01 a 03: clientes, tipos y perfiles | `customer` |
| RF-10 a 15: cuentas, reglas por tipo de cliente, titulares y firmantes | `account` |
| RF-16 a 19: apertura mínima, VIP, PYME, transacciones libres y comisión | `account` (tarjeta de crédito vía `credit`) |
| RF-20 a 25: créditos, tarjetas, pagos y consumos | `credit` |
| RF-26: pago de productos de terceros | `credit` (`payment-info` + `payerCustomerId`) |
| RF-30: depósitos y retiros | `transaction` → `account` |
| RF-31: saldos | `account` (cuentas), `credit` (tarjetas) |
| RF-32: movimientos por producto | `transaction` |
| RF-33: transferencias | `transaction` (saga) → `account` |
| RF-40 y 41: reportes | `report` (+ `debit` para la tarjeta de débito) |
| RF-50 y 51: tarjetas de débito y pagos | `debit` |
| RF-60: deuda vencida | `credit` (fuente); se aplica en `account`, `credit` y `debit` |
| RF-70 a 73: Yanki | `yanki` (+ `auth` para el registro y `debit` para la cuenta principal) |

### 8.2 Requisitos no funcionales transversales

| Requisito | Dónde se cumple |
|---|---|
| RNF-12 a 14: draw.io, secuencia, UML, OpenAPI | Sección 11 de cada ficha; un contrato por servicio |
| RNF-16: Eureka | `eureka-server`; todos los servicios se registran |
| RNF-17: Gateway | `api-gateway` (sección 3) |
| RNF-18: circuit breaker con timeout de 2 s | Gateway (todas las rutas) y clientes REST de `account`, `credit`, `transaction` y `report`. Ver 10.1 |
| RNF-24 y 25: Kafka; nuevos sin REST | `auth`, `debit`, `yanki` nacen sin REST; los demás migran (2.1) |
| RNF-27: JWT | `auth-service`, Gateway y servicios |
| RNF-28: Redis para datos maestros | `customer` (`findById`) y `account` (catálogo de condiciones) |
| RNF-29: repositorio Postman | Repo aparte; una carpeta por bloque del guion |

---

## 9. Decisiones tomadas

- Se implementan **todos** los servicios, sin fusionar; `debit-service` separado de `account-service`.
- Arquitectura hexagonal y DDD en cada servicio; **RxJava 3** en los puertos.
- **Acceso:** documento + contraseña; el personal crea al cliente y su usuario; Yanki se registra solo. Permisos por rol (sección 6).
- **Solicitar un crédito** no tiene aprobación: crearlo lo otorga si cumple las reglas.
- **Deuda vencida:** fecha de pago pasada con saldo pendiente. La detecta `credit-service` (proceso diario y endpoint manual con `asOf`). Bloquea **solo adquirir productos nuevos**, no operar ni pagar.
- **Saldo y reglas de cuentas** en `account-service`; **historial** en `transaction-service`, único que pide movimientos de cuenta.
- **Sagas orquestadas** (no coreografiadas), como en el bootcamp. El orquestador es dueño de la operación y de su estado persistido, envía los comandos, espera las respuestas y compensa. Los participantes solo ejecutan y responden, de forma idempotente por `operationId`.
  - Transferencia: `transaction-service` orquesta (retirar origen → depositar destino → compensar).
  - Pago con débito: `debit-service` orquesta.
  - Pago Yanki: `yanki-service` orquesta.
- **Cliente personal:** 1 ahorro, 1 corriente y varias cuentas a plazo fijo.
- **Yanki:** monedero independiente (saldo propio) o asociado a tarjeta (usa la cuenta principal). Sin recargas: el saldo inicial llega recibiendo un pago.
- **JWT:** RS256, solo access token, clave pública por Config Server.
- **Redis** solo para datos maestros (cliente por id y catálogo de condiciones).

---

## 10. Pendientes

### 10.1 Confirmar con el instructor (decisiones de Heber, 24-sep-2026)

| # | Tema | Situación actual | Decisión |
|---|---|---|---|
| 1 | **CRUD del historial de movimientos** | El enunciado pide CRUD para todas las entidades. Se implementó restringido: solo editar la descripción y descartar los `FAILED` | Aceptado tal cual |
| 2 | **"Reporte completo por producto del banco"** | Se entiende como el reporte de un producto concreto (P2). También se ofrece por categoría de producto (P3) | Aceptado: los reportes son **por producto** |
| 3 | **RF-60 y Yanki** | El monedero no se bloquea por deuda vencida (no es un producto de crédito ni exige ser cliente) | Aceptado |
| 4 | **Saldo propio de Yanki** | El enunciado no menciona recargas. Se asumió saldo propio que se llena recibiendo pagos | Aceptado |
| 5 | **Circuit breaker "en los microservicios"** | Se aplica en el Gateway y en los clientes REST. `customer-service` no tiene llamadas salientes, así que solo lo cubre el Gateway | Aceptado |
| 6 | **REST entre servicios existentes en P3** | El enunciado lo prohíbe solo a los servicios nuevos. El diseño migra todo a eventos (más coherente, más trabajo). Si se permite REST entre los existentes, se ahorra la migración de 2.1 | **Se mantiene el diseño actual:** todo se migra a eventos (2.1). Heber lo prefiere y no hay restricción en el enunciado |
| 7 | **Reporte de la tarjeta de débito** | Está en la Parte II, pero las tarjetas de débito nacen en la III. Se habilita en P3 | Aceptado |
| 8 | **RxJava sobre WebFlux** | Se usan tipos RxJava 3 en los puertos y controladores. WebFlux usa Reactor por dentro y el generador OpenAPI produce tipos Reactor, por lo que se adapta en el borde | Aceptado: se mantiene WebFlux + RxJava 3 (adaptación en el borde) |
| 9 | **"Un ahorro, una corriente o cuentas a plazo fijo"** | Se interpretó como 1 ahorro + 1 corriente + N plazo fijo | Aceptado |

### 10.2 A resolver en el diseño de flujos (sagas)

1. ~~**Contrato de Kafka**~~ **Hecho:** `contracts/events/kafka-contract.md` (15 tópicos, sobre común, claves, campos y reglas de consumo).
2. ~~**Saga asíncrona pura o comando con espera (1,5 s) y 202.**~~ **Resuelto** en `flows/01-transfer.md` y `flows/03-yanki-payment.md` (sección 5): en P3 el consumidor de resultados es el único que avanza la saga; la petición espera hasta 1,5 s el estado terminal y, si no llega, responde 202. Se aplica a `transaction`, `debit` y `yanki`.
3. ~~**Evento de reversa**~~ **Resuelto** en `flows/03-yanki-payment.md`: `transaction.reversed` y `transaction.reversal.failed`.
4. **Reversa de un depósito sin saldo suficiente:** hoy pasa a `COMPENSATION_FAILED` y queda para revisión manual.
5. **Publicación confiable de eventos** (patrón outbox), común a todos. En P1/P2, qué pasa si el historial de un pago de crédito falla después de aplicarse (reintento u outbox).
6. **Pago de un producto de crédito desde una cuenta bancaria:** hoy queda fuera de alcance; sería una saga entre `credit` y `account`.
7. **Códigos de fallo compartidos** (`INSUFFICIENT_FUNDS`, `MONTHLY_LIMIT_EXCEEDED`, `NOT_ALLOWED_DAY`, ...) y su propagación entre servicios.

### 10.3 Backlog aceptado para el demo

- **Gap de seguridad conocido: registro Yanki sin prueba de identidad.** Quien conozca el documento de un cliente que aún no tiene usuario puede registrarse antes con ese documento; si después el `TELLER` lo vincula (`PUT /auth/users/{id}/customer`), esa persona hereda el acceso como `CUSTOMER`. Mitigaciones posibles fuera del demo: que la vinculación restablezca la contraseña, o verificar correo o celular. Detalle en `contracts/auth-service/data-model.md`, sección 10.
- Token de un usuario deshabilitado válido hasta 30 minutos; `mustChangePassword` sin forzar; rotación de claves; recuperación de contraseña.
- Comisión de mantenimiento sin cobro automático; incumplir el promedio VIP solo informa; consumo permitido con una tarjeta vencida.
- Límite diario de pagos con débito y monto máximo de pago Yanki: sin definir.
- Reconstrucción de los read models desde Kafka; exportación de reportes a CSV o PDF.
- Varias instancias por servicio (procesos programados y esperas en memoria).

---

## 11. Siguientes pasos

1. ~~**Flujos con sagas**~~ **Hecho:** cinco flujos en `flows/` (transferencia, pago con débito, pago Yanki, deuda vencida, alta de cliente y acceso), con índice en `flows/README.md`. Falta cerrar el **contrato de Kafka** (nombres finales de tópicos y esquemas).
2. ~~**Fichas de infraestructura**~~ **Hecho:** `services/config-server.md`, `services/eureka-server.md` y `services/api-gateway.md`.
3. **Plan de implementación:** **borrador hecho** en `implementation-plan.md` (modo guiado, decisiones técnicas de base, repositorios, receta por servicio y fases 0, P1, P2 y P3). Faltan confirmar las decisiones de su sección 9.

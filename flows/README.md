# Flujos entre servicios

Cada flujo describe cómo colaboran los servicios para una operación: participantes, estados, diagramas Mermaid, mensajes, fallos y recuperación, pruebas y decisiones. Los detalles de cada servicio están en `services/<servicio>.md`.

| # | Flujo | Tipo | Orquestador / dueño | Participantes | Fase |
|---|---|---|---|---|---|
| 01 | [Transferencia entre cuentas](01-transfer.md) | Saga orquestada | `transaction-service` | `account` | P2 (REST) → P3 (Kafka) |
| 02 | [Pago con tarjeta de débito](02-debit-payment.md) | Saga de un paso | `debit-service` | `transaction` → `account` | P3 |
| 03 | [Pago Yanki por celular](03-yanki-payment.md) | Saga orquestada (4 rutas, pasos locales y remotos) | `yanki-service` | `transaction` → `account` | P3 |
| 04 | [Deuda vencida y bloqueo de adquisición](04-overdue-debt.md) | Propagación de estado por eventos | `credit-service` (fuente) | `account`, `debit` | Detección desde P1; bloqueo en P3 |
| 05 | [Alta de cliente y acceso](05-customer-onboarding-and-access.md) | Propagación de estado por eventos | `customer-service` (fuente) | `auth`, `yanki` | P3 |

## Reglas comunes

- **Sagas orquestadas** (01, 02, 03): el orquestador guarda el estado, envía los comandos, espera respuesta y compensa. Los participantes solo ejecutan, de forma idempotente por `operationId`.
- **Ante un timeout no se compensa:** se reintenta la misma pata con el mismo identificador hasta tener una respuesta definitiva.
- **Quién avanza la saga en P3:** solo el consumidor de resultados. La petición HTTP espera el estado terminal hasta 1,5 s (propiedad `payment.await-timeout`, configurable y siempre menor que el timeout de 2 s del Gateway) y, si no llega, responde 202.
- **Idempotencia y reemisión:** un comando repetido no duplica el efecto y, si ya terminó, el receptor vuelve a publicar su resultado.
- **Recuperación:** cada orquestador tiene un proceso programado y un endpoint (`/transaction-recovery-runs`, `/debit-payment-recovery-runs`, `/wallet-payment-recovery-runs`) que retoman las operaciones sin avanzar.
- **Estado por eventos** (04, 05): un tópico por aggregate, con el tipo de evento dentro del mensaje, clave = id del aggregate y compactación; los consumidores guardan una copia local con fecha y aplican solo el evento más reciente.
- **Dónde se ve cada cosa en la demo:** guion de Postman en `bootcamp-bank-microservices-definition.md`, sección 7.

## Contrato de mensajes

Las columnas "tópico" de las tablas de mensajes de los flujos muestran el **tipo de evento** (`eventType`). El tópico físico, la clave, el sobre y todos los campos están en `../contracts/events/kafka-contract.md`, que prevalece.

## Pendientes comunes a todos los flujos

- **Outbox** para publicar eventos de forma confiable.
- Fichas de infraestructura: `config-server`, `eureka-server` y `api-gateway`.

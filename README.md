# bank-docs

Documentación y contratos del sistema bancario de microservicios (proyecto final del bootcamp).

## Por dónde empezar

| Documento | Para qué |
|---|---|
| `bootcamp-bank-microservices-guide.md` | Enunciado, stack, prácticas y requisitos (RF/RNF) |
| `bootcamp-bank-microservices-definition.md` | Visión consolidada: mapa de servicios, rutas del Gateway, eventos, seguridad, guion de demo y pendientes |
| `implementation-plan.md` | Plan de implementación: modo de trabajo, decisiones técnicas, receta por servicio y fases |
| `services/` | Una ficha por servicio (11): dominio, puertos, API, persistencia, mensajería, pruebas |
| `flows/` | Sagas y flujos entre servicios (transferencia, pago con débito, pago Yanki, deuda vencida, alta y acceso) |
| `contracts/` | Contratos: `openapi.yaml` y `data-model.md` por servicio, esquemas comunes y contrato de Kafka |

Si hay diferencias: prevalecen los contratos, luego las fichas, luego la definición general.

## Validar los contratos

```
pip install openapi-spec-validator pyyaml
python contracts/validate_oas.py contracts/*/openapi.yaml
```

# `<service-name>`

> Plantilla común para todos los microservicios. Copiar, completar y borrar las notas en cursiva.

## 1. Resumen

| Campo | Valor |
|---|---|
| Propósito | *una frase* |
| Bounded context | *qué parte del negocio es dueño* |
| Fase | P1 / P2 / P3 |
| Puerto | *808x* |
| Base de datos | *colecciones propias* |
| Depende de (eventos / REST) | *servicios de los que consume datos* |

## 2. Responsabilidades

**Hace:**
- *…*

**No hace (fuera de alcance):**
- *…*

## 3. Modelo de dominio (DDD)

### 3.1 Aggregates y entidades
| Elemento | Tipo | Descripción |
|---|---|---|

### 3.2 Value objects
| VO | Campos | Validaciones |
|---|---|---|

### 3.3 Enums
| Enum | Valores |
|---|---|

### 3.4 Reglas de negocio e invariantes
| # | Regla | Dónde se aplica (aggregate / VO / caso de uso / otro servicio) |
|---|---|---|

### 3.5 Domain services
*Solo si una regla no cabe en un aggregate. Si no aplica, indicar "No aplica".*

### 3.6 Eventos de dominio
| Evento | Cuándo | Datos mínimos |
|---|---|---|

## 4. Casos de uso y puertos

### 4.1 Puertos de entrada (casos de uso)
| Caso de uso | Retorno | Descripción |
|---|---|---|

### 4.2 Puertos de salida
| Puerto | Métodos | Adaptador (fase) |
|---|---|---|

## 5. API (contrato OpenAPI)

| Método | Ruta | Descripción | Roles | Éxito | Errores |
|---|---|---|---|---|---|

Convención de errores: *cuerpo estándar, códigos usados*.

## 6. Persistencia y caché
- **Colecciones / índices:** *…*
- **Caché (P3):** *…*

## 7. Mensajería (Kafka) — P3
| Publica | Consume |
|---|---|

**Rol en sagas:** *participante / iniciador / ninguno (se completa al diseñar los flujos)*

## 8. Filesystem

```
<service>/
└── ...
```

## 9. Stack y configuración
- **Dependencias específicas:** *…*
- **Propiedades externalizadas (Config Server):** *…*
- **Resiliencia:** *…*

## 10. Estrategia de pruebas
| Capa | Qué se prueba | Herramientas |
|---|---|---|

## 11. Diagramas a elaborar
- [ ] Secuencia: *…*
- [ ] UML de dominio

## 12. Decisiones y pendientes
- **Decidido:** *…*
- **Pendiente:** *…*

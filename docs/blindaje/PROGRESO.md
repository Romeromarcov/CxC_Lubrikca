# Blindaje de CxC — libro de a bordo

Ejecución del plan «Blindaje de CxC Lubrikca» (artifact del 10-sep-2026).
Este archivo es el estado; se actualiza al cerrar cada tarea.

## Entregables

| Documento | Qué contiene |
|---|---|
| [1.1 — Fallbacks silenciosos](1.1-fallbacks-silenciosos.md) | 456 sitios clasificados; 8 minas confirmadas a mano |
| [1.2 a 1.5 — Auditoría](1.2-1.5-auditoria.md) | cobertura, integridad, conciliación, y qué prueba cada partida |
| [2.3 — Alertas](2.3-alertas.md) | la corrida diaria, y los 1.333,85 USD aplicados que nadie pagó |
| [5 — Dashboard](5-dashboard.md) | seis hallazgos, los seis aplicados |
| [escenarios/README.md](../../escenarios/README.md) | el banco de 31 escenarios y sus tres barreras fiscales |

## Herramientas nuevas, todas reejecutables

```bash
python scripts/auditar_fallbacks.py --json out.json      # 1.1
python scripts/auditar_integridad.py --env .env.qa --detalle   # 1.3
python scripts/conciliar_espejo_odoo.py --env .env.qa     # 1.4
python scripts/vigilancia_diaria.py --alertar             # 2.3, va como cron
python scripts/qa_entorno.py crear|sync|resync|motor|estado
./scripts/escenarios.sh                                   # Fase 3
```

## Entorno de pruebas (Fase 0 — resuelta)

| Decisión | Resuelta como |
|---|---|
| De dónde sale el Odoo de prueba | **Duplicado en Odoo** (`lixie-dev-lubrika-qa1`, Odoo 18.0+e): 950 órdenes, 8.277 asientos, 1.438 pagos, 2.809 pickings, 570 clientes, 261 productos, 16 listas, 830 tasas. Espejo local en `cxc_qa`: 26.113 filas, sync completo en 17 s. |
| Copia de datos reales | **Sí, sin anonimizar.** Entorno efímero provisto por gerencia. |
| Quién toca Odoo y qué se equivoca | Pendiente. **No bloqueó**: la Fase 3 se ordenó por severidad estimada. |

**Riesgo del entorno, resuelto con tu decisión:** el Odoo de prueba comparte la
imprenta fiscal con producción (proveedor real, documentos aprobados el 09-sep
con números de control y URL pública). Se resolvió con un diario de ventas sin
conector, una ubicación de cliente con la imprenta apagada, y un canario que
aborta la corrida si aparece un documento fiscal. Ninguna corrida emitió nada.

## Estado por fase

| # | Tarea | Estado |
|---|---|---|
| 0 | Rotar credencial de producción | **pendiente, y es el ítem más urgente.** Requiere tus manos: rotar en Railway y luego purgar el historial de git, porque mientras el valor viejo siga ahí cualquiera con acceso al repo lo tiene. |
| 1.1 | Inventario de fallbacks silenciosos | **cerrada** — 132 minas, 140 ruidosos, 184 legítimos; 8 minas verificadas a mano con severidad y costo |
| 1.2 | Cobertura de los caminos de dinero | **medida** — la barrera está en ROJO (73,13 % contra umbral 74 %) sin ningún test roto; el tramo de 1.381 líneas confirmado (`get_balance_comprobacion` + `get_auditoria`, 410 sin cubrir) |
| 1.3 | Integridad de las tablas | **cerrada** — 30 chequeos, 25 limpios; 16 órdenes canceladas con entrega por 11.995,68 USD |
| 1.4 | Conciliación espejo vs Odoo | **cerrada** — 9 de 9 cuadran al centavo tras un sync completo |
| 1.5 | Qué prueba cada partida del balance | **cerrada** — 8 externas, 1 invariante, 9 internas; etiquetado aplicado |
| 2.1 | Ausencia de dato ≠ número | pendiente — espera tu decisión sobre las minas 1, 5 y 8 |
| 2.2 | Invariantes al escribir | **parcial** — las 6 invariantes existen y se evalúan a diario; convertirlas en barrera de escritura es lo que falta |
| 2.3 | Alertas | **cerrada** — corrida diaria funcionando, con el hallazgo de sobreaplicación |
| 3 | Escenarios de error humano | **31 escenarios escritos y la fontanería verificada** (8/8 de humo en verde: crear, entregar completa, facturar, cobrar, sincronizar, y los cuatro reportes contestan). Corrida completa pendiente. |
| 4 | Estrés y fallas | pendiente |
| 5 | Dashboard | **cerrada** — 6 hallazgos, 6 aplicados |
| 6 | Bugs y deuda | en curso — ver abajo |

## Lo que espera tu decisión

| # | Qué | Por qué no lo aplico solo |
|---|---|---|
| 1 | **1.333,85 USD aplicados que nadie pagó** (10 pagos, 20 órdenes) | Cuánto de una diferencia de cambio cuenta como cobranza para los descuentos por pago previo es una decisión de negocio. Ver [2.3](2.3-alertas.md). |
| 2 | 16 órdenes canceladas con entrega, 11.995,68 USD | Puede ser que estén canceladas *porque* la mercancía volvió por otro camino. El chequeo no puede distinguirlo. |
| 3 | El `36,5 / 38,0` de 2019 | Arrastra 42 tests que asertan montos calculados con esa tasa falsa. |
| 4 | Precio 0 con Odoo caído, y teórico ausente = saldo cero | Cambian lo que muestra la pantalla para una orden no evaluable. |
| 5 | La barrera de cobertura en rojo | Bajar el umbral al valor medido o cubrir las ~90 sentencias que faltan. |
| 6 | ¿«Ventas» del dashboard debería mostrar también el neto teórico? | Es una tarjeta nueva, no un arreglo. |

## Deuda de la Fase 6, medida

| Ítem | Lo que se midió |
|---|---|
| `OdooPriceResolver` sin calibrar para Odoo 18 | Está `pragma: no cover`, así que sus 4 minas ALTA no tienen un solo test. En el espejo de QA hay 2 teóricos valorados por fórmula. |
| El default `36,5 / 38,0` | Ya deja rastro (`logger.error`), pero sigue devolviendo el número. |
| `SerieTasas` sin caché | 24 lecturas directas repartidas por `app.py`. |
| Rotar la credencial de producción | Sin hacer. Es el ítem más urgente de la lista y el único que no depende de ninguna fase. |

# Blindaje de CxC — libro de a bordo

Ejecución del plan «Blindaje de CxC Lubrikca» (artifact del 10-sep-2026).
Este archivo es el estado; se actualiza al cerrar cada tarea.

## Entregables

| Documento | Qué contiene |
|---|---|
| [1.1 — Fallbacks silenciosos](1.1-fallbacks-silenciosos.md) | 456 sitios clasificados; 8 minas confirmadas a mano |
| [1.2 a 1.5 — Auditoría](1.2-1.5-auditoria.md) | cobertura, integridad, conciliación, y qué prueba cada partida |
| [2.2 — Invariantes](2.2-invariantes.md) | 8 restricciones de base, y las 2 que deliberadamente no entran |
| [2.3 — Alertas](2.3-alertas.md) | la corrida diaria, y los 1.333,85 USD aplicados que nadie pagó |
| [3 — Escenarios](3-escenarios.md) | la imprenta fiscal, y los 1.108,59 USD de mercancía devuelta que la factura sigue cobrando |
| [4 — Estrés](4-estres.md) | 6 escenarios en verde, y el motor que a 10× tarda 16 horas |
| [5 — Dashboard](5-dashboard.md) | seis hallazgos, los seis aplicados |
| [6 — Deuda medida](6-deuda-medida.md) | los 8 ítems del plan, medidos; el default de 2019 escrito en 1.462 vinculaciones |
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
| 1.2 | Cobertura de los caminos de dinero | **cerrada** — la barrera volvió a VERDE (74,41 %). Estaba roja desde antes, sin ningún test roto. El test de punta a punta del balance cubrió 142 sentencias y confirmó las 24 partidas. |
| 1.3 | Integridad de las tablas | **cerrada** — 30 chequeos, 25 limpios; 16 órdenes canceladas con entrega por 11.995,68 USD |
| 1.4 | Conciliación espejo vs Odoo | **cerrada** — 9 de 9 cuadran al centavo tras un sync completo |
| 1.5 | Qué prueba cada partida del balance | **cerrada** — 8 externas, 1 invariante, 9 internas; etiquetado aplicado |
| 2.1 | Ausencia de dato ≠ número | pendiente, y **subió de prioridad**: el default de 2019 no solo se lee, se **escribe** en un campo congelado. Espera tu decisión sobre las minas 1, 5 y 8. |
| 2.2 | Invariantes al escribir | **cerrada del lado de la base** — 8 restricciones `CHECK` aplicadas y verificadas contra la copia de producción. Falta la validación en el repositorio, que necesita decidir qué hacer con la fila rechazada. |
| 2.3 | Alertas | **cerrada** — corrida diaria funcionando, con el hallazgo de sobreaplicación |
| 2.4 | Sacar caminos de dinero de app.py | **empezada** — primera pieza extraída (`engine/saldos.py`), con su medición A/B. Es la tarea más larga del plan y se hace por pedazos. |
| 3 | Escenarios de error humano | **cerrada** — 46 pruebas, corrida completa en 25 min. Produjo 2 hallazgos del sistema, 2 protecciones de Odoo que la tabla no contemplaba, y 8 bugs del propio andamiaje ya arreglados. |
| 4 | Estrés y fallas | **cerrada** — 6 escenarios en verde. El volumen se midió y extrapoló en vez de ejecutarse; el motor a 10× tarda ~16 h por un tope de 50/ciclo. |
| 5 | Dashboard | **cerrada** — 6 hallazgos, 6 aplicados |
| 6 | Bugs y deuda | **medida** — ver [6 — Deuda medida](6-deuda-medida.md) |

## Lo que espera tu decisión

| # | Qué | Por qué no lo aplico solo |
|---|---|---|
| 1 | **1.333,85 USD aplicados que nadie pagó** (10 pagos, 20 órdenes) | Cuánto de una diferencia de cambio cuenta como cobranza para los descuentos por pago previo es una decisión de negocio. Ver [2.3](2.3-alertas.md). |
| 2 | 16 órdenes canceladas con entrega, 11.995,68 USD | Puede ser que estén canceladas *porque* la mercancía volvió por otro camino. El chequeo no puede distinguirlo. |
| 3 | El `36,5 / 38,0` de 2019 | Arrastra 42 tests que asertan montos calculados con esa tasa falsa. |
| 4 | Precio 0 con Odoo caído, y teórico ausente = saldo cero | Cambian lo que muestra la pantalla para una orden no evaluable. |
| 5 | **1.108,59 USD** de mercancía devuelta que la factura sigue cobrando (3 órdenes) | Hay que emitir la nota de crédito en Odoo: son tus manos. El sistema ahora las lista. |
| 6 | Las 2 órdenes con las dos definiciones de «orden histórica» en desacuerdo | Unificar mueve el equivalente congelado de dos órdenes, 457,51 USD. |
| 7 | ¿«Ventas» del dashboard debería mostrar también el neto teórico? | Es una tarjeta nueva, no un arreglo. |

## Lo que este trabajo encontró, en un renglón cada uno

Ordenado por lo que costaría no arreglarlo, no por severidad nominal.

| Hallazgo | Cuánto | Dónde |
|---|---|---|
| El default de 2019 está **escrito** en las 1.462 vinculaciones del espejo | 82,7 M aplicados | [6](6-deuda-medida.md) |
| Pagos sobreaplicados: se acredita plata que el cliente no puso | 1.333,85 USD | [2.3](2.3-alertas.md) |
| Mercancía devuelta con la factura viva, fuera de la bandeja | 1.108,59 USD | [3](3-escenarios.md) |
| Órdenes canceladas con entrega, invisibles en los totales | 11.995,68 USD | [1.3](1.2-1.5-auditoria.md) |
| El motor a 10× tarda ~16 h por un tope de 50/ciclo | — | [4](4-estres.md) |
| El dashboard decía «Tasa BCV» sobre el equivalente de Odoo | — | [5](5-dashboard.md) · aplicado |
| El espejo de líneas de factura era 89,6 % ruido | — | [1.3](1.2-1.5-auditoria.md) · aplicado |
| La barrera de cobertura estaba roja sin ningún test fallando | — | [1.2](1.2-1.5-auditoria.md) · aplicado |
| Devuelto supera lo entregado (cantidad negativa) | 14 unidades | [2.2](2.2-invariantes.md) |
| Dos definiciones de «orden histórica» en desacuerdo | 457,51 USD | [6](6-deuda-medida.md) |

Y lo que hay que hacer con las manos, que es lo más urgente de todo: **rotar la
credencial de producción** y purgar el valor viejo del historial de git. Rotar sin
purgar solo cambia qué credencial está expuesta.

# Blindaje de CxC — libro de a bordo

Ejecución del plan «Blindaje de CxC Lubrikca» (artifact del 10-sep-2026).
Este archivo es el estado; se actualiza al cerrar cada tarea.

## Entregables

| Documento | Qué contiene |
|---|---|
| [0 — La credencial en el historial](0-credencial.md) | dónde está exactamente, y el procedimiento listo para correr |
| [1.1 — Fallbacks silenciosos](1.1-fallbacks-silenciosos.md) | 456 sitios clasificados; 8 minas confirmadas a mano |
| [1.2 a 1.5 — Auditoría](1.2-1.5-auditoria.md) | cobertura, integridad, conciliación, y qué prueba cada partida |
| [2.2 — Invariantes](2.2-invariantes.md) | 8 restricciones de base, y las 2 que deliberadamente no entran |
| [2.3 — Alertas](2.3-alertas.md) | la corrida diaria, y los 1.333,85 USD aplicados que nadie pagó |
| [2.4 — Modularizar](2.4-modularizar.md) | dos piezas sacadas de `app.py`, con su medición A/B, y qué sigue |
| [3 — Escenarios](3-escenarios.md) | la imprenta fiscal, y los 1.108,59 USD de mercancía devuelta que la factura sigue cobrando |
| [4 — Estrés](4-estres.md) | 6 escenarios en verde, y el motor que a 10× tarda 16 horas |
| [5 — Dashboard](5-dashboard.md) | seis hallazgos, los seis aplicados |
| [2.1 — Ausencia de dato](2.1-ausencia-de-dato.md) | la premisa del plan medida, y el costo real de la decisión |
| [6 — Deuda medida](6-deuda-medida.md) | los 8 ítems del plan, medidos; el default de 2019 escrito en 1.462 vinculaciones |
| [escenarios/README.md](../../escenarios/README.md) | el banco de 31 escenarios y sus tres barreras fiscales |

## Herramientas nuevas, todas reejecutables

```bash
python scripts/auditar_fallbacks.py --json out.json      # 1.1
python scripts/auditar_integridad.py --env .env.qa --detalle   # 1.3
python scripts/conciliar_espejo_odoo.py --env .env.qa     # 1.4
python scripts/vigilancia_diaria.py --alertar             # 2.3, va como cron
python scripts/auditar_listas_de_precio.py --env .env.qa --sin-pruebas  # 6
python scripts/auditar_equivalentes_congelados.py --env .env.qa --detalle  # 6
python scripts/volumen_10x.py --origen cxc_qa --factor 10  # 4
python scripts/verificar_secretos.py                      # 0, y va en barreras.sh
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
| 0 | Rotar credencial de producción | **pendiente, y sigue siendo el ítem más urgente.** Requiere tus manos (Railway). Lo que sí se hizo: está **medido** —una sola credencial remota, un archivo, 269 commits, empujada a `main` y `develop`—, el procedimiento está escrito paso a paso en [0](0-credencial.md), y `scripts/verificar_secretos.py` entró como primera etapa de la barrera para que no vuelva a pasar. **Ojo:** el commit que la introdujo dice «Staging», no producción — hay que reconciliar si son la misma. |
| 1.1 | Inventario de fallbacks silenciosos | **cerrada** — 132 minas, 140 ruidosos, 184 legítimos; 8 minas verificadas a mano con severidad y costo |
| 1.2 | Cobertura de los caminos de dinero | **cerrada** — barrera subida de 74 a **74,9** (medida: 74,99 %), y los caminos de dinero modularizados al **93,23 %**, por encima del 90 % que pedía el plan. La brecha que queda es `app.py` al 64 %, que es la Fase 2.4. Estaba roja desde antes, sin ningún test roto. El test de punta a punta del balance cubrió 142 sentencias y confirmó las 24 partidas. |
| 1.3 | Integridad de las tablas | **cerrada** — 35 chequeos. Y una **corrección**: publiqué las 16 canceladas con entrega como 11.995,68 USD de plata en riesgo y no lo son (las 16 tienen `qty_delivered = 0`). Dentro de esas 16 apareció un hallazgo real en la dirección opuesta: 4 órdenes donde el espejo dice `entregada_completa` y su única salida figura cancelada. |
| 1.4 | Conciliación espejo vs Odoo | **cerrada** — 9 de 9 cuadran al centavo tras un sync completo |
| 1.5 | Qué prueba cada partida del balance | **cerrada** — 8 externas, 1 invariante, 9 internas; etiquetado aplicado |
| 2.1 | Ausencia de dato ≠ número | **preparada, espera tu decisión.** Ver [2.1](2.1-ausencia-de-dato.md): la premisa del plan («42 tests asertan cifras calculadas con una tasa falsa») medida resultó falsa, y sembrar tasas bajó el costo de la decisión de 40 tests a 2 (más 5 que hay que reescribir a propósito). Sigue siendo prioritario: el default de 2019 no solo se lee, se **escribe** en un campo congelado. Espera tu decisión sobre las minas 1, 5 y 8. La 1 ya está medida y contada (`engine/precios_rapidos.py`), así que la decisión se toma sobre números y no sobre una lectura del código. |
| 2.2 | Invariantes al escribir | **cerrada, las dos mitades.** 8 restricciones `CHECK` en la base, y la validación en el repositorio (`db/invariantes.py`) enganchada en los dos caminos que escriben dinero. No cambia qué se acepta —falla igual que la base— pero dice qué fila y con qué valores en vez de un `IntegrityError` crudo. 23 tests, incluidos los que verifican que la versión Python y la cláusula SQL coincidan. |
| 2.3 | Alertas | **cerrada** — corrida diaria funcionando (51 chequeos), con el hallazgo de sobreaplicación. Se le sumó el desacuerdo de listas de precio, y se arregló un bug del informe que contaba hallazgos sin imprimirlos. |
| 2.4 | Sacar caminos de dinero de app.py | **empezada** — cuatro piezas extraídas: `engine/saldos.py` (cuánto falta cobrar), `engine/universo.py` (qué órdenes entran), `engine/listas.py` (cuál lista valora el teórico — de ahí salió el hallazgo de las 789 órdenes) `engine/balance.py` (las 15 partidas internas, al 100 %) `engine/precios_rapidos.py` (la mina 1 del inventario, al 100 %, y sus ceros ahora quedan contados) `engine/conciliacion.py` (las dos referencias de un residual, que estaba definida adentro de un `for`) las **8 partidas externas del balance** (probables con un Odoo de mentira) y el **reparto FIFO de un pago** entre las órdenes de su cliente. 237 tests nuevos; `app.py` bajó ~740 líneas. Es la tarea más larga del plan y se hace por pedazos. |
| 3 | Escenarios de error humano | **cerrada** — 46 pruebas, corrida completa en 25 min. Produjo 2 hallazgos del sistema, 2 protecciones de Odoo que la tabla no contemplaba, y 8 bugs del propio andamiaje ya arreglados. |
| 4 | Estrés y fallas | **cerrada** — 6 escenarios en verde, y el volumen **ejecutado** (ya no extrapolado): a 10× los cinco reportes terminan con crecimiento lineal. Apareció un falso rojo del balance que solo se ve a volumen. El motor sigue extrapolado en ~16 h por un tope de 50/ciclo. |
| 5 | Dashboard | **cerrada** — 6 hallazgos, 6 aplicados |
| 6 | Bugs y deuda | **medida** — ver [6 — Deuda medida](6-deuda-medida.md). Corregido: el ítem `OdooPriceResolver` había bajado a Media por una medición mía mal hecha; vuelve a Alta y por otro motivo. |

## Lo que espera tu decisión

| # | Qué | Por qué no lo aplico solo |
|---|---|---|
| 1 | **1.333,85 USD aplicados que nadie pagó** (10 pagos, 20 órdenes) | Cuánto de una diferencia de cambio cuenta como cobranza para los descuentos por pago previo es una decisión de negocio. Ver [2.3](2.3-alertas.md). |
| 2 | ~~16 órdenes canceladas con entrega, 11.995,68 USD~~ → **4 órdenes donde el espejo se contradice**: dice `entregada_completa` y su única salida figura `cancel`. No es plata sin cobrar — es un dato interno inconsistente que decide si una orden cancelada cuenta como venta. | Corregir `entregada_completa` mueve el universo de órdenes de seis páginas. Ver la corrección en [1.3](1.2-1.5-auditoria.md). |
| 3 | El `36,5 / 38,0` de 2019 | Arrastra 42 tests que asertan montos calculados con esa tasa falsa. |
| 4 | Precio 0 con Odoo caído, y teórico ausente = saldo cero | Cambian lo que muestra la pantalla para una orden no evaluable. |
| 5 | **1.108,59 USD** de mercancía devuelta que la factura sigue cobrando (3 órdenes) | Hay que emitir la nota de crédito en Odoo: son tus manos. El sistema ahora las lista. |
| 6 | Las 2 órdenes con las dos definiciones de «orden histórica» en desacuerdo | Unificar mueve el equivalente congelado de dos órdenes, 457,51 USD. |
| 7 | ¿«Ventas» del dashboard debería mostrar también el neto teórico? | Es una tarjeta nueva, no un arreglo. |
| 9 | **El 2 % de primera compra cableado**: se otorga cuando no hay promoción configurada, y las nueve tablas de reglas están vacías. Que deje de conceder mueve el teórico de 119 órdenes. Y hay que decidir qué tabla manda, porque las dos dan números distintos. | Es una decisión de negocio: hoy se otorga por ausencia de configuración, no porque alguien lo haya resuelto. Ver [6](6-deuda-medida.md). |
| 8 | **El reporte de saldos valora el teórico con una lista archivada y vencida** — falta `_primer_id_activo` en `app.py:4434`, el único de los cuatro sitios que no lo tiene | Poner la guarda mueve el teórico de 789 órdenes en una pantalla que ya se usa: −18,9 % en VES, −16,8 % en USD. Está medido y listo; aplicarlo es tu visto bueno. Ver [3](3-escenarios.md). |

## Lo que este trabajo encontró, en un renglón cada uno

Ordenado por lo que costaría no arreglarlo, no por severidad nominal.

| Hallazgo | Cuánto | Dónde |
|---|---|---|
| El default de 2019 está **escrito** en las 1.462 vinculaciones del espejo | 82,7 M aplicados | [6](6-deuda-medida.md) |
| El reporte de saldos valora con una lista vencida; las otras tres páginas no | 789 órdenes, 194.532,51 VES / 115.805,93 USD de desvío bruto | [3](3-escenarios.md) |
| Una lista de precios vencida **no puede** marcarse: `rules[0]` gana sobre la fecha | — | [3](3-escenarios.md) |
| Pagos sobreaplicados: se acredita plata que el cliente no puso | 1.333,85 USD | [2.3](2.3-alertas.md) |
| Mercancía devuelta con la factura viva, fuera de la bandeja | 1.108,59 USD | [3](3-escenarios.md) |
| El espejo dice «entregada» en 4 órdenes cuya única salida está cancelada | 4 órdenes, 8.535,41 USD de monto | [1.3](1.2-1.5-auditoria.md) |
| El motor a 10× tarda ~16 h por un tope de 50/ciclo | — | [4](4-estres.md) |
| El dashboard decía «Tasa BCV» sobre el equivalente de Odoo | — | [5](5-dashboard.md) · aplicado |
| El espejo de líneas de factura era 89,6 % ruido | — | [1.3](1.2-1.5-auditoria.md) · aplicado |
| La barrera de cobertura estaba roja sin ningún test fallando | — | [1.2](1.2-1.5-auditoria.md) · aplicado |
| El 2 % de primera compra se otorga por **ausencia de configuración**, no por decisión | 119 órdenes, 2.782,41 USD | [6](6-deuda-medida.md) |
| El mismo descuento da dos montos según qué tabla se lea | brecha de 1.128,91 USD | [6](6-deuda-medida.md) |
| El verificador de huecos de vigencia decía «ninguno» sin poder mirar | 0 de 16 listas evaluables | [6](6-deuda-medida.md) · aplicado |
| Devuelto supera lo entregado (cantidad negativa) | 14 unidades | [2.2](2.2-invariantes.md) |
| Dos definiciones de «orden histórica» en desacuerdo | 457,51 USD | [6](6-deuda-medida.md) |

Y lo que hay que hacer con las manos, que es lo más urgente de todo: **rotar la
credencial de producción** y purgar el valor viejo del historial de git. Rotar sin
purgar solo cambia qué credencial está expuesta.

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
| [2.3 — Alertas](2.3-alertas.md) | la corrida diaria, y los pagos sobreaplicados (1.269,25 USD contra el campo crudo; la cifra vieja de 1.333,85 pasaba por la conversión a equivalente) |
| [2.4 — Modularizar](2.4-modularizar.md) | cuarenta piezas sacadas de `app.py`/`runner.py`, con su medición A/B, y qué sigue |
| [Quiz de decisiones](https://claude.ai/code/artifact/5a35b1e4-3b92-4108-bd47-4fd81c0e8f0b) | las once decisiones abiertas, cada una con su medición y la consecuencia de cada camino; las respuestas se guardan |
| [3 — Escenarios](3-escenarios.md) | la imprenta fiscal, y los 1.108,59 USD de mercancía devuelta que la factura sigue cobrando |
| [4 — Estrés](4-estres.md) | 6 escenarios en verde, y el motor que a 10× tarda 16 horas |
| [5 — Dashboard](5-dashboard.md) | seis hallazgos, los seis aplicados |
| [2.1 — Ausencia de dato](2.1-ausencia-de-dato.md) | la premisa del plan medida, y el costo real de la decisión |
| [6 — Deuda medida](6-deuda-medida.md) | los 8 ítems del plan, medidos; el default de 2019 escrito en 1.463 vinculaciones |
| [escenarios/README.md](../../escenarios/README.md) | el banco de 31 escenarios y sus tres barreras fiscales |
| [7 — Incidente de producción, 17-sep](7-incidente-produccion.md) | una factura que consolida varias órdenes tumbaba el ciclo del demonio cada 5 min, en vivo |

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

## Las cifras de cabecera del plan, al inicio y hoy

El plan abrió con ocho números medidos el 10-sep-2026. Los mismos, medidos hoy con la
misma vara donde se pudo (el conteo de `except Exception` es un `grep`; el de defaults
silenciosos es una aproximación por regex, y por eso va con esa nota):

| | 10-sep-2026 | hoy | |
|---|---:|---:|---|
| tests | 1.280 | **2.110** | +830 |
| cobertura total | — | **82,30 %** | la barrera subió de 74 a 74,9 |
| cobertura de `app.py` | 62 % | **69,9 %** | |
| sentencias sin cubrir en `app.py` | 2.361 | **1.538** | −823 |
| líneas de `app.py` | 17.484 | **16.791** | −693, en 35 piezas; hoy subió ~300 con las guardas y los avisos de «sin tasa» |
| `except Exception` en `app.py` | 190 | **176** | y una guarda para que ninguno se trague un `HTTPException` |
| defaults silenciosos (`or 0`, `.get(…, 0)`) | 173 | **~90** | por regex; el inventario 1.1 los clasificó uno por uno |
| cobertura del motor | 94–100 % | **87–100 %** en 27 módulos | bajó el mínimo porque el motor tiene 18 módulos más que antes; `balance.py`, el más bajo, subió hoy de 71 a 94 |

## Estado por fase

| # | Tarea | Estado |
|---|---|---|
| 0 | Rotar credencial de producción | **pendiente, y sigue siendo el ítem más urgente.** Requiere tus manos (Railway). Lo que sí se hizo: está **medido** —una sola credencial remota, un archivo, 269 commits, empujada a `main` y `develop`—, el procedimiento está escrito paso a paso en [0](0-credencial.md), y `scripts/verificar_secretos.py` entró como primera etapa de la barrera para que no vuelva a pasar. **Ojo:** el commit que la introdujo dice «Staging», no producción — hay que reconciliar si son la misma. |
| 1.1 | Inventario de fallbacks silenciosos | **cerrada** — 132 minas, 140 ruidosos, 184 legítimos; 8 minas verificadas a mano con severidad y costo |
| 1.2 | Cobertura de los caminos de dinero | **cerrada** — barrera subida de 74 a **74,9** (medida: 74,99 %), y los caminos de dinero modularizados al **93,23 %**, por encima del 90 % que pedía el plan. La brecha que queda es `app.py` al 64 %, que es la Fase 2.4. Estaba roja desde antes, sin ningún test roto. El test de punta a punta del balance cubrió 142 sentencias y confirmó las 24 partidas. |
| 1.3 | Integridad de las tablas | **cerrada** — 35 chequeos. Y una **corrección**: publiqué las 16 canceladas con entrega como 11.995,68 USD de plata en riesgo y no lo son (las 16 tienen `qty_delivered = 0`). Dentro de esas 16 apareció un hallazgo real en la dirección opuesta: 4 órdenes donde el espejo dice `entregada_completa` y su única salida figura cancelada. |
| 1.4 | Conciliación espejo vs Odoo | **cerrada** — 9 de 9 cuadran al centavo tras un sync completo |
| 1.5 | Qué prueba cada partida del balance | **cerrada** — 8 externas, 1 invariante, 9 internas; etiquetado aplicado |
| 2.1 | Ausencia de dato ≠ número | **cerrada** — decidido el 11-sep-2026: error duro. Costó los 9 tests que la medición predecía. Escritura levanta, lectura devuelve cero, las sugerencias omiten el pago. Verificado contra la copia sin tasas: ninguna pantalla se cayó. Destapó una mina mayor (una NC en bolívares se sumaba como dólares) que quedó desactivada. **Antes:** preparada, esperaba decisión. Ver [2.1](2.1-ausencia-de-dato.md): la premisa del plan («42 tests asertan cifras calculadas con una tasa falsa») medida resultó falsa, y sembrar tasas bajó el costo de la decisión de 40 tests a 2 (más 5 que hay que reescribir a propósito). Sigue siendo prioritario: el default de 2019 no solo se lee, se **escribe** en un campo congelado. Espera tu decisión sobre las minas 1, 5 y 8. La 1 ya está medida y contada (`engine/precios_rapidos.py`), así que la decisión se toma sobre números y no sobre una lectura del código. |
| 2.2 | Invariantes al escribir | **cerrada, las dos mitades.** 8 restricciones `CHECK` en la base, y la validación en el repositorio (`db/invariantes.py`) enganchada en los dos caminos que escriben dinero. No cambia qué se acepta —falla igual que la base— pero dice qué fila y con qué valores en vez de un `IntegrityError` crudo. 23 tests, incluidos los que verifican que la versión Python y la cláusula SQL coincidan. |
| 2.3 | Alertas | **cerrada** — corrida diaria funcionando (51 chequeos), con el hallazgo de sobreaplicación. Se le sumó el desacuerdo de listas de precio, y se arregló un bug del informe que contaba hallazgos sin imprimirlos. |
| 2.4 | Sacar caminos de dinero de app.py | **empezada** — **cuarenta piezas** extraídas o cerradas en **dieciocho módulos**, cada una con su medición A/B: `engine/universo.py` (qué órdenes entran), `engine/listas.py` (cuál lista valora el teórico — de ahí salió el hallazgo de las 789 órdenes), `engine/balance.py` (las 15 partidas internas **y** las 8 externas, probables con un Odoo de mentira), `engine/precios_rapidos.py` (la mina 1 del inventario, y sus ceros ahora quedan contados), `engine/conciliacion.py` (las dos referencias de un residual, que estaba definida adentro de un `for`, **y** el reparto FIFO de un pago), `engine/saldos.py` (cuánto falta cobrar: los cuatro saldos, **el que consume el FIFO**, con sus cuatro trampas fijadas de a una, más la valuación de notas de crédito) y `engine/reversadas.py` (las dos lecturas de una factura anulada, con el defecto medido en 17 órdenes y 4.489,12 USD) y `engine/facturado.py` (qué le queda facturado a una orden y qué le falta: las cuatro reglas que dictaminaron S00372, incluido el orden no intercambiable entre la retención de IVA y la alerta de NC). Las ocho últimas, todas del 11-sep-2026: `listas.vigencia_efectiva` (el rango de vigencia **con el denominador de donde sale** — las nueve listas activas no tienen una sola regla con fecha de fin, así que nunca disparan la mina del precio vencido), `listas.reglas_duplicadas` (seis productos con **dos precios distintos en la misma lista activa**, dos de ellos con una regla en 0,00), `listas.comparar_fuentes_de_lista` (las **dos fuentes de configuración** que nada sincroniza), `discount_audit.descuento_de_linea` (la regla del descuento de línea en el camino **vivo** — la pieza 21 había quedado cableada en una función que nadie llama), `identidad_de_reglas.py` (de qué tabla salió la elección al prender una regla), `kpis_de_saldos.py` (el encabezado del reporte contra sus propias filas), `equivalents.equivalente_usd_a_tasa` y `_congelar_equivalentes` (el equivalente **congelado** se guardaba con dos precisiones según quién lo escribiera, en cuatro sitios). Del 12-sep: `notas_de_credito.topar_nota_de_credito` (los dos techos de la NC sugerida, con cinco `1.16` a mano reemplazados por la config), y la **corrección de la novena invariante** más la escritura tolerante de los lotes del demonio, que salieron del banco de escenarios. Del 17-sep: el incidente de producción de la factura consolidada ([7](7-incidente-produccion.md)); `resolver_tasa_bcv_vinculacion` — que una decisión de un día antes había dejado sin nada que hacer — reducida a lo que ya era: siempre BCV-USD, sin tocar el repo (`orden_en_periodo_historico`, su único llamador, se borró); y el mismo hallazgo repetido en un tercer sitio, `_get_conciliaciones_sugerencias_sync` (pieza 37): la sustitución por tasa euro de un pago huérfano, también inalcanzable desde la misma decisión, y nadie la había mirado. La misma familia de hallazgo apareció una cuarta vez, esta al revés (pieza 38, commits 9800a10/7cf19ad): `EngineRunner.build_inputs` (`engine/runner.py`) tenía su **propia** lectura, independiente, del mismo selector de Configuración que la decisión 5 fijó en `False` — 58 de 83 órdenes de la ventana histórica en QA calculaban el monto real con el precio de la Lista Histórica que esa decisión prohibió, porque ningún camino de `web/app.py` pasa por `build_inputs`. Corregido: `build_inputs` deja de leer el selector, igual que `web/app.py`. Del 18-sep: `conciliacion.clientes_con_pagos_huerfanos` (pieza 39), un concepto de adentro de `_get_reporte_saldos_sync` — dedupea por `pago_id` (saldo máximo) y filtra por el umbral compartido del módulo para decidir qué clientes entran a la regla "Equiparar" del Diferencial Cambiario; baja esa función de 65 a 64 líneas sin cubrir sobre 1.029. Y `engine/reasignaciones.py::pago_reasignado_mas_reciente` (pieza 40, módulo nuevo), un concepto de adentro de `get_cobranza_pagos_unificado` — de qué fila de `BandejaAuditoria` es la reasignación por Odoo MÁS RECIENTE de un pago, con test explícito del bug que corregía (antes se quedaba con la última fila que devolviera la consulta, sin garantía de orden); baja esa función de 36 a 29 líneas sin cubrir sobre 481. **2.141 tests**, cobertura **82,43 %**, ~1.479 líneas sin cubrir en `app.py`. Es la tarea más larga del plan y se hace por pedazos. **Una desviación del plan, dicha:** el cierre del plan decía «no migrar los 19 llamadores de `get_rate_for_datetime` porque el fallback nunca dispara». Eso era cierto mientras el fallback devolvía 36,5/38,0; cuando 2.1 lo convirtió en error (decisión del 11-sep), cada llamador pasó a tener que decidir qué hacer ante la ausencia, y el banco mostró el 12-sep que ocho de quince tumbaban algo entero. Se decidió en los quince, con la regla «leer degrada, escribir es 400». |
| 3 | Escenarios de error humano | **cerrada** — 46 pruebas que cubren las 31 filas de la tabla del plan, verificado marca por marca. Produjo 2 hallazgos del sistema, 2 protecciones de Odoo que la tabla no contemplaba, y **9 bugs del propio andamiaje**. El noveno apareció al reejecutar el banco después de las ocho extracciones: hacía fallar `facturar()` y con él 20 escenarios en cascada. Reejecutado después del arreglo: **45 pasan y 1 falla, y es el rojo intencional** (el teórico que no se re-verifica al cambiar la fecha). **El canario dio cero en las 46: nada se emitió.** |
| 4 | Estrés y fallas | **cerrada** — 6 escenarios en verde, y el volumen **ejecutado** (ya no extrapolado): a 10× los cinco reportes terminan con crecimiento lineal. Apareció un falso rojo del balance que solo se ve a volumen. El motor sigue extrapolado en ~16 h por un tope de 50/ciclo. |
| 5 | Dashboard | **cerrada** — 6 hallazgos, 6 aplicados |
| 6 | Bugs y deuda | **medida** — ver [6 — Deuda medida](6-deuda-medida.md). Corregido: el ítem `OdooPriceResolver` había bajado a Media por una medición mía mal hecha; vuelve a Alta y por otro motivo. |

## Lo que espera tu decisión

**Están las once en un quiz**, cada una con su medición y la consecuencia de cada
camino: <https://claude.ai/code/artifact/5a35b1e4-3b92-4108-bd47-4fd81c0e8f0b>. Las
respuestas se guardan solas y las leo de acá; no hace falta copiarlas.

Tres de las que estaban en esta tabla se cerraron y salieron:

| Qué era | Cómo cerró |
|---|---|
| El `36,5 / 38,0` de 2019 | **Error duro**, decidido el 11-sep-2026. Costó 2 tests más 5 reescritos a propósito. Ver [2.1](2.1-ausencia-de-dato.md). |
| El 2 % de primera compra, cableado y con la categoría invertida | Corregido: la regla quedó configurada al 2 % sobre Comercial, más el campo `categorias_descuento` que hacía falta. A/B sobre las 119 órdenes: 742,24 USD de las dos maneras, cero diferencias. |
| «Falta `_primer_id_activo` en `app.py:4434`» (789 órdenes) | La guarda está aplicada en los **cinco** sitios. Pero el hallazgo no se cerró: se movió un nivel arriba — ver la decisión 4, las dos fuentes de configuración. |

### Las once del quiz — respondidas el 12-sep-2026

| # | Qué | Tu respuesta | Estado |
|---|---|---|---|
| 1 | Parciales corrompidos | «Dame la lista por orden, empezando por las 10 con exceso, y las voy reconciliando en Odoo» | **Entregada**: [Listas del 12-sep](https://claude.ai/code/artifact/c40c4e58-a490-4550-a3f2-408efaa10a35), sección 2: 80 órdenes con señal fuerte, 138 pagos, las 19 con exceso primero. Las señales fuertes ya están en la vigilancia diaria, y el demonio ya no deja que un parcial corrompido agrande el espejo. Lo que sigue es en Odoo: **tus manos**. |
| 2 | Alcance del descuento de línea | solo obsequios | **Aplicado** (`ALCANCE_OBSEQUIOS`). |
| 3 | Los cuatro sobrepagos | «Revisar los cuatro casos antes de decidir» | **Revisados, misma lista, sección 1**: en los cuatro el residual negativo es *exactamente* la suma de los parciales «Ajuste Dif» que Odoo aplicó contra la factura. No hay plata del cliente de más: ni nota de crédito ni saldo a favor; hay que deshacer esos ajustes en Odoo (**tus manos**). |
| 4 | Fuente de configuración de listas | el mapeo | **Aplicado.** |
| 5 | Vía euro / lista histórica | «apaga todo», y la nota: *solo para auditoría, no debe modificar los montos reales* | **Aplicado**: ninguna ruta que produzca un monto real lee ya la lista histórica ni la tasa euro (teórico de saldos y Ventas, abonos, sugerencias, vinculaciones nuevas); la auditoría sigue leyendo el selector. Medido en la copia: 54 órdenes sin lista + 38 en la ventana, 75 con teórico congelado que **no se recalcula** (es otra decisión). |
| 6 | Tope de descuento | «Otro», con la regla del 2 % de primera compra | La regla que enunciaste (2 % solo si no aplica obsequio, o si la compra está fuera de su vigencia) **es lo que el motor hace**, y la regla configurable que preguntaste ya existe: `PRIMERA_COMPRA_COMERCIAL_2PCT`. Quedó fijada en cuatro tests. El tope del auditor (4 %) sigue siendo un parámetro, no una política. |
| 7 | Reglas de precio en 0,00 | borrarlas | **Tus manos** en Odoo (ítems 3896 y 3858). |
| 8 | Encabezado del reporte de saldos | corregirlo | **Aplicado**: los KPI se recalculan sobre las filas que quedan; el juego viejo queda en `kpis_antes_del_filtro`. |
| 9 | Las tres lecturas muertas | borrarlas | **Aplicado**: 224 líneas menos, y la pieza 21 del motor que solo ellas usaban. |
| 10 | Escenario que no quedó claro | «pagada», «proponme una solución para que coincida la información», y después «aplicalo» | **Aplicado**: las tres pantallas (reporte de saldos, sugerencias, auditoría) usan `pagada_unificada` = por estado, más tolerancia de centavos, más señal de sobrepago aparte, y las anuladas nunca cuentan. Medido en la copia: 47 órdenes pasan a cobradas por centavos, 15 anuladas dejan de contar en sugerencias, 4 quedan pagadas con señal (las de «Ajuste Dif»). |
| 11 | Por dónde arrancar con el precio desviado | por las líneas con descuento por las dos vías | **Entregada**, misma lista, sección 3: 72 líneas en 43 órdenes, 2.822,88 USD (el quiz decía 92: la referencia por población se afinó después). |

### Las que no están en el quiz porque no son una elección entre dos caminos

| # | Qué | Por qué no lo aplico solo |
|---|---|---|
| 12 | **4 órdenes donde el espejo se contradice**: dice `entregada_completa` y su única salida figura `cancel` | 8.535,41 USD de monto. Corregir `entregada_completa` mueve el universo de órdenes de seis páginas. Ver [1.3](1.2-1.5-auditoria.md). |
| 13 | **1.108,59 USD** de mercancía devuelta que la factura sigue cobrando (3 órdenes) | Hay que emitir la nota de crédito en Odoo: **tus manos**. El sistema ahora las lista. |
| 14 | Precio 0 con Odoo caído, y teórico ausente = saldo cero | Cambian lo que muestra la pantalla para una orden no evaluable. |
| 15 | ¿«Ventas» del dashboard debería mostrar también el neto teórico? | Es una tarjeta nueva, no un arreglo. |
| 16 | **Fase 0: rotar la credencial de producción** y purgar el historial de git | **Requiere Railway: tus manos.** Sigue siendo el ítem más urgente del plan. El procedimiento está escrito paso a paso en [0](0-credencial.md), y `scripts/verificar_secretos.py` ya es la primera etapa de la barrera para que no vuelva a entrar. |

## Lo que este trabajo encontró, en un renglón cada uno

Ordenado por lo que costaría no arreglarlo, no por severidad nominal.

| Hallazgo | Cuánto | Dónde |
|---|---|---|
| **Una factura que consolida varias órdenes tumbaba el ciclo del demonio, en producción, cada 5 min** | encontrado el 17-sep vía logs de Railway, no en QA | [7](7-incidente-produccion.md) · **aplicado** |
| El default de 2019 está **escrito** en las 1.463 vinculaciones del espejo | 82,7 M aplicados | [6](6-deuda-medida.md) |
| El reporte de saldos valora con una lista vencida; las otras tres páginas no | 789 órdenes, 194.532,51 VES / 115.805,93 USD de desvío bruto | [3](3-escenarios.md) |
| Una lista de precios vencida **no puede** marcarse: `rules[0]` gana sobre la fecha | — | [3](3-escenarios.md) |
| Pagos sobreaplicados: se acredita plata que el cliente no puso | 1.269,25 USD contra el campo crudo (la cifra vieja de 1.333,85 pasaba por la conversión a equivalente) | [2.3](2.3-alertas.md) · [6](6-deuda-medida.md) |
| Mercancía devuelta con la factura viva, fuera de la bandeja | 1.108,59 USD | [3](3-escenarios.md) |
| El espejo dice «entregada» en 4 órdenes cuya única salida está cancelada | 4 órdenes, 8.535,41 USD de monto | [1.3](1.2-1.5-auditoria.md) |
| El motor a 10× tarda ~16 h por un tope de 50/ciclo | — | [4](4-estres.md) |
| El dashboard decía «Tasa BCV» sobre el equivalente de Odoo | — | [5](5-dashboard.md) · aplicado |
| El espejo de líneas de factura era 89,6 % ruido | — | [1.3](1.2-1.5-auditoria.md) · aplicado |
| **Cinco sitios deciden con qué lista se valora un teórico leyendo de dos fuentes que nada sincroniza** | USD=11/BCV=10 contra USD=4/BCV=5 (archivadas) | [2.4](2.4-modularizar.md) · chequeo diario en ALTA |
| El equivalente **congelado** se guardaba con dos precisiones según quién lo escribiera, en cuatro sitios | `333.333333` vs `333.3333333333…` en un campo que no se recalcula nunca | [2.4](2.4-modularizar.md) · aplicado |
| 491 de 914 órdenes con al menos una línea de precio desviado de su lista | 33.684,39 USD metidos **en el precio** · 7.193,61 en el campo · 80 líneas cobradas **por encima** | `scripts/auditar_precios_contra_lista.py` |
| 37 órdenes sin lista de precio, valuadas en **EUR** (moneda inactiva en la base) | 2.916,49 USD contra la lista histórica; 68 de 75 líneas sobre el tope | `scripts/auditar_precios_contra_lista.py` |
| Seis productos con **dos precios distintos en la misma lista activa**, dos con una regla en **0,00** | un tambor que podría salir gratis; el cero nunca se sirvió | [2.4](2.4-modularizar.md) · reportado por el endpoint |
| El encabezado del reporte de saldos no coincide con sus propias filas | 8.278,15 USD | [2.4](2.4-modularizar.md) · las dos lecturas expuestas |
| Diez `hasattr` podían vaciar la bandeja de auditoría en silencio, y el PATCH decía «ok» sin guardar | — | [2.4](2.4-modularizar.md) · aplicado |
| Quién aprueba un descuento salía del **cuerpo del request**, con un `prompt` que la persona tipea | — | [2.4](2.4-modularizar.md) · aplicado |
| Una regla de volumen sin unidad de medida dejaba en blanco **toda** la pantalla de reglas | — | [2.4](2.4-modularizar.md) · aplicado |
| El camino del **pago** no usaba la definición que rige el precio, contra tu decisión del 11-sep | 15 órdenes, 3 con abonos en bolívares | [2.4](2.4-modularizar.md) · aplicado |
| **Los diez pagos «sobreaplicados» no son dinero: son parciales corrompidos por el bug de Odoo al editar fechas** | 9 de 10 con la firma, 1.259,05 de 1.269,25 USD; 609 pagos con alguna señal, 390 órdenes | `scripts/cruzar_diferencial_cambiario.py` · vigilancia diaria |
| `invalidar_tasas()` no tenía ningún llamador: una tasa cargada a mano tardaba hasta 5 min en verse en `tasas_vigentes()` | dos lectores en desacuerdo durante esa ventana | [2.4](2.4-modularizar.md) · aplicado, y las 24 lecturas directas del plan ahora sirven del caché |
| La suite leía `secrets/pricelist_mapeo.json` del disco del desarrollador | 4 tests veían mi config local en vez del default | `tests/conftest.py` · aplicado |
| La barrera de cobertura estaba roja sin ningún test fallando | — | [1.2](1.2-1.5-auditoria.md) · aplicado |
| El 2 % de primera compra se otorgaba por **ausencia de configuración**, y sobre la categoría invertida | 85 de 119 órdenes no le tocaba | [6](6-deuda-medida.md) · **cerrado** el 11-sep |
| El mismo descuento da dos montos según qué tabla se lea | brecha de 1.128,91 USD | [6](6-deuda-medida.md) |
| El verificador de huecos de vigencia decía «ninguno» sin poder mirar | 0 de 16 listas evaluables | [6](6-deuda-medida.md) · aplicado |
| Devuelto supera lo entregado (cantidad negativa) | 14 unidades | [2.2](2.2-invariantes.md) |
| La novena invariante (de ayer) tumbaba el lote **entero** de vinculaciones del motor cada 5 min, por los diez pagos ya sobreaplicados: «10 filas no se escribieron» y no se escribía ninguna | toda promoción PENDIENTE→CONCILIADO y todo recálculo congelados en cualquier base con esos diez; producción los tiene (no desplegado: `main` no lleva la invariante) | [2.4](2.4-modularizar.md) · **aplicado** el 12-sep, lo encontró el banco de escenarios la primera vez que corrió con ella |
| Un solo pago con fecha sin tasa abortaba el sync de aplicaciones de Odoo y el resync **enteros**, cada 5 min, desde que 2.1 dejó de inventar 36,5/38,0 (`TasaNoDisponible` es `RuntimeError`, y el único `except` cercano miraba `ValueError`) | toda aplicación nueva de Odoo sin reflejar hasta que alguien cargue esa tasa; en QA pasaba con dos fechas | [2.4](2.4-modularizar.md) · **aplicado** el 12-sep: el pago se salta y queda en la bandeja como `pago_sin_tasa_para_su_fecha` |
| Un solo pago con fecha sin tasa tumbaba **ocho** lecturas o pasos: `/api/auditoria` (500), el balance (500), el historial y con él cobranza (500), el bloque de Odoo de Ventas (vacío), el detalle de ventas (sin pagos de Odoo), el resumen (sumaba de menos en silencio), el sync de aplicaciones, el resync, el lote masivo y el Auto-FIFO (ciclo entero). Y cinco sitios usaban **HOY** como fecha de un pago sin fecha | todo lo que la decisión de 2.1 (no inventar 36,5/38,0) dejó sin la otra mitad: degradarse a «no se pudo mirar» | [2.4](2.4-modularizar.md) · **aplicado** el 12-sep en los 15 llamadores de `get_rate_for_datetime`: donde se lee, `sin_tasa_para_su_fecha` visible en la respuesta; donde se escribe, 400 que nombra la fecha |
| Una factura que Odoo borró después del sync (borrador eliminado) reventaba la consulta de pagos y dejaba a **todas** las órdenes sin abono de Odoo en Ventas y en el reporte de saldos hasta el sync siguiente | visto en QA (`account.move(12439,)`) midiendo la pieza 35 | [2.4](2.4-modularizar.md) · **aplicado** el 12-sep: se pregunta primero cuáles facturas siguen existiendo |
| El abono del reporte de saldos aplica «local manda si existe» aunque la regla general del sistema sea «Odoo prevalece»; la rama del máximo que decía otra cosa nunca corrió | no medible en QA (0 vinculaciones conciliadas); el log del reporte en producción ya nombra las órdenes y la diferencia | [2.4 §35](2.4-modularizar.md) · **decisión tuya** una vez medido |
| Dos definiciones de «orden histórica» en desacuerdo | medido de nuevo: **15 órdenes**, 3 con abonos en bolívares | [2.4](2.4-modularizar.md) · aplicado al camino del pago el 11-sep |
| `EngineRunner.build_inputs` tenía su **propia** lectura del selector histórico, independiente de la que la decisión 5 fijó en `False` — el motor mismo (monto real y teórico) seguía valorando con la Lista Histórica | 58 de 83 órdenes de la ventana histórica en QA, diferencias de 6 a 1.133 VES por orden | [2.4 §38](2.4-modularizar.md) · **aplicado** el 17-sep (commits 9800a10/7cf19ad); sin confirmar en producción (Postgres solo por red interna de Railway, ver [7](7-incidente-produccion.md)) |
| `GET /api/config/descuentos-volumen` y `GET /api/config/descuentos-diferencial-cambiario` reventaban con `AttributeError` en **todas** las llamadas (`r.litros_minimo` y `r.nombre`, dos campos que la migración de unificación de nombres había borrado hace semanas de los dataclasses) — las tablas "Reglas Vigentes" de esos dos paneles quedaban en "Cargando..." para siempre en producción | confirmado en logs de producción vía `railway logs` el 18-sep; el mismo bug ya documentado en `engine/discounts.unidad_de_volumen`, corregido ahí solo para `get_todas_reglas_descuento` y nunca replicado en estos dos endpoints hermanos | **aplicado** el 18-sep, 3 tests nuevos que reproducen el 500 |
| Balance de comprobación: tres partidas de pagos ("importe en su moneda", "equivalente BCV" y "tasa de Odoo") comparaban el monto NETO de nuestro espejo (ya descontado un reembolso de sobrepago embebido en el mismo asiento) contra el monto BRUTO que devuelve Odoo -- un pago con reembolso salía nombrado en las tres a la vez, como "plata que falta" y "tasa mal cargada", sin serlo | pago real de producción id 1084 (19-sep): reembolso de 2.372.712,49 VES en el mismo asiento, que solo explicaba las tres partidas descuadradas | **aplicado** el 19-sep: `montos_reembolsados_en_pagos` (extraída a función de módulo en `cxc/odoo/client.py`) se llama desde `engine/balance.py` y las tres partidas comparan neto contra neto; 15 tests nuevos |
| Dos pagos reales (id 1813 y 1814, agosto) con la tasa de Odoo (`tax_today`) desviada más de 2 % del BCV oficial de su fecha de valor | la tasa estampada (832,49) coincide EXACTO con el BCV oficial del 11 al 14 de **septiembre** -- un mes después de la fecha del pago (10/11 de agosto): se cargaron tarde y Odoo les clavó la tasa del día de carga, no la del día de valor | **investigado el 19-sep, no corregido**: es un dato mal cargado en Odoo, no un bug de código -- corresponde corregir la tasa de esos dos pagos directamente en Odoo |
| 5 órdenes que Ventas cuenta "por cobrar" (con saldo real) pero el Reporte de Saldos excluye por `pagada_unificada` -- causa raíz: `_facturas_confirmadas_pagadas_por_so` (regla 5 de `clasificar_estado_cxc`, la que alimenta a Ventas) tenía su PROPIA lectura de estado exacto, sin la tolerancia de centavos ni la señal de sobreaplicada que la regla unificada ya tiene desde la decisión 10 del quiz | S00061 (-38,98), S00182 (-46,44), S00795 (-56,63) = **142,05 USD de sobrepago real**; S00237 (0,01)/S00454 (0,00) sin materialidad | **corregido el 21-sep**: `_facturas_confirmadas_pagadas_por_so` pasa a usar `pagada_unificada`, igual que las otras tres pantallas (ahora cuatro sitios); verificado contra producción, `faltantes` de esta partida pasó de 5 a 0. La corrección de código NO resuelve el sobrepago -- los 142,05 USD siguen necesitando tus manos en Odoo (nota de crédito, reembolso, o reaplicar al siguiente cobro de cada cliente); la señal `sobreaplicada` sigue disponible para encontrarlos |
| Una promoción "Recurrente" tipo producto (`PROMO_NUEVO_GLOBAL`, el 12+1 de GLOBAL OIL/CAJA) tenía `descuento_fallback=0,02` cargado -- ese campo es el respaldo porcentual que se usa SOLO cuando la orden no llega a la compra mínima del obsequio, y debe ser 0 salvo que la regla lo defina así a propósito (su hermana, `PROMO_12_MAS_1`, lo tiene en 0). Con el 2 % cargado, la regla le regalaba ese % a CUALQUIER orden de esa marca/categoría que no llegara al mínimo -- sin importar si era la primera compra del cliente o no. Y la etiqueta del desglose decía "Descuento primera compra" **siempre**, sin importar si el descuento salía del camino de primera compra real o del de promociones "Recurrente" | reportado con captura el 22-sep sobre S01046 (la compra número 18 del cliente, no la primera); confirmado que "todas las órdenes" mostraban el mismo concepto | **aplicado el 22-sep**: dato corregido en producción (`scripts/corregir_fallback_promo_nuevo_global.py`, idempotente) y la etiqueta ahora distingue el origen real (usa la `descripcion` de la regla si existe, si no un rótulo genérico "Descuento recurrente X%" en vez de "primera compra"); 3 tests nuevos, verificado contra producción que S01046 ya no muestra el concepto fantasma |
| Los 6 paneles de solo lectura por familia (Recompra, Pronto Pago, Volumen, Obsequio/Promociones, Producto, Diferencial Cambiario) en Configuración → Descuentos seguían mostrándose bajo el editor único, 100 % redundantes con la Matriz Consolidada (mismas 6 familias, misma data) — confundible con los formularios viejos que sí se retiraron el 11-sep | reportado por vos con captura el 19-sep, confirmado que era diseño intencional (nota del propio HTML) y no un bug de código o de deploy | **aplicado** el 19-sep: los 6 paneles se eliminaron del HTML; "Días de Crédito Máximo por Volumen" y "Exclusiones Mutuas" se conservan (no son de las 6 familias, ni la Matriz Consolidada los cubre) |

Y lo que hay que hacer con las manos, que es lo más urgente de todo: **rotar la
credencial de producción** y purgar el valor viejo del historial de git. Rotar sin
purgar solo cambia qué credencial está expuesta.

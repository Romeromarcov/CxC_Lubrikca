# Fase 6 — La deuda, medida

El plan listaba ocho ítems de deuda de código con una severidad estimada. Acá
están medidos contra la copia de producción. Tres bajan de severidad al medirlos,
uno sube, y dos se cierran.

| Ítem | Severidad del plan | Medida | Estado |
|---|---|---|---|
| `OdooPriceResolver` sin calibrar para Odoo 18 | Alta | **789 órdenes valoradas con una lista vencida** | **sigue Alta, y por otro motivo** |
| El default `36,5 / 38,0` | Alta | 42 tests dependen; ya deja rastro | sigue Alta, espera tu decisión |
| Dos definiciones de «orden histórica» | Media | **2 órdenes vivas, 457,51 USD** | confirmada, acotada |
| Equivalentes congelados sin propagar correcciones | Media | **no medible en QA**; apareció otra cosa peor | sigue Media, y sube la 2.1 |
| El arreglo del scraper nunca corrió en producción | Media | no verificable desde acá | tuyo |
| Formularios de reglas legacy | Baja | — | espera tu confirmación |
| `SerieTasas` se lee sin caché | Baja | **3 sitios, 2 deliberados** | **cerrada** |
| Tramos de volumen en USD | Baja | — | cuando lo pidas |

---

## `OdooPriceResolver`: no es que falten precios, es que se elige la lista vencida

El plan pedía medir esto antes de decidir. Lo medí, publiqué **10 de 801 órdenes
(1,2 %), 10.059,44 USD** valoradas por fórmula, y con eso bajé el ítem a Media
diciendo que el arreglo era cargar los precios faltantes y no tocar el resolver.

**Esa conclusión estaba mal.** Al reejecutar el chequeo dio 7 en vez de 10 sobre
datos que nadie tocó; perseguir la diferencia mostró que el problema real es otro y
es más grande. El detalle completo, incluidos mis tres errores de método, está en
la [3](3-escenarios.md#cómo-apareció-y-qué-medí-mal).

Lo medido ahora, con `scripts/auditar_listas_de_precio.py`:

| | órdenes reales | monto |
|---|---:|---:|
| con alguna línea cuyo producto no tiene regla de precio | 13 (1,5 %) | 7.743,11 USD de teórico guardado |
| **valoradas con una lista archivada y vencida por el reporte de saldos** | **789** | **194.532,51 VES / 115.805,93 USD de desvío bruto** |

La primera fila es lo que yo había medido (mal, y da parecido por casualidad). La
segunda es el hallazgo: `_get_reporte_saldos_sync` (`app.py:4434`) elige la lista
de precios sin pasar por `_primer_id_activo`, mientras los otros tres sitios que
arman un resolver sí lo hacen. En esta base eso es la lista 3/7 —archivadas, cero
reglas vigentes desde abril— contra la 10/11. El reporte de saldos **subvalúa** el
teórico un 18,9 % en VES y 16,8 % en USD.

Cargar los precios faltantes —el otro ítem, «Precios faltantes en la lista 5»—
arregla el 1,5 %. No toca el 18,9 %.

**Lo que sigue siendo Alta, y ahora tiene compañía:** el resolver está marcado
`pragma: no cover`, así que sus minas de la [1.1](1.1-fallbacks-silenciosos.md) no
tienen un solo test. Y hay una mina más, que la auditoría AST no podía cazar:
`_precio_fijo_en_lista` devuelve `rules[0]` cuando ninguna regla calza por fecha
(`odoo/price.py:174-177`), así que **una lista vencida entrega precios viejos sin
marcar `usa_fallback` nunca**. No traga una excepción ni devuelve un centinela
—devuelve un dato real de otra fecha—, y el clasificador busca las dos primeras
cosas.

## Dos definiciones de «orden histórica»: el plan tenía razón, y son 4

Las dos funciones:

- `orden_en_periodo_historico` (`app.py:7225`) — solo la ventana de fechas
  (20-feb a 12-mar-2026) y el toggle.
- `es_orden_historica` (`engine/historical_pricing.py:28`) — la ventana, **más**
  dos excepciones: `True` incondicional si la orden no tiene lista asignada, y
  `False` si su lista es una USD válida.

Medido sobre las 980 órdenes, difieren en **15**, que se parten en dos grupos de
naturaleza opuesta:

**11 órdenes: en la ventana, con lista USD válida.** Esta diferencia es
**deliberada y documentada** — el docstring de `es_orden_historica` la explica y
dice «11 órdenes reales de esa ventana SÍ tienen una lista USD real y vigente
asignada (ej. lista #7 "Pago USD Marzo")». Medido: **exactamente 11**. No es un
defecto; es la excepción funcionando.

**4 órdenes: sin lista, fuera de la ventana.** Éstas son las que el plan dice, y
el plan tiene razón en el número. Pero al mirarlas de cerca solo dos importan:

| orden | fecha | estado | monto | teórico | ¿vive? |
|---|---|---|---:|---:|:---:|
| S00162 | 2026-03-27 | **cancel**, sin entregar, sin facturar | 161.679,06 | — | no |
| S00091 | 2026-03-13 | **cancel**, entregada, con devolución | 1.578,86 | — | no |
| S00088 | 2026-03-13 | `sale`, entregada, facturada | 286,51 | 237,07 | **sí** |
| S00090 | 2026-03-13 | `sale`, entregada, facturada | 171,00 | 146,55 | **sí** |

S00162 es justamente la que el comentario del dashboard ya documenta como
`cancel` en Odoo e inflando «Ventas del Año»; S00091 está entre las 16
canceladas-con-entrega de la [1.3](1.2-1.5-auditoria.md). Las dos que quedan
vivas suman **457,51 USD** de orden y **383,62** de teórico.

**Qué les pasa concretamente.** Las dos son del 13-mar-2026, que es exactamente
`HISTORICAL_PRICE_LIST_END_EXCLUSIVE` — el primer día **fuera** de la ventana — y
no tienen lista asignada. Entonces:

- el **precio** sale por `es_orden_historica`, que devuelve `True` (no hay lista),
  así que se valoran con la lista histórica, que está referenciada al **euro**;
- el **pago** se mide por `orden_en_periodo_historico`, que devuelve `False` (13-mar
  está excluido), así que su equivalente se congela a la tasa **BCV-USD**.

O sea: la venta se valora en una referencia y el cobro en otra. Es chico —457,51
USD— pero es exactamente la clase de incoherencia que este blindaje busca, y no
se arregla sola.

*Arreglo:* una sola definición. La respuesta correcta para una orden sin lista
parece ser la de `es_orden_historica` (si no hay otra lista, la histórica es la
única que puede valorarla), y entonces el camino del pago debería usar la misma.
**Costo: bajo** en código, y necesita tu confirmación porque mueve el equivalente
congelado de dos órdenes.

*Nota aparte:* `HISTORICAL_PRICE_LIST_START` y `_END_EXCLUSIVE` están definidas
**dos veces**, en `historical_pricing.py:24` y en `app.py:6615`. Hoy coinciden.
Si alguna vez divergen, las dos definiciones van a diferir también en la ventana,
y eso sería mucho peor que estas 4 órdenes.

## Equivalentes congelados: no se pudo medir, y en el intento apareció algo peor

El ítem dice que si se corrige una tasa vieja, las vinculaciones ya aplicadas
conservan el equivalente que congelaron —correcto como diseño contable— y que hoy
nada lista cuáles quedaron con una tasa que después se corrigió.

**No se pudo medir en el espejo de QA**, y hay que decirlo en vez de dar un cero
tranquilizador: `tasas_historicas_auditoria` tiene **0 filas** ahí y `serie_tasas`
tiene **6**, todas de hoy. Son tablas de trabajo humano y del scraper, no vienen
del sync de Odoo, así que la comparación «tasa congelada contra la serie actual»
no tiene contra qué correr. Queda pendiente contra producción, donde el script de
integridad corre igual (es solo lectura).

### Lo que sí apareció al mirar

Al revisar con qué tasa se congelaron esas vinculaciones, **las 1.462 tienen
`tasa_bcv_aplicada = 36,50` y `tasa_binance_aplicada = 38,00`**. Una sola
combinación, en el 100 % de las filas. Son las tasas de 2019: el default de
`get_rate_for_datetime`, la mina 8 del inventario [1.1](1.1-fallbacks-silenciosos.md).
La tasa real de hoy es **827,74**, o sea 22 veces más.

### Cuánto es en plata

La suma cruda de `monto_aplicado` no sirve para medirlo: mezcla monedas, que es
justamente lo que el balance audita. Separadas:

| moneda del abono | vinculaciones | nominal | equivalente acreditado | al cambio real |
|---|---:|---:|---:|---:|
| USD | 916 | 199.453,77 USD | 199.453,77 | igual |
| **VES** | **546** | **82.496.372,90 Bs** | **2.260.174,60 USD** | **~99.664 USD** |

Los abonos en bolívares quedaron acreditados como **2,26 millones de dólares**
cuando a la tasa real son unos **99.664**. Veintidós veces y media de más.
(82.496.372,90 ÷ 36,50 da exactamente los 2.260.174,60 acreditados, que es la
confirmación de que la tasa usada fue el default.)

**Esto no dice que producción esté así hoy** — ahí la serie de tasas existe. Dice
de qué tamaño es el agujero cuando no existe, y que nada avisa mientras se está
cayendo dentro.

Esto cambia cómo hay que leer ese ítem. El plan dice del default: «Hoy nunca
dispara». Medido: en un entorno sin la serie sembrada **dispara en todas**, y no
solo devuelve un número equivocado — **lo escribe en un campo congelado**, que por
diseño no se corrige después. Un espejo levantado de cero, una migración, o una
ventana en la que el scraper estuvo caído, y cada equivalente que se calcule en
ese hueco queda mal para siempre.

Los 42 tests que dependen del valor no son la exposición principal. La exposición
es que el default escribe.

Vale aclarar qué NO invalida esto de lo medido antes: el hallazgo de los 10 pagos
sobreaplicados por 1.333,85 USD compara `monto_aplicado` contra `pagos.monto`, las
dos cifras en la moneda del pago y sin ninguna tasa en el medio, así que se
sostiene. Y la restricción `equivalente ≤ nominal` de la
[2.2](2.2-invariantes.md) validó contra estos datos y sigue siendo correcta —
aunque conviene saber que acá pasa con margen de sobra justamente porque la tasa
es 22 veces menor de lo que debería.

*Arreglo:* el de la 2.1, y sube de prioridad. `get_rate_for_datetime` tiene que
levantar `TasaNoDisponible` en vez de devolver el default, y quien congela un
equivalente tiene que negarse a hacerlo sin tasa. Mientras eso no esté, la
corrida diaria de la [2.3](2.3-alertas.md) debería incluir un chequeo de
«vinculaciones congeladas a 36,50» — que es una consulta de una línea y detecta
el caso en el día en vez de en un balance de meses después.

## `SerieTasas` sin caché: cerrada, el ítem estaba sobrestimado

El ítem dice «quedan 24 lecturas directas a la base repartidas por `app.py`. Las
que pasan las filas por parámetro están bien; las otras deberían ir por
`tasas_vigentes()`».

Medido con el AST: **23 llamadas**, no 24, y el reparto es muy distinto del que
sugiere el ítem:

| forma | cuántas | ¿hay que tocarlas? |
|---|---:|---|
| pasan las filas por parámetro | 19 | no, están bien |
| es la carga del caché mismo (`tasas_vigentes`) | 1 | no |
| construyen `Tasas(...)` directo | **3** | **dos de las tres, no** |

De las tres que construyen `Tasas` a mano:

- `resolver_tasa_bcv_vinculacion` (`app.py:7263`) lo hace **a propósito**, y el
  comentario lo dice: «Fresco, no `tasas_vigentes`: acá se está fijando la tasa
  con la que va a quedar congelada una Vinculación, y el caché de 5 minutos podría
  ocultar una tasa recién cargada.» Es correcto.
- `post_cambiar_tipo_tasa_bcv` (`app.py:9214`) es el mismo caso: cambia el tipo de
  tasa de una vinculación, así que necesita la serie fresca.
- `get_rate_for_datetime` (`app.py:2173`) la carga solo cuando el llamador no le
  pasó `rows`. Es el respaldo perezoso de una función que casi siempre las
  recibe.

Así que el trabajo real del ítem es **cero o casi**: dos de los tres son
deliberados y documentados, y el tercero es un respaldo. Se cierra, y queda
anotado para que el próximo inventario no lo vuelva a levantar.

## Seguridad: rotar la credencial de producción

El ítem más urgente de toda la lista y el único que **no puedo hacer yo**. Dos
pasos, y el segundo es el que se olvida:

1. **Rotar** la contraseña de la base en Railway y en cualquier `.env` local.
2. **Purgar el valor viejo del historial de git.** Mientras siga ahí, cualquiera
   con acceso al repositorio lo tiene, y rotar sin purgar solo cambia qué
   credencial está expuesta. Se hace con `git filter-repo` o el BFG, y obliga a
   reescribir el historial: todos los clones existentes quedan divergentes, así
   que hay que avisar antes.

Lo mismo aplica a la API key del Odoo de prueba que compartiste en este hilo:
está en `.env.qa`, que **no** se versiona (lo agregué a `.gitignore`), pero sigue
viva hasta que la revoques o el entorno se destruya.

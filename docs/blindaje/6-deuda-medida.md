# Fase 6 — La deuda, medida

El plan listaba ocho ítems de deuda de código con una severidad estimada. Acá
están medidos contra la copia de producción. Tres bajan de severidad al medirlos,
uno sube, y dos se cierran.

| Ítem | Severidad del plan | Medida | Estado |
|---|---|---|---|
| `OdooPriceResolver` sin calibrar para Odoo 18 | Alta | **789 órdenes valoradas con una lista vencida** | **sigue Alta, y por otro motivo** |
| El default `36,5 / 38,0` | Alta | 42 tests dependen; ya deja rastro | sigue Alta, espera tu decisión |
| Dos definiciones de «orden histórica» | Media | **2 órdenes vivas, 457,51 USD** | confirmada, acotada |
| Equivalentes congelados sin propagar correcciones | Media | **ya hay algo que los lista** — 1.487 con el default de 2019, 2.687.701,19 USD | herramienta entregada; el número real sale de producción |
| El arreglo del scraper nunca corrió en producción | Media | no verificable desde acá | tuyo |
| Formularios de reglas legacy | Baja | — | espera tu confirmación |
| Vigencias de listas sembradas | — | **0 de 16 listas las tienen**; el verificador decía «ninguno» sin poder mirar | instrumento arreglado; sembrarlas es tuyo |
| `SerieTasas` se lee sin caché | Baja | **22 sitios: 21 legítimos, 1 era un N+1** | **cerrada, y con un arreglo** |
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

## El descuento de TERA: no hay regla mal configurada, no hay regla

El plan lo dejaba como decisión con dos opciones: «hay que decidir si se marca como
no otorgado o si la regla está mal configurada». **Hay una tercera, y es la que pasa.**

### Qué es, medido

No es un descuento por volumen. La orden es **S00010** de TERA INGENIERIA
(cliente 202, 16-mar-2026), y su descuento tiene esta forma:

| | |
|---|---|
| origen | `primera_compra` |
| descripción | «Descuento primera compra Industrial 2.00%» |
| monto | 774,72 USD |
| **regla** | **`FALLBACK_PRIMERA_COMPRA_INDUSTRIAL_2PCT`** |

El `regla_id` dice `FALLBACK`. Y el código lo declara sin vueltas
(`engine/discounts.py:394`): «Id del 2 % de primera compra que aplica **cuando no hay
ninguna promoción configurada** a la fecha de la orden. No es una regla de la tabla:
es el respaldo histórico».

**Las nueve tablas de reglas de descuento están vacías** en esta base:
`descuentos_volumen`, `descuentos_pronto_pago`, `descuentos_producto`,
`descuentos_recompra`, `descuentos_diferencial_cambiario`, `promocion_primera_compra`,
`reglas_recurrencia`, `exclusiones`, `descuentos_sistema_aprobados` — cero filas todas.

Así que **las 119 filas de `descuento_aplicado` vienen del mismo respaldo cableado**,
por **2.782,41 USD**. No hay un solo descuento que salga de una regla configurada.

### Y sí, a TERA no se le otorgó

Eso queda establecido y no es opinión. Las siete líneas de S00010 tienen
`descuento = 0,0000`, y la factura `00000167` cobró el total: 23.948,80 USD netos de
impuesto contra 23.949,20 de suma de líneas — cuarenta centavos de redondeo. **El
descuento se calculó y no se cobró menos.**

El mecanismo para marcarlo existe —la tabla `descuentos_no_otorgados`— y tiene **cero
filas**: nunca se usó.

### El hallazgo que apareció al medirlo

El mismo 2 % da **dos montos distintos en dos tablas, para las 119 órdenes**:

| | total |
|---|---:|
| `descuento_aplicado.monto` | **2.782,41 USD** |
| `descuentos_teorico_usd` | **1.653,50 USD** |
| brecha | **1.128,91 USD** |

Y no es una conversión de moneda: el factor entre las dos va de **0,156 a 2,191**
según la orden — en algunas el aplicado es *menor* que el teórico. Ni siquiera la
relación del lado teórico es uniforme: `descuentos_teorico_usd` es el 2 % de
`teorico_usd` en **83 de las 119**, no en todas.

O sea que **cuánto descuento le corresponde a un cliente depende de qué tabla se
mire**, y la diferencia entre las dos lecturas es de 1.128,91 USD sobre 119 órdenes.

**Advertencia sobre estas cifras:** el lado teórico hereda el problema de las listas
archivadas ([más arriba](#odoopriceresolver-no-es-que-falten-precios-es-que-se-elige-la-lista-vencida)),
así que su magnitud es indicativa. El lado `descuento_aplicado` no depende del
teórico y es firme.

### Qué hay que decidir, reformulado

El plan preguntaba entre dos opciones y ninguna aplica: no hay una regla mal
configurada porque **no hay ninguna regla**. Las preguntas reales son tres:

1. **¿El 2 % cableado tiene que seguir otorgándose?** Hoy se otorga por ausencia de
   configuración, no por decisión. Si la respuesta es no, el respaldo tiene que dejar
   de conceder y pasar a abstenerse — y eso mueve el teórico de 119 órdenes.
2. **¿Qué tabla manda?** Mientras las dos den números distintos, «cuánto se le debe a
   este cliente en descuentos» no tiene una sola respuesta.
3. **Las 119 órdenes con descuento calculado y no cobrado**: marcarlas como no
   otorgadas es un `INSERT` en una tabla que existe y está vacía.

## Vigencias de listas sembradas: el instrumento decía «ninguno» sin mirar

El plan pedía «verificar que los períodos que se sembraron coinciden con la realidad
del negocio». La realidad del negocio la sabés vos; lo que se puede verificar sin vos
es la **consistencia**, y para eso ya existía `huecos_de_cobertura` — la función que
se escribió en septiembre cuando pediste «revisá que no queden períodos vacíos, por
ejemplo en USD no hay nada entre el 1 y el 6 de abril».

La corrí, y devolvió **«ninguno»**. Pero mirá por qué:

```python
desde = str(info.get("desde") or "")
if not desde:
    continue          # <-- saltea toda lista sin vigencia declarada
```

**Las 16 listas del mapeo de esta base no tienen ni una vigencia declarada.** Así que
la función saltea las 16, no le quedan tramos entre los que buscar un hueco, y
devuelve una lista vacía. Y una lista vacía se lee como «la cobertura está bien»,
cuando lo que pasó es que **no se pudo buscar nada**.

Es la misma trampa que tenían las dos partidas de tasa del balance, en otro lugar y
encontrada de la misma forma: corriendo el instrumento y preguntando qué había detrás
del verde.

### El arreglo, que no mueve ningún monto

`engine/listas.py::diagnostico_de_huecos` agrega el **denominador que faltaba**, y el
endpoint lo expone junto a los huecos:

```json
"huecos_cobertura": [],
"cobertura_vigencias": {
  "evaluable": false,
  "listas_totales": 16,
  "listas_con_vigencia": 0,
  "nota": "NO SE PUDO EVALUAR: ninguna de las 16 listas del mapeo tiene vigencia
           declarada (campo «desde»)... Cero huecos acá no significa que la
           cobertura esté bien."
}
```

Cinco tests lo fijan, incluido el que verifica que el denominador use **el mismo
criterio** que la función para contar una lista como evaluable: si contara listas que
la función nunca mira, diría que evaluó más de lo que evaluó, que es exactamente el
error que esto arregla.

**Qué queda para vos:** sembrar las vigencias. Sin ellas el instrumento no puede
opinar, y el ítem del plan —confirmar que los períodos coinciden con el negocio— no
tiene sobre qué. Si en producción sí están sembradas, correr el endpoint ahí y mirar
el `nota`: ahora dice cuántas evaluó.

## `SerieTasas` sin caché: corrijo mi propio conteo, y había un N+1

**Lo que había reportado estaba mal.** Cerré este ítem diciendo «3 sitios, 2
deliberados». Son **22** —el plan decía 24 y tenía razón, contando la definición y
un alias— y es mi segundo error de conteo en este trabajo, del mismo género que el
del 1,2 %: reporté una cifra sin contarla como corresponde.

Contados y clasificados con el criterio que el plan mismo da («las que pasan las
filas por parámetro están bien; las otras deberían ir por `tasas_vigentes()`»):

| | sitios | |
|---|---:|---|
| leen una vez y **pasan las filas** hacia abajo | **18** | el patrón que el plan aprueba |
| son el propio punto de entrada o el camino de escritura | **3** | `get_rate_for_datetime`, `post_sync_odoo_rates`, `get_config_tasas` |
| **armaban su propio `Tasas` dentro de un bucle** | **1** | `resolver_tasa_bcv_vinculacion` |

Así que 21 de 22 estaban bien, y el conteo crudo de 24 exageraba el problema tanto
como mi 3 lo minimizaba.

### El que faltaba, y por qué importa más que el rendimiento

`resolver_tasa_bcv_vinculacion` leía la serie completa para resolver la tasa
BCV-Euro, y su comentario decía «es una operación puntual, así que pagar la lectura
sale barato». Para cuatro de sus seis llamadores es cierto. **Los otros dos lo
llaman dentro de un bucle sobre pagos** (`_sincronizar_aplicaciones_conciliadas` y
`_vincular_masivo_sync`).

Medido: 85 órdenes caen en la ventana histórica y **206 vinculaciones** apuntan a
ellas — la única rama que llega a esa lectura. En producción son 919 filas de serie
por cada una, por ciclo. Es la misma forma de N+1 que en su momento dejó la página
de Auditoría en 18 minutos.

Pero el argumento que decidió el arreglo no fue el rendimiento. **Esos dos
llamadores ya tienen la serie leída antes del bucle** y se la pasan a
`get_rate_for_datetime` para la tasa del día:

```python
tasa_bcv_dia, tasa_binance = get_rate_for_datetime(hora_pago, tasas_rows)   # snapshot
tasa_bcv, variante = resolver_tasa_bcv_vinculacion(repo, so_id, hora_pago, tasa_bcv_dia)
#                    ^ leía la serie DE NUEVO, fresca
```

O sea que la tasa USD salía de un snapshot y la EUR de una lectura fresca, **y las
dos se congelan juntas en la misma vinculación**. Pasar la misma serie no es solo
más rápido: hace que el par sea consistente, que es más correcto y no menos.

`serie_rows` es opcional y por defecto lee fresco, así que los cuatro llamadores
puntuales no cambian: ahí la lectura fresca **es** lo correcto, porque se está
fijando una tasa que queda congelada y el caché de cinco minutos podría ocultar una
recién cargada. Ese razonamiento del comentario original era bueno y se preserva.

Cinco tests lo fijan, incluido el que distingue `[]` de `None` — «no hay tasas»
contra «leelas vos», que es la misma distinción que este blindaje persigue en todo
lo demás.

## Equivalentes congelados: ya hay algo que los lista

El plan describía este ítem con una frase que era el problema entero:

> Es correcto como diseño contable, pero **hoy no hay nada que liste cuáles
> quedaron con una tasa que después se corrigió**.

Ahora hay: `scripts/auditar_equivalentes_congelados.py`. No corrige nada
—congelar el equivalente es la decisión contable correcta, y descongelarlo es
tuya—; convierte «no hay nada que lo liste» en «está listado, con su monto».

Separa tres cosas que no son lo mismo, y esa separación es el aporte:

| | qué es |
|---|---|
| **congeladas con el default de 2019** | nunca hubo tasa. El número quedó escrito y por diseño no se revisa. |
| **congeladas con una tasa que hoy es otra** | la corrección posterior propiamente dicha: había tasa, se congeló, después cambió. |
| **sin tasa para comparar** | no se puede afirmar nada. Se cuentan aparte y **no** entran a «coinciden». |

Medido en la copia de prueba: **las 1.487 vinculaciones** están en la primera
categoría, por **2.687.701,19 USD** de equivalente acreditado, de los cuales 569
son abonos en bolívares por 90.774.985,31 Bs.

La segunda categoría dio **cero, y el script lo dice como corresponde**: «NO SE
COMPARÓ NINGUNA. Sin tasa conocida para ninguna fecha, este cero no significa
"todas bien" — significa "no se pudo mirar"». La copia de prueba tiene una sola
fecha con tasa (las 13 filas de la serie son todas de hoy, escritas por el
scraper durante este trabajo). Es la misma trampa que las dos partidas de tasa del
balance tenían, evitada a propósito acá.

**El número real de la segunda categoría sale de producción**, donde la serie sí
existe, y el script corre contra ella sin escribir nada.

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

## Con qué tasa se congelaron: el default de 2019, en todas

Esta sección se escribió antes de que existiera el listado, y decía «no se pudo
medir». La herramienta está [más arriba](#equivalentes-congelados-ya-hay-algo-que-los-lista);
lo que sigue es la otra mitad de la medición, la que **sí** se pudo hacer en QA.

Lo que **no** se puede medir ahí es la *corrección posterior* de una tasa: eso exige
un historial, y `tasas_historicas_auditoria` tiene **0 filas** en esa base mientras
`serie_tasas` tiene solo las del día. Son tablas de trabajo humano y del scraper, no
vienen del sync de Odoo. Esa mitad queda para producción, donde el script corre igual
porque es de solo lectura.

Lo que **sí** se pudo medir es con qué tasa se congelaron, y ahí está el agujero.

### Lo que sí apareció al mirar

Al revisar con qué tasa se congelaron esas vinculaciones, **las 1.463 tienen
`tasa_bcv_aplicada = 36,50` y `tasa_binance_aplicada = 38,00`**. Una sola
combinación, en el 100 % de las filas. Son las tasas de 2019: el default de
`get_rate_for_datetime`, la mina 8 del inventario [1.1](1.1-fallbacks-silenciosos.md).
La tasa real de hoy es **827,74**, o sea 22 veces más.

### Cuánto es en plata

Las cifras de abajo son **solo de clientes reales**: el banco de escenarios agrega
vinculaciones entre corridas, y sin ese filtro el número sube según cuántas veces se
haya corrido. Reproducible con
`scripts/auditar_equivalentes_congelados.py --env .env.qa`.

La suma cruda de `monto_aplicado` no sirve para medirlo: mezcla monedas, que es
justamente lo que el balance audita. Separadas:

| moneda del abono | vinculaciones | nominal | equivalente acreditado | al cambio real |
|---|---:|---:|---:|---:|
| USD | 916 | 199.453,77 USD | 199.453,77 | igual |
| **VES** | **547** | **81.979.553,52 Bs** | **2.246.015,17 USD** | **~99.040 USD** |

Los abonos en bolívares quedaron acreditados como **2,26 millones de dólares**
cuando a la tasa real son unos **99.040**. Veintidós veces y media de más.
(81.979.553,52 ÷ 36,50 da exactamente los 2.246.015,17 acreditados, que es la
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

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
| Formularios de reglas legacy | Baja | **verificado: el unificado cubre los 9**; las 5 brechas aparentes eran renombres | espera tu confirmación, ahora informada |
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

## Formularios de reglas legacy: el unificado los cubre a los nueve

El ítem decía «el formulario unificado ya cubre las siete familias y las 19 reglas de
producción viajan por él sin cambiar. Faltaba tu confirmación para retirar los viejos».
Verificado campo por campo, porque «cubre» es justamente lo que había que comprobar.

**Los dos formularios coexisten hoy en la pantalla.** El unificado existe y funciona
(`POST /api/config/regla`, campos `ru-*`), y los nueve viejos siguen ahí, cada uno con
su `GET` para listar y su `POST`/`PUT` para guardar. Retirar los viejos es quitar la UI
**y** los endpoints, no solo los endpoints.

**Y sí, el unificado los cubre.** Comparados los modelos de request uno contra otro:

| formulario legacy | campos | estado |
|---|---:|---|
| `PromocionRequest` | 14 | cubierto |
| `ProntoPagoRequest` | 15 | cubierto |
| `RecompraRequest` | 16 | cubierto |
| `ProductoPromoRequest` | 16 | cubierto |
| `VolumenRequest` / `DescuentoVolumenRequest` | 16 / 12 | cubierto, con renombre |
| `DiferencialCambiarioRequest` | 15 | cubierto, con renombre |
| `DescuentoMarcaRequest` | 7 | cubierto, con renombre |
| `ReglaDiasCreditoVolumenRequest` | 6 | cubierto, con renombre |

La primera comparación mecánica marcó **cinco campos «faltantes»** y los cinco eran
**renombres**, no huecos. Vale escribir el mapeo para que nadie repita el susto:

| campo legacy | en el unificado |
|---|---|
| `litros_minimo` / `litros_maximo` | `min_unidades` / `max_unidades` + `unidad_medida` |
| `nombre` | `descripcion` |
| `tipo_descuento` | `tipo_regla` (el campo de despacho) |
| `tipo_calculo` | **se deriva** de `tipo_diferencial` |

El último no es un renombre sino una **consolidación deliberada**, y el propio código
dice por qué: «`tipo_diferencial` y un segundo selector era una trampa». Pedir los dos
permitía combinarlos de forma incoherente.

Los otros modelos que la comparación encontró —`AprobarDescuentoSistemaRequest`,
`MarcarDescuentoNoOtorgadoRequest`, `MarcarRecibidoRequest`, `EliminarDescuentoRequest`,
`ToggleDescuentoRequest`— **no son formularios de regla**: son acciones sobre una regla
o un pago ya existentes. No entran en el retiro.

### Retirados el 11-sep-2026

Lo confirmaste y están afuera. Lo que salió, en tres capas y en ese orden:

| capa | qué se fue |
|---|---|
| la pantalla | **9 bloques de formulario, 765 líneas** de `index.html`, cada uno reemplazado por un aviso que manda al formulario único |
| el navegador | **9 manejadores de guardado, 420 líneas** de `app.js` |
| el servidor | **14 endpoints de escritura (535 líneas) y 9 modelos de request (131 líneas)** de `app.py` |

**Lo que NO se fue, y es la mitad del punto:** los ocho `GET` de listado. Las tablas
siguen mostrando las reglas vigentes — 15 tablas en la pantalla. Lo que se retiró es
el editor viejo, no la vista.

Verificado sobre el servidor corriendo: **cero** rutas de escritura de reglas,
`POST /api/config/regla` como único editor, y ninguno de los nueve formularios en el
HTML servido.

Tres cosas que aparecieron al hacerlo:

1. **Eran nueve, no ocho.** Apareció un `descuento-form` de descuento por marca que
   ya **no existía en la pantalla**: su manejador nunca se enganchaba y su `POST` era
   inalcanzable. Código muerto que igual confundía al leer.
2. **El editor unificado sí puede editar**, y eso había que comprobarlo antes de
   quitar los `PUT`. Usa `append_*` para seis de las siete familias, que en la época
   de Sheets agregaba una fila — pero en Postgres es un `_upsert` por `regla_id`. El
   nombre quedó viejo; el comportamiento es el correcto.
3. **La cobertura subió sola de 75,87 % a 76,94 %** al sacar 666 líneas de `app.py`
   que ningún test ejercitaba.

Y un error propio que conviene dejar escrito: el primer script de borrado se comió
`_repo_cache`, una asignación de nivel de módulo que estaba pegada a una de las
clases. La suite lo agarró en el acto —33 tests con `NameError`— pero el borrado
automático de bloques por indentación se lleva lo que tiene al lado si no se mira el
diff. Lo miré después, no antes.

## El 2 % de primera compra: era de Comercial, y se daba a Industrial

El plan planteaba este ítem con dos opciones —«decidir si se marca como no otorgado
o si la regla está mal configurada»— y yo agregué una tercera: «no hay ninguna
regla». **Las tres eran incompletas.** Tu aclaración del 11-sep-2026:

> la regla del 2 % solo aplica a comercial y es solo para la primera compra, si no
> se le dio otra promoción por primera compra. Esa regla estaba configurada como un
> fallback de la regla de primera compra, pero parece que nunca funcionó bien.

### La categoría estaba invertida

El motor aplicaba el 2 % a las líneas **Industrial**. El id del respaldo lo decía
con todas las letras — `FALLBACK_PRIMERA_COMPRA_INDUSTRIAL_2PCT` — mientras el
modelo `PromocionPrimeraCompra` trae `categorias_aplica = "Comercial"` por defecto.
Dos declaraciones contradictorias que nadie había puesto una al lado de la otra.

Medido sobre las 119 órdenes que recibieron el respaldo, con subtotales del espejo:

| base | subtotal | 2 % |
|---|---:|---:|
| líneas **Industrial** (lo que hacía) | 103.055,13 | 2.061,10 |
| líneas **Comercial** (lo correcto) | 37.111,82 | **742,24** |

La base cae un **64 %**. Y la mitad que más importa: **85 de las 119 órdenes no
tienen ni una línea Comercial**, así que no les correspondía nada. Corregirlo
**sube la deuda**, que es la dirección contraria a la que yo había supuesto al
ofrecer las opciones.

### Y hacía falta un campo que no existía

Configurarlo como regla de la tabla no era directo: **la rama de reglas
configuradas sumaba TODAS las líneas**, sin filtrar por categoría, mientras el
respaldo suma solo las Comercial. Con 122 órdenes que tienen líneas de las dos
categorías, crear la regla habría ensanchado la base otra vez.

Intenté reusar `categorias_aplica` y **dos tests me desmintieron**: ese campo
gobierna qué unidades **califican** para el mínimo de compra, no sobre qué líneas
se aplica el descuento. El comentario del modelo («no cambia el cálculo») era
correcto y mi lectura no. Revertido.

Así que ahora hay un campo nuevo, `categorias_descuento`, que dice exactamente
eso. Vacío significa «todas», que es lo que la rama hacía siempre — **ninguna
regla existente cambia de comportamiento al migrar**.

### La regla existe, y no movió un peso

`scripts/configurar_2pct_primera_compra.py` la crea, con un A/B previo orden por
orden sobre las 119: el respaldo da **742,24 USD** y la regla con
`categorias_descuento = COMERCIAL` da **742,24 USD**, con **cero** órdenes que
difieran. Lo que cambió es de dónde sale el número: de una regla que se puede ver
y editar en la pantalla, en vez de un valor cableado que se otorgaba por ausencia
de configuración.

El respaldo sigue en el código a propósito y solo dispara cuando no hay **ninguna**
promoción configurada a la fecha de la orden. Retirarlo es un paso aparte, para
cuando la regla lleve un tiempo viva.

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

## S00573 «refacturar» y S00372 «revisar el caso»: los dos, diagnosticados

El plan lista estos dos como una línea cada uno, sin decir qué tienen. Ahora lo
dicen, y salieron cosas distintas: S00372 **ya está señalado por el sistema**, y
S00573 destapó un defecto que afecta a diecisiete órdenes.

### S00372: el sistema lo dice, y lo dice bien

Consultado en el Odoo de prueba, la orden es esto:

| | |
|---|---|
| estado | `sale`, facturada |
| mercancía | 6 unidades pedidas, **0 entregadas** |
| entregas | salida el 13-may-2026, **devolución el 14-may** |
| factura | 00000049, `posted`, 327.608,74 VES = 644,12 USD |
| pago | 282.415,63 VES, `payment_state='partial'` |
| residual | **45.193,11 VES** |

Las tres cifras cierran al centavo: 327.608,74 − 282.415,63 = 45.193,11, y la
factura tiene `wh_iva_aplicado = True`. **Ese residual es el IVA retenido**, no
una deuda impaga: lo cierra el comprobante de retención y no la cobranza. Así que
cuando el reporte clasifica la orden como «legalmente la factura ya está saldada»
y la saca de CxC activa, **tiene razón** — es la lógica que pediste en la Fase 3
del plan de pagos.

Lo que está mal es otra cosa, y `/api/ventas` ya la nombra:

> `revisar_motivo`: Devolución registrada (total o parcial); **Falta crear Nota de
> Crédito en Odoo por la devolución**

Eso es exactamente el caso. La mercancía volvió completa el 14 de mayo, la factura
sigue emitida por 644,12 USD, y el cliente pagó 555,29 USD por productos que
devolvió. **Falta la nota de crédito que formalice la devolución**, y hasta que
exista, el cobro de 555,29 USD no tiene respaldo.

Y de paso, la misma orden es un ejemplo con nombre del hallazgo de las tasas
congeladas: su vinculación quedó con `tasa_bcv_aplicada = 36,50` y acredita
`equiv_usd_bcv = 7.737,41` sobre una orden de 644,13 USD. **Doce veces.**

### S00573: la orden que nadie está persiguiendo, y las otras dieciséis

S00573 es lo opuesto: 29 unidades **entregadas y no devueltas**, `facturado = 0`,
y su única factura (00000530) está `reversed` — anulada por la nota de crédito
00000014. O sea mercancía afuera, ningún documento que la cobre, **1.860,48 USD**.
Confirma el ítem del plan: hay que refacturar.

Pero al preguntar por qué el reporte de saldos no la muestra, apareció el defecto.
No es que la saltee: la clasifica **«Pagado vs Teórico Lista USD»** con saldo
0,00. Y S00573 no tiene ni una vinculación registrada — nunca entró un bolívar.

#### Una factura anulada tiene residual cero, y la resta lo lee como cobro

`_pagos_odoo_por_orden` tiene dos caminos. El principal pregunta por los
`account.payment` reconciliados, que es el importe real. Cuando no hay ninguno,
cae al fallback:

```python
paid_inv = max(Decimal("0"), tot - res)   # amount_total - amount_residual
```

La resta es correcta para una factura que se está cobrando: lo que ya no se debe,
se cobró. Pero **una factura anulada tiene residual cero por definición**. Lo que
la dejó en cero fue la nota de crédito, no un bolívar que entró.

Y no depende de ninguna tasa. El fallback deduce la tasa dividiendo el total de la
factura por el total de la orden, así que el abono fantasma sale **exactamente
igual al monto de la orden en dólares**, con la tasa que sea. Por eso el saldo da
0,00 y no un número raro.

El caso más limpio no es S00573 sino **S00886**:

| documento | estado | total VES | residual VES |
|---|---|---|---|
| 00000677 (25-ago) | `reversed` | 581.034,93 | 0,00 |
| 00000701 (26-ago) | `not_paid` | 596.091,85 | **596.091,85** |

Cero `account.payment` reconciliados. La resta suma (581.034,93 − 0) + (596.091,85
− 596.091,85) = 581.034,93, que es el total de la orden. **Nadie pagó nada y el
reporte la da por cobrada.**

#### Cuánto es

Medido con `scripts/auditar_facturas_anuladas.py` sobre la copia de producción:
22 facturas `out_invoice` `posted` con `payment_state='reversed'`, ninguna con
`account.payment` reconciliado. Descontadas 3 del banco de pruebas y 1 sin
mercancía entregada, quedan **17 órdenes reales, y las 17 están fuera de la cuenta
por cobrar**.

Acá importa no inflar el número. De las 17:

| | órdenes | plata |
|---|---:|---:|
| **Sin ninguna factura viva** — hay que refacturar | 1 | **1.860,48 USD** |
| **Refacturada, con residual real sin cobrar** | 7 | **2.628,64 USD** |
| Refacturada y ya cobrada — el abono cuenta dos veces | 9 | — |
| | **17** | **4.489,12 USD** |

Dieciséis de las diecisiete **fueron refacturadas bien**: tienen una segunda
factura viva. Publicar los 11.667,17 USD que suman sus órdenes sería mentir. Lo
que el defecto esconde son los 2.628,64 USD de residual que esas facturas nuevas
todavía tienen sin cobrar, más los 1.860,48 de S00573. Ninguno de los siete
residuales se explica por retención de IVA — se verificó contra el IVA estimado de
cada factura, igual que en S00372.

El más grande es **S00479**: 1.308,12 USD debiéndose sobre la factura 00000526 en
`partial`, y la orden fuera de CxC.

#### Lo que queda hecho, y lo que no

Hecho: `src/cxc/engine/reversadas.py` expone las **dos lecturas** del mismo par
`(amount_total, amount_residual)` y un diagnóstico por orden que dice si hay
factura viva, cuánto es el abono fantasma y cuánto el residual que sí se debe. 25
tests, con los tres casos reales (S00886, S00573 y una refacturada ya cobrada)
fijados con sus números exactos. Y el script que lo mide sobre cualquier entorno.

**No hecho, y a propósito:** aplicar la distinción. Devolver 17 órdenes a la
cuenta por cobrar mueve montos, y la Fase 1 dice que eso pasa por tu visto bueno.
Cuando lo apruebes, el cambio es de una línea — que el fallback aporte cero
cuando `payment_state` es `reversed` — y los tests ya describen qué tiene que
pasar.

Una nota sobre cómo encontré esto, porque cambia qué se puede afirmar. Lo vi
primero como notas de crédito valuadas en 37.335,66 USD sobre una orden de
1.860,48 — veinte veces. Eso resultó ser un artefacto de esta copia: **la QA no
tiene ninguna tasa cargada**, así que toda conversión cae al default de 2019, y
1.362.751,66 / 36,50 = 37.335,6619 exacto. La magnitud era del entorno; el defecto
del fallback, no. Por eso el número que se publica arriba es el residual medido en
Odoo y no una conversión nuestra.

### Y una tabla que no se puede consultar por orden

De paso: **las 47 notas de crédito del espejo tienen `so_id` en NULL**, porque en
Odoo esas notas traen `invoice_origin = False` y se vinculan solo por
`factura_origen_id`. Una consulta directa por `so_id` a `facturas` no encuentra
ninguna NC. El código llega igual, encadenando por la factura de origen, así que
no es un bug vivo — pero cualquier consulta nueva que filtre NCs por orden va a
devolver cero sin avisar. Y el espejo ya guarda el valor correcto al lado
(`monto_total_signed_usd = −1.860,43` para la NC de S00573, calculado por Odoo a
la tasa real), que es lo que conviene usar en vez de reconvertir a mano.

## Mina 9: el precio de la regla vencida ahora deja rastro

La mitad de este ítem que **no mueve montos**, aplicada el 11-sep-2026.

`_precio_fijo_en_lista` busca la regla de precio que cubre la fecha pedida y, si
ninguna la cubre, **devuelve la primera igual**. Nunca dice «no hay precio»
mientras exista alguna regla, aunque todas hayan vencido hace meses — y la única
señal que dice «este teórico no es confiable» se enciende *solo* cuando devuelve
«no hay precio».

Ahora cada uno de esos precios queda registrado con el producto, la lista, la
fecha pedida y **la vigencia de la regla que se usó**. Esa última parte es
deliberada: una regla que venció ayer y una que venció en abril son decisiones
distintas, y un contador no las distingue.

**El valor devuelto no cambió.** Devolver `None` dejaría la pantalla sin precio en
vez de con un precio viejo, y eso mueve montos — sigue siendo tu decisión.

13 tests, los primeros del módulo (estaba marcado `pragma: no cover`). Dos merecen
mención porque acotan el hallazgo en vez de inflarlo: **una regla que todavía no
empezó también cuenta** —es el mismo problema con el signo invertido—, y **un
precio pedido sin fecha no cuenta**, porque ahí el filtro de vigencia no corre y la
regla que hay es la regla que corresponde.

### Y el instrumento dice que no midió nada

Corrido contra la copia de prueba: **cero** precios de regla vencida. Ese cero **no
significa que no haya**: instrumenté la función para contar sus llamadas y el
reporte de saldos la consulta **cero veces**. El resolver rápido —el que lee
precios del espejo local— responde todo, así que el camino donde vive la mina no se
ejercita en esa corrida.

Lo digo con todas las letras porque es la misma trampa que este plan viene
corrigiendo en otros cuatro lugares: un cero de un instrumento que no corrió se lee
igual que un cero de un instrumento que verificó. El rastro va a hablar en
producción, donde la configuración de listas existe y el resolver de Odoo sí se
alcanza.

## «Pagada en Odoo» tiene dos definiciones, y difieren en 66 órdenes

Apareció al extraer la duodécima pieza de la Fase 2.4. `so_pagada_en_odoo` —la
variable que decide si una orden sale de la cuenta por cobrar— se calculaba en
**tres sitios** de `app.py`, y uno usaba **otra regla**:

| dónde | regla |
|---|---|
| reporte de saldos | `all(payment_state in ("paid", "in_payment"))` |
| auditoría | igual, y su comentario dice «misma regla que /api/reporte-saldos» |
| **sugerencias de conciliación** | **`sum(amount_residual_usd) <= 0,05`** |

El nombre compartido lo escondía. Es la misma forma del hallazgo de las listas de
precio: el mismo concepto calculado de dos maneras en pantallas distintas.

**Medido: 66 de 796 órdenes con factura discrepan**, todas en la misma dirección —
la regla del residual las da por pagadas y la del estado no. Y las tres causas
apuntan a **lados distintos** sobre cuál regla es la correcta:

| causa | órdenes | quién tiene razón |
|---|---:|---|
| alguna factura **anulada** | 15 | **la de estado.** El residual es cero por la nota de crédito, no por un cobro |
| `partial` con residual de **centavos** (0,01–0,03) | 47 | **la del residual.** Un centavo no es deuda, y la de estado deja la orden en CxC para siempre |
| residual **negativo** | 4 | **ninguna** |

### Los 4 del residual negativo: 258,74 USD de sobrepago que nadie reporta

S00188, S00795, S00182 y S00061. Odoo dice `payment_state = 'partial'` y el
residual es **negativo**: −116,69, −56,63, −46,44 y −38,98 USD. Se cobró más de lo
facturado.

| orden | total factura (VES) | residual (VES) | residual (USD) |
|---|---:|---:|---:|
| S00188 | 68.102,81 | −55.537,27 | **−116,69** |
| S00795 | 517.467,79 | −42.897,85 | **−56,63** |
| S00182 | 57.594,46 | −23.072,92 | **−46,44** |
| S00061 | 6.223.421,82 | −28.728,47 | **−38,98** |

Una regla lo llama pagado y la otra impago, y **las dos se pierden la plata a
devolver o acreditar**. Son 258,74 USD que el cliente tiene a favor y que ninguna
pantalla dice.

### Qué queda hecho, y qué no

`engine/pagada_en_odoo.py` expone las **dos lecturas** y un diagnóstico que nombra
la causa de cada divergencia. Los tres sitios ahora llaman a la función que les
corresponde —los dos de estado comparten una, el de residual usa la otra con su
tolerancia nombrada— así que **no pueden separarse más sin que alguien lo note**, y
el reporte de saldos registra cada divergencia con su causa.

24 tests. Dos merecen mención: **el sobrepago se detecta aunque las dos reglas
coincidan** (una factura `paid` con residual negativo las tiene de acuerdo, y sigue
habiendo plata de más), y **una divergencia que no es ninguna de las tres se marca
para mirar a mano** en vez de forzarla dentro de una causa conocida.

**Qué hay que decidir:** unificar mueve el universo de órdenes de tres pantallas, y
la medición dice que **ninguna de las dos es correcta en los tres casos**. La
respuesta buena parece ser: por estado, **más** una tolerancia de centavos, **más**
una señal de sobrepago. Las tres partes mueven montos.

## El obsequio que cuenta como venta, y los 8.611,64 que vienen pegados

Salió de una aclaración tuya del 11-sep-2026, textual:

> la única excepción es cuando aplica la promoción de primera compra en la que se
> obsequia producto, lo que se hacía era incluir el producto para que se descontara
> del inventario, pero se le ponía al producto precio 0 o dcto del 99% **para que
> no afectara la cxc**. También a veces se le pone el precio en 0 y cantidad
> entregada 0 si hubo una devolución.

Los dos patrones están en los datos, y **el sistema respeta uno y no el otro**.

### El patrón del obsequio no se respeta

`monto_entregado_neto_usd` —el umbral que decide si una orden genera cuenta por
cobrar, y la cifra que el reporte publica como `subtotal`— hace
`cantidad_entregada × precio_unitario` y **no mira el descuento de línea**. Así que
un obsequio cargado como decís cuenta su precio completo:

| orden | producto | entregada | precio | dcto | `subtotal` del espejo | lo que el reporte suma |
|---|---|---:|---:|---:|---:|---:|
| S00671 | 1033 | 1 | 35,81 | 99,99 % | **0,00** | **35,81** |
| S00674 | 1033 | 1 | 35,81 | 99,99 % | 0,00 | 35,81 |
| S00679 | 1033 | 1 | 35,81 | 99,99 % | 0,00 | 35,81 |
| S00336 | 902 | 0 | 74,27 | 100 % | 0,00 | 0,00 |

El espejo **ya trae el valor correcto** en su columna `subtotal` —lo calcula Odoo
con el descuento— y el reporte lo ignora y lo recalcula mal. Son **107,42 USD** de
obsequio contados como venta.

### El patrón de la devolución sí se respeta

Tres líneas con precio 0: S00569 con `entregada = 0`, y **S00925 y S00952 con
entregada NEGATIVA** (−10 y −4), que es una devolución que supera la línea. Ninguna
línea con precio 0 fue entregada, así que **ninguna está generando cuenta por
cobrar**. Ese patrón funciona.

### Y esto cambia la decisión del precio en cero

La tenía planteada como «hacer que un precio 0 sea un error». Con tu regla eso
**rompería los dos flujos**, porque ahí el cero es correcto y deliberado. La
decisión real es más fina: distinguir «cero porque se regaló» de «cero porque no
se pudo resolver el precio». El descuento ≥99 % es la marca que las separa, y
ahora `es_obsequio` la nombra.

### Por qué NO lo apliqué, aunque lo autorizaste

Aplicar el descuento arregla el obsequio. Pero medido sobre la copia:

| | órdenes | monto |
|---|---:|---:|
| cambian su `subtotal` | **204** | **−8.719,06 USD** (−1,10 %) |
| de las cuales, obsequio | 4 | −107,42 |
| **descuentos de línea normales** | ~200 | **−8.611,64** |
| cruzan el umbral y saldrían del reporte | **0** | — |

Los 107,42 los autorizaste. **Los 8.611,64 no**, y meterlos en el mismo cambio
sería colar una corrección de 204 órdenes bajo el permiso que diste para tres.

Así que la bandera `aplicar_descuento` existe, viene en `False`, y
`diagnostico_de_obsequios` **separa las dos brechas** para que el número autorizado
no se confunda con el que no lo es. 8 tests nuevos.

**Qué hay que decidir:** si el descuento de línea tiene que entrar en el valor
entregado. Si la respuesta es sí para todos, son 8.719,06 USD menos de venta
reportada en 204 órdenes. Si es solo para los obsequios, alcanza con filtrar por
`es_obsequio` y son 107,42.

## Los pagos sobreaplicados: el diagnóstico, y los dos repartos medidos

La decisión 1, con la propuesta que pediste. El hallazgo original decía «10 pagos,
1.333,85 USD»; medido de nuevo contra el campo correcto son **1.269,25 USD**, y la
diferencia es que aquella cifra pasaba por la conversión a equivalente de la app y
ésta compara el campo crudo. Es el mismo hallazgo.

### Por qué pasa

Las aplicaciones se leen de los `account.partial.reconcile` de Odoo, tomando
`credit_amount_currency`. El pago 200 lo muestra entero: **vale 134,00 USD y tiene
nueve parciales que suman 715,04**, todos contra facturas de cliente reales. Las
tasas implícitas van de **4,1 a 709,7** dentro del mismo pago, cuando la oficial de
esa fecha es 483,9.

El docstring del lector dice que los asientos de diferencial cambiario «quedan
fuera por construcción» porque no tienen un `account.payment` detrás. **No los
excluye**: los nueve están contra facturas reales. Los parciales posteriores al
primero son revaluación, y entran igual.

Y el dato que ordena todo: **en 7 de los 10 pagos el primer parcial ya ES el pago
completo.**

### La propuesta, en tres capas

| capa | qué | decide |
|---|---|---|
| 1 · la cota | la suma no puede superar el pago | nadie: es aritmética |
| 2 · el reparto | por orden de parcial, o proporcional | **vos** |
| 3 · la invariante | que no vuelva a crecer | **hecha** el 11-sep-2026 |

La capa 3 ya está: ver [2.2 — Invariantes](2.2-invariantes.md). No corrige los diez
existentes ni los bloquea; sólo impide que el exceso crezca.

### Los dos repartos, medidos sobre los diez pagos

Los dos entregan **exactamente 2.483,00 USD**, que es lo que valen los diez pagos
juntos. La diferencia es **quién** lo recibe:

| pago | vale | aplicado hoy | por orden | proporcional |
|---|---:|---:|---|---|
| 200 | 134,00 | 715,04 | todo a S00279 | las 4 reciben fracción |
| 86 | 220,00 | 367,22 | S00158 210,99 · S00795 **9,01** | S00158 126,40 · S00795 **93,60** |
| 40 | 130,00 | 208,46 | S00607 **37,49** | S00607 **72,31** |
| 230 | 300,00 | 364,60 | S00061 **0,00** | S00061 53,15 |
| 181 | 250,00 | 254,90 | S00274 **0,00** | S00274 4,81 |

**Siete órdenes pierden todo el crédito** con «por orden»: S00061, S00220, S00274,
S00412, S00595, S00617 y S00638. Con «proporcional» todas reciben menos y ninguna
pierde todo.

### Lo que recomiendo, y lo que no puedo decidir

**Por orden**, por una razón que los datos sostienen: que el primer parcial
coincida exactamente con el pago en 7 de 10 no es casualidad, es la conciliación
real que hizo Odoo; los parciales posteriores son revaluación cambiaria, que es un
resultado contable y no cobranza.

Pero en los otros 3 —los pagos 86, 40 y 845— el primer parcial es **menor** que el
pago, así que ahí el reparto es genuino y la elección pesa más: S00795 pasa de
93,60 a 9,01 según qué se elija.

`engine/acotar_pago.py` calcula los dos y `diagnostico_de_reparto` los compara, con
el caso del pago 200 fijado en un test. 15 tests. **No aplica ninguno.**

## Dos decisiones más, ejecutadas el 11-sep-2026

### «Manda la definición que rige el precio, que es la de la lista histórica»

Las dos definiciones de «orden histórica» quedaron unificadas, y la que gana es la
del precio (`es_orden_historica`). Medido con la definición nueva sobre las 1.111
órdenes: **15 cambian de referencia**, y se parten en dos grupos.

**Cuatro sin lista asignada**, que pasan a históricas sin importar la fecha:

| orden | fecha | estado | vinculaciones | |
|---|---|---|---:|---|
| S00088 | 13-mar | `sale` | 1 | **viva** |
| S00090 | 13-mar | `sale` | 2 | **viva** |
| S00091 | 13-mar | `cancel` | 0 | la del campo de entrega contradictorio |
| S00162 | 27-mar | `cancel` | 0 | la de 161.679,06 que figuraba `sale` en el espejo |

Las dos vivas son las que había publicado, y son los **457,51 USD**: su precio
salía por la histórica, referenciada al euro, y su pago por la BCV-USD.

**Once de la ventana con una lista USD real** (la #7, «Pago USD Marzo»): S00007,
S00022, S00023, S00026, S00027, S00033, S00049, S00052, S00059, S00061 y S00069. El
precio ya las trataba como NO históricas —es la excepción documentada del caso SJMG
2012— y el pago las seguía pagando en euro.

Yo había llamado a esas 11 «una excepción deliberada que funciona», y era verdad a
medias: funcionaba **del lado del precio**. Del lado del pago seguían con la
referencia vieja.

Y una precisión sobre el conteo: al aplicar esto escribí «difieren en 13», sumando
mal las 2 vivas con las 11. Son **15** — la medición vieja decía «4 difieren, de las
cuales 2 siguen vivas» y esa estaba bien. El error fue mío al resumirla.

**No mueve ningún equivalente ya congelado.** `resolver_tasa_bcv_vinculacion` se
llama sólo al *crear* una vinculación; las que ya están escritas conservan su tasa
por diseño contable. Lo que cambia es de qué referencia salen las nuevas.

Tres tests, y uno de los viejos falló por la razón correcta: su orden de mentira no
tenía lista, y con la definición unificada eso la vuelve histórica sin importar la
fecha. Quedó separado en dos casos, uno por cada mitad de la unificación.

### «Mantené la tolerancia al mínimo»

Elegido: las tolerancias del balance **no** se hacen proporcionales al volumen. Eso
cierra el riesgo de que una tolerancia generosa tape un descuadre real.

Pero deja el otro, que es el que había medido: a diez veces los datos el residuo de
«Saldo a favor» pasa de 1,53 a 15,55 y la partida se pone **roja con la tolerancia
en 5,0, sin que nada esté mal**. Un instrumento que grita lobo a medida que el
negocio crece deja de mirarse.

La salida que no afloja nada: cada partida ahora expone **cuánta de su tolerancia
está usando**, y se marca `al_limite` al llegar al 60 %. No cambia ningún veredicto
—una partida al 70 % sigue cuadrando— pero el aviso llega mientras todavía se puede
decidir, en vez del día que el balance amanece rojo.

El umbral es 0,6 y no 0,9 a propósito: el residuo escala con el volumen, así que a
diez veces los datos una partida que hoy esté al 60 % ya cruzó.

Cinco tests, y el de la forma de las partidas —que existe para que la pantalla no
rompa— sirvió para lo contrario: confirmó que las tres claves nuevas llegan a las
**24** y no sólo a las internas.

## Pagos duplicados: 19 grupos, y 13 son de mi propio banco

Salió del barrido que busca funciones de `app.py` que hablen de dinero y que
ninguna prueba nombre. `_detectar_pagos_duplicados` era una: detecta dos pagos con
cliente, monto, moneda, método y fecha idénticos — el mismo pago cargado dos veces,
o un banco que reporta la misma transacción dos veces. Un duplicado infla la
cobranza y saca de la cuenta por cobrar una orden que no se cobró.

**Medido: 19 grupos.** Y lo que importa para poder usarlo: **13 son del banco de
escenarios** (clientes `ZZ BLINDAJE`, todos del 05-sep con montos idénticos) y
**6 son de clientes reales**:

| cliente | monto | fecha |
|---|---:|---|
| En ascenso 2011,c.a. | 40.187,53 VES | 19-ago |
| En ascenso 2011,c.a. | 72.287,74 VES | 31-jul |
| INVERSIONES MI LINDA YEMAIRE 2019 | 56.976,38 VES | 30-abr |
| AUTOPERIQUITOS LA CAMPIÑA C.A. | 14.757,63 VES | 23-jul |
| Pedro Castro | 100,00 USD | 22-jul |
| Angel ARMAS | 75,00 USD | 05-jun |

«En ascenso 2011,c.a.» aparece dos veces, y es el mismo cliente de S00372.

**El detector no puede distinguir** los del banco de los reales, así que muestra 19
y alguien filtra a mano. **No se filtra por nombre de cliente a propósito**: una
función que descarta pagos así esconderá un duplicado real el día que alguien llame
a un cliente parecido. La separación es del llamador, que sabe si mira un entorno de
prueba.

### Lo que la clave de cinco campos protege

Cada campo está por una razón, y 16 tests las fijan:

* **cliente** — dos clientes pueden pagar lo mismo el mismo día.
* **monto**, a dos decimales — el mismo pago llega con un tercer decimal distinto
  según por dónde entró. Pero **un centavo sí separa**: es redondeo, no tolerancia.
* **moneda**, y **sin convertir** — un duplicado es el mismo documento, no dos
  montos equivalentes. Convertir haría que 100 USD y 3.650 VES parezcan el mismo.
* **método** — el mismo monto por transferencia y en efectivo es plausible.
* **fecha, truncada al día** — el mismo pago cargado dos veces suele tener horas
  distintas.

Y un borde que importa: un pago **sin `pago_id` se saltea**. Agruparlos entre sí
inventaría duplicados, porque dos filas sin id compartirían clave y se acusarían
mutuamente.

Verificado además que **cero pagos** de los 1.320 vienen sin `cliente_id` ni sin
`metodo_pago`, así que el riesgo de que los campos vacíos agrupen pagos ajenos existe
en el código pero no tiene instancia viva.

## Un docstring que prometía lo contrario de lo que el código hacía

El mismo barrido encontró `pago_monto_usd`, nueve líneas usadas en cinco lugares. Su
docstring decía:

> Nunca trata un monto en VES como si ya fuera USD.

Y el código hacía exactamente eso cuando la tasa llegaba en cero o negativa: la
guarda era `if moneda == "VES" and bcv_rate > 0`, así que una tasa inválida caía al
`return monto_raw` final y devolvía los bolívares como si fueran dólares.

**Cuánto sería.** Hay **513 pagos en VES por 120.848.004,12 Bs**. Contados así serían
**120,8 millones de dólares** en vez de unos 164.980 a la tasa real — **732 veces**.

**Y no está viva.** Los cinco llamadores toman la tasa de `get_rate_for_datetime`,
que desde la decisión de esta misma fecha **levanta** en vez de devolver el default
de 2019. Antes tampoco era alcanzable, porque devolvía 36,50 y nunca cero. La suite
completa pasó sin un solo cambio al hacerla levantar, que es la confirmación de que
ningún camino la tocaba.

**Por qué la cambié igual.** Una garantía escrita que la función no cumple es peor
que no escribirla: el próximo que lea el docstring le va a pasar una tasa sin
comprobarla. Ahora levanta, que es la misma decisión que se tomó para la tasa — no
inventar un número donde no hay dato.

7 tests. Uno fija la mitad que **no** cambió y que es la próxima trampa: sólo `VES`
dispara la conversión, así que si alguien agrega una tercera moneda sin tocar esta
función, sus montos entran como dólares sin convertirse. El mismo error con otro
nombre.

## Los promedios de Binance: dos ventanas que se solapan, y muerde hoy

`_get_tasas_promedios_sync` calcula tres promedios de la tasa Binance —mañana,
tarde y diario— y el diferencial contra la BCV. Alimenta la pantalla de tasas y las
columnas `tasa_binance_*` de la serie. Ninguna prueba la nombraba.

### Las ventanas se solapan

| ventana | horas |
|---|---|
| mañana preferida | 6 a 9 |
| tarde preferida | 10 a 13 |
| mañana de respaldo | hora < 12 **← incluye 10 y 11** |
| tarde de respaldo | hora ≥ 12 |

Cuando no hay captura entre las 6 y las 9, la mañana cae a su respaldo, que llega
hasta las 11 — y esas horas ya están en la tarde preferida. **La misma captura entra
como promedio de mañana y de tarde.**

### Y hoy pasó

De los dos días con capturas en la serie:

| día | con solapamiento | con ventanas disjuntas |
|---|---|---|
| 10-sep (27 capturas) | mañana 959,66 · tarde 962,46 | idéntico |
| **11-sep (3 capturas, todas a las 10)** | mañana **954,75** · tarde **954,75** | mañana **`None`** · tarde 954,75 |

Hoy **no hubo captura de mañana**, y la pantalla muestra un promedio de mañana igual
al de tarde. El dato verdadero es `None`: no se midió.

### Dos cosas más que la función hacía sin decirlo

**Sin capturas de hoy, promedia las últimas 24 filas de cualquier fecha.**
`rates_today if rates_today else rows[-24:]` — el «promedio de hoy» puede ser el de
la semana pasada, y nada lo dice.

**El diferencial se calcula sobre Binance, no sobre la BCV**, y el nombre del campo
(`diferencial_bcv_binance_pct`) no lo aclara. Con BCV 700 y Binance 800, sobre
Binance es 12,5 % y sobre BCV sería 14,29 %.

### Qué queda hecho

`engine/promedios_tasas.py` calcula las **dos lecturas** y `diagnostico_de_promedios`
las compara, nombrando la hora compartida. 32 tests.

Y **el endpoint ahora los expone en su respuesta**, que es lo que convierte el
hallazgo en algo que se ve en vez de algo que está en un docstring:

```json
"tasa_binance_manana": 954.75,
"tasa_binance_tarde":  954.75,
"ventanas_solapadas":  true,
"horas_compartidas":   [10],
"filas_son_de_hoy":    true,
"capturas": { "manana": 3, "tarde": 3, "diario": 3 }
```

Ésa es la respuesta real de hoy: los dos promedios son el mismo número sobre las
mismas tres capturas, y ahora la pantalla puede decirlo.

Dos merecen mención porque son de la misma familia que el resto del plan: **un sello
de hora ilegible no cuenta como medianoche** (devolver 0 lo metería en el promedio de
la mañana) y **una captura fallida no arrastra el promedio** (guarda cero, y con
1000 y un cero el promedio sería 500 — la mitad de la tasa real).

**No corrige ninguno de los dos hallazgos:** cambiar las ventanas o el respaldo mueve
las cifras que la pantalla muestra y las que la serie guarda. Es decisión tuya, y se
suma a la del «sesgo de la tasa Binance» que el plan ya tenía anotada — resulta que
hay **dos** sesgos, no uno: el del promedio de 5 compras y 5 ventas, y éste,
estructural, en cómo se parten las horas.

## La validación de rango de la tasa Binance está apagada en el 100 % de los casos

`post_editar_tasa_binance` corrige la tasa Binance de una vinculación y valida que
la nueva esté **dentro del rango capturado ese día** en `SerieTasas`. Es una buena
guarda, con una condición silenciosa:

```python
if binance_vals:      # <- si ese día NO hay capturas, no valida NADA
```

**Medido: las 1.494 vinculaciones del espejo están en fechas sin capturas Binance.**
La serie tiene capturas de dos días (10 y 11 de septiembre) y las vinculaciones se
reparten en 144 días distintos. La validación **no corre en ningún caso**, y
cualquier tasa se acepta sin decirlo.

En producción la serie tiene más días, pero el hueco es el mismo que el plan ya
había nombrado: la serie arranca el 25-jul y las facturas el 01-feb. Toda
vinculación anterior al scraper está en la misma situación.

**No se rechaza**, porque eso impediría corregir la tasa de un pago viejo — que es
justo para lo que sirve esa pantalla. Lo que cambió es que deja de ser invisible:
queda un `warning` en el log y la respuesta trae `rango_verificado: false`, así que
la pantalla puede decirlo.

La guarda estaba **duplicada** en los dos endpoints de tasa Binance (el de la
vinculación y el del pago pendiente). Ahora los dos usan
`engine/promedios_tasas.py::rango_binance_del_dia`, donde `acepta()` y `verificado`
son dos preguntas separadas a propósito: *«¿esta tasa entra?»* y *«¿había con qué
comprobarlo?»*. Juntarlas es lo que hacía que la segunda no se pudiera contestar.
6 tests más.

Y un arreglo de paso en el endpoint del pago pendiente: buscaba el pago recorriendo
`all_pagos()` —cargar los 1.320 para encontrar uno— cuando `get_pago` existía. Eso
descubrió que **sí había un test** para ese endpoint (mi barrido lo había marcado
como sin pruebas porque busca el nombre de la función, y el test lo ejercita sin
nombrarla) y que su repo de mentira no implementaba `get_pago`: sobre un `MagicMock`
devolvía un objeto falso, así que el 404 nunca disparaba. El repo real devuelve
`None`, y ahora el mock también.

## Editar la variante de tasa reescribía un equivalente congelado con otra precisión

`post_cambiar_tipo_tasa_bcv` cambia la variante USD/EUR de una vinculación y
**recongela su equivalente**. Es de las escrituras más delicadas del sistema —el
equivalente congelado por diseño no se vuelve a revisar— y ninguna prueba la
nombraba. Salió del mismo barrido.

Dos cosas aparecieron:

**1 · La cuenta estaba duplicada, y las dos copias no coincidían.** El endpoint
reimplementaba inline lo que `calcular_equivalentes` ya hace, **sin pasar por
`q6`**. Con 1.000 Bs a tasa 3, el motor guarda `333.333333` y el endpoint guardaba
`333.3333333333…`. El mismo concepto calculado de dos maneras, y una de ellas
reescribiendo un valor congelado con otra precisión que la que tenía al nacer.

Ahora las dos rutas comparten `equivalentes_bcv`. Y el mismo defecto estaba del
**lado Binance**: `post_editar_tasa_binance` reimplementaba su mitad también sin
`q6`. Los dos lados quedaron como `equivalentes_bcv` y `equivalentes_binance`, y
`calcular_equivalentes` los usa a los dos — así que las tres rutas comparten la
cuenta y no pueden volver a divergir. 7 tests.

Son dos funciones y no una con los cuatro valores a propósito: cada endpoint edita
**un** lado, y una función que devolviera los cuatro obligaría a cada uno a
descartar dos. Descartar invita a pisar el lado que no venía a tocar.

**2 · Una regresión que yo mismo introduje hoy, y que encontré al mirar esto.** En
la rama USD el endpoint llama a `get_rate_for_datetime`, que desde la decisión de
esta misma fecha **levanta** en vez de devolver el default de 2019. Su
`except Exception` convertía eso en un **500 con el mensaje crudo**, cuando el caso
es exactamente el que su propio 400 de más abajo contempla: no hay tasa con la que
recongelar.

Alguien que eligiera «USD» sobre un pago de una fecha sin tasa habría visto un error
de servidor en vez de una explicación. Ahora devuelve 400 con la fecha adentro.

Vale anotar la forma del error, porque va a repetirse: **convertir un valor de
retorno en una excepción cambia el comportamiento de todos los `except Exception`
que haya en el camino.** La suite no lo agarró porque ninguna prueba llegaba a este
endpoint — que es precisamente por lo que estaba en la lista del barrido.

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

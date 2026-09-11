# Fase 3 — Escenarios de error humano

El corazón del plan. Las 31 filas de la tabla, cada una como una prueba
reejecutable contra el Odoo de prueba: no un informe de una corrida manual sino
un banco que se vuelve a correr cada vez que se toque el motor o el sync.

Cómo se corre, qué barreras tiene y qué se aprendió del Odoo está en
[`escenarios/README.md`](../../escenarios/README.md). Este documento es lo que el
banco **encontró**.

## Antes que nada: el entorno casi emite documentos fiscales

El hallazgo que cambió la forma de todo lo demás. El Odoo de prueba **comparte la
imprenta digital con producción**: el conector `account.digital.invoicing.conn`
apunta a `thefactoryhka.com.ve` y había emitido documentos aprobados el
**09-sep-2026** —el día anterior— con números de control `00-00001573` a
`00-00001576` y URL pública de consulta, firmados por usuarios reales.

Emitir un documento fiscal es irreversible y consume la secuencia de la empresa.
Y no es solo la factura: **la nota de entrega también se emite**, así que validar
un picking de salida lo dispara igual. Lo que decide si se emite es
`is_digital_invoicing` de la **ubicación de destino** — no el campo homónimo del
picking, que es calculado, de solo lectura, y sigue leyendo `True` aunque no se
emita nada.

El banco quedó con **cuatro** barreras independientes: diario sin conector,
diario forzado a impresión libre, ubicación con la imprenta apagada, y un canario
que cuenta los números de control antes y después de cada escenario. **Ninguna
corrida emitió nada**, verificado con el canario en cero en cada una.

La segunda barrera apareció tarde y merece nombrarse: **un diario de venta nuevo
no nace neutro**. El default de `billing_type` en esta base es `fiscal_printer`
—una vía fiscal distinta del conector digital— y yo estaba verificando solo el
conector. El canario nunca se movió, así que no se emitió nada; lo que se cerró
es la posibilidad.

---

# Lo que el banco encontró

## El riesgo fiscal dejó de ser hipotético: cinco emisiones reales, el mismo día

Esta sección se escribió con el riesgo en condicional —«si un escenario logra
emitir»—. **Ese condicional se cerró el 10 de septiembre, y no por un escenario.**

Entre las **13:19 y las 14:22 UTC** se validaron cinco entregas salientes con
destino `Socios/Clientes` —la ubicación real, con `is_digital_invoicing = True`— y
**cada una emitió una nota de entrega fiscal real**:

| control | picking | cliente |
|---|---|---|
| `00-00001586` | MATU/OUT/00002 | Marco Romero |
| `00-00001587` | ALM/OUT/00848 | Marco Romero |
| `00-00001588` | ALM/OUT/00849 | Marco Romero |
| `00-00001589` | ALM/OUT/00850 | Marco Romero |
| `00-00001590` | ALM/OUT/00851 | SERVICIOS Y MANTENIMIENTO SPACARS |

Los cinco fueron a `emisionv2.thefactoryhka.com.ve` —el proveedor **de producción**,
no un sandbox—, volvieron `"Documento procesado correctamente"`, quedaron `approved`
y **tienen URL pública de consulta**. Cinco números de control de la empresa,
consumidos.

**No salieron del banco de escenarios, y la evidencia es estructural y no de
horarios**: el destino de los cinco es la ubicación real y los clientes son reales.
El andamiaje *siempre* redirige `location_dest_id` a
`Socios/ZZ PRUEBAS BLINDAJE clientes` y usa clientes con prefijo `ZZ BLINDAJE`, así
que no puede producir esas filas. Y el canario dio delta 0 en todas las corridas.

Lo que esto confirma es la frase que esta sección ya decía y que era una advertencia:
**la nota de entrega también se emite**, así que validar una salida del depósito
dispara el proveedor igual que postear una factura. Cinco veces, en poco más de una
hora, sin que nada lo pidiera dos veces.

### Y por qué las cuatro barreras se quedan

El conector se archivó (`account.digital.invoicing.conn.active = False`) a las
**21:46 UTC** — *después* de las cinco emisiones. Así que todo lo observable es de
antes y **no hay evidencia de que archivarlo impida emitir**. Dos cosas siguen como
estaban: el diario `INV` todavía resuelve el conector archivado
(`invoicing_digital_conn = [1, 'THKA…']`, `billing_type = 'digital_inv'`) —en Odoo un
`many2one` a un registro archivado sigue resolviendo— y `Socios/Clientes` sigue con
la bandera encendida.

**Y quitarlas no compraría nada.** `changed_facturas` filtra por `move_type` y **no
por diario**: una factura posteada por `ZZPRU` la ve el sync exactamente igual que
una por `INV`. El diario de pruebas es un sustituto fiel de todo lo que nuestra
aplicación observa; lo único que cambia es el efecto fiscal de Odoo, que no es lo que
esta fase mide. Las barreras no cuestan fidelidad.

## ALTA — 1.108,59 USD de mercancía devuelta que la factura sigue cobrando, y nadie lo ve

Esta es la respuesta al ítem que el plan tenía pendiente: *«Recálculo con
devolución — marcado como parcial en TODO.md. La opción D está implementada;
falta confirmar que el recálculo se comporta como esperás. La fase 3 lo prueba.»*

**El recálculo se comporta como se diseñó.** La opción D dice que si la orden está
entregada completa y tiene devolución, el motor factura sobre `cantidad_entregada`
(neta de la devolución). Cuando se devuelve **todo**, esa cantidad queda en cero
en cada línea, así que el teórico queda en cero. Y eso es correcto: la mercancía
volvió, no hay nada que cobrar.

**Lo que falta es la otra mitad.** En el Odoo de prueba hay **24 órdenes**
devueltas por completo, por **21.577,48 USD** de mercancía. De ellas, **4 siguen
con la factura posteada y viva**:

| orden | monto de la orden | facturado vivo | ¿en la bandeja? |
|---|---:|---:|:---:|
| S00161 | 712,48 | 712,47 | **no** |
| S00372 | 644,13 | 644,12 | sí |
| S00599 | 279,22 | 279,20 | **no** |
| S00485 | 116,93 | 116,92 | **no** |

Las cuatro tienen teórico **cero** y factura **viva**. O sea: el sistema dice que
no hay nada que cobrar y Odoo le sigue pidiendo la plata al cliente. Eso es una
nota de crédito pendiente por definición.

Y **solo una de las cuatro está en la bandeja de facturación** — S00372, que
justamente es la que el plan ya tenía anotada como «Revisar el caso». Las otras
tres, **1.108,59 USD**, no aparecen en ningún lado: ni en la bandeja, ni como
discrepancia, ni en el balance. El teórico en cero las saca de la cuenta por
cobrar y nada las recoge del otro lado.

**Por qué ningún chequeo lo agarraba.** Los dos candidatos naturales fallan por
razones opuestas: `entregado_supera_lo_pedido` mira el caso inverso (salió más de
lo pedido), y un chequeo de «teórico en cero con líneas» las agarra pero
**mezcladas con las que no se pudieron calcular** — que es exactamente la
distinción que este blindaje persigue. Un cero calculado bien y un cero por no
haber podido calcular no son lo mismo, y un chequeo que los junta no sirve para
ninguno de los dos.

**Aplicado:** el chequeo se partió en dos.
`teorico_en_cero_sin_explicacion` excluye las devueltas (y da **limpio**), y
`devuelta_completa_con_factura_viva` es nuevo, encuentra las 4, y lleva la
columna `en_bandeja` para que se vea de una que 3 están invisibles. Los dos entran
a la corrida diaria de la 2.3 automáticamente, porque importa el catálogo.

**Qué queda para vos:** las tres facturas hay que acreditarlas en Odoo. El sistema
ahora las lista; emitir la nota de crédito es tu decisión y tus manos.

## ALTA — el reporte de saldos valora el teórico con otra lista que el resto de la app

Esto salió de perseguir un número mío que no cerraba, y es el hallazgo más grande
de esta tanda. La historia de cómo apareció está abajo, porque el error de método
importa tanto como el resultado.

Cuatro sitios de `app.py` arman un `OdooPriceResolver`. Tres pasan por
`_primer_id_activo`, que existe justamente para que una lista **archivada** no
quede como primaria solo por aparecer primera en la configuración (bug real de
agosto 2026, documentado en su propio docstring). El cuarto no:

| sitio | elige la lista con | en esta base |
|---|---|---|
| `app.py:2623` | `_primer_id_activo` | BCV → 10, USD → 11 |
| `app.py:3573` | `_primer_id_activo` | BCV → 10, USD → 11 |
| `app.py:3709` | `_primer_id_activo` | BCV → 10, USD → 11 |
| **`app.py:4434`** (`_get_reporte_saldos_sync`) | **`ves_ids[0]` / `usd_ids[0]` crudo** | **BCV → 3, USD → 7** |

Las listas 3 y 7 están archivadas en Odoo **y no tienen una sola regla de precio
vigente desde abril de 2026**. Las 10 y 11 están activas con las 154 vigentes.

Medido línea por línea sobre las 1.887 líneas comparables de órdenes reales
(`scripts/auditar_listas_de_precio.py --sin-pruebas`):

| | reporte de saldos (3 / 7) | las otras tres páginas (10 / 11) | diferencia |
|---|---:|---:|---:|
| VES | 686.466,48 | 846.558,83 | **−160.092,35 (−18,9 %)** |
| USD | 457.648,41 | 550.264,50 | **−92.616,09 (−16,8 %)** |

**789 órdenes** se valoran distinto según qué página las mire. El bruto —sin que
los desvíos de un signo tapen los del otro— es 194.532,51 en VES y 115.805,93 en
USD. Y el signo importa: el reporte de saldos **subvalúa** el teórico, así que la
subfacturación es justo lo que no se ve.

Dos advertencias sobre esa tabla, porque son la diferencia entre un número y un
número citable: compara **precios de línea**, no el teórico completo del motor
(que aplica descuentos, volumen, el fallback de ficha y la lógica de moneda), así
que acota el orden de magnitud y no es el monto exacto de la pantalla; y la
configuración de listas de esta base de prueba puede no ser la de producción. Lo
que **no** depende del entorno es el defecto: la guarda está en tres de cuatro
sitios.

**Qué queda para vos:** poner `_primer_id_activo` en el cuarto sitio mueve montos
en una pantalla que ya se usa, así que no lo toqué — es la regla de la Fase 1.
Está medido y listo para tu visto bueno.

## ALTA — una lista de precios vencida no puede marcarse, por diseño

Por qué lo de arriba pudo pasar cinco meses sin que nadie lo note.

`_precio_fijo_en_lista` (`odoo/price.py:174-177`) busca la regla que calza por
fecha y, **si ninguna calza, devuelve `rules[0]` igual**. Nunca devuelve `None`
mientras exista alguna regla para ese producto en esa lista. Y `usa_fallback` solo
se marca cuando devuelve `None`.

O sea: una lista con todas las reglas vencidas entrega precios de abril con la
misma cara que una lista al día. No hay excepción, no hay log, no hay bandera.

Es una mina nueva para el inventario de la [1.1](1.1-fallbacks-silenciosos.md), y
mi auditoría AST **no la cazó** porque no traga una excepción ni devuelve un
centinela: devuelve un dato real, de otra fecha. El clasificador busca `except`
vacíos y ceros por defecto; esto no es ninguno de los dos.

## Cómo apareció, y qué medí mal

Había publicado en este documento y en la [6](6-deuda-medida.md) que **10 de 801
órdenes (1,2 %), por 10.059,44 USD**, se valoraban por fórmula en vez de por
precio de lista, y con eso bajé el ítem `OdooPriceResolver sin calibrar` de Alta a
Media. Al reejecutar el chequeo dio **7**, no 10, y varios teóricos habían
cambiado de valor sobre datos que nadie tocó. Perseguir esa diferencia destapó
todo lo de arriba, y también tres errores míos:

1. **`app_settings` de esta base no tiene `valid_pricelists_ves`/`_usd`.**
   `build_inputs` los lee de ahí, no los encuentra, y el teórico cae al nombre
   lógico `"BCV"`/`"USD"` cableado en `engine/discounts.py:377-378`. Los 883
   teóricos de la tabla se calcularon así, sin pasar por la lógica de pareo de
   listas. **Ese es un hueco de mi entorno de prueba, no un hallazgo de
   producción**, y contamina toda medición de teóricos que hice acá.
2. **Mis dos scripts tenían el mismo defecto que `app.py:4434`.**
   `scripts/qa_entorno.py` y `escenarios/sistema.py` armaban el resolver con
   `ids_*[0]` sin la guarda, así que escribieron los 883 teóricos contra las
   listas archivadas. Eso ya está arreglado en los dos.
3. **La marca `usa_fallback` subreporta, y no lo puedo explicar del todo.** Contra
   las mismas listas archivadas, un conteo directo da **39 órdenes** con algún
   producto sin regla de precio; la marca guardada dice **7**. Un factor de cinco.

La medición corregida, contra las listas que la app elegiría con la guarda puesta:

| | órdenes reales | |
|---|---:|---:|
| consideradas | 863 | |
| **con alguna línea cuyo producto no tiene regla de precio** | **13 (1,5 %)** | teórico USD guardado: 7.743,11 |

El 1,5 % se parece al 1,2 % que había publicado, pero **por casualidad**: el
número anterior salía de una marca que subreporta, sobre teóricos calculados con
las listas equivocadas. Y ese teórico USD de 7.743,11 hay que leerlo con pinzas
por lo mismo — está calculado con las listas archivadas.

Lo que cambia de fondo es la conclusión: bajé el ítem a Media diciendo que el
problema era «faltan precios en las listas históricas, no hay que reescribir el
resolver». **Faltar precios es el 1,5 %; elegir la lista equivocada es el 18,9 %.**
El ítem vuelve a Alta, y por un motivo distinto al del plan.

## BAJA — el espejo trae 10 facturas en borrador, y sumarlas es fácil

`changed_facturas` incluye los borradores a propósito, para reflejar una factura
en cuanto se crea. No es un defecto; es una trampa. Cualquier consumidor que sume
`monto_total_signed_usd` sin filtrar `estado = 'posted'` cuenta **5.891,98 USD**
que todavía no se emitieron.

Lo reproduje yo mismo: al medir S00161 me dio 1.326,67 facturado contra una orden
de 712,48 —casi el doble— y parecía una doble facturación. Era una posteada de
712,47 más un borrador de 614,20. Queda como chequeo
`facturas_borrador_en_el_espejo` para que el número esté a la vista en vez de
sorprender a quien haga la próxima suma.

## Lo que quedó confirmado funcionando

No todo hallazgo es un defecto. Estas son las defensas que el banco puso a prueba
y aguantaron:

- **El sync completo es fiel.** 9 de 9 partidas cuadran contra Odoo al centavo,
  26.113 filas en 17 segundos (ver [1.4](1.2-1.5-auditoria.md)).
- **El espejo borra las líneas que Odoo ya no tiene** — el arreglo de S00792 sigue
  vivo.
- **`cantidad_entregada` es neta de devoluciones**, que es lo que la opción D
  necesita para funcionar.
- **La detección de devoluciones anda**: las 24 órdenes devueltas están marcadas
  `tiene_devolucion`.
- **El universo del dashboard coincide con el del reporte de saldos** — misma
  función, mismos argumentos, con el estado en vivo de Odoo.
- **El balance se abstiene** cuando no tiene contra qué comparar.

## La corrida completa: 33 pasan, 12 fallan, y las 12 dicen cosas distintas

Primera corrida de las 45 pruebas (31 escenarios + 8 de humo + 6 de estrés):
**25 minutos y 44 segundos**, 33 en verde y 12 en rojo. Lo importante no es el
número sino que las 12 se parten en tres grupos, y solo uno de los tres es un
defecto del sistema.

### Dos hallazgos del sistema

**Las dos partidas de tasa dan verde justo cuando menos pueden opinar.**
Es el escenario «Cargan una tasa equivocada en Odoo», que el plan pedía provocar
«para confirmar que las partidas lo agarran». Se movió la tasa de Odoo un 50 % y
las dos siguieron en verde. La causa no es que no detecten el desvío: es que
**no compararon ni un documento**. Las dos despejan la tasa implícita de cada
factura o pago y la comparan contra **nuestro** BCV de esa fecha; cuando no
tenemos tasa para esa fecha, saltean el documento con un `continue` silencioso.
Sin serie sembrada saltean todos y reportan cero divergencias — que se lee
exactamente igual que «verifiqué y está todo bien».

Son las dos partidas más fuertes del balance, las únicas que comparan contra el
BCV y no contra Odoo. Y es la misma trampa de «sin datos no es cero», adentro
del instrumento que audita a los demás.

*Aplicado:* las dos dicen ahora cuántos compararon, y cuando no compararon
ninguno lo dicen con todas las letras. **No se cambió el veredicto**: volverlas
rojas podría enrojecer el balance de producción por documentos viejos sin tasa,
y eso es una decisión aparte. Lo que se arregló es que el cero deje de poder
leerse como una confirmación.

**El teórico no se re-verifica cuando cambia la fecha de la orden.** Es una de
las filas de severidad alta, y el escenario confirma la predicción del plan:
cambiar `date_order` cambia la lista vigente y la tasa aplicable, y
`ventas_teoricos` sigue con el valor y la marca de tiempo viejos. La orden queda
valorada con la lista de una fecha en la que ya no está, y nada avisa.

### Cuatro hallazgos sobre Odoo, y los cuatro bajan el riesgo

Estos no eran defectos: eran suposiciones de la tabla que Odoo no permite.

**No se puede cambiar la lista de precios de una orden confirmada.** Odoo
contesta «No puede cambiar la lista de precios de una orden confirmada». La fila
estaba en Alta y por esa vía no puede darse.

**No se puede cobrar dos veces una factura ya saldada.** El asistente de cobro
contesta «no queda nada por pagar en los apuntes contables seleccionados». La
duplicación sigue siendo posible mientras la factura tenga residual —y ese caso
sí se prueba— pero el camino fácil está cerrado.

**No se puede quitar un producto de una orden ya entregada**, y ésta es la que
más baja el riesgo. Dos protecciones encadenadas: no se puede borrar la línea de
una orden confirmada («establece la cantidad en 0») y no se puede poner la
cantidad por debajo de lo entregado («cree una devolución en su inventario»). O
sea que sacar un producto entregado **exige hacer la devolución primero**, que es
exactamente lo que la fila —de severidad alta— temía que se pudiera saltear.

Eso además explica el mecanismo real de las dos líneas con `cantidad_entregada`
negativa de los datos reales: **no salen de saltear la devolución, salen de
hacerla primero y recortar la orden después**. Cada paso es legítimo; el
resultado, negativo.

**No se puede emitir una nota de crédito por más que su factura.** La fila decía
«debe rechazarse o señalarse; hay un techo implementado pero no se probó contra
Odoo real». Probado: Odoo la rechaza en la raíz, con los dos montos en el
mensaje —«El monto de la Nota de Crédito no puede exceder el monto total de la
factura original. Monto Factura: 517.288,72 / Monto NC: 1.034.577,45»—. O sea que
**el techo del sistema nunca llega a ejercitarse**, porque la situación no puede
darse por esta vía.

El escenario verifica las dos mitades: que la inflada se rechace **y** que una
normal sí salga. Sin la segunda, el rechazo no probaría nada — podría estar
fallando por cualquier otro motivo.

Los cuatro escenarios se reescribieron para **vigilar la protección** en vez de
suponer que no existe: si un día Odoo dejara de bloquearlos, los tests fallan y
avisan que la fila volvió a estar viva.

### Ocho eran bugs míos, y tres enseñaron algo

Un banco con ocho escenarios que no llegan a armar su situación no es un banco.
Los ocho quedaron arreglados, y tres de ellos valen más que el arreglo:

| Lo que fallaba | Lo que enseñó |
|---|---|
| «Especifique al menos una cantidad diferente a cero» | El asistente de devolución crea sus líneas en cero: hay que llenarlas siempre, no solo para una devolución parcial. |
| `account.move.reversal` exige `journal_id`… | …y **no acepta el diario de pruebas**: «the journal must be of the credit note type», y el único que acepta es el real, con la imprenta conectada. No alcanza con `free_form` ni con `refund_sequence` — la validación es del módulo de localización. Las notas de crédito del banco se construyen a mano. |
| «cannot marshal None unless allow_none is enabled» | `action_draft` de un pago devuelve `None` y el servidor XML-RPC de Odoo no lo puede serializar. La operación **sí corre**; revienta al armar la respuesta. Misma trampa que `action_unlock`. |
| **«No puedes eliminar ninguna de sus líneas… Establece la cantidad en 0»** | **Odoo no deja borrar una línea de una orden confirmada.** Sacar un producto es poner su cantidad en cero — y eso es exactamente el mecanismo que produjo las dos líneas con `cantidad_entregada` negativa de los datos reales (S00925 con −10 unidades, S00952 con −4). |

Esa última cambia el escenario para mejor: la línea **no desaparece**, se queda
con cantidad cero y lo entregado intacto, así que el espejo no pierde el rastro.
Lo que queda es que **lo entregado supera lo pedido**, que es el caso que hay
que detectar, y el escenario ahora lo verifica explícitamente.

## La corrida de verificación: un bug más del andamiaje, y por qué valía correrla

Después de sacar ocho piezas de `app.py`, el banco se volvió a correr completo contra
el Odoo real. **20 fallaron y 26 pasaron**, contra un solo rojo intencional de la
corrida anterior.

Lo primero y lo que importa: **el canario dio cero en las 46**. Nada se emitió.

Las 20 fallas eran **una sola**, en cascada. `facturar()` reventaba y con él todo lo
que necesita una factura: el archivo entero de pagos, el de devoluciones y el de
facturación. El error de Odoo:

```
account_dual_currency._compute_date  ->  rec.invoice_date = datetime.now()
l10n_ve_full.write  ->  ValueError: La fecha contable no puede ser menor
                                     a la fecha de la factura
```

Al escribir solo `journal_id` sobre el borrador, Odoo recomputa y el módulo de doble
moneda pisa `invoice_date` con **hoy**, mientras la fecha contable había quedado en la
de la orden. Es el **noveno bug del propio andamiaje**, y se arregla fijando las dos
fechas en la misma escritura: sin nada que pisar, la validación se cumple por
construcción.

**Lo que esto dice del banco.** Ocho extracciones de un archivo de diecisiete mil
líneas no rompieron nada del sistema — el fallo era del andamiaje y en una interacción
con dos módulos venezolanos de Odoo que ningún test unitario podía ver. Correrlo era la
única forma de saberlo, y la razón por la que el plan pedía un banco reejecutable y no
un informe de una corrida.

## Dónde quedó el banco

Después de arreglar lo mío y reescribir lo que Odoo resultó proteger, las 12
fallas originales quedaron así:

| | |
|---|---:|
| bugs del andamiaje, arreglados | 7 |
| escenarios reescritos para vigilar una protección de Odoo | 4 |
| **hallazgos del sistema, que siguen en rojo a propósito** | **1** |

Ese único rojo es **«el teórico no se re-verifica cuando cambia la fecha de la
orden»**, y tiene que quedarse rojo: es el hallazgo. El día que se arregle, deja
de fallar solo — que es exactamente para lo que sirve un banco de escenarios y no
un informe de una corrida.

El otro hallazgo del sistema —las partidas de tasa que daban verde sin haber
comparado nada— **ya está aplicado**, así que su escenario pasó a verde
verificando la corrección.

## Lo que queda para la próxima corrida

La corrida completa tarda 25 minutos y el grueso son los reportes: el de saldos
resuelve precios producto por producto contra Odoo. El backfill de teóricos ya se
sacó del primer escenario a `scripts/qa_entorno.py motor`, donde se paga una sola
vez (520 órdenes en 677 s, o sea 46 por minuto).

Y una advertencia para quien lo corra: **el banco deja datos en el Odoo de
prueba**. Odoo no permite borrar una orden confirmada ni una factura posteada, así
que las órdenes `ZZ BLINDAJE` se acumulan entre corridas. En un entorno efímero
está bien; en uno que dure, conviene recrearlo cada tanto.

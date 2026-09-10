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

## MEDIA — el 1,2 % de las órdenes se valora por fórmula, no por precio de lista

La medición que el plan pedía antes de decidir si el ítem `OdooPriceResolver sin
calibrar para Odoo 18` es urgente. Corriendo el motor sobre las 801 órdenes reales
con teórico calculado:

| | órdenes | teórico USD |
|---|---:|---:|
| valoradas por precio de lista | 791 (98,8 %) | — |
| **valoradas por fórmula** | **10 (1,2 %)** | **10.059,44** |

Las diez, con su lista: S00328 (lista 3), S00411 (8), S00801/S00709/S00723/S00743/
S00925/S00327/S00708 (5) y S00220 (4). **Nueve de las diez están en listas
archivadas** (3, 4, 5, 8) — las históricas. Solo tienen que valorarse por fórmula
porque su lista ya no tiene el precio de ese producto cargado.

Eso reencuadra el ítem: sigue siendo real, pero es **1,2 % y está concentrado en
el histórico**, no una hemorragia en las ventas de hoy. Baja de ALTA a MEDIA, y su
arreglo natural es cargar los precios faltantes en las listas históricas —que es
el otro ítem pendiente, «Precios faltantes en la lista 5»— y no reescribir el
resolver.

Lo que **sí** sigue siendo ALTA es que el resolver está `pragma: no cover`: sus
cuatro minas de la [1.1](1.1-fallbacks-silenciosos.md) no tienen un solo test, y
lo que las dispara no es la falta de precio sino un fallo de red disfrazado de
falta de precio.

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

### Tres hallazgos sobre Odoo, y los tres bajan el riesgo

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

Los tres escenarios se reescribieron para **vigilar la protección** en vez de
suponer que no existe: si un día Odoo dejara de bloquearlos, los tests fallan y
avisan que la fila volvió a estar viva.

### Ocho eran bugs míos, y tres enseñaron algo

Un banco con ocho escenarios que no llegan a armar su situación no es un banco.
Los ocho quedaron arreglados, y tres de ellos valen más que el arreglo:

| Lo que fallaba | Lo que enseñó |
|---|---|
| «Especifique al menos una cantidad diferente a cero» | El asistente de devolución crea sus líneas en cero: hay que llenarlas siempre, no solo para una devolución parcial. |
| `account.move.reversal` exige `journal_id` | Y ese campo es además lo que mantiene la nota de crédito **fuera de la imprenta digital**: sin pasarlo, saldría por el diario real. |
| «cannot marshal None unless allow_none is enabled» | `action_draft` de un pago devuelve `None` y el servidor XML-RPC de Odoo no lo puede serializar. La operación **sí corre**; revienta al armar la respuesta. Misma trampa que `action_unlock`. |
| **«No puedes eliminar ninguna de sus líneas… Establece la cantidad en 0»** | **Odoo no deja borrar una línea de una orden confirmada.** Sacar un producto es poner su cantidad en cero — y eso es exactamente el mecanismo que produjo las dos líneas con `cantidad_entregada` negativa de los datos reales (S00925 con −10 unidades, S00952 con −4). |

Esa última cambia el escenario para mejor: la línea **no desaparece**, se queda
con cantidad cero y lo entregado intacto, así que el espejo no pierde el rastro.
Lo que queda es que **lo entregado supera lo pedido**, que es el caso que hay
que detectar, y el escenario ahora lo verifica explícitamente.

## Lo que queda para la próxima corrida

La corrida tarda 25 minutos y el grueso son los reportes: el de saldos resuelve
precios producto por producto contra Odoo. El backfill de teóricos ya se sacó
del primer escenario a `scripts/qa_entorno.py motor`, donde se paga una sola vez
(520 órdenes en 677 s, o sea 46 por minuto).

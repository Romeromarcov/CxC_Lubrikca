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

El banco quedó con tres barreras independientes (diario sin conector, ubicación
con la imprenta apagada, y un canario que cuenta los números de control antes y
después de cada escenario). **Ninguna corrida emitió nada**, verificado con el
canario en cero en cada una.

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

## Estado de las 31 filas

El banco tiene los 31 escenarios escritos y la fontanería verificada de punta a
punta: los 8 de humo en verde (crear una orden, entregarla completa, facturarla
por el diario de pruebas, cobrarla, sincronizar, y los cuatro reportes
contestando), más los 6 de la [Fase 4](4-estres.md), también en verde.

La corrida completa de las 31 filas es lenta por una razón medida y no por un
defecto: el motor procesa **46 órdenes por minuto** contra Odoo, así que el
backfill inicial de los teóricos son ~11 minutos que ahora se pagan una sola vez
en `scripts/qa_entorno.py motor` en vez de dentro del primer escenario.

Los hallazgos de arriba salieron de correr el motor y los reportes completos sobre
el espejo de QA, que es el trabajo que los escenarios ejercitan fila por fila.

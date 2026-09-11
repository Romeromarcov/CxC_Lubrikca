# Fase 4 — Estrés y fallas

La Fase 3 pregunta si el sistema entiende datos raros. Esta pregunta si aguanta
condiciones malas.

Cinco escenarios en `escenarios/test_estres.py`, reejecutables con el resto del
banco. El de volumen se corre aparte, contra un espejo multiplicado por diez --
ver más abajo.

```bash
./scripts/escenarios.sh escenarios/test_estres.py
```

## Reejecución — el sync es idempotente

**Verificado.** Dos corridas completas seguidas sobre los mismos datos dejan el
espejo idéntico, tabla por tabla: 950 órdenes, 2.193 líneas, 583 clientes, 1.273
pagos, 793 facturas, 930 entregas, 255 productos de catálogo, 1.787 líneas de
factura y 1.969 líneas de entrega, las dos veces.

No es un detalle de estilo: es la propiedad que hace que el sync sea
**reparable**. Si no fuera idempotente, la única forma de arreglar un espejo
dudoso sería recrearlo desde cero, y eso son 17 segundos hoy pero no lo serían
con diez veces los datos.

El segundo escenario cubre el delta. El delta **sí** encuentra filas aunque nada
haya cambiado, porque su ventana mira 48 horas atrás por `write_date` — eso es
deliberado y cubre el reloj desalineado entre Odoo y nosotros. Lo que se verifica
es que refrescar esas filas no mueva los conteos, porque un conteo que crece
significaría que el upsert está insertando en vez de actualizar.

## El volumen, ejecutado (ya no extrapolado)

En la primera pasada esto se **midió y extrapoló** en vez de correrse, con el
argumento de que escribir 9.000 órdenes en Odoo por XML-RPC tardaría más de una
hora y dejaría el entorno inservible. El argumento era cierto y la conclusión
estaba mal: **la pregunta del plan no era sobre Odoo.**

> «Diez veces las órdenes y los pagos actuales. Hoy el reporte tarda unos diez
> minutos con los cachés fríos; con diez veces los datos hay que ver si termina.»

Los reportes leen del **espejo local**. Multiplicar el espejo no requiere tocar
Odoo: se clonó `cxc_qa` en `cxc_vol` y se insertaron nueve copias de órdenes,
líneas, pagos, vinculaciones, facturas, entregas, teóricos y bandeja, con los ids
sufijados. Los clientes **no** se multiplicaron a propósito: en el negocio crecen
las órdenes, no la cartera, y mantener los 630 deja diez veces más filas por
cliente, que es el caso pesado para la agrupación.

| | 1× | 10× | factor |
|---|---:|---:|---:|
| órdenes | 1.037 | 10.370 | 10× |
| líneas | 2.286 | 22.860 | 10× |
| pagos | 1.304 | 13.040 | 10× |
| vinculaciones | 1.487 | 14.870 | 10× |

Con **Odoo desconectado a propósito** —así se mide el cómputo local, que es lo que
crece con el volumen, y de paso se ejercita el modo degradado—:

| Reporte | 1× | 10× | factor |
|---|---:|---:|---:|
| Ventas | 0,7 s | **4,4 s** | 6,3× |
| Reporte de saldos | 0,7 s | **4,7 s** | 6,7× |
| Reporte por cliente | 0,2 s | **1,9 s** | 9,5× |
| Bandeja | 0,1 s | **0,7 s** | 7,0× |
| Balance de comprobación | 0,9 s | **6,6 s** | 7,3× |

**Los cinco terminan, y el crecimiento es lineal.** La incógnita que este
documento declaraba —«no se sabe si terminan»— queda cerrada, y con un dato que
reencuadra el resto: **los ~10 minutos del reporte con cachés fríos no son cómputo
local, son ida y vuelta a Odoo.** Con Odoo fuera del camino el mismo reporte tarda
menos de un segundo a volumen actual. Lo que hay que optimizar cuando el volumen
crezca no son los reportes: son las llamadas XML-RPC.

### Y un hallazgo que solo aparece a volumen

A 10× el balance **se pone rojo en una partida que a 1× cuadra**, y no porque algo
esté mal:

| | filas | residuo de «Saldo a favor de clientes» | veredicto |
|---|---:|---:|---|
| 1× | 1.037 órdenes | 1,53 | verde |
| 10× | 10.370 órdenes | **15,55** | **ROJA** |

La partida lleva `tolerancia=5.0` y su propia nota dice que «cubre el redondeo de
cientos de filas». A diez veces los datos hay miles, el residuo escala **lineal**
(10,2×) y la tolerancia es fija. El plan mismo aceptaba «+1,87 de saldo a favor»
como redondeo a no perseguir — y esa aceptación **tiene fecha de vencimiento**: se
agota cuando el negocio crece.

La dirección es lo que importa: es un **falso rojo**. Un instrumento que grita lobo
a medida que el negocio crece deja de mirarse, y entonces el día que haya un
descuadre real nadie lo va a ver.

**No se cambió la tolerancia.** Hacerla proporcional la volvería más permisiva a
volumen alto y podría tapar un descuadre verdadero: es un cambio de veredicto y es
tu decisión, igual que las dos partidas de tasa. Queda fijado con un test
(`test_la_tolerancia_es_absoluta_y_no_escala_con_el_volumen`) que lo reproduce con
mil filas y un centavo de residuo por fila.


### Y los tres residuos que el plan propone aceptar tienen fecha de vencimiento

El plan cierra con tres residuos «que propongo aceptar y no perseguir»: −6,06 en pagos
contra Odoo, +1,87 de saldo a favor y −0,02 del teórico USD. La decisión es razonable:
los tres tienen explicación y ninguno cambia nada.

Lo que la prueba de volumen agrega es que **los tres crecen con el negocio y las
tolerancias que los absorben no**. Medidos en las dos escalas:

| partida | 1× | 10× | tolerancia | veredicto a 10× |
|---|---:|---:|---:|---|
| Saldo a favor de clientes | 1,53 | **15,55** | 5,0 | **ROJA** |
| Cuadre interno — Venta Real | 0,01 | 0,23 | 1,0 | verde |
| Cuadre interno — Teórico USD | 0,05 | 0,39 | 1,0 | verde |

El primero ya se pasó. Los otros dos todavía entran, pero con el mismo patrón: el
residuo escala con las filas y el umbral es un número fijo escrito una vez.

Aceptar un residuo es correcto; **aceptarlo con un umbral absoluto es aceptarlo hasta
que el negocio crezca**, y sin avisar cuándo. Las tres partidas se ponen rojas tarde o
temprano por redondeo, y cuando eso pase el balance va a estar señalando algo que no es
un error.

## Sync interrumpido a la mitad

**La defensa existe y es el orden de las operaciones.** `set_last_sync(now)` es
lo **último** que hace `IncrementalSync.run`: si el proceso muere antes, el
cursor sigue donde estaba y la próxima corrida vuelve a leer la misma ventana.
El escenario lo provoca —un lector que falla justo al llegar a los pagos, con
las órdenes ya escritas— y comprueba que el cursor no se movió.

Lo que **no** hay es una transacción que abarque el ciclo entero: las órdenes
quedan escritas y los pagos no. Eso está bien dado el diseño (el espejo converge
en la próxima corrida) y es la razón por la que la idempotencia de arriba
importa tanto: sin ella, un corte a la mitad dejaría un espejo mezclado sin
forma de sanearlo salvo recrearlo.

## Odoo caído

**El balance ya se abstenía; el dashboard ahora lo dice.** Los dos escenarios
verifican los dos comportamientos:

- el balance devuelve `evaluable: false` con su motivo cuando no tiene contra qué
  comparar, en vez de armar 24 partidas contra una lista vacía (que daba a la vez
  un falso verde en las 17 de monto y un falso rojo en las 2 de bandeja);
- el reporte diario **no puede** abstenerse porque tiene respaldos válidos para
  todo lo que muestra, así que lo que le faltaba era declararlos. Ahora devuelve
  `fuente: {odoo_respondio, cobranza, litros, degradado}` y la pantalla muestra
  un aviso. Ver [5 — Dashboard](5-dashboard.md).

El escenario del balance además comprueba algo más fino: que si evalúa, ninguna
partida **externa** esté comparando cero contra cero y saliendo en verde. Un cero
que viene de no haber podido leer no es un cero, y una partida externa en verde
sobre dos ceros es exactamente la forma que toma ese engaño.

## Dos ciclos de sync a la vez

Los `upsert_*` van por clave primaria, así que dos ciclos simultáneos deberían
converger al mismo estado. El escenario lanza dos en paralelo y verifica que los
conteos no crezcan. Que uno de los dos falle (un lock, una transacción abortada)
es aceptable; lo que no lo es es que falle **dejando el espejo distinto**.

## Volumen — la extrapolación de la primera pasada

Se deja escrita porque el motor **sí** sigue extrapolado, y porque comparar la
estimación contra la medición de arriba dice qué tan buena era.

El plan pide diez veces las órdenes y los pagos actuales. Escribir ~9.000 órdenes
en el Odoo de prueba por XML-RPC es más de una hora de escrituras contra un
servidor compartido, y dejaría el entorno inservible para el resto del banco. En
vez de eso se midieron los tiempos reales y se extrapola. La extrapolación es una
estimación y está marcada como tal; las mediciones no.

### Lo medido

| Operación | Volumen actual | Tiempo | Ritmo |
|---|---|---|---|
| Sync completo (Odoo → espejo) | 26.113 filas | **17 s** | ~1.540 filas/s |
| Backfill de teóricos | 486 órdenes | **605 s** | **48 órdenes/min** |
| Los cuatro reportes, cachés fríos | 967 órdenes | **~11 min** | — |

### Lo extrapolado a 10×

**El sync aguanta.** 261.000 filas a 1.540 filas/s son unos **3 minutos**. La
lectura por XML-RPC es lineal en el número de filas y no hay N+1 en el camino del
sync. Vale notar que el arreglo del filtro `move_type` en `changed_lineas_factura`
(ver [1.3](1.2-1.5-auditoria.md)) importa más a 10× que hoy: sin él ese espejo
solo habría pasado de 17.167 a 172.000 filas de las cuales el 90 % es ruido.

**El motor no aguanta, y el cuello no es la velocidad sino un tope.** A 48
órdenes por minuto, 9.670 órdenes son **3,4 horas** en una sola pasada. Pero el
daemon no hace una sola pasada: `recalculate_all_orders` llama a
`run_teoricos_pendientes(limite=50)` cada ciclo. Con el ciclo de 5 minutos eso
son 600 órdenes por hora — y el tope de 50, no la velocidad, es lo que manda: 50
órdenes tardan ~62 segundos, así que el ciclo pasa cuatro minutos sin hacer nada.

- Hoy: llenar 967 teóricos desde cero por el daemon son **~1,6 horas**.
- A 10×: 9.670 teóricos son **~16 horas**.

Dieciséis horas para que el sistema sepa cuánto vale lo que vendió es demasiado
para una recuperación después de un despliegue o una migración. El arreglo no es
acelerar el motor: es **subir el tope hasta llenar el ciclo**. Con 48 órdenes/min
medidos, un tope de 200 por ciclo de 5 minutos deja margen y baja las 16 horas a
**~4 horas**. Es un número en una línea, y conviene medirlo antes de fijarlo.

**Los reportes eran la incógnita, y ya no lo son.** Esta sección decía que «no se
sabe» si a 10× terminan. Se midió (arriba): **terminan, y el crecimiento es
lineal** — entre 6,3× y 9,5× para 10× de datos.

Y la sospecha de esta sección resultó **correcta en el diagnóstico**: el costo no
está en las filas sino en las consultas a Odoo. Lo que estaba mal era la
conclusión de que eso hacía la medición imposible — bastaba con desconectar Odoo y
medir el cómputo local, que es lo que crece con el volumen. Los ~11 minutos con
cachés fríos son ida y vuelta a Odoo; sin Odoo, los mismos reportes tardan menos
de un segundo a volumen actual y menos de siete a 10×.

`FastPriceResolver` —el patrón señalado acá como el que decide— salió de `app.py` a
`engine/precios_rapidos.py` en la [2.4](2.4-modularizar.md), y sus ceros silenciosos
quedaron contados.

## Resumen

| Escenario | Resultado |
|---|---|
| Reejecución del sync completo | **verificado idempotente** |
| Volumen 10× — los cuatro reportes y el balance | **ejecutado: terminan, crecimiento lineal (6,3× a 9,5×)** |
| Volumen 10× — la tolerancia del balance | **falso rojo**: el residuo escala y la tolerancia es fija |
| Reejecución del delta | **verificado** |
| Sync interrumpido a la mitad | **verificado** — el cursor no avanza |
| Odoo caído: el balance se abstiene | **verificado** |
| Odoo caído: el dashboard se declara degradado | **verificado** (tras el arreglo de la Fase 5) |
| Dos ciclos a la vez | **verificado** — los conteos no crecen |
| Volumen 10× — sync | estimado en ~3 min. Aguanta. |
| Volumen 10× — motor | estimado en ~16 h por el tope de 50/ciclo. **No aguanta**, y el arreglo es una línea. |
| Volumen 10× — reportes | **terminan.** Medido contra un espejo multiplicado. |

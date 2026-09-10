# Fase 4 — Estrés y fallas

La Fase 3 pregunta si el sistema entiende datos raros. Esta pregunta si aguanta
condiciones malas.

Cinco escenarios en `escenarios/test_estres.py`, reejecutables con el resto del
banco. El de volumen es el único que no se ejecuta, y más abajo está por qué y
qué se hizo en su lugar.

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

## Volumen — medido y extrapolado, no ejecutado

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

**Los reportes son la incógnita real.** Los cuatro tardan ~11 minutos con los
cachés fríos, y ahí no se puede extrapolar linealmente porque el costo no está en
las filas sino en las consultas a Odoo por producto y por orden. La única
respuesta honesta es que **no se sabe** si a 10× termina, y que averiguarlo
requiere el escenario de volumen de verdad.

Lo que sí se puede decir es dónde mirar primero: `_get_reporte_saldos_sync` son
1.131 líneas con 114 sin cubrir y contiene el `FastPriceResolver` cuyo fallback
consulta Odoo producto por producto. Ese es el patrón que a 10× decide si el
reporte termina o no.

## Resumen

| Escenario | Resultado |
|---|---|
| Reejecución del sync completo | **verificado idempotente** |
| Reejecución del delta | **verificado** |
| Sync interrumpido a la mitad | **verificado** — el cursor no avanza |
| Odoo caído: el balance se abstiene | **verificado** |
| Odoo caído: el dashboard se declara degradado | **verificado** (tras el arreglo de la Fase 5) |
| Dos ciclos a la vez | **verificado** — los conteos no crecen |
| Volumen 10× — sync | estimado en ~3 min. Aguanta. |
| Volumen 10× — motor | estimado en ~16 h por el tope de 50/ciclo. **No aguanta**, y el arreglo es una línea. |
| Volumen 10× — reportes | **no se sabe.** Requiere el escenario de verdad. |

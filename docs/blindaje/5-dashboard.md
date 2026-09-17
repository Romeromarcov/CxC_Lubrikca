# Fase 5 — Dashboard: cada número rastreado hasta su origen

El dashboard es una SPA (`static/app.js`, función `loadReporteDiario`) alimentada
por un solo endpoint: `GET /api/reporte/diario` → `_get_reporte_diario_sync`
(`app.py:17150-17512`, 363 líneas, 20 sin cubrir).

## Qué muestra, exactamente

**Ocho tarjetas de acumulado**, en cuatro períodos (hoy / mes / trimestre / año):

| Tarjeta | Campo | Qué es de verdad |
|---|---|---|
| Ventas — USD | `ventas.total_usd` | suma de `ordenes_venta.monto_total`, el **bruto de lista** de Odoo |
| Ventas — litros | `ventas.litros` | `sale.report.product_volume` de Odoo, con respaldo local |
| Cobranza — USD | `cobranza.total_eq_bcv` | suma de `account.payment.amount_ref` **de Odoo** (no del BCV) |
| Cobranza — VES | `cobranza.ves_monto` + `ves_eq_usd` | nominal en bolívares y su equivalente |
| Cobranza — métodos | `cobranza.por_metodo` | desglose por diario de Odoo, en el mismo equivalente |

**Dos tablas por día**: ventas (fecha, nº de órdenes, USD, litros) y cobranza
(fecha, equivalente total, desglose por moneda, desglose por método).

## Las cuatro preguntas del plan, contestadas

### 1. ¿Qué universo cuenta cada tarjeta, y coincide con el reporte de saldos?

**Sí, coincide, y está bien construido.** El dashboard filtra con
`orden_excluida(o, live_state=..., entrega_valida=...)` — la misma función y con
los mismos dos argumentos que usa `_get_reporte_saldos_sync`. Incluso consulta el
estado **en vivo** de cada orden en Odoo antes de decidir, con un comentario que
explica por qué: S00162 ($161.679,06) figuraba como `sale` en el espejo y estaba
`cancel` en Odoo, inflando «Ventas del Año» en ese monto.

Es la respuesta que uno querría, y no era la esperable: el universo es la parte
que más fácil se desincroniza entre dos páginas.

### 2. ¿En qué moneda y contra qué teórico está expresado cada monto?

Acá aparecen tres hallazgos.

**BAJA — «Ventas» es el bruto de lista, y la pantalla ya lo dice a medias.**
La tarjeta suma `o.monto_total`, el `amount_total` de la orden en Odoo: precio de
lista, **sin** los descuentos que el motor calcula. El reporte de saldos, en
cambio, mide contra el teórico **neto de descuentos**.

Hay que corregir lo que yo mismo había escrito antes de mirar el HTML: la
etiqueta **ya decía «$ Bruto»**, así que un lector atento podía notarlo. Lo que
faltaba era decir bruto *de qué* y que por eso no es comparable contra la
cartera. **Aplicado:** la etiqueta ahora dice «$ Bruto (lista)» y lleva un
`title` que explica la relación con el Reporte de Saldos.

**MEDIA — `total_eq_bcv` no siempre es el equivalente BCV, y el nombre miente en
el caso normal.** El campo se calcula por dos caminos distintos:

```python
# camino normal (Odoo responde):
eq_usd = parse_decimal_safe(str(p.get("amount_ref") or "0"))   # la cifra de ODOO

# camino degradado (Odoo no responde):
bcv_rate, _ = get_rate_for_datetime(fecha_dt, tasas_rows)
eq_usd = monto if moneda == "USD" else (monto / bcv_rate if bcv_rate > 0 else Decimal("0"))
```

En el camino normal el equivalente es el que **Odoo** estampó, no el que sale de
nuestra serie BCV. Y sabemos que los dos pueden diferir: la partida por cobrar
del pago 200 tiene 96.660,31 VES por 134,00 USD, o sea una tasa de 721,3, cuando
la tasa oficial de Odoo para ese día es 483,87 (ver [2.3](2.3-alertas.md)). Las
partidas 13 y 16 del balance existen justamente para detectar ese desvío — así
que el dashboard está mostrando, con el nombre «eq BCV», la cifra que el balance
audita por sospechosa.

La etiqueta de la pantalla lo decía peor todavía: **«Total (Tasa BCV):»**,
afirmando exactamente lo que no es.

**Aplicado:** la etiqueta ahora dice «Total (eq. Odoo)» y su `title` explica que
los dos pueden diferir y que las partidas 13 y 16 del balance existen para
detectar ese desvío. El campo del JSON se dejó con su nombre para no romper nada
que lo consuma; lo que cambió es lo que el usuario lee.

**MEDIA (latente) — un pago sin `amount_ref` suma cero a la cobranza.** El
`or "0"` de arriba es una mina del inventario 1.1 en la tarjeta más mirada del
sistema: un pago sin `amount_ref` aporta su nominal completo a `por_moneda` y
**cero** a `total_eq_bcv`, así que la tarjeta principal lo pierde y el desglose
lo muestra. En los datos reales no está pasando —medido: 1.273 de 1.275 pagos
confirmados tienen `amount_ref`— pero los dos que faltan son **los que creé yo**
al armar el banco de escenarios, por 517.404,72 VES entre los dos. O sea: un
pago registrado por una vía que no llena ese campo desaparece de la cobranza sin
dejar rastro, y ya se reprodujo.

**Aplicado:** cuando falta `amount_ref` se calcula el equivalente con nuestra
serie, que es lo que el camino degradado ya sabía hacer. Los dos caminos ahora
comparten `_eq_usd_por_serie`, en vez de dos copias del mismo `monto / bcv_rate`
con dos manejos distintos del caso «no hay tasa».

**Es el único de los seis que cambia un número**, y solo donde hoy es cero para
un pago que existe. Lo anoto explícito porque la regla de la fase pide visto
bueno para lo que mueve montos: acá el monto que se mueve está mal por
definición, pero conviene que lo sepas.

### 3. ¿Qué pasa cuando Ventas está recalculando?

**El dashboard no depende del caché de Ventas**, así que la trampa que hacía
falsear el balance no aplica: lee `repo.all_ordenes()` y `repo.all_catalogo()`
directo del espejo. No tiene por qué abstenerse por ese motivo, y no abstenerse
es lo correcto.

**Pero sí tiene un modo degradado, y no lo decía.** Si Odoo no responde:

- los litros caen al cálculo local por producto en vez de `sale.report`;
- el estado en vivo de las órdenes no se consulta, así que el universo vuelve al
  del espejo (el caso S00162 volvería a inflar el total);
- la cobranza cae a la tabla local de pagos, con la tasa de nuestra serie.

Los tres son respaldos razonables. El problema era que **la respuesta no llevaba
ninguna bandera** que dijera por qué camino se fue: existía una variable
`cobranza_desde_odoo` en el código y no se exponía, así que la pantalla mostraba
los mismos números con la misma confianza en los dos casos.

Es literalmente el mismo defecto que el balance ya resolvió abstiéndose, un
nivel más suave: acá no hace falta abstenerse, hace falta **decirlo**.

**Aplicado:** la respuesta ahora incluye

```json
"fuente": {"odoo_respondio": true, "cobranza": "odoo",
           "litros": "sale.report", "degradado": false}
```

y el dashboard muestra un aviso ámbar —«Datos de respaldo»— explicando qué parte
salió de dónde cuando `degradado` es `true`.

**Aplicado:** el comentario quedó corregido. Decía que el respaldo local queda
«~$16.562 por debajo del real» porque la tabla de pagos «solo sincroniza
`is_reconciled=False`», y eso dejó de ser cierto cuando `changed_pagos` quitó
ese filtro (septiembre 2026). El respaldo local es bastante mejor de lo que el
comentario afirmaba, y decidir en base a la versión vieja llevaría a
sobreestimar cuánto se pierde con Odoo caído. La lectura en vivo se conserva
igual, porque `amount_ref` no está en el espejo.

### 4. ¿Los porcentajes y las variaciones se calculan sobre la misma base?

**No hay porcentajes ni variaciones.** El dashboard muestra acumulados absolutos
por período (hoy / mes / trimestre / año) y nada más: ni variación contra el
período anterior, ni porcentaje de cumplimiento, ni participación. La pregunta
del plan no tiene objeto acá.

Vale dejarlo escrito por dos motivos: para que la próxima revisión no vuelva a
buscarlos, y porque **si se agregan, el riesgo que el plan anticipa es real** —
`total_usd` (bruto) y `total_eq_bcv` (equivalente de Odoo) no son bases
comparables, así que un «% cobrado sobre vendido» calculado con esos dos campos
daría un número que parece significar algo y no significa nada.

## Un detalle de los acumulados que conviene saber

`_periodo_bounds` calcula los inicios de mes, trimestre y año **sobre
`date.today()` del servidor**, mientras que el filtro de fechas de la pantalla
(`fecha_desde` / `fecha_hasta`) se aplica antes, al armar los días. Así que si el
usuario filtra un rango pasado, las tarjetas de acumulado siguen midiendo desde
el inicio del mes/trimestre/año **actual** y quedan en cero o incompletas,
mientras las tablas por día sí muestran el rango pedido. No es un error de
cálculo, pero es una pantalla que se contradice consigo misma cuando se usa el
filtro.

**Aplicado:** los períodos se anclan a `fecha_hasta` cuando está puesto, y al
día del servidor cuando no. Una fecha ilegible cae al día del servidor en vez de
tumbar el reporte.

## Resumen

| # | Hallazgo | Sev. | Estado |
|---|---|---|---|
| D1 | «Ventas» es el bruto de lista; la etiqueta no decía de qué | Baja | **aplicado** |
| D2 | La pantalla decía «Total (Tasa BCV)» sobre el equivalente de **Odoo** | Media | **aplicado** |
| D3 | Un pago sin `amount_ref` sumaba cero a la cobranza | Media | **aplicado** |
| D4 | El modo degradado no se señalaba | Media | **aplicado** |
| D5 | Las tarjetas de acumulado ignoraban el filtro de fechas | Baja | **aplicado** |
| D6 | Un comentario del código afirmaba algo que dejó de ser cierto | Baja | **aplicado** |

Los seis quedaron aplicados, con `tests/test_dashboard_fuente_y_periodos.py`
fijando los tres comportamientos nuevos. Cinco de los seis son etiquetado o
señalización y no tocan ningún número. El D3 **sí cambia un número**, y solo
donde hoy vale cero para un pago que existe.

Lo que queda para tu decisión es si «Ventas» debería mostrar además el neto
teórico, que es lo que hace comparable esa tarjeta contra la cartera. Es una
tarjeta nueva, no un arreglo.

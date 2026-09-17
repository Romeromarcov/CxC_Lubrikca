# 7 — El incidente del 17-sep: una factura consolidada tumbaba el ciclo del demonio

El plan (Fases 0–6) cerró el 12-sep-2026. Esto pasó después del despliegue, en
producción real, y se encontró leyendo los logs de Railway al revisar el estado del
servidor — no en una medición contra la copia de QA.

## Lo que decían los logs

Cada ciclo del demonio (cada cinco minutos), dos pasos fallaban con el mismo error:

```
Error sincronizando aplicaciones de Odoo: (psycopg.errors.ForeignKeyViolation) insert
or update on table "vinculaciones" violates foreign key constraint
"vinculaciones_so_id_fkey"
DETAIL:  Key (so_id)=(S00718, S00700) is not present in table "ordenes_venta".

Error re-sincronizando Vinculaciones con Odoo: (psycopg.errors.ForeignKeyViolation) ...
```

`"S00718, S00700"` no es una orden: son **dos** nombres de orden, separados por
`", "`. Ninguna orden se llama así, así que el `INSERT` violaba la restricción, y como
las dos escrituras van en lote, la fila mala se llevaba puesta a las demás del mismo
ciclo — o, en `_sincronizar_aplicaciones_conciliadas`, cortaba el resto del `for` antes
de que las aplicaciones siguientes (alfabéticamente después de esa) llegaran a
procesarse. Esto corría cada cinco minutos desde que ese pago entró en el lote.

## La causa

Cuando Odoo arma UNA factura consolidando VARIAS órdenes de venta (una sola factura
para varios pedidos del mismo cliente), el campo `invoice_origin` de esa factura no
nombra una orden: nombra todas, separadas por `", "`. Es un dato real de Odoo, no un
error de captura.

Dos lugares leían ese campo como si fuera un `so_id` único y lo escribían tal cual en
`Vinculacion.so_id` (que sí tiene clave foránea contra `ordenes_venta`):

1. `OdooXmlRpcReader.aplicaciones_conciliadas()` (`odoo/client.py`) — alimenta
   `_sincronizar_aplicaciones_conciliadas`.
2. `get_live_pagos_conciliados()` (`app.py`) — arma el conjunto `so_ids` de cada pago;
   con el nombre sin partir, ese conjunto tenía **un** elemento (el string completo),
   así que `_resincronizar_vinculaciones_con_odoo` lo trataba como el caso «una sola
   orden, sin ambigüedad» en vez del caso «ambiguo entre varias» que ya existía
   (`discrepancia_multi_orden`, que solo audita y no escribe).

## Lo que no se hizo, a propósito

Repartir el monto de una factura consolidada entre las órdenes que la componen es una
decisión de negocio (¿por línea? ¿por peso del subtotal de cada orden?) que este
arreglo no toma. `so_ids_de_invoice_origin()` separa los nombres; cuando hay más de
uno, la factura se **excluye** de la sincronización automática y queda avisada — no se
inventa un reparto. Es la misma regla que ya rige el resto del plan: "no sé" no es
"cero", y acá tampoco es "adivino".

## El arreglo, en dos capas

**La causa.** `so_ids_de_invoice_origin()` (nuevo, en `odoo/client.py`) separa los
nombres de `invoice_origin`. `aplicaciones_conciliadas()` excluye las facturas
multi-orden (con un `logger.warning` que las nombra); `get_live_pagos_conciliados()`
expande el conjunto `so_ids` de cada pago, así que una factura consolidada cae en la
rama `discrepancia_multi_orden` que ya existía — auditada, nunca escrita.

**La red.** Aunque la causa ya está tapada, `_sincronizar_aplicaciones_conciliadas`
escribía cada aplicación con `repo.update_vinculacion(...)` sin ningún `try` adentro
del `for`: cualquier error de base en una fila cortaba el procesamiento de las que
venían después en la misma corrida. Ahora esa escritura está protegida; una fila que
la base rechace por cualquier motivo (una FK, lo que sea) queda en
`aplicacion_no_escrita_error_inesperado` — la vigilancia diaria ya lo lee — y las
demás se procesan igual. Es la misma lección del 12-sep (`docs/blindaje/2.4-
modularizar.md`, sección 33), aplicada a un tercer tipo de fallo: ya cubríamos "viola
una invariante" y "no hay tasa"; esto cubre "la base la rechaza por cualquier otra
razón".

## Lo que NO se tocó, y por qué

`Factura.so_id` (la tabla espejo, no `vinculaciones`) también se llena con
`invoice_origin` sin partir (`map_factura_espejo`, `odoo/client.py`), y esa columna no
tiene clave foránea — no crashea nada. Para una factura consolidada, hoy queda
invisible en cualquier lectura "por orden" (saldos, Ventas, reportes históricos, todo
lo que hace `WHERE so_id = ...`). Es un problema real pero silencioso, no uno que
tumbe el sistema, y corregirlo bien exige la misma decisión de negocio que el punto de
arriba evita tomar: cómo repartir el total de una factura entre varias órdenes.
**Queda pendiente, con esta nota, para cuando se decida esa regla — no es urgente
porque no rompe nada, y apurar una decisión de reparto sin la regla de negocio sería
peor que dejarlo como está.**

## Lo que no se pudo medir

No hay acceso de este lado a la base de producción (el CLI de Railway instalado en
esta máquina — v5.30.1 — no pudo tunelizar ni listar variables sin degradar la
respuesta a texto plano de vuelta, y actualizarlo requiere `npm`, no disponible acá) ni
a las credenciales de Odoo de producción. El diagnóstico y el arreglo se hicieron
enteramente a partir de los logs de Railway (que sí incluían los parámetros exactos de
la fila que fallaba) y probados contra la suite hermética y el banco de escenarios de
QA. No se pudo medir **cuántas** facturas consolidadas existen en producción ni desde
cuándo. Recomendado: correr, una vez desplegado, `vigilancia_diaria.py`, que ahora
reporta `aplicacion_no_escrita_error_inesperado` y `vinculacion_discrepancia_multi_
orden` si queda algo pendiente de revisar a mano.

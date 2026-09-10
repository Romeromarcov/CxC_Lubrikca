# Banco de escenarios de error humano (Fase 3 del blindaje)

Las 31 filas de la tabla de la Fase 3, cada una como una prueba que se puede
volver a correr cada vez que se toque el motor o el sync. Es la diferencia
entre haber probado una vez y quedar protegido.

```bash
./scripts/escenarios.sh                                # las 39
./scripts/escenarios.sh escenarios/test_humo.py        # solo la fontanería
./scripts/escenarios.sh escenarios/test_ordenes.py     # una familia
./scripts/escenarios.sh -k cancelan                    # una fila
```

No es parte de la suite normal: escribe en un Odoo de prueba por la red y
tarda minutos. Vive fuera de `testpaths` a propósito, y `scripts/escenarios.sh`
desactiva la cobertura, que acá no significa nada.

## Antes de correrlo

1. `.env.qa` con las credenciales del Odoo de prueba (copiar de
   `.env.qa.example`).
2. El espejo `cxc_qa` creado y sincronizado:
   ```bash
   python scripts/qa_entorno.py crear
   python scripts/qa_entorno.py sync
   ```

## La seguridad, que acá no es un detalle

**El Odoo de prueba comparte la imprenta fiscal con producción.** El conector
`account.digital.invoicing.conn` apunta al proveedor real
(`thefactoryhka.com.ve`) y emitió documentos aprobados el 09-sep-2026, con
números de control y URL pública de consulta. Emitir un documento fiscal es
irreversible y sale de la secuencia de la empresa.

El banco no puede emitir ninguno, por tres barreras independientes:

| Barrera | Qué impide |
|---|---|
| **Diario `ZZPRU`** | Las facturas del banco van por un diario de ventas creado sin `invoicing_digital_conn`. El único diario real de la base, `INV`, sí lo tiene. |
| **Ubicación `ZZ PRUEBAS BLINDAJE clientes`** | Las notas de entrega **también** se emiten. Lo que decide es `is_digital_invoicing` de la ubicación de DESTINO — no el campo homónimo del picking, que es calculado y sigue leyendo `True` aunque no se emita nada. La ubicación del banco lo tiene apagado. |
| **Canario** | `account.digital.ctrl.number` se cuenta antes y después de **cada** escenario. Si creció, la corrida falla en el acto con `EmisionFiscalDetectada`. Es la barrera que no depende de que yo entienda bien las otras dos. |

Y dos más, de entorno: `ODOO_URL` tiene que llevar `.dev.odoo.com`, y
`DATABASE_URL` tiene que ser local y distinta de `cxc_ci` (la base de CI, que
los escenarios modificarían).

## Lo que costó aprender del Odoo

Seis cosas que no son obvias, todas encodadas en `odoo_qa.py` con su comentario:

1. **Cancelar una orden abre un asistente que puede mandar correo al cliente.**
   Se usa `action_cancel`, nunca `action_send_mail`.
2. **Un cliente con RIF `J-` queda marcado como agente de retención**, y eso
   dispara el comprobante de retención de IVA, que en esta base se rompe con un
   error de SQL (`COALESCE types character varying and jsonb cannot be
   matched`) e impide postear. Los clientes del banco van sin identificación
   fiscal — lo que además los hace inelegibles para emisión.
3. **La entrega es de tres pasos** (PICK → PACK → OUT) y `delivery_status` no
   llega a `full` hasta validar los tres. El paso OUT exige vehículo, y el
   vehículo exige conductor: de los cuatro de la base, uno no tiene.
4. **Los productos no tienen existencias**, así que el picking queda en
   `confirmed` y Odoo se niega a validarlo. Hay que registrar las cantidades a
   mano (`quantity` + `picked`).
5. **176 de los 249 productos vendibles llevan lote**, y validar su entrega
   exige dar un número de lote. El banco trabaja con los 8 que tienen precio
   fijo en la lista vigente y no llevan lote.
6. **`_create_invoices` es privado** y no se puede llamar por XML-RPC; la vía
   pública es el asistente `sale.advance.payment.inv`.

Y una del propio sistema: **`ENGINE_LISTA_USD=4` / `ENGINE_LISTA_BCV=5`
apuntan a listas archivadas.** Las vigentes son otras (10 a 19). El banco
resuelve las listas por el mapeo unificado, igual que el motor, y hay un test
de humo que lo fija.

## Cómo está armado

| Archivo | Qué es |
|---|---|
| `odoo_qa.py` | El único lugar que ESCRIBE en Odoo, con las barreras. |
| `fabrica.py` | Los cuatro estados de partida: pedida, entregada, facturada, pagada. |
| `sistema.py` | Sync, motor, y los cuatro reportes por sus endpoints reales. |
| `conftest.py` | Fixtures y las barreras de entorno. |
| `test_humo.py` | Que la fontanería ande, antes de creerle a un escenario. |
| `test_ordenes.py` | 8 filas, las tres primeras son «la orden cambia después de la entrega». |
| `test_pagos.py` | 6 filas. El hilo común es el equivalente congelado. |
| `test_entregas.py` | 4 filas. La CxC nace con la entrega. |
| `test_devoluciones.py` | 5 filas. Entregado contra facturado, en los dos sentidos. |
| `test_facturacion.py` | 4 filas. S00573 y el detector de doble facturación. |
| `test_catalogo.py` | 4 filas. Lo que cambia la base sobre la que TODAS se valoran. |

**Cada escenario arma su propia orden** en vez de mutar una copiada de
producción. Cuesta unos segundos más y compra dos cosas: el banco es
reejecutable —correrlo dos veces da lo mismo, que es la propiedad que la Fase 4
le exige al sync— y los hallazgos son inequívocos, porque si algo sale mal
salió mal por lo que el escenario hizo.

## Qué asertan, y qué no

Las pruebas leen del **espejo y de las tablas del motor** (`ventas_teoricos`,
`bandeja_facturacion`), no de los cuatro reportes completos. No es pereza: el
reporte de saldos tarda ~10 minutos con los cachés de Odoo fríos porque
resuelve precios producto por producto, y 31 escenarios × 10 minutos no es un
banco que alguien vaya a correr. `test_humo.py` sí verifica que los cuatro
reportes contesten, y los escenarios de catálogo consultan el balance cuando la
fila lo pide.

Cada prueba dice **qué debería pasar**, no lo que pasa. Cuando lo que pasa es
otra cosa, la prueba falla y ese fallo ES el hallazgo — con el mensaje
explicando por qué importa, no solo qué número no calzó.

## Lo que deja atrás

Todo lo que el banco crea lleva el prefijo `ZZ BLINDAJE` (clientes) o `ZZPRU`
(facturas), y la ubicación y el diario de pruebas se reusan entre corridas. Las
órdenes quedan: Odoo no permite borrar una orden confirmada ni una factura
posteada, y forzarlo sería peor que dejarlas. En un entorno efímero eso está
bien; para encontrarlas:

```sql
SELECT so_id, estado_orden FROM ordenes_venta
WHERE cliente_id IN (SELECT cliente_id FROM clientes WHERE nombre LIKE 'ZZ BLINDAJE%');
```

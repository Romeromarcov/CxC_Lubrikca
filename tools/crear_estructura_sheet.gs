/**
 * Crear estructura del Sheet — Sistema de Cobros y Conciliación Lubrikca (CxC)
 * ---------------------------------------------------------------------------
 * Cómo usar:
 *   1. Abre el Google Sheet destino.
 *   2. Extensiones → Apps Script.
 *   3. Pega este archivo completo, guarda.
 *   4. Ejecuta la función  crearEstructuraCxC  (autoriza permisos la 1ª vez).
 *
 * Qué hace (idempotente — puedes correrlo varias veces):
 *   - Crea cada pestaña que falte con sus cabeceras EXACTAS (las que espera el
 *     backend en src/cxc/sheets/serde.py). Si la pestaña ya existe, solo
 *     asegura la fila de cabecera.
 *   - Congela y pone en negrita la fila 1.
 *   - Carga datos semilla en las tablas de configuración (Descuentos, Reglas,
 *     MetodosPago, Feriados) con valores REALES del Odoo QA. REVÍSALOS.
 *   - Crea una pestaña MAPEO_ODOO con la trazabilidad campo Sheet → campo Odoo.
 *
 * Encabezados = claves del serializador del backend. No los cambies.
 */

function crearEstructuraCxC() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();

  var TABLAS = {
    'Clientes': ['cliente_id', 'nombre', 'vendedor_email'],
    'OrdenesVenta': ['so_id', 'cliente_id', 'fecha', 'fecha_entrega', 'monto_total',
      'lista_precios', 'vendedor_email', 'es_primera_compra', 'facturada',
      'factura_id', 'monto_facturado'],
    'LineasOrden': ['linea_id', 'so_id', 'producto', 'marca', 'categoria',
      'cantidad', 'precio_unitario'],
    'Pagos': ['pago_id', 'cliente_id', 'monto', 'moneda', 'metodo_pago',
      'fecha_pago', 'vendedor_email'],
    'MetodosPago': ['metodo_id', 'nombre', 'moneda', 'tipo_tasa', 'es_contado'],
    'SerieTasas': ['timestamp', 'tasa_bcv', 'tasa_binance', 'fuente',
      'es_heredada', 'capturada_ok'],
    'DescuentosMarcaCategoria': ['regla_id', 'marca', 'categoria', 'tipo_descuento',
      'porcentaje', 'vigencia_desde', 'vigencia_hasta', 'activo'],
    'DescuentoBCVCompleto': ['vigencia_desde', 'porcentaje', 'vigencia_hasta',
      'activo'],
    'PromocionPrimeraCompra': ['producto', 'vigencia_desde', 'vigencia_hasta',
      'activo'],
    'ReglasRecurrencia': ['condicion', 'tipo_beneficio', 'valor', 'vigencia_desde',
      'vigencia_hasta', 'activo'],
    'Feriados': ['fecha', 'descripcion', 'tipo'],
    'Vinculaciones': ['vinc_id', 'pago_id', 'so_id', 'monto_aplicado',
      'hora_pago_confirmada', 'tasa_bcv_aplicada', 'tasa_binance_aplicada',
      'es_tasa_heredada', 'equiv_usd_bcv', 'equiv_usd_binance', 'equiv_ves_bcv',
      'equiv_ves_binance', 'confirmado_por', 'timestamp_registro', 'estado',
      'moneda_abono', 'tipo_tasa_abono'],
    'BandejaFacturacion': ['so_id', 'lista_aplicada', 'precio_base_calculado',
      'descuentos_detalle', 'total_descuentos', 'ncs_calculadas', 'total_motor',
      'requiere_revision', 'candidata_a_cierre', 'aprobado_por', 'estado'],
    'Conciliacion': ['so_id', 'total_motor', 'monto_odoo', 'ncs_odoo', 'diferencia',
      'resultado', 'revisado_por'],
    '_Meta': ['key', 'value']
  };

  Object.keys(TABLAS).forEach(function (nombre) {
    asegurarHoja(ss, nombre, TABLAS[nombre]);
  });

  sembrarConfig(ss);
  crearMapeo(ss);

  SpreadsheetApp.getUi().alert('Estructura CxC creada/actualizada. Revisa MetodosPago y DescuentosMarcaCategoria.');
}

/** Crea la hoja si falta y garantiza la fila de cabecera. */
function asegurarHoja(ss, nombre, headers) {
  var sh = ss.getSheetByName(nombre);
  if (!sh) sh = ss.insertSheet(nombre);
  var rango = sh.getRange(1, 1, 1, headers.length);
  rango.setValues([headers]);
  rango.setFontWeight('bold').setBackground('#e8eaed');
  sh.setFrozenRows(1);
  sh.autoResizeColumns(1, headers.length);
  return sh;
}

/** Carga semillas SOLO si la tabla está vacía (no pisa datos existentes). */
function sembrarConfig(ss) {
  sembrarSiVacia(ss, 'MetodosPago', [
    // metodo_id = id del DIARIO (journal) de Odoo. Catalogo informativo:
    // el metodo NO determina contado ni ruta de tasa. El contado lo decide la
    // fecha del pago (ventana) y la ruta BCV/Binance se estampa por abono en la
    // Vinculacion. Las columnas moneda/tipo_tasa/es_contado quedan de referencia.
    ['29', 'Efectivo moneda extranjera (USD)', 'USD', 'N_A', 'TRUE'],
    ['15', 'Cash (efectivo Bs)', 'VES', 'BCV', 'TRUE'],
    ['14', 'Bank', 'VES', 'BCV', 'TRUE'],
    ['30', 'Banco Bancamiga 7806', 'VES', 'BCV', 'TRUE'],
    ['31', 'Banco Nacional de Credito', 'VES', 'BCV', 'TRUE'],
    ['32', 'Banco Banesco', 'VES', 'BCV', 'TRUE'],
    ['33', 'Banco Provincial', 'VES', 'BCV', 'TRUE']
  ]);

  sembrarSiVacia(ss, 'DescuentosMarcaCategoria', [
    // marca = product.brand.name ; categoria = nivel del árbol categ_id.
    // OJO: hoy brand_id está casi vacío en Odoo (ver blocker #1). Ajusta cuando
    // se normalice. '*' = comodín.
    ['D1', 'Global Oil', 'Comercial', 'contado', '0.08', '2026-01-01', '', 'TRUE'],
    ['D2', 'Global Oil', 'Industrial', 'contado', '0.06', '2026-01-01', '', 'TRUE'],
    ['D3', 'Sinoco', '*', 'contado', '0.03', '2026-01-01', '', 'TRUE']
  ]);

  // Descuento BCV-completo: la GERENCIA fija el % cada dia. El motor aplica
  // min(%, diferencial real BCV vs Binance). Actualiza/agrega una fila por
  // periodo de vigencia (deja vigencia_hasta vacio mientras rija).
  sembrarSiVacia(ss, 'DescuentoBCVCompleto', [
    ['2026-01-01', '0.10', '', 'TRUE']
  ]);

  // Recurrencia: solo recompra (porcentaje configurable). La primera compra
  // ya NO va aqui: su NC = precio del producto-promo (ver PromocionPrimeraCompra).
  sembrarSiVacia(ss, 'ReglasRecurrencia', [
    ['recompra', 'porcentaje', '0.03', '2026-01-01', '', 'TRUE']
  ]);

  // Producto de regalo por primera compra (ej. caja de liga de frenos). Pon el
  // ID/codigo del producto en Odoo y su periodo de vigencia. La NC toma el
  // precio de ese producto en la lista de nacimiento de la SO.
  sembrarSiVacia(ss, 'PromocionPrimeraCompra', [
    ['PONER_ID_PRODUCTO_LIGA', '2026-01-01', '', 'TRUE']
  ]);

  sembrarSiVacia(ss, 'Feriados', [
    ['2026-01-01', 'Año Nuevo', 'nacional'],
    ['2026-05-01', 'Día del Trabajador', 'nacional'],
    ['2026-07-05', 'Día de la Independencia', 'nacional'],
    ['2026-07-24', 'Natalicio de Bolívar', 'nacional']
  ]);

  // _Meta arranca sin cursor (full load en la primera corrida del sync).
}

function sembrarSiVacia(ss, nombre, filas) {
  var sh = ss.getSheetByName(nombre);
  if (!sh) return;
  if (sh.getLastRow() > 1) return; // ya tiene datos
  sh.getRange(2, 1, filas.length, filas[0].length).setValues(filas);
}

/** Pestaña de trazabilidad Sheet -> Odoo (referencia para el equipo). */
function crearMapeo(ss) {
  var sh = asegurarHoja(ss, 'MAPEO_ODOO',
    ['tabla_sheet', 'columna_sheet', 'modelo_odoo', 'campo_odoo', 'nota']);
  if (sh.getLastRow() > 1) return;
  var m = [
    ['Clientes', 'cliente_id', 'res.partner', 'id', ''],
    ['Clientes', 'nombre', 'res.partner', 'name', ''],
    ['Clientes', 'vendedor_email', 'res.partner', 'user_id.login', 'Vendedor = login del usuario (8.1)'],
    ['OrdenesVenta', 'so_id', 'sale.order', 'name', 'p.ej. S00553 (no el id numérico)'],
    ['OrdenesVenta', 'cliente_id', 'sale.order', 'partner_id', ''],
    ['OrdenesVenta', 'fecha', 'sale.order', 'date_order', ''],
    ['OrdenesVenta', 'fecha_entrega', 'stock.picking', 'date_done', 'Despacho saliente del SO (commitment_date suele venir vacío)'],
    ['OrdenesVenta', 'monto_total', 'sale.order', 'amount_total', ''],
    ['OrdenesVenta', 'lista_precios', 'sale.order', 'pricelist_id', 'id 4 Lista USD / id 5 Precio USD Pago VES'],
    ['OrdenesVenta', 'vendedor_email', 'sale.order', 'user_id.login', ''],
    ['OrdenesVenta', 'es_primera_compra', '(computado)', '-', 'Contar SO previas del partner'],
    ['OrdenesVenta', 'facturada', 'sale.order', 'invoice_status', "invoiced => TRUE"],
    ['OrdenesVenta', 'factura_id', 'account.move', 'id', 'move ligada por invoice_origin'],
    ['OrdenesVenta', 'monto_facturado', 'account.move', 'amount_total', 'OJO: la factura está en VES'],
    ['LineasOrden', 'linea_id', 'sale.order.line', 'id', ''],
    ['LineasOrden', 'so_id', 'sale.order.line', 'order_id', ''],
    ['LineasOrden', 'producto', 'sale.order.line', 'product_id', ''],
    ['LineasOrden', 'marca', 'product.product', 'brand_id', 'product.brand (blocker #1: casi vacío)'],
    ['LineasOrden', 'categoria', 'product.product', 'categ_id', 'Árbol Comercial/Industrial/...'],
    ['LineasOrden', 'cantidad', 'sale.order.line', 'product_uom_qty', ''],
    ['LineasOrden', 'precio_unitario', 'sale.order.line', 'price_unit', ''],
    ['Pagos', 'pago_id', 'account.payment', 'id', ''],
    ['Pagos', 'cliente_id', 'account.payment', 'partner_id', ''],
    ['Pagos', 'monto', 'account.payment', 'amount', ''],
    ['Pagos', 'moneda', 'account.payment', 'currency_id', 'USD (id 1) / VES (id 166)'],
    ['Pagos', 'metodo_pago', 'account.payment', 'journal_id', 'id del diario => MetodosPago.metodo_id'],
    ['Pagos', 'fecha_pago', 'account.payment', 'date', ''],
    ['Pagos', 'vendedor_email', 'account.payment', 'partner_id.user_id.login', ''],
    ['Conciliacion', 'monto_odoo', 'account.move', 'amount_total', 'move_type out_invoice, ligada por invoice_origin'],
    ['Conciliacion', 'ncs_odoo', 'account.move', 'amount_total', 'move_type out_refund (notas de crédito)']
  ];
  sh.getRange(2, 1, m.length, m[0].length).setValues(m);
  sh.autoResizeColumns(1, 5);
}

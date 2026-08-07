"""Esquema PostgreSQL — reemplazo de las 23 hojas de Google Sheets.

Tablas definidas con SQLAlchemy Core (no ORM): tipadas, componibles, sin
sesiones ni mapeo automático objeto-fila -- las dataclasses de ``cxc.models``
siguen siendo el contrato de datos; ``PostgresRepository``
(``cxc.db.postgres_repository``) es quien traduce entre fila y dataclass,
igual que ``cxc.sheets.serde`` lo hace hoy para Sheets.

Convenciones:
    - Dinero/cantidades: ``NUMERIC(18, 4)``. Tasas de cambio (más precisión
      relativa): ``NUMERIC(14, 6)``. Porcentajes de descuento, siempre
      fraccionarios (0.05, nunca 5): ``NUMERIC(7, 4)``.
    - Enumerados de ``cxc.models`` -> ENUM nativo de Postgres, mismos
      valores literales (incluye el mixed-case ``TipoTasa.BINANCE="Binance"``).
    - Fechas -> ``DATE``; timestamps -> ``TIMESTAMP`` SIN zona horaria
      (``DateTime(timezone=False)``), a propósito: todo el resto del código
      (lecturas de Odoo XML-RPC, ``datetime.now()``) usa datetimes naive en
      hora local (America/Caracas); si esta capa devolviera datetimes
      tz-aware, cualquier comparación naive-vs-aware existente rompería con
      ``TypeError``. El valor se guarda y se lee tal cual, sin conversión.
    - Cadena vacía en Sheets == ``NULL`` aquí para columnas opcionales
      (``factura_id``, ``aprobado_por``, ``revisado_por``, etc.).
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

MONEY = Numeric(18, 4)
RATE = Numeric(14, 6)
PCT = Numeric(7, 4)

# --- Enums nativos (mismos valores que los StrEnum de cxc.models) -----------
moneda_enum = Enum("USD", "VES", name="moneda")
tipo_tasa_enum = Enum("BCV", "Binance", "N_A", name="tipo_tasa")
condicion_enum = Enum("primera_compra", "recompra", name="condicion")
tipo_beneficio_enum = Enum("nota_credito", "porcentaje", name="tipo_beneficio_regla")
tipo_feriado_enum = Enum("nacional", "regional", "bancario", name="tipo_feriado")
estado_vinculacion_enum = Enum(
    "pendiente", "aprobado", "facturado", "conciliado", name="estado_vinculacion"
)
estado_bandeja_enum = Enum("calculado", "aprobado", "facturado", name="estado_bandeja")
resultado_conciliacion_enum = Enum("verde", "amarillo", "rojo", name="resultado_conciliacion")

# --- 3.1 Clientes (espejo, sync-owned) ---------------------------------------
clientes = Table(
    "clientes",
    metadata,
    Column("cliente_id", String, primary_key=True),
    Column("nombre", String, nullable=False),
    Column("vendedor_email", String, nullable=False, server_default=""),
    Column("wh_iva_agent", Boolean, nullable=False, server_default="false"),
    Column("wh_iva_rate", Numeric(7, 4), nullable=False, server_default="75.0"),
)

# --- 3.2 OrdenesVenta (espejo, sync-owned) -----------------------------------
ordenes_venta = Table(
    "ordenes_venta",
    metadata,
    Column("so_id", String, primary_key=True),
    Column("cliente_id", String, ForeignKey("clientes.cliente_id"), nullable=False, index=True),
    Column("fecha", Date, nullable=False),
    Column("fecha_entrega", Date, nullable=True),
    Column("monto_total", MONEY, nullable=False),
    Column("lista_precios", String, nullable=False, server_default=""),
    Column("vendedor_email", String, nullable=False, server_default=""),
    Column("es_primera_compra", Boolean, nullable=False, server_default="false"),
    Column("facturada", Boolean, nullable=False, server_default="false"),
    Column("factura_id", String, nullable=True),
    Column("monto_facturado", MONEY, nullable=True),
    Column("estado_orden", String, nullable=False, server_default="sale"),
    Column("estado_entrega", String, nullable=False, server_default=""),
    Column("entregada_completa", Boolean, nullable=False, server_default="false"),
    Column("tiene_devolucion", Boolean, nullable=False, server_default="false"),
    Column("dias_credito", Integer, nullable=False, server_default="0"),
)

# --- 3.3 LineasOrden (espejo, sync-owned) ------------------------------------
lineas_orden = Table(
    "lineas_orden",
    metadata,
    Column("linea_id", String, primary_key=True),
    Column("so_id", String, ForeignKey("ordenes_venta.so_id"), nullable=False, index=True),
    Column("producto", String, nullable=False, server_default=""),
    Column("marca", String, nullable=False, server_default=""),
    Column("categoria", String, nullable=False, server_default=""),
    Column("cantidad", MONEY, nullable=False),
    Column("precio_unitario", MONEY, nullable=False),
    Column("cantidad_entregada", MONEY, nullable=False, server_default="0"),
    Column("descuento", MONEY, nullable=False, server_default="0"),
)

# --- 3.4 Pagos (espejo, sync-owned + columnas humanas de cobranza) ----------
# Las columnas recibido/numero_recibido/fecha_recibido/recibido_por las
# escribe /api/cobranza/marcar-recibido (humano); el sync (upsert_pagos)
# nunca las toca -- en SQL esto se garantiza simplemente no incluyendo esas
# columnas en el UPDATE/INSERT del sync, no hace falta partir la tabla.
pagos = Table(
    "pagos",
    metadata,
    Column("pago_id", String, primary_key=True),
    Column("cliente_id", String, ForeignKey("clientes.cliente_id"), nullable=False, index=True),
    Column("monto", MONEY, nullable=False),
    Column("moneda", moneda_enum, nullable=False),
    Column("metodo_pago", String, nullable=False, server_default=""),
    Column("fecha_pago", DateTime(timezone=False), nullable=False),
    Column("vendedor_email", String, nullable=False, server_default=""),
    Column("recibido", Boolean, nullable=False, server_default="false"),
    Column("numero_recibido", String, nullable=True),
    Column("fecha_recibido", DateTime(timezone=False), nullable=True),
    Column("recibido_por", String, nullable=True),
)

# --- 3.5 MetodosPago (catálogo) ----------------------------------------------
metodos_pago = Table(
    "metodos_pago",
    metadata,
    Column("metodo_id", String, primary_key=True),
    Column("nombre", String, nullable=False),
    Column("moneda", moneda_enum, nullable=False),
    Column("tipo_tasa", tipo_tasa_enum, nullable=False),
    Column("es_contado", Boolean, nullable=False, server_default="false"),
)

# --- 3.6 SerieTasas (auditoría inmutable, append-only) -----------------------
# El rol de la app no tiene UPDATE/DELETE sobre esta tabla (REVOKE explícito
# en la migración Alembic) -- refuerza en SQL lo que hoy es solo convención
# de código ("el sync NUNCA la toca").
serie_tasas = Table(
    "serie_tasas",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("timestamp", DateTime(timezone=False), nullable=False, index=True),
    Column("tasa_bcv", RATE, nullable=False),
    Column("tasa_binance", RATE, nullable=False),
    Column("fuente", String, nullable=False, server_default=""),
    Column("es_heredada", Boolean, nullable=False, server_default="false"),
    Column("capturada_ok", Boolean, nullable=False, server_default="true"),
    Column("tasa_binance_manana", RATE, nullable=True),
    Column("tasa_binance_tarde", RATE, nullable=True),
    Column("tasa_binance_diario", RATE, nullable=True),
    Column("diferencial_bcv_binance_pct", PCT, nullable=True),
    Column("tasa_bcv_euro", RATE, nullable=True),
)

# --- 3.7 DescuentosProntoPago (config; alias histórico DescuentosMarcaCategoria) ---
descuentos_pronto_pago = Table(
    "descuentos_pronto_pago",
    metadata,
    Column("regla_id", String, primary_key=True),
    Column("marca", String, nullable=False, server_default="*"),
    Column("categoria", String, nullable=False, server_default="*"),
    Column("min_cantidad", MONEY, nullable=False, server_default="0"),
    Column("max_cantidad", MONEY, nullable=False, server_default="999999"),
    Column("unidad_medida", String, nullable=False, server_default="USD"),
    Column("tipo_beneficio", String, nullable=False, server_default="descuento"),
    Column("dias_gracia", Integer, nullable=False, server_default="3"),
    Column("porcentaje", PCT, nullable=False, server_default="0.05"),
    Column("monedas_aplicables", String, nullable=False, server_default="*"),
    Column("listas_aplicables", String, nullable=False, server_default="*"),
    Column("vigencia_desde", Date, nullable=False),
    Column("vigencia_hasta", Date, nullable=True),
    Column("activo", Boolean, nullable=False, server_default="true"),
    Column("tipo_descuento", String, nullable=False, server_default="contado"),
    Column("requiere_pago_previo", Boolean, nullable=False, server_default="true"),
    Column("aplica_a", String, nullable=False, server_default="linea"),
    Column("descripcion", Text, nullable=False, server_default=""),
)

# --- 3.7_vol DescuentosVolumen (config) --------------------------------------
descuentos_volumen = Table(
    "descuentos_volumen",
    metadata,
    Column("regla_id", String, primary_key=True),
    Column("marca", String, nullable=False, server_default="*"),
    Column("categoria", String, nullable=False, server_default="*"),
    Column("litros_minimo", MONEY, nullable=False, server_default="0"),
    Column("porcentaje", PCT, nullable=False, server_default="0.05"),
    Column("min_cantidad", MONEY, nullable=False, server_default="0"),
    Column("max_cantidad", MONEY, nullable=False, server_default="999999"),
    Column("unidad_medida", String, nullable=False, server_default="UNIDADES"),
    Column("tipo_beneficio", String, nullable=False, server_default="descuento"),
    Column("tipo_evaluacion", String, nullable=False, server_default="orden"),
    Column("dias_evaluacion", Integer, nullable=False, server_default="30"),
    Column("vigencia_desde", Date, nullable=False),
    Column("vigencia_hasta", Date, nullable=True),
    Column("listas_aplicables", String, nullable=False, server_default="*"),
    Column("activo", Boolean, nullable=False, server_default="true"),
    Column("requiere_pago_previo", Boolean, nullable=False, server_default="false"),
    Column("aplica_a", String, nullable=False, server_default="linea"),
    Column("descripcion", Text, nullable=False, server_default=""),
)

# --- 3.7b DescuentoBCVCompleto (config, effective dating, sin id) -----------
descuento_bcv_completo = Table(
    "descuento_bcv_completo",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("vigencia_desde", Date, nullable=False, unique=True),
    Column("porcentaje", PCT, nullable=False),
    Column("vigencia_hasta", Date, nullable=True),
    Column("activo", Boolean, nullable=False, server_default="true"),
    Column("requiere_pago_previo", Boolean, nullable=False, server_default="true"),
    Column("aplica_a", String, nullable=False, server_default="linea"),
    Column("descripcion", Text, nullable=False, server_default=""),
)

# --- 3.7c PromocionPrimeraCompra (config) ------------------------------------
promocion_primera_compra = Table(
    "promocion_primera_compra",
    metadata,
    Column("regla_id", String, primary_key=True),
    Column("tipo_beneficio", String, nullable=False, server_default="producto"),
    Column("productos", String, nullable=False, server_default=""),
    Column("valor", MONEY, nullable=False, server_default="1"),
    Column("compra_minima", MONEY, nullable=False, server_default="3"),
    Column("regalo_tipo", String, nullable=False, server_default="solo_uno"),
    Column("vigencia_desde", Date, nullable=False),
    Column("vigencia_hasta", Date, nullable=True),
    Column("descuento_fallback", PCT, nullable=False, server_default="0.02"),
    Column("categorias_aplica", String, nullable=False, server_default="Comercial"),
    Column("marca", String, nullable=False, server_default="GLOBAL OIL"),
    Column("categoria", String, nullable=False, server_default="CAJA"),
    Column("min_cantidad", MONEY, nullable=False, server_default="3"),
    Column("max_cantidad", MONEY, nullable=False, server_default="999999"),
    Column("unidad_medida", String, nullable=False, server_default="CAJAS"),
    Column("listas_aplicables", String, nullable=False, server_default="*"),
    Column("solo_primera_compra", Boolean, nullable=False, server_default="false"),
    Column("activo", Boolean, nullable=False, server_default="true"),
    Column("requiere_pago_previo", Boolean, nullable=False, server_default="false"),
    Column("aplica_a", String, nullable=False, server_default="linea"),
    Column("descripcion", Text, nullable=False, server_default=""),
)

# --- 3.7d DescuentoRecompra (config) -----------------------------------------
descuentos_recompra = Table(
    "descuentos_recompra",
    metadata,
    Column("regla_id", String, primary_key=True),
    Column("marca", String, nullable=False, server_default="GLOBAL OIL"),
    Column("categoria", String, nullable=False, server_default="CAJA"),
    Column("min_cajas", Integer, nullable=False, server_default="2"),
    Column("max_cajas", Integer, nullable=False, server_default="4"),
    Column("min_cantidad", MONEY, nullable=False, server_default="2"),
    Column("max_cantidad", MONEY, nullable=False, server_default="4"),
    Column("unidad_medida", String, nullable=False, server_default="CAJAS"),
    Column("tipo_beneficio", String, nullable=False, server_default="descuento"),
    Column("porcentaje", PCT, nullable=False, server_default="0.03"),
    Column("max_usos_mes", Integer, nullable=False, server_default="1"),
    Column("dias_ventana", Integer, nullable=False, server_default="30"),
    Column("listas_aplicables", String, nullable=False, server_default="*"),
    Column("vigencia_desde", Date, nullable=False),
    Column("vigencia_hasta", Date, nullable=True),
    Column("activo", Boolean, nullable=False, server_default="true"),
    Column("requiere_pago_previo", Boolean, nullable=False, server_default="false"),
    Column("aplica_a", String, nullable=False, server_default="linea"),
    Column("descripcion", Text, nullable=False, server_default=""),
    Column("dias_gracia", Integer, nullable=False, server_default="3"),
)

# --- 3.7e DescuentoProducto (config) -----------------------------------------
descuentos_producto = Table(
    "descuentos_producto",
    metadata,
    Column("regla_id", String, primary_key=True),
    Column("productos", String, nullable=False, server_default="*"),
    Column("marca", String, nullable=False, server_default="*"),
    Column("categoria", String, nullable=False, server_default="*"),
    Column("min_cantidad", MONEY, nullable=False, server_default="0"),
    Column("max_cantidad", MONEY, nullable=False, server_default="999999"),
    Column("unidad_medida", String, nullable=False, server_default="CAJAS"),
    Column("tipo_beneficio", String, nullable=False, server_default="descuento"),
    Column("porcentaje", PCT, nullable=False, server_default="0.05"),
    Column("monedas_aplicables", String, nullable=False, server_default="*"),
    Column("listas_aplicables", String, nullable=False, server_default="*"),
    Column("vigencia_desde", Date, nullable=False),
    Column("vigencia_hasta", Date, nullable=True),
    Column("activo", Boolean, nullable=False, server_default="true"),
    Column("requiere_pago_previo", Boolean, nullable=False, server_default="false"),
    Column("aplica_a", String, nullable=False, server_default="linea"),
    Column("descripcion", Text, nullable=False, server_default=""),
)

# --- 3.7f DescuentoDiferencialCambiario (config) -----------------------------
descuentos_diferencial_cambiario = Table(
    "descuentos_diferencial_cambiario",
    metadata,
    Column("regla_id", String, primary_key=True),
    Column("nombre", String, nullable=False, server_default=""),
    Column("tipo_diferencial", String, nullable=False, server_default="fijo_35_ves_usd"),
    Column("tipo_calculo", String, nullable=False, server_default="fijo"),
    Column("porcentaje_fijo", PCT, nullable=False, server_default="0.35"),
    Column("marca", String, nullable=False, server_default="*"),
    Column("categoria", String, nullable=False, server_default="*"),
    Column("min_cantidad", MONEY, nullable=False, server_default="0"),
    Column("max_cantidad", MONEY, nullable=False, server_default="999999"),
    Column("unidad_medida", String, nullable=False, server_default="USD"),
    Column("tipo_beneficio", String, nullable=False, server_default="descuento"),
    Column("monedas_aplicables", String, nullable=False, server_default="*"),
    Column("listas_aplicables", String, nullable=False, server_default="*"),
    Column("vigencia_desde", Date, nullable=False),
    Column("vigencia_hasta", Date, nullable=True),
    Column("activo", Boolean, nullable=False, server_default="true"),
    Column("requiere_pago_previo", Boolean, nullable=False, server_default="true"),
    Column("aplica_a", String, nullable=False, server_default="linea"),
    Column("descripcion", Text, nullable=False, server_default=""),
)

# --- 3.8 ReglasRecurrencia (config, effective dating, sin id natural) -------
reglas_recurrencia = Table(
    "reglas_recurrencia",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("condicion", condicion_enum, nullable=False),
    Column("tipo_beneficio", tipo_beneficio_enum, nullable=False),
    Column("valor", MONEY, nullable=False),
    Column("vigencia_desde", Date, nullable=False),
    Column("vigencia_hasta", Date, nullable=True),
    Column("activo", Boolean, nullable=False, server_default="true"),
    Column("requiere_pago_previo", Boolean, nullable=False, server_default="false"),
    Column("aplica_a", String, nullable=False, server_default="linea"),
    Column("descripcion", Text, nullable=False, server_default=""),
)

# --- 3.8b Feriados (config) ---------------------------------------------------
feriados = Table(
    "feriados",
    metadata,
    Column("fecha", Date, primary_key=True),
    Column("descripcion", String, nullable=False, server_default=""),
    Column("tipo", tipo_feriado_enum, nullable=False),
)

# --- 3.9 Vinculaciones (trabajo humano; el sync NUNCA la toca) --------------
vinculaciones = Table(
    "vinculaciones",
    metadata,
    Column("vinc_id", String, primary_key=True),
    Column("pago_id", String, ForeignKey("pagos.pago_id"), nullable=False, index=True),
    Column("so_id", String, ForeignKey("ordenes_venta.so_id"), nullable=False, index=True),
    Column("monto_aplicado", MONEY, nullable=False),
    Column("hora_pago_confirmada", DateTime(timezone=False), nullable=False),
    Column("tasa_bcv_aplicada", RATE, nullable=False),
    Column("tasa_binance_aplicada", RATE, nullable=False),
    Column("es_tasa_heredada", Boolean, nullable=False, server_default="false"),
    # Equivalentes congelados -- calculados UNA vez, nunca recalculados.
    Column("equiv_usd_bcv", MONEY, nullable=True),
    Column("equiv_usd_binance", MONEY, nullable=True),
    Column("equiv_ves_bcv", MONEY, nullable=True),
    Column("equiv_ves_binance", MONEY, nullable=True),
    Column("confirmado_por", String, nullable=False, server_default=""),
    Column("timestamp_registro", DateTime(timezone=False), nullable=True),
    Column(
        "estado",
        estado_vinculacion_enum,
        nullable=False,
        server_default="pendiente",
    ),
    Column("moneda_abono", moneda_enum, nullable=False, server_default="VES"),
    Column("tipo_tasa_abono", tipo_tasa_enum, nullable=False, server_default="N_A"),
    Column("bcv_variante", String, nullable=False, server_default="USD"),
)

# --- 3.10 BandejaFacturacion (salida del motor + trabajo humano) -----------
bandeja_facturacion = Table(
    "bandeja_facturacion",
    metadata,
    Column("so_id", String, ForeignKey("ordenes_venta.so_id"), primary_key=True),
    Column("lista_aplicada", String, nullable=False, server_default=""),
    Column("precio_base_calculado", MONEY, nullable=False),
    Column("total_descuentos", MONEY, nullable=False, server_default="0"),
    Column("ncs_calculadas", MONEY, nullable=False, server_default="0"),
    Column("total_motor", MONEY, nullable=False, server_default="0"),
    Column("requiere_revision", Boolean, nullable=False, server_default="false"),
    Column("candidata_a_cierre", Boolean, nullable=False, server_default="false"),
    Column("aprobado_por", String, nullable=True),
    Column("estado", estado_bandeja_enum, nullable=False, server_default="calculado"),
    # Tarea 3 (rediseño de Ventas) -- equivalentes/teóricos por lista.
    Column("equivalente_lista_usd", MONEY, nullable=False, server_default="0"),
    Column("teorico_lista_ves", MONEY, nullable=False, server_default="0"),
    Column("teorico_lista_usd", MONEY, nullable=False, server_default="0"),
    Column("descuentos_teorico_ves", MONEY, nullable=False, server_default="0"),
    Column("descuentos_teorico_usd", MONEY, nullable=False, server_default="0"),
)

# --- VentasTeoricos (Fase 10) -----------------------------------------------
# El teórico VES/USD de una orden es el punto de comparación FIJO contra el
# que se audita lo real (venta/factura) -- por eso vive en su propia tabla
# del módulo Ventas, NO en BandejaFacturacion (que es la bandeja de trabajo
# de Facturación: se recalcula constantemente y explícitamente SALTA
# órdenes ya facturadas -- EngineRunner.run_all -- así que sus teóricos
# quedaban ausentes justo para las órdenes donde más se necesita comparar).
# Se calcula UNA VEZ por orden (nueva orden que llega, o backfill) y NO se
# recalcula salvo que algún producto de la orden haya usado el precio de
# fallback de la ficha (no encontrado en la lista VES/USD específica) --
# ahí sí puede cambiar si luego se completa esa lista de precios.
ventas_teoricos = Table(
    "ventas_teoricos",
    metadata,
    Column("so_id", String, ForeignKey("ordenes_venta.so_id"), primary_key=True),
    Column("teorico_ves", MONEY, nullable=False, server_default="0"),
    Column("teorico_usd", MONEY, nullable=False, server_default="0"),
    Column("descuentos_teorico_ves", MONEY, nullable=False, server_default="0"),
    Column("descuentos_teorico_usd", MONEY, nullable=False, server_default="0"),
    Column("lista_ves_id", String, nullable=False, server_default=""),
    Column("lista_usd_id", String, nullable=False, server_default=""),
    # True si algún producto de la orden NO tenía precio fijo en la lista
    # VES/USD específica y se resolvió por fallback (otra pricelist
    # configurada, o el precio de venta $ de la ficha) -- señal de que este
    # teórico debe re-verificarse cuando esa lista se complete.
    Column("usa_fallback_ves", Boolean, nullable=False, server_default="false"),
    Column("usa_fallback_usd", Boolean, nullable=False, server_default="false"),
    Column("calculado_en", DateTime(timezone=False), nullable=False),
)

# Hija normalizada de BandejaFacturacion.descuentos_detalle (antes un blob
# JSON en una celda de Sheets) -- DescuentoAplicado ya es un dataclass propio.
descuento_aplicado = Table(
    "descuento_aplicado",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "so_id",
        String,
        ForeignKey("bandeja_facturacion.so_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("origen", String, nullable=False),
    Column("descripcion", String, nullable=False, server_default=""),
    Column("monto", MONEY, nullable=False),
)

# --- 3.11 Conciliacion (computada) -------------------------------------------
conciliacion = Table(
    "conciliacion",
    metadata,
    Column("so_id", String, ForeignKey("ordenes_venta.so_id"), primary_key=True),
    Column("total_motor", MONEY, nullable=False),
    Column("monto_odoo", MONEY, nullable=False),
    Column("ncs_odoo", MONEY, nullable=False),
    Column("diferencia", MONEY, nullable=False),
    Column("resultado", resultado_conciliacion_enum, nullable=False),
    Column("revisado_por", String, nullable=True),
)

# --- 3.12 Exclusiones (pares de descuentos mutuamente excluyentes) ----------
# El par se trata como no ordenado en el código de aplicación (se busca (a,b)
# y (b,a) antes de upsertar); se mantiene esa misma semántica aquí en vez de
# forzar un orden canónico, para no tocar la lógica que la consume.
exclusiones = Table(
    "exclusiones",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("regla_tipo_a", String, nullable=False),
    Column("regla_tipo_b", String, nullable=False),
    Column("activo", Boolean, nullable=False, server_default="true"),
    UniqueConstraint("regla_tipo_a", "regla_tipo_b", name="uq_exclusiones_par"),
)

# --- _Meta (cursor de sync + config global, key/value) -----------------------
app_settings = Table(
    "app_settings",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", Text, nullable=False, server_default=""),
)

# --- UsuariosPlataforma (roles del panel web) --------------------------------
# Mismas columnas que la pestaña Sheets "UsuariosPlataforma" (ver cxc.auth):
# nombre == nombre_odoo, + salt/activo/fecha_registro, necesarias para
# verificar_password() y para desactivar un usuario sin borrar su historial.
usuarios_plataforma = Table(
    "usuarios_plataforma",
    metadata,
    Column("email", String, primary_key=True),
    Column("nombre", String, nullable=False, server_default=""),
    Column("rol", String, nullable=False, server_default=""),
    Column("password_hash", String, nullable=True),
    Column("salt", String, nullable=True),
    Column("activo", Boolean, nullable=False, server_default="true"),
    Column("fecha_registro", String, nullable=False, server_default=""),
)

# --- BandejaAuditoria (log de discrepancias motor vs. Odoo) ------------------
bandeja_auditoria = Table(
    "bandeja_auditoria",
    metadata,
    Column("audit_id", String, primary_key=True),
    Column("so_id", String, nullable=False, index=True),
    Column("tipo_auditoria", String, nullable=False, server_default=""),
    Column("motor_calcula_usd", MONEY, nullable=True),
    Column("odoo_registrado_usd", MONEY, nullable=True),
    Column("diferencia_usd", MONEY, nullable=True),
    Column("detalle_odoo", Text, nullable=False, server_default=""),
    Column("detalle_motor", Text, nullable=False, server_default=""),
    Column("estado", String, nullable=False, server_default=""),
    Column("revisado_por", String, nullable=True),
    Column("timestamp_audit", DateTime(timezone=False), nullable=False),
)

# --- AnomaliasAceptadas (waivers de discrepancias de facturación) ----------
anomalias_aceptadas = Table(
    "anomalias_aceptadas",
    metadata,
    Column("anomalia_id", String, primary_key=True),
    Column("so_id", String, nullable=False, index=True),
    Column("factura_id", String, nullable=False, server_default=""),
    Column("tipo_anomalia", String, nullable=False, server_default=""),
    Column("motivo_aceptacion", String, nullable=False, server_default=""),
    Column("aprobado_por", String, nullable=False, server_default=""),
    Column("timestamp_aprobacion", DateTime(timezone=False), nullable=False),
)

# --- TasasHistoricasAuditoria (archivo diario BCV/Binance -- una fila por
# día, regenerada por scripts/cargar_tasas_historicas.py desde Odoo +
# SerieTasas; la usa get_binance_rate_for_date() para convertir pagos VES a
# USD con la tasa del día EXACTO del pago). Columnas reales de Sheets --
# diferencial_bcv_binance_pct y notas quedan como texto libre (vienen con
# "%" y comentarios humanos, no son valores numéricos limpios). -----------
tasas_historicas_auditoria = Table(
    "tasas_historicas_auditoria",
    metadata,
    Column("fecha", Date, primary_key=True),
    Column("tasa_bcv_usd", RATE, nullable=True),
    Column("tasa_bcv_euro", RATE, nullable=True),
    Column("tasa_binance_promedio_diario", RATE, nullable=True),
    Column("diferencial_bcv_binance_pct", String, nullable=True),
    Column("fuente", String, nullable=False, server_default=""),
    Column("notas", String, nullable=False, server_default=""),
)

# --- ListasPreciosHistoricas (precios de catálogo de referencia antes de un
# corte -- mantenida a mano, la lee /api/reporte-saldos y /api/auditoria para
# comparar contra el precio vigente) -----------------------------------------
listas_precios_historicas = Table(
    "listas_precios_historicas",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("codigo", String, nullable=False, index=True),
    Column("producto_nombre", String, nullable=False, server_default=""),
    Column("precio_usd", MONEY, nullable=False, server_default="0"),
    Column("precio_bcv_euro", MONEY, nullable=False, server_default="0"),
    # Crosswalk resuelto (código interno de Lubrikca -> product.product.id
    # de Odoo): ``codigo`` es la numeración propia de la lista histórica
    # (CSV origen), NO el default_code de Odoo ni el product_id -- y
    # ``LineaOrden.producto`` (usado en tiempo de ejecución por
    # ``_precio_unitario_linea``) es el product_id de Odoo. Sin esta
    # columna el override histórico NUNCA hace match contra una orden real
    # (bug real encontrado en auditoría, agosto 2026 -- ver
    # scripts/cruzar_codigos_lista_historica.py). Null = sin match
    # resuelto todavía.
    Column("producto_id_odoo", Integer, nullable=True, index=True),
)

# --- PagosHuerfanosCerrados (marca local -- pago sin orden abierta del
# cliente, cerrado "a favor de la empresa" por un humano; no crea asiento
# contable ni toca Odoo, solo deja de aparecer en sugerencias) -------------
pagos_huerfanos_cerrados = Table(
    "pagos_huerfanos_cerrados",
    metadata,
    Column("pago_id", String, primary_key=True),
    Column("motivo", String, nullable=False, server_default=""),
    Column("cerrado_por", String, nullable=False, server_default=""),
    Column("timestamp_cierre", DateTime(timezone=False), nullable=False),
)

# --- DescuentosSistemaAprobados (descuento aprobado manualmente desde la
# Bandeja 1 de Facturación -- NUNCA se escribe a Odoo, solo ajusta los
# saldos internos de CxC en /api/ventas. Un registro activo por orden;
# "activo=false" revoca sin perder el historial de aprobaciones previas) ---
descuentos_sistema_aprobados = Table(
    "descuentos_sistema_aprobados",
    metadata,
    Column("so_id", String, ForeignKey("ordenes_venta.so_id"), primary_key=True),
    Column("monto", MONEY, nullable=False, server_default="0"),
    Column("motivo", String, nullable=False, server_default=""),
    Column("aprobado_por", String, nullable=False, server_default=""),
    Column("timestamp_aprobacion", DateTime(timezone=False), nullable=False),
    Column("activo", Boolean, nullable=False, server_default="true"),
)

# --- ReglasDiasCreditoVolumen (config; alerta, NO alimenta la formula de ----
# recompra) -- rangos de volumen (litros) de la orden -> maximo de dias de
# credito que Odoo debería haber otorgado. Se usa SOLO para comparar contra
# el plazo de pago real (payment_term de la orden en Odoo) y generar una
# alerta en Ventas si Odoo otorgó más días de los que correspondían -- la
# ventana de recompra (dias de gracia) sigue usando el plazo REAL de pago de
# la orden anterior, no este máximo tabulado.
reglas_dias_credito_volumen = Table(
    "reglas_dias_credito_volumen",
    metadata,
    Column("regla_id", String, primary_key=True),
    Column("litros_minimo", MONEY, nullable=False, server_default="0"),
    # NULL = sin tope superior (ej. "501 en adelante").
    Column("litros_maximo", MONEY, nullable=True),
    Column("dias_credito_max", Integer, nullable=False),
    Column("descripcion", Text, nullable=False, server_default=""),
    Column("activo", Boolean, nullable=False, server_default="true"),
)

# --- FechasHistoricas (fecha histórica alterna por orden) -------------------
fechas_historicas = Table(
    "fechas_historicas",
    metadata,
    Column("so_id", String, primary_key=True),
    Column("fecha_historica", Date, nullable=False),
)

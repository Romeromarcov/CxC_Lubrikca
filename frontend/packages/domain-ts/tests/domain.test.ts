import { describe, expect, it } from "vitest";
import {
  BANCOS_SUDEBAN,
  clasificarErrorRed,
  clasificarResultadoPush,
  describirResolucionConflicto,
  detectarDuplicadosContactoOffline,
  evaluarPoliticaComercialOffline,
  generarUuidV7,
  rutaDeLista,
  sugerirVinculacionFifo,
  validarCompatibilidadPagoConPedido,
  validarDestinoPagoOffline,
  validarNoMezclaRutasDocumento,
  type ClienteCartera,
  type ContactoMaestroResumen,
  type CuentaReceptora,
  type CuentaTercero,
  type ListaPrecio,
} from "../src/index";

const cuentasPropias: CuentaReceptora[] = [
  {
    id: 1,
    cuenta_id: 1,
    nombre: "Banco Provincial Principal",
    tipo_canal: "banco",
    banco_codigo_sudeban: "0108",
    banco_nombre: "Banco Provincial",
    numero_cuenta: "01080098761234567890",
    titular_nombre: "LUBRIKCA C.A.",
    titular_rif: "J-40123456-7",
    telefono: "04141112233",
    email_zelle: "",
    binance_id: "",
    moneda: "VES",
    diario_odoo_id: 11,
    diario_odoo_codigo: "PROV",
    diario_odoo_nombre: "Banco Provincial",
    activa: true,
    es_propia: true,
    actualizado_en: "2026-11-22T00:00:00",
  },
  {
    id: 2,
    cuenta_id: 2,
    nombre: "Binance Lubrikca",
    tipo_canal: "binance",
    banco_codigo_sudeban: "",
    banco_nombre: "Binance",
    numero_cuenta: "",
    titular_nombre: "LUBRIKCA C.A.",
    titular_rif: "",
    telefono: "",
    email_zelle: "",
    binance_id: "BIN-9988",
    moneda: "USD",
    diario_odoo_id: 35,
    diario_odoo_codigo: "BIN",
    diario_odoo_nombre: "Binance USDT",
    activa: true,
    es_propia: true,
    actualizado_en: "2026-11-22T00:00:00",
  },
];

const cuentasTerceros: CuentaTercero[] = [
  {
    id: 5,
    cuenta_tercero_id: 5,
    titular: "ALIADO ZELLE MIAMI LLC",
    email: "aliado@zelle.test",
    moneda: "USD",
    activa: true,
  },
];

describe("SUDEBAN y validador offline de destino", () => {
  it("rechaza transferencia 0108 Provincial hacia 0104 Venezolano de Crédito", () => {
    expect(BANCOS_SUDEBAN["0104"]).toBe("Venezolano de Crédito");
    const res = validarDestinoPagoOffline(
      {
        banco_origen: "Banco Provincial",
        banco_destino: "Venezolano de Crédito",
        cuenta_destino: "01040011223344556677",
      },
      cuentasPropias,
      cuentasTerceros,
    );
    expect(res.valido).toBe(false);
    expect(res.bloqueante).toBe(true);
    expect(res.estado).toBe("DESTINO_NO_PERTENECE_LUBRIKCA");
    expect(res.limpiar_diario).toBe(true);
    expect(res.diario_odoo_id).toBeNull();
  });

  it("valida cuenta receptora propia exacta de Lubrikca", () => {
    const res = validarDestinoPagoOffline(
      {
        banco_origen: "Banesco",
        banco_destino: "Banco Provincial",
        cuenta_destino: "01080098761234567890",
        rif_destino: "J-40123456-7",
      },
      cuentasPropias,
    );
    expect(res.valido).toBe(true);
    expect(res.bloqueante).toBe(false);
    expect(res.estado).toBe("VALIDADO_LUBRIKCA");
    expect(res.diario_odoo_id).toBe(11);
  });

  it("permite Zelle de tercero autorizado y efectivo exento", () => {
    const z = validarDestinoPagoOffline(
      { es_zelle_tercero: true, cuenta_tercero_id: 5, metodo_pago: "Zelle" },
      cuentasPropias,
      cuentasTerceros,
    );
    expect(z.valido).toBe(true);
    expect(z.estado).toBe("EXENTO_ZELLE_TERCERO");

    const zInactivo = validarDestinoPagoOffline(
      { es_zelle_tercero: true, cuenta_tercero_id: 999, metodo_pago: "Zelle" },
      cuentasPropias,
      cuentasTerceros,
    );
    expect(zInactivo.valido).toBe(false);
    expect(zInactivo.bloqueante).toBe(true);
  });
});

describe("Rutas BCV vs Binance", () => {
  const listas: ListaPrecio[] = [
    { lista_id: "21", nombre: "Pago VES Nov 2026", moneda: "VES", ruta_pago: "bcv" },
    { lista_id: "22", nombre: "Pago USD Nov 2026", moneda: "USD", ruta_pago: "binance" },
  ];

  it("resuelve rutas por lista y bloquea mezclas en documento o pago", () => {
    expect(rutaDeLista("21", listas)).toBe("bcv");
    expect(rutaDeLista("22", listas)).toBe("binance");

    const mezcla = validarNoMezclaRutasDocumento(
      "bcv",
      [{ lista_id: "21" }, { lista_id: "22" }],
      listas,
    );
    expect(mezcla.valido).toBe(false);

    expect(validarCompatibilidadPagoConPedido("binance", "VES", "Transferencia BCV").valido).toBe(false);
    expect(validarCompatibilidadPagoConPedido("bcv", "USD", "Binance Pay").valido).toBe(false);
    expect(validarCompatibilidadPagoConPedido("binance", "USD", "Binance Pay").valido).toBe(true);
  });
});

describe("Política comercial y FIFO por ruta", () => {
  const cli: ClienteCartera = {
    cliente_id: "197",
    partner_odoo_id: 197,
    cliente_nombre: "1080 ZIP MARKET",
    vendedor_email: "vendedor@lubrikca.com",
    saldo_deudor_usd: 400,
    saldo_favor_usd: 0,
    saldo_bcv_usd: 250,
    saldo_binance_usd: 150,
    ventas_bcv_usd: 300,
    pagos_bcv_usd: 50,
    ventas_binance_usd: 150,
    pagos_binance_usd: 0,
    max_dias_mora: 12,
    tramos: { vigente: 250, "1_30": 150 },
    politica_credito: {
      autosend_activo: true,
      limite_credito_usd: 500,
      limite_facturas_pendientes: 4,
      retener_si_vencidas: true,
      dias_mora_max: 5,
    },
    ordenes_pendientes: [
      {
        so_id: "SO-BCV-01",
        fecha: "2026-10-01",
        fecha_vencimiento: "2026-10-08",
        dias_credito: 7,
        ruta_pago: "bcv",
        monto_total_usd: 200,
        saldo_pendiente_usd: 100,
      },
      {
        so_id: "SO-BIN-01",
        fecha: "2026-10-02",
        fecha_vencimiento: "2026-10-02",
        dias_credito: 0,
        ruta_pago: "binance",
        monto_total_usd: 150,
        saldo_pendiente_usd: 150,
      },
      {
        so_id: "SO-BCV-02",
        fecha: "2026-10-15",
        fecha_vencimiento: "2026-10-22",
        dias_credito: 7,
        ruta_pago: "bcv",
        monto_total_usd: 150,
        saldo_pendiente_usd: 150,
      },
    ],
  };

  it("retiene pedidos cuando supera mora o límite de crédito", () => {
    const res = evaluarPoliticaComercialOffline({
      tipoDocumento: "pedido",
      montoPedidoUsd: 200,
      diasCredito: 7,
      cliente: cli,
    });
    expect(res.requiereAprobacion).toBe(true);
    expect(res.estadoSugerido).toBe("PENDIENTE_APROBACION");
    expect(res.alertas.length).toBeGreaterThanOrEqual(2);
  });

  it("sugiere vinculación FIFO sin cruzar ruta BCV con Binance", () => {
    const fifoBcv = sugerirVinculacionFifo(160, "bcv", cli.ordenes_pendientes ?? []);
    expect(fifoBcv.asignaciones.map((a) => a.so_id)).toEqual(["SO-BCV-01", "SO-BCV-02"]);
    expect(fifoBcv.asignaciones[0]?.monto_asignado_usd).toBe(100);
    expect(fifoBcv.asignaciones[1]?.monto_asignado_usd).toBe(60);
    expect(fifoBcv.remanente_a_favor_usd).toBe(0);
  });
});

describe("Anti-duplicados de contactos y Outbox anti-loop", () => {
  const existentes: ContactoMaestroResumen[] = [
    {
      contacto_id: 1,
      odoo_partner_id: 197,
      rol: "cliente",
      nombre: "LUBRICANTES EL REY, C.A.",
      nombre_normalizado: "lubricantes el rey",
      rif: "J-30112233-4",
      rif_normalizado: "J301122334",
      telefono: "04141234567",
      vendedor_email: "vendedor@lubrikca.com",
      actualizado_en: "2026-11-22T00:00:00",
    },
  ];

  it("detecta RIF idéntico antes de encolar contacto offline", () => {
    const dups = detectarDuplicadosContactoOffline(
      { nombre: "EL REY SA", rif: "J30112233-4", telefono: "04129990000" },
      existentes,
    );
    expect(dups).toHaveLength(1);
    expect(dups[0]?.motivo).toBe("RIF_IDENTICO");
    expect(dups[0]?.bloqueante).toBe(true);
  });

  it("genera UUIDv7 válidos y detiene loops ante 400/403/404/409/422", () => {
    const u1 = generarUuidV7(1732300000000);
    const u2 = generarUuidV7(1732300000000);
    expect(u1).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(u2 > u1).toBe(true);

    for (const status of [400, 403, 404, 409, 422]) {
      const cls = clasificarResultadoPush(
        {
          idempotency_key: u1,
          tipo: "pago",
          http_status: status,
          estado: "CONFLICTO_NEGOCIO",
          codigo_negocio: status === 422 ? "DESTINO_NO_PERTENECE_LUBRIKCA" : "POSIBLE_DUPLICADO",
          reintentable: false,
          error: "Rechazo de negocio",
        },
        0,
      );
      expect(cls.nuevoEstado).toBe("CONFLICT_USER_ACTION");
      expect(cls.reintentarAuto).toBe(false);
      expect(cls.esperaSegundos).toBeNull();
    }

    const r503 = clasificarErrorRed(new Error("timeout"), 1, 5);
    expect(r503.nuevoEstado).toBe("FAILED_RETRY");
    expect(r503.reintentarAuto).toBe(true);
    expect(r503.esperaSegundos).toBe(10);

    const rMax = clasificarErrorRed(new Error("timeout"), 4, 5);
    expect(rMax.reintentarAuto).toBe(false);

    expect(describirResolucionConflicto({ codigoError: "DESTINO_NO_PERTENECE_LUBRIKCA" }).escenario).toBe(
      "DESTINO_NO_LUBRIKCA",
    );
    expect(describirResolucionConflicto({ retenidoAprobacion: true }).escenario).toBe(
      "PENDIENTE_APROBACION",
    );
  });
});

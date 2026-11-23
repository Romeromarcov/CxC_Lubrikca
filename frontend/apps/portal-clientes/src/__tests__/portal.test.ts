import { describe, expect, it } from "vitest";
import type { CuentaReceptora } from "@lubrikca/domain-ts";

import {
  filtrarCuentasPorRutaPago,
  permisosPorRolPortal,
  prepararCarritoRecompra,
  validarPagoPortalAntesDeEnviar,
} from "../portalLogic";

const cuentasMixtas: CuentaReceptora[] = [
  {
    id: 1,
    cuenta_id: 1,
    nombre: "Bancamiga VE",
    tipo_canal: "banco",
    banco_codigo_sudeban: "0172",
    banco_nombre: "Bancamiga",
    numero_cuenta: "01720111223344556677",
    titular_nombre: "LUBRIKCA",
    titular_rif: "J-40123456-7",
    telefono: "04141112233",
    email_zelle: "",
    binance_id: "",
    moneda: "VES",
    diario_odoo_id: 15,
    diario_odoo_codigo: "BAMI",
    diario_odoo_nombre: "Bancamiga",
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
    titular_nombre: "LUBRIKCA",
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

describe("Portal Clientes — RBAC, Recompra y Filtrado por Ruta", () => {
  it("aplica permisos diferenciados para titular, compras y tesoreria", () => {
    expect(permisosPorRolPortal("titular").administrarEquipo).toBe(true);
    const compras = permisosPorRolPortal("compras");
    expect(compras.verCatalogoYCrearPedidos).toBe(true);
    expect(compras.registrarPagos).toBe(false);
    expect(compras.verEstadoCuenta).toBe(false);

    const tes = permisosPorRolPortal("tesoreria");
    expect(tes.verEstadoCuenta).toBe(true);
    expect(tes.registrarPagos).toBe(true);
    expect(tes.verCatalogoYCrearPedidos).toBe(false);
  });

  it("prepara carrito de recompra rápida desde último pedido", () => {
    const carrito = prepararCarritoRecompra({
      referencia: "S01313",
      lista_precios_id: "21",
      ruta_pago: "bcv",
      lineas: [{ producto_id: "1033", codigo: "0761", descripcion: "LIGA DOT3", cantidad: 5, precio_unitario: 24 }],
    });
    expect(carrito?.rutaPago).toBe("bcv");
    expect(carrito?.items).toHaveLength(1);
    expect(carrito?.items[0]?.cantidad).toBe(5);
  });

  it("filtra estrictamente cuentas por ruta y rechaza banco destino ajeno (0108->0104)", () => {
    const bcv = filtrarCuentasPorRutaPago(cuentasMixtas, "bcv");
    expect(bcv.map((c) => c.cuenta_id)).toEqual([1]);

    const bin = filtrarCuentasPorRutaPago(cuentasMixtas, "binance");
    expect(bin.map((c) => c.cuenta_id)).toEqual([2]);

    const invalido = validarPagoPortalAntesDeEnviar({
      ruta: "bcv",
      moneda: "VES",
      metodoPago: "Transferencia Bancaria",
      bancoOrigen: "Banco Provincial",
      bancoDestino: "Venezolano de Crédito",
      cuentaDestino: "01040011223344556677",
      cuentasRuta: bcv,
    });
    expect(invalido.valido).toBe(false);

    const rutaCruzada = validarPagoPortalAntesDeEnviar({
      ruta: "binance",
      moneda: "VES",
      metodoPago: "Transferencia Bancaria",
      bancoOrigen: "Banesco",
      bancoDestino: "Binance",
      cuentaDestino: "",
      cuentasRuta: bin,
    });
    expect(rutaCruzada.valido).toBe(false);
  });
});

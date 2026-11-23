import React, { useEffect, useState } from "react";
import type { CuentaReceptora, LubrikcaApiClient, RutaPago } from "@lubrikca/domain-ts";

import {
  calcularSha256Hex,
  filtrarCuentasPorRutaPago,
  validarArchivoComprobante,
  validarPagoPortalAntesDeEnviar,
} from "../portalLogic";

interface Props {
  api: LubrikcaApiClient;
}

export function RegistrarPagoView({ api }: Props): React.JSX.Element {
  const [ruta, setRuta] = useState<RutaPago>("bcv");
  const [cuentas, setCuentas] = useState<CuentaReceptora[]>([]);
  const [pedidoRef, setPedidoRef] = useState("");
  const [monto, setMonto] = useState("");
  const [moneda, setMoneda] = useState("VES");
  const [metodoPago, setMetodoPago] = useState("Transferencia Bancaria");
  const [referencia, setReferencia] = useState("");
  const [bancoOrigen, setBancoOrigen] = useState("");
  const [cuentaSelId, setCuentaSelId] = useState<number | null>(null);
  const [archivoComprobante, setArchivoComprobante] = useState<File | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [copiado, setCopiado] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    void api.portalCuentasPago(ruta).then((res) => {
      const lista = filtrarCuentasPorRutaPago((res.items as CuentaReceptora[]) || [], ruta);
      setCuentas(lista);
      setCuentaSelId(lista[0]?.cuenta_id ?? null);
    });
  }, [api, ruta]);

  const cuentaSel = cuentas.find((c) => c.cuenta_id === cuentaSelId) ?? null;

  function copiar(etiqueta: string, texto: string): void {
    if (!texto) return;
    void navigator.clipboard?.writeText(texto);
    setCopiado(`Copiado ${etiqueta}: ${texto}`);
    setTimeout(() => setCopiado(null), 2500);
  }

  async function enviarPago(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    setMsg(null);
    setErr(null);
    const val = validarPagoPortalAntesDeEnviar({
      ruta,
      moneda,
      metodoPago,
      bancoOrigen,
      bancoDestino: cuentaSel?.banco_nombre || cuentaSel?.nombre || "",
      cuentaDestino: cuentaSel?.numero_cuenta || "",
      rifDestino: cuentaSel?.titular_rif || "",
      telefonoDestino: cuentaSel?.telefono || "",
      emailDestino: cuentaSel?.email_zelle || "",
      cuentasRuta: cuentas,
    });
    if (!val.valido) {
      setErr(val.error || "La cuenta destino no pertenece a Lubrikca.");
      return;
    }
    setEnviando(true);
    try {
      let adjuntoMeta: { adjunto_id: number; storage_key: string } | null = null;
      if (archivoComprobante) {
        const valArch = validarArchivoComprobante(archivoComprobante);
        if (!valArch.valido) {
          setErr(valArch.error || "Comprobante inválido.");
          setEnviando(false);
          return;
        }
        const bytes = new Uint8Array(await archivoComprobante.arrayBuffer());
        const sha256 = await calcularSha256Hex(bytes);
        const subido = await api.subirAdjuntoBytes({
          bytes,
          mime: archivoComprobante.type,
          sha256,
          nombre_archivo: archivoComprobante.name,
          authMode: "portal",
        });
        adjuntoMeta = {
          adjunto_id: subido.adjunto_id,
          storage_key: subido.storage_key,
        };
      }
      const creado = await api.portalRegistrarPago({
        pedido_ref: pedidoRef.trim() || undefined,
        monto: Number(monto),
        moneda,
        metodo_pago: metodoPago,
        referencia: referencia.trim(),
        banco_origen: bancoOrigen.trim(),
        banco_destino: cuentaSel?.banco_nombre || cuentaSel?.nombre || "",
        cuenta_destino: cuentaSel?.numero_cuenta || "",
        rif_destino: cuentaSel?.titular_rif || "",
        telefono_destino: cuentaSel?.telefono || "",
        email_destino: cuentaSel?.email_zelle || "",
        diario_odoo_id: cuentaSel?.diario_odoo_id ?? undefined,
        comprobante_adjunto_id: adjuntoMeta?.adjunto_id,
        comprobante_storage_key: adjuntoMeta?.storage_key,
      });
      setMonto("");
      setReferencia("");
      setArchivoComprobante(null);
      setMsg(
        `Pago #${String(creado.numero || creado.documento_id)} registrado y enviado a revisión de Tesorería.`,
      );
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "No se pudo registrar el pago.");
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Registrar Pago hacia Cuentas de Lubrikca</h2>
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        {(["bcv", "binance"] as const).map((r) => (
          <button
            key={r}
            type="button"
            className={`btn btn-tab ${ruta === r ? "active" : ""}`}
            onClick={() => {
              setRuta(r);
              setMoneda(r === "binance" ? "USD" : "VES");
              setMetodoPago(r === "binance" ? "Binance Pay" : "Transferencia Bancaria");
            }}
          >
            {r === "bcv" ? "Ruta BCV (VES · Bancos VE)" : "Ruta Binance (USD · Binance/Zelle)"}
          </button>
        ))}
      </div>

      {copiado ? <div className="badge badge-ok" style={{ display: "block", marginBottom: 8 }}>{copiado}</div> : null}

      <div className="dual-grid">
        {cuentas.map((c) => {
          const sel = c.cuenta_id === cuentaSelId;
          return (
            <div
              key={c.cuenta_id}
              className="card"
              style={{ borderColor: sel ? "var(--primary)" : "var(--border)", borderWidth: sel ? 2 : 1 }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <strong>{c.nombre}</strong>
                <button type="button" className="btn btn-sm" onClick={() => setCuentaSelId(c.cuenta_id)}>
                  {sel ? "Seleccionada" : "Usar cuenta"}
                </button>
              </div>
              <div style={{ fontSize: "0.82rem", color: "var(--muted)", margin: "6px 0" }}>
                {c.banco_nombre} ({c.moneda}) · {c.titular_nombre}
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {c.numero_cuenta ? (
                  <button type="button" className="btn btn-sm" onClick={() => copiar("cuenta", c.numero_cuenta)}>
                    Copiar Cuenta
                  </button>
                ) : null}
                {c.titular_rif ? (
                  <button type="button" className="btn btn-sm" onClick={() => copiar("RIF", c.titular_rif)}>
                    Copiar RIF ({c.titular_rif})
                  </button>
                ) : null}
                {c.telefono ? (
                  <button type="button" className="btn btn-sm" onClick={() => copiar("Pago Móvil", c.telefono)}>
                    Copiar Móvil ({c.telefono})
                  </button>
                ) : null}
                {c.email_zelle || c.binance_id ? (
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => copiar("ID/Email", c.email_zelle || c.binance_id)}
                  >
                    Copiar ID/Email
                  </button>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>

      <form className="card" onSubmit={(e) => void enviarPago(e)}>
        <h3 style={{ marginTop: 0 }}>Datos de tu transferencia / depósito</h3>
        {msg ? <div className="badge badge-ok" style={{ display: "block", marginBottom: 8 }}>{msg}</div> : null}
        {err ? <div className="badge badge-err" style={{ display: "block", marginBottom: 8 }}>{err}</div> : null}

        <input
          className="input"
          placeholder="Pedido / Orden a abonar (ej. S01313, opcional)"
          value={pedidoRef}
          onChange={(e) => setPedidoRef(e.target.value)}
        />
        <input
          className="input"
          required
          type="number"
          step="0.01"
          placeholder={`Monto transferido (${moneda})`}
          value={monto}
          onChange={(e) => setMonto(e.target.value)}
        />
        <input
          className="input"
          required
          placeholder="Número de referencia bancaria"
          value={referencia}
          onChange={(e) => setReferencia(e.target.value)}
        />
        <input
          className="input"
          placeholder="Tu banco de origen (ej. Banesco / Provincial)"
          value={bancoOrigen}
          onChange={(e) => setBancoOrigen(e.target.value)}
        />
        <div style={{ margin: "8px 0 12px" }}>
          <label
            style={{
              display: "block",
              fontSize: "0.84rem",
              fontWeight: 600,
              marginBottom: 4,
            }}
          >
            Comprobante / captura (JPG, PNG, WEBP o PDF · máx. 10 MB)
          </label>
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp,application/pdf"
            onChange={(e) => {
              const f = e.target.files?.[0] ?? null;
              setErr(null);
              if (f) {
                const v = validarArchivoComprobante(f);
                if (!v.valido) {
                  setErr(v.error || "Archivo inválido.");
                  setArchivoComprobante(null);
                  return;
                }
              }
              setArchivoComprobante(f);
            }}
          />
          {archivoComprobante ? (
            <div style={{ marginTop: 6, fontSize: "0.82rem", display: "flex", gap: 8, alignItems: "center" }}>
              <span>
                Adjunto: <strong>{archivoComprobante.name}</strong> ({Math.ceil(archivoComprobante.size / 1024)} KB)
              </span>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => setArchivoComprobante(null)}
              >
                Quitar
              </button>
            </div>
          ) : null}
        </div>
        <button type="submit" className="btn btn-primary" disabled={enviando}>
          {enviando ? "Subiendo y enviando pago..." : "Reportar Pago a Lubrikca"}
        </button>
      </form>
    </div>
  );
}

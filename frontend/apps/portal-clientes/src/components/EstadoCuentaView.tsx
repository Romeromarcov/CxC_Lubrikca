import React, { useEffect, useState } from "react";
import type { EstadoCuentaPortalResponse, LubrikcaApiClient } from "@lubrikca/domain-ts";

interface Props {
  api: LubrikcaApiClient;
}

export function EstadoCuentaView({ api }: Props): React.JSX.Element {
  const [data, setData] = useState<EstadoCuentaPortalResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    void api
      .portalEstadoCuenta()
      .then(setData)
      .catch((e) => setErr(e instanceof Error ? e.message : "Error cargando estado de cuenta."));
  }, [api]);

  async function descargarCsv(): Promise<void> {
    const csv = await api.portalExportEstadoCuenta("csv");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "estado_cuenta_lubrikca.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  async function abrirHtmlImprimible(): Promise<void> {
    const html = await api.portalExportEstadoCuenta("html");
    const win = window.open("", "_blank");
    if (win) {
      win.document.write(html);
      win.document.close();
    }
  }

  async function verRecibo(docId: number): Promise<void> {
    const html = await api.portalDescargarReciboHtml(docId, "operativo");
    const win = window.open("", "_blank");
    if (win) {
      win.document.write(html);
      win.document.close();
    }
  }

  if (err) return <div className="card"><span className="badge badge-err">{err}</span></div>;
  if (!data) return <div className="card">Cargando estado de cuenta dual...</div>;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>Estado de Cuenta Dual · {data.cliente_nombre}</h2>
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" className="btn" onClick={() => void descargarCsv()}>
            Exportar Excel/CSV
          </button>
          <button type="button" className="btn" onClick={() => void abrirHtmlImprimible()}>
            Vista PDF / Imprimir
          </button>
        </div>
      </div>

      <div className="dual-grid">
        <div className="card card-bcv">
          <div style={{ fontSize: "0.8rem", fontWeight: 700, color: "var(--bcv)" }}>SALDO RUTA BCV (VES)</div>
          <div style={{ fontSize: "1.65rem", fontWeight: 800, margin: "4px 0" }}>
            ${data.ruta_bcv.saldo_usd.toFixed(2)} USD
          </div>
          <div style={{ fontSize: "0.82rem", color: "var(--muted)" }}>
            Ventas: ${data.ruta_bcv.ventas_usd.toFixed(2)} · Pagos: ${data.ruta_bcv.pagos_usd.toFixed(2)}
          </div>
        </div>

        <div className="card card-binance">
          <div style={{ fontSize: "0.8rem", fontWeight: 700, color: "var(--binance)" }}>SALDO RUTA BINANCE (USD)</div>
          <div style={{ fontSize: "1.65rem", fontWeight: 800, margin: "4px 0" }}>
            ${data.ruta_binance.saldo_usd.toFixed(2)} USD
          </div>
          <div style={{ fontSize: "0.82rem", color: "var(--muted)" }}>
            Ventas: ${data.ruta_binance.ventas_usd.toFixed(2)} · Pagos: ${data.ruta_binance.pagos_usd.toFixed(2)}
          </div>
        </div>
      </div>

      {data.pagos_en_revision.length > 0 ? (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Pagos Reportados en Revisión / Conciliación</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Número / Recibo</th>
                  <th>Fecha</th>
                  <th>Monto</th>
                  <th>Referencia</th>
                  <th>Estado</th>
                  <th>Recibo</th>
                </tr>
              </thead>
              <tbody>
                {data.pagos_en_revision.map((p) => (
                  <tr key={String(p.documento_id)}>
                    <td>{String(p.numero_recibo || p.numero || "")}</td>
                    <td>{String(p.fecha_documento || "—")}</td>
                    <td>{p.monto != null ? `${Number(p.monto).toFixed(2)} ${String(p.moneda || "")}` : "—"}</td>
                    <td>{String(p.referencia || "—")}</td>
                    <td><span className="badge badge-warn">{String(p.estado)}</span></td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={() => void verRecibo(Number(p.documento_id))}
                      >
                        Ver Recibo
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {[
        { title: "Movimientos Ruta BCV", movs: data.ruta_bcv.movimientos },
        { title: "Movimientos Ruta Binance", movs: data.ruta_binance.movimientos },
      ].map((blk) => (
        <div className="card" key={blk.title}>
          <h3 style={{ marginTop: 0 }}>{blk.title}</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Concepto</th>
                  <th>Referencia</th>
                  <th>Débito USD</th>
                  <th>Crédito USD</th>
                </tr>
              </thead>
              <tbody>
                {blk.movs.length === 0 ? (
                  <tr><td colSpan={5} style={{ color: "var(--muted)" }}>Sin movimientos registrados.</td></tr>
                ) : (
                  blk.movs.map((m, idx) => (
                    <tr key={idx}>
                      <td>{String(m.fecha || "")}</td>
                      <td>{String(m.etiqueta || m.tipo || "")}</td>
                      <td>{String(m.referencia || "")}</td>
                      <td>${Number(m.debito_usd || 0).toFixed(2)}</td>
                      <td>${Number(m.credito_usd || 0).toFixed(2)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}

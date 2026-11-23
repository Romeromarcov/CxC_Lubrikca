import React from "react";

interface Paso {
  codigo: string;
  titulo: string;
  completado: boolean;
}

export interface PedidoTrazabilidad {
  origen: string;
  documento_id: number | null;
  numero: string;
  odoo_order_name: string;
  tipo: string;
  estado: string;
  fecha: string | null;
  ruta_pago: string;
  total_usd: number;
  etapa_actual: string;
  pasos: Paso[];
}

interface Props {
  pedidos: PedidoTrazabilidad[];
}

export function TrazabilidadView({ pedidos }: Props): React.JSX.Element {
  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Trazabilidad de Pedidos y Entregas</h2>
      {pedidos.length === 0 ? (
        <div className="card">No hay cotizaciones ni pedidos registrados aún.</div>
      ) : (
        pedidos.map((p) => (
          <div key={`${p.origen}-${p.numero}`} className="card">
            <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
              <div>
                <strong>{p.odoo_order_name || p.numero}</strong>{" "}
                <span className="badge badge-muted">{p.ruta_pago.toUpperCase()}</span>
              </div>
              <div>
                <span className="badge badge-ok">{p.etapa_actual}</span> ·{" "}
                <strong>${p.total_usd.toFixed(2)} USD</strong>
              </div>
            </div>
            <div style={{ fontSize: "0.8rem", color: "var(--muted)", marginTop: 4 }}>
              Fecha: {p.fecha || "—"} · Origen: {p.origen.toUpperCase()} ({p.estado})
            </div>
            <div className="timeline">
              {p.pasos.map((s) => (
                <div key={s.codigo} className={`timeline-step ${s.completado ? "done" : "todo"}`}>
                  {s.completado ? "✓ " : "○ "}
                  {s.titulo}
                </div>
              ))}
            </div>
          </div>
        ))
      )}
    </div>
  );
}

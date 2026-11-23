import React, { useEffect, useState } from "react";
import {
  rutaDeLista,
  type ListaPrecio,
  type LubrikcaApiClient,
  type ProductoCatalogo,
} from "@lubrikca/domain-ts";

import { prepararCarritoRecompra, type ItemCarritoPortal, type RecompraPayload } from "../portalLogic";

interface Props {
  api: LubrikcaApiClient;
  recompra: RecompraPayload | null;
}

export function CatalogoPedidosView({ api, recompra }: Props): React.JSX.Element {
  const [listas, setListas] = useState<ListaPrecio[]>([]);
  const [items, setItems] = useState<ProductoCatalogo[]>([]);
  const [listaId, setListaId] = useState("");
  const [query, setQuery] = useState("");
  const [carrito, setCarrito] = useState<ItemCarritoPortal[]>([]);
  const [diasCredito, setDiasCredito] = useState("0");
  const [notas, setNotas] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    void api.portalCatalogo().then((res) => {
      const ls = (res.listas_precio as ListaPrecio[]) || [];
      setListas(ls);
      if (ls[0]) setListaId(String(ls[0].lista_id));
      setItems((res.items as ProductoCatalogo[]) || []);
    });
  }, [api]);

  function agregar(p: ProductoCatalogo): void {
    const precio = (listaId && p.precios_por_lista?.[listaId]) ?? p.precio_usd;
    setCarrito((prev) => {
      const ex = prev.find((x) => x.producto_id === p.producto_id);
      if (ex) {
        return prev.map((x) =>
          x.producto_id === p.producto_id ? { ...x, cantidad: x.cantidad + 1 } : x,
        );
      }
      return [
        ...prev,
        {
          producto_id: p.producto_id,
          codigo: p.codigo,
          descripcion: p.nombre,
          cantidad: 1,
          precio_unitario: Number(precio),
        },
      ];
    });
  }

  function repetirUltimoPedido(): void {
    const prep = prepararCarritoRecompra(recompra);
    if (!prep) {
      setErr("No hay un pedido previo con líneas disponibles para repetir.");
      return;
    }
    if (prep.listaPreciosId) setListaId(prep.listaPreciosId);
    setCarrito(prep.items);
    setMsg(`Cargadas ${prep.items.length} línea(s) de tu último pedido (${recompra?.referencia || ""}).`);
    setErr(null);
  }

  async function enviarCotizacion(): Promise<void> {
    setErr(null);
    setMsg(null);
    if (carrito.length === 0) {
      setErr("Agrega al menos un producto al carrito.");
      return;
    }
    const ruta = rutaDeLista(listaId, listas);
    try {
      const creada = await api.portalCrearCotizacion({
        lista_precios_id: listaId,
        ruta_pago: ruta,
        dias_credito: Number(diasCredito || 0),
        notas: notas.trim(),
        lineas: carrito.map((c) => ({
          producto_id: c.producto_id,
          codigo: c.codigo,
          descripcion: c.descripcion,
          cantidad: c.cantidad,
          precio_unitario: c.precio_unitario,
        })),
      });
      setCarrito([]);
      setNotas("");
      setMsg(
        `Cotización #${String(creada.numero || creada.documento_id)} enviada a tu vendedor asignado para revisión.`,
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : "No se pudo crear la cotización.");
    }
  }

  const filtrados = items.filter((p) => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return `${p.codigo} ${p.nombre} ${p.marca}`.toLowerCase().includes(q);
  });

  const totalUsd = carrito.reduce((acc, c) => acc + c.cantidad * c.precio_unitario, 0);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>Catálogo de Productos y Nuevo Pedido</h2>
        <button type="button" className="btn btn-primary" onClick={repetirUltimoPedido}>
          Repetir último pedido
        </button>
      </div>

      {msg ? <div className="badge badge-ok" style={{ display: "block", marginBottom: 10 }}>{msg}</div> : null}
      {err ? <div className="badge badge-err" style={{ display: "block", marginBottom: 10 }}>{err}</div> : null}

      <div className="card">
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input
            className="input"
            style={{ flex: 2, marginBottom: 0 }}
            placeholder="Buscar lubricante, liga o SKU..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select
            className="select"
            style={{ flex: 1, marginBottom: 0 }}
            value={listaId}
            onChange={(e) => setListaId(e.target.value)}
          >
            {listas.map((l) => (
              <option key={l.lista_id} value={l.lista_id}>
                {l.nombre} ({rutaDeLista(l.lista_id, listas).toUpperCase()})
              </option>
            ))}
          </select>
        </div>
      </div>

      {carrito.length > 0 ? (
        <div className="card" style={{ borderColor: "var(--primary)" }}>
          <h3 style={{ marginTop: 0 }}>Tu Solicitud ({carrito.length} líneas) · Total estimado: ${totalUsd.toFixed(2)}</h3>
          {carrito.map((c) => (
            <div key={c.producto_id} style={{ display: "flex", justifyContent: "space-between", marginBottom: 4, fontSize: "0.88rem" }}>
              <span>{c.codigo} · {c.descripcion} (x{c.cantidad})</span>
              <strong>${(c.cantidad * c.precio_unitario).toFixed(2)}</strong>
            </div>
          ))}
          <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
            <input
              className="input"
              style={{ maxWidth: 180, marginBottom: 0 }}
              type="number"
              min={0}
              placeholder="Días crédito"
              value={diasCredito}
              onChange={(e) => setDiasCredito(e.target.value)}
            />
            <input
              className="input"
              style={{ flex: 1, marginBottom: 0 }}
              placeholder="Notas de despacho / observación"
              value={notas}
              onChange={(e) => setNotas(e.target.value)}
            />
            <button type="button" className="btn btn-primary" onClick={() => void enviarCotizacion()}>
              Enviar Cotización a Ventas
            </button>
          </div>
        </div>
      ) : null}

      <div className="dual-grid">
        {filtrados.map((p) => {
          const disp = p.disponibilidad ?? "BAJO_PEDIDO";
          const cls = disp === "DISPONIBLE" ? "badge-ok" : disp === "BAJO_STOCK" ? "badge-warn" : "badge-muted";
          const lbl = disp === "DISPONIBLE" ? "Disponible" : disp === "BAJO_STOCK" ? "Pocas unidades" : "Bajo pedido";
          const precio = (listaId && p.precios_por_lista?.[listaId]) ?? p.precio_usd;
          return (
            <div key={p.producto_id} className="card">
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <strong>{p.codigo} · {p.nombre}</strong>
                <span className={`badge ${cls}`}>{lbl}</span>
              </div>
              <div style={{ color: "var(--muted)", fontSize: "0.82rem", margin: "4px 0 8px 0" }}>
                {p.marca} · {p.presentacion} · Precio lista: <strong>${Number(precio).toFixed(2)}</strong>
              </div>
              <button type="button" className="btn btn-sm" onClick={() => agregar(p)}>
                + Agregar al pedido
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

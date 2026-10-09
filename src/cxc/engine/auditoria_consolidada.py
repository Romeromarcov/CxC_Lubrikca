"""Una sola vista de Auditoria: lo que hay que revisar, agrupado por orden, por pago y por entrega.

Hasta el 9-oct-2026 la pagina listaba la misma realidad en ~12 paneles. Medido en produccion, sobre
las ordenes: «Alertas de venta» (431) contiene al 94% de «orden/factura por debajo» (197); la
bandeja de NC (69) esta 100% dentro de la de descuento de orden (311); «Descuento manual no
explicado» (53) esta 100% dentro de esa misma bandeja; y «Precio inferior a lista» (294) comparte la
mitad de sus ordenes con las dos anteriores. Mismo hallazgo, contado hasta cuatro veces.

Esto junta las fuentes en UNA fila por orden con la lista de sus hallazgos (cada concepto aparece
una vez, con su monto mayor y todas las fuentes que lo senalaron), y deja los pagos y las
entregas en sus propias tablas. Funciones puras: no leen ni escriben nada.
"""

from __future__ import annotations

from typing import Any

# codigo -> etiqueta corta
ETIQUETAS = {
    "PRECIO": "Precio bajo la lista",
    "FACTURA": "Factura menor que la orden",
    "DESCUENTO": "Descuento vs motor",
    "NC": "NC vs motor",
    "ENTREGA": "Entrega ≠ orden",
    "SALDO": "Saldo ≠ Odoo",
}

_TIPO_DISCREPANCIA_A_CODIGO = {
    "Precio Inferior a Lista": "PRECIO",
    "Descuento Manual No Explicado": "DESCUENTO",
    "Producto Entregado No Coincide con la Orden": "ENTREGA",
}


def _f(valor: Any) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


class _Orden:
    def __init__(self, so_id: str) -> None:
        self.so_id = so_id
        self.cliente = ""
        self.vendedor = ""
        self.fecha = ""
        self.hallazgos: dict[str, dict[str, Any]] = {}

    def poner(self, row: dict[str, Any] | None = None, **datos: str) -> None:
        for campo in ("cliente", "vendedor", "fecha"):
            nuevo = datos.get(campo) or ""
            if nuevo and not getattr(self, campo):
                setattr(self, campo, nuevo)

    def agregar(
        self,
        codigo: str,
        fuente: str,
        monto: float,
        detalle: str,
        aceptar: dict[str, Any] | None = None,
    ) -> None:
        h = self.hallazgos.setdefault(
            codigo,
            {
                "codigo": codigo,
                "etiqueta": ETIQUETAS[codigo],
                "monto": 0.0,
                "detalles": [],
                "fuentes": [],
                "aceptar": [],
            },
        )
        h["monto"] = max(h["monto"], abs(monto))
        if detalle and detalle not in h["detalles"]:
            h["detalles"].append(detalle)
        if fuente not in h["fuentes"]:
            h["fuentes"].append(fuente)
        if aceptar:
            h["aceptar"].append(aceptar)

    def como_dict(self) -> dict[str, Any]:
        hallazgos = sorted(self.hallazgos.values(), key=lambda h: -h["monto"])
        for h in hallazgos:
            h["detalle"] = " · ".join(h.pop("detalles"))
        return {
            "so_id": self.so_id,
            "cliente": self.cliente,
            "vendedor": self.vendedor,
            "fecha": self.fecha,
            "hallazgos": hallazgos,
            "n_hallazgos": len(hallazgos),
            "monto_mayor": max((h["monto"] for h in hallazgos), default=0.0),
        }


def consolidar_ordenes(
    *,
    discrepancias: list[dict[str, Any]],
    ventas_con_alerta: list[dict[str, Any]],
    bandeja: list[dict[str, Any]],
    nc_fuera_de_regla: list[dict[str, Any]],
    por_debajo: list[dict[str, Any]],
    saldos: list[dict[str, Any]],
    info_orden: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Una fila por orden con todos sus hallazgos, la de mayor monto primero."""
    ordenes: dict[str, _Orden] = {}

    def orden(so_id: str) -> _Orden:
        o = ordenes.get(so_id)
        if o is None:
            o = ordenes[so_id] = _Orden(so_id)
            info = info_orden.get(so_id, {})
            o.poner(
                cliente=info.get("cliente", ""),
                vendedor=info.get("vendedor", ""),
                fecha=info.get("fecha", ""),
            )
        return o

    for d in discrepancias:
        codigo = _TIPO_DISCREPANCIA_A_CODIGO.get(str(d.get("tipo") or ""), "PRECIO")
        o = orden(str(d["so_id"]))
        o.poner(cliente=d.get("cliente_nombre", ""), vendedor=d.get("vendedor", ""))
        o.agregar(
            codigo, "discrepancias", _f(d.get("diferencia_monto")), str(d.get("detalle") or ""), d
        )

    for v in ventas_con_alerta:
        o = orden(str(v["so_id"]))
        o.poner(
            cliente=v.get("cliente_nombre", ""),
            vendedor=v.get("vendedor", ""),
            fecha=str(v.get("fecha") or "")[:10],
        )
        o.agregar("PRECIO", "ventas", _f(v.get("diferencia")), "facturado por debajo del teorico")

    for r in bandeja:
        tipo = str(r.get("tipo_auditoria") or "")
        if tipo not in ("descuento_orden", "descuento_factura", "nota_credito"):
            continue
        codigo = "NC" if tipo == "nota_credito" else "DESCUENTO"
        o = orden(str(r["so_id"]))
        o.agregar(
            codigo,
            f"bandeja:{tipo}",
            _f(r.get("diferencia_usd")),
            str(r.get("detalle_motor") or r.get("detalle_odoo") or ""),
        )

    for h in nc_fuera_de_regla:
        o = orden(str(h["so_id"]))
        sentido = "mayor" if _f(h.get("diferencia")) > 0 else "menor"
        o.agregar(
            "NC",
            "nc_vs_motor",
            _f(h.get("diferencia")),
            f"NC {_f(h.get('nc_usd')):,.2f} vs motor {_f(h.get('motor_usd')):,.2f} ({sentido})",
        )

    for a in por_debajo:
        o = orden(str(a["so_id"]))
        codigo = "FACTURA" if a.get("tipo") == "factura_menor_que_orden" else "PRECIO"
        o.agregar(codigo, "por_debajo", _f(a.get("diferencia_usd")), str(a.get("detalle") or ""))

    for s in saldos:
        o = orden(str(s["so_id"]))
        o.poner(
            cliente=s.get("cliente_nombre", ""),
            vendedor=s.get("vendedor", ""),
            fecha=str(s.get("fecha") or "")[:10],
        )
        o.agregar("SALDO", "saldo", _f(s.get("diferencia")), str(s.get("causa_probable") or ""), s)

    filas = [o.como_dict() for o in ordenes.values()]
    filas.sort(key=lambda f: (-f["n_hallazgos"], -f["monto_mayor"]))
    return filas


def consolidar_pagos(
    aud: dict[str, list[dict[str, Any]]], pendientes_a_revisar: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Todos los problemas de pagos y vinculaciones en una sola lista."""
    filas: list[dict[str, Any]] = []

    for p in aud.get("pagos_con_residual_sin_aplicar", []):
        filas.append(
            {
                "tipo": "Saldo a favor"
                if p.get("clase") == "remanente"
                else "Residual sin aplicar",
                "pago": str(p.get("pago_id") or ""),
                "referencia": str(p.get("numero_pago_odoo") or ""),
                "cliente": str(p.get("cliente_nombre") or ""),
                "detalle": f"{p.get('moneda') or 'USD'}",
                "monto": abs(_f(p.get("residual_sin_aplicar_usd"))),
                "moneda": str(p.get("moneda") or "USD"),
                "aceptar": p,
            }
        )
    for a in aud.get("ajustes_cambio_huerfanos", []):
        filas.append(
            {
                "tipo": "Ajuste de cambio huérfano",
                "pago": "",
                "referencia": str(a.get("move_name") or ""),
                "cliente": str(a.get("so_id") or ""),
                "detalle": (
                    f"factura {a.get('factura_numero') or '—'} · {str(a.get('ref') or '')[:60]}"
                ),
                "monto": abs(_f(a.get("residual_ves"))),
                "moneda": "VES",
                "aceptar": a,
            }
        )
    for p in aud.get("pagos_importe_local_desincronizado", []):
        filas.append(
            {
                "tipo": "Importe local desincronizado",
                "pago": str(p.get("pago_id") or ""),
                "referencia": str(p.get("numero_pago_odoo") or ""),
                "cliente": "",
                "detalle": (
                    f"importe {_f(p.get('importe_local_ves')):,.2f} vs asiento "
                    f"{_f(p.get('monto_asiento_ves')):,.2f} Bs"
                ),
                "monto": abs(_f(p.get("diferencia_ves"))),
                "moneda": "VES",
                "aceptar": p,
            }
        )
    for v in aud.get("vinculaciones_sobreaplicadas", []):
        filas.append(
            {
                "tipo": "Vinculaciones sobreaplicadas",
                "pago": str(v.get("pago_id") or ""),
                "referencia": "",
                "cliente": "",
                "detalle": (
                    f"pago {_f(v.get('monto_pago_usd')):,.2f} · vinculado "
                    f"{_f(v.get('total_vinculado_usd')):,.2f} USD"
                ),
                "monto": abs(_f(v.get("exceso_usd"))),
                "moneda": "USD",
                "aceptar": v,
            }
        )
    for v in aud.get("vinculaciones_tasa_implausible", []):
        filas.append(
            {
                "tipo": "Tasa implícita fuera de rango",
                "pago": str(v.get("pago_id") or ""),
                "referencia": str(v.get("vinc_id") or ""),
                "cliente": str(v.get("so_id") or ""),
                "detalle": (
                    f"tasa {_f(v.get('tasa_implicita')):.4f} vs real {_f(v.get('tasa_real')):.4f}"
                ),
                "monto": abs(_f(v.get("diferencia_pct"))),
                "moneda": "%",
                "aceptar": v,
            }
        )
    for r in pendientes_a_revisar:
        filas.append(
            {
                "tipo": "Propuesta sin confirmar",
                "pago": str(r.get("pago_id") or ""),
                "referencia": "",
                "cliente": str(r.get("so_id") or ""),
                "detalle": str(r.get("detalle_motor") or ""),
                "monto": 0.0,
                "moneda": "",
                "aceptar": None,
            }
        )
    filas.sort(key=lambda f: (f["tipo"], -f["monto"]))
    return filas


def consolidar_entregas(aud: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    filas: list[dict[str, Any]] = []
    for d in aud.get("devolucion_no_reflejada_en_cantidad", []):
        filas.append(
            {
                "tipo": "Faltante de devolución por línea",
                "so_id": str(d.get("so_id") or ""),
                "detalle": (
                    f"{d.get('producto_codigo') or ''} {d.get('producto_nombre') or ''}: pedida "
                    f"{d.get('cantidad_ordenada')}, entregada {d.get('cantidad_entregada')}, "
                    f"faltante {d.get('faltante')}"
                ),
                "monto": abs(_f(d.get("valor_potencial_afectado"))),
                "aceptar": d,
            }
        )
    for d in aud.get("entregadas_sin_fecha_de_entrega", []):
        filas.append(
            {
                "tipo": "Entregada sin fecha",
                "so_id": str(d.get("so_id") or ""),
                "detalle": str(d.get("detalle") or ""),
                "monto": abs(_f(d.get("monto_orden"))),
                "aceptar": d,
            }
        )
    filas.sort(key=lambda f: (f["tipo"], -f["monto"]))
    return filas

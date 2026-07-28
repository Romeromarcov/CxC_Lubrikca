"""Tests unitarios para la auditoría de descuentos y notas de crédito (discount_audit.py)."""

from decimal import Decimal

from cxc.engine.discount_audit import (
    EstadoAuditoria,
    TipoAuditoria,
    auditar_descuento_factura,
    auditar_descuento_orden,
    auditar_nota_credito,
)


def test_auditar_descuento_orden_coincide():
    res = auditar_descuento_orden(
        so_id="SO001",
        motor_total_descuentos=Decimal("10.00"),
        odoo_descuento_aplicado=Decimal("10.00"),
        tolerance_rounding=Decimal("0.01"),
        tolerance_red=Decimal("1.00"),
    )
    assert res.estado == EstadoAuditoria.OK
    assert not res.enviar_a_bandeja
    assert res.diferencia_usd == Decimal("0.00")
    assert res.descuento_adicional_a_aplicar == Decimal("0.00")


def test_auditar_descuento_orden_motor_mayor():
    # Motor calcula 15$, Odoo tiene 10$ -> dif +5.00$ -> DISCREPANCIA (enviar a bandeja)
    res = auditar_descuento_orden(
        so_id="SO002",
        motor_total_descuentos=Decimal("15.00"),
        odoo_descuento_aplicado=Decimal("10.00"),
        tolerance_rounding=Decimal("0.01"),
        tolerance_red=Decimal("1.00"),
    )
    assert res.estado == EstadoAuditoria.DISCREPANCIA
    assert res.enviar_a_bandeja
    assert res.diferencia_usd == Decimal("5.00")
    assert res.descuento_adicional_a_aplicar == Decimal("5.00")


def test_auditar_descuento_orden_odoo_mayor():
    # Odoo tiene 20$, Motor calcula 15$ -> dif -5.00$ -> DISCREPANCIA
    # (enviar a bandeja, pero adicional=0)
    res = auditar_descuento_orden(
        so_id="SO003",
        motor_total_descuentos=Decimal("15.00"),
        odoo_descuento_aplicado=Decimal("20.00"),
        tolerance_rounding=Decimal("0.01"),
        tolerance_red=Decimal("1.00"),
    )
    assert res.estado == EstadoAuditoria.DISCREPANCIA
    assert res.enviar_a_bandeja
    assert res.diferencia_usd == Decimal("-5.00")
    assert res.descuento_adicional_a_aplicar == Decimal("0.00")


def test_auditar_descuento_factura_discrepancia_menor():
    # Dif = 0.50$ -> entre rounding (0.01) y red (1.00) -> DISCREPANCIA_MENOR
    res = auditar_descuento_factura(
        so_id="SO004",
        motor_total_descuentos=Decimal("10.50"),
        odoo_descuento_factura=Decimal("10.00"),
        tolerance_rounding=Decimal("0.01"),
        tolerance_red=Decimal("1.00"),
    )
    assert res.estado == EstadoAuditoria.DISCREPANCIA_MENOR
    assert res.enviar_a_bandeja
    assert res.tipo == TipoAuditoria.DESCUENTO_FACTURA


def test_auditar_nota_credito():
    res_ok = auditar_nota_credito(
        so_id="SO005",
        motor_ncs_calculadas=Decimal("50.00"),
        odoo_nc_monto=Decimal("50.00"),
    )
    assert res_ok.estado == EstadoAuditoria.OK
    assert not res_ok.enviar_a_bandeja

    res_dif = auditar_nota_credito(
        so_id="SO006",
        motor_ncs_calculadas=Decimal("0.00"),
        odoo_nc_monto=Decimal("50.00"),
    )
    assert res_dif.estado == EstadoAuditoria.DISCREPANCIA
    assert res_dif.enviar_a_bandeja
    assert res_dif.tipo == TipoAuditoria.NOTA_CREDITO


def test_sheets_repository_auditoria():
    from cxc.sheets.gateway import InMemorySheetGateway
    from cxc.sheets.repository import SheetsRepository

    gw = InMemorySheetGateway()
    repo = SheetsRepository(gw)

    filas = [
        {
            "audit_id": "SO001_descuento_orden_2026-07-28",
            "so_id": "SO001",
            "tipo_auditoria": "descuento_orden",
            "motor_calcula_usd": 15.0,
            "odoo_registrado_usd": 10.0,
            "diferencia_usd": 5.0,
            "detalle_odoo": "Sin desc",
            "detalle_motor": "Pronto pago 5%",
            "estado": "pendiente",
            "revisado_por": "",
            "timestamp_audit": "2026-07-28T00:00:00",
        }
    ]
    repo.append_auditoria(filas[0])
    repo.append_auditoria_rows(filas)

    audits = repo.all_auditoria()
    assert len(audits) >= 1

    repo.update_auditoria_estado("SO001_descuento_orden_2026-07-28", "aprobado", "admin")
    audits_after = repo.all_auditoria()
    assert any(a.get("estado") == "aprobado" for a in audits_after)

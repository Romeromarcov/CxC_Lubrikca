"""Tests de la auditoría hora declarada vs banco (sección 6.3)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from cxc.audit.hour_audit import AuditFinding, BankMovement, HourAuditor, Prioridad
from cxc.config import HourAuditConfig
from cxc.models import Moneda, TipoTasa

from . import builders as b

CFG = HourAuditConfig(threshold_minutes=60, rate_swing_pct=Decimal("0.03"))


def test_hora_dentro_de_umbral_no_se_reporta() -> None:
    vinc = b.vinculacion(monto_aplicado="100", hora=datetime(2026, 6, 5, 10, 0))
    mov = BankMovement(monto=Decimal("100"), fecha_hora=datetime(2026, 6, 5, 10, 30))
    assert HourAuditor(CFG).auditar([vinc], [mov]) == []


def test_hora_fuera_de_umbral_se_reporta() -> None:
    vinc = b.vinculacion(monto_aplicado="100", hora=datetime(2026, 6, 5, 10, 0))
    mov = BankMovement(monto=Decimal("100"), fecha_hora=datetime(2026, 6, 5, 13, 0))
    res = HourAuditor(CFG).auditar([vinc], [mov])
    assert len(res) == 1
    assert res[0].delta_minutos == 180
    assert "difiere" in res[0].motivos[0]


def test_sin_movimiento_bancario_es_alta_prioridad() -> None:
    vinc = b.vinculacion(monto_aplicado="100", hora=datetime(2026, 6, 5, 10, 0))
    res = HourAuditor(CFG).auditar([vinc], [])
    assert res[0].prioridad == Prioridad.ALTA
    assert res[0].hora_real is None


def test_tasa_heredada_se_revisa_primero() -> None:
    heredada = b.vinculacion(
        "VH", monto_aplicado="100", hora=datetime(2026, 6, 5, 10, 0),
        es_heredada=True,
    )
    normal_desviada = b.vinculacion(
        "VN", monto_aplicado="200", hora=datetime(2026, 6, 5, 10, 0),
    )
    movs = [
        BankMovement(Decimal("100"), datetime(2026, 6, 5, 10, 5)),
        BankMovement(Decimal("200"), datetime(2026, 6, 5, 14, 0)),  # desvío
    ]
    res = HourAuditor(CFG).auditar([normal_desviada, heredada], movs)
    # La heredada (ALTA) va primero aunque su desvío de minutos sea menor.
    assert res[0].vinc_id == "VH"
    assert res[0].prioridad == Prioridad.ALTA


def test_swing_de_tasa_se_marca() -> None:
    vinc = b.vinculacion(
        monto_aplicado="100", hora=datetime(2026, 6, 5, 10, 0),
        tasa_binance="40.0",
    )
    mov = BankMovement(Decimal("100"), datetime(2026, 6, 5, 10, 10))

    # Tasa real en la hora del banco subió a 42 (>3% de swing).
    def lookup(_dt: datetime) -> Decimal:
        return Decimal("42.0")

    res = HourAuditor(CFG, rate_lookup=lookup).auditar([vinc], [mov])
    assert len(res) == 1
    assert any("swing" in m for m in res[0].motivos)


def test_finding_dataclass_basico() -> None:
    f = AuditFinding(
        vinc_id="V1",
        hora_declarada=datetime(2026, 6, 5, 10, 0),
        hora_real=None,
        delta_minutos=None,
        prioridad=Prioridad.ALTA,
    )
    assert f.motivos == []


def test_moneda_y_tipo_no_afectan_emparejamiento_por_monto() -> None:
    vinc = b.vinculacion(
        monto_aplicado="100", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV,
        hora=datetime(2026, 6, 5, 10, 0),
    )
    mov = BankMovement(Decimal("100"), datetime(2026, 6, 5, 10, 5))
    assert HourAuditor(CFG).auditar([vinc], [mov]) == []

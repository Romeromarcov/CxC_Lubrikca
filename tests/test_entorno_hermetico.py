"""La suite tiene que correr igual con y sin ``.env`` en la máquina.

Auditoría de agosto 2026: 16 tests pasaban en local y fallaban en CI con
"Falta la variable de entorno requerida: ODOO_URL" -- y al cubrir esa,
aparecía la siguiente (``BINANCE_P2P_URL``). El pipeline llevaba tiempo
rojo en parte por esto. Este test evita descubrirlas de a una: compara la
lista de variables obligatorias que declara ``config.py`` contra las que
rellena ``conftest``, y falla si alguien agrega una nueva sin cubrirla.
"""

from __future__ import annotations

import re
from pathlib import Path

from .conftest import _ENV_DE_PRUEBA

_CONFIG = Path(__file__).resolve().parents[1] / "src" / "cxc" / "config.py"


def _variables_obligatorias() -> set[str]:
    """Las que ``config.py`` pide con ``_get("X")`` sin valor por defecto."""
    return set(re.findall(r'_get\(\s*"([A-Z0-9_]+)"\s*\)', _CONFIG.read_text(encoding="utf-8")))


def test_conftest_cubre_todas_las_variables_obligatorias() -> None:
    faltantes = _variables_obligatorias() - set(_ENV_DE_PRUEBA)
    assert not faltantes, (
        f"Variables obligatorias nuevas en config.py sin valor de prueba: {sorted(faltantes)}. "
        "Agregalas a _ENV_DE_PRUEBA en tests/conftest.py o la suite fallará en CI, "
        "donde no hay .env."
    )


def test_no_sobran_variables_en_el_conftest() -> None:
    """Al revés: si una deja de ser obligatoria, que no quede basura."""
    sobrantes = set(_ENV_DE_PRUEBA) - _variables_obligatorias()
    assert not sobrantes, (
        f"Estas ya no son obligatorias en config.py: {sorted(sobrantes)}. "
        "Se pueden sacar de _ENV_DE_PRUEBA."
    )


def test_la_config_se_construye_sin_dotenv() -> None:
    """El caso concreto que fallaba en CI: armar la config completa."""
    from cxc.config import AppConfig

    config = AppConfig.from_env()
    assert config.odoo.url == _ENV_DE_PRUEBA["ODOO_URL"]
    # Confirma que el .env de la máquina NO se coló (la fixture lo anula).
    assert config.odoo.db == _ENV_DE_PRUEBA["ODOO_DB"]

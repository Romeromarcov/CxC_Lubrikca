#!/usr/bin/env bash
# Las tres barreras del proyecto, en un solo comando que FALLA RUIDOSO.
#
# Existe por un error real (septiembre 2026): se desplegó un commit con dos
# tests en rojo porque la cadena era
#
#     pytest ... | tail -3 && git commit ...
#
# `tail` devuelve éxito, así que tapó el código de salida de pytest y el
# `&&` siguió adelante. La barrera nunca llegó a evaluarse.
#
# Acá cada barrera se evalúa por su propio código de salida, nunca por lo
# que imprime, y `set -e` corta en la primera que falle.
#
#   ./scripts/barreras.sh          las tres
#   ./scripts/barreras.sh rapido   sin cobertura (para iterar)
set -euo pipefail

: "${DATABASE_URL:=postgresql://cxc:cxc_ci_pw@localhost:5432/cxc_ci}"
export DATABASE_URL

echo "── ruff ──────────────────────────────────────────"
python -m ruff check src/ tests/

echo "── mypy ──────────────────────────────────────────"
python -m mypy

echo "── pytest ────────────────────────────────────────"
if [ "${1:-}" = "rapido" ]; then
    python -m pytest --no-cov
else
    python -m pytest
fi

echo
echo "✔ Las tres barreras en verde."

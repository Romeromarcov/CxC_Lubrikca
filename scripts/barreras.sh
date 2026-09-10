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
# ``scripts/`` y ``escenarios/`` entran a la barrera aunque no sean el
# deliverable: la corrida diaria de vigilancia y el banco de escenarios son
# codigo que corre contra produccion y contra el Odoo de prueba, y dejarlos
# fuera del lint es como no tenerlo. No entran a mypy ni a la cobertura --
# ``mypy`` cubre el paquete ``cxc`` y la cobertura mide lo mismo.
python -m ruff check src/ tests/ scripts/ escenarios/

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
echo
echo "El banco de escenarios NO entra acá: escribe en el Odoo de prueba por la"
echo "red y tarda ~25 minutos. Se corre aparte con ./scripts/escenarios.sh"

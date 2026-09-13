#!/usr/bin/env bash
# El banco de escenarios de la Fase 3, que NO es parte de la suite normal.
#
# Escribe en el Odoo de prueba por la red y tarda minutos, asi que vive fuera
# de `testpaths` y se corre aparte. `-o addopts=` desactiva la cobertura y su
# umbral, que aca no significan nada: la cobertura del proyecto se mide con la
# suite hermetica (ver pyproject.toml).
#
#   ./scripts/escenarios.sh                                  todos
#   ./scripts/escenarios.sh escenarios/test_ordenes.py       una familia
#   ./scripts/escenarios.sh -k cancelan                      una fila
set -euo pipefail
cd "$(dirname "$0")/.."
exec python -m pytest escenarios/ -o addopts= -v --no-header "$@"

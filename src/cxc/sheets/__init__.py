"""Utilidades de serialización compartidas -- ver ``serde.py``.

El backend Google Sheets (``SheetsRepository``/``GspreadGateway``) se
retiró por completo en agosto 2026, semanas después de completada la
migración a Postgres (único backend de ``Repository`` hoy). Este paquete
sobrevive solo por ``serde``: convierte dataclasses del dominio a/desde
filas de strings -- un formato que varias partes de ``web/app.py``
(reportes, exports) siguen consumiendo por su forma, no por su origen.
"""

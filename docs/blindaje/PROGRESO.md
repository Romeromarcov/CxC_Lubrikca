# Blindaje de CxC — libro de a bordo

Ejecución del plan «Blindaje de CxC Lubrikca» (artifact del 10-sep-2026).
Este archivo es el estado; se actualiza al cerrar cada tarea.

## Entorno de pruebas (Fase 0 — resuelta)

| Decisión | Resuelta como |
|---|---|
| De dónde sale el Odoo de prueba | **Duplicado en Odoo** (`lixie-dev-lubrika-qa1-37747815.dev.odoo.com`), Odoo 18.0+e. Es la opción más fiel: 950 órdenes, 8.277 asientos, 1.438 pagos, 2.809 pickings, 570 clientes, 261 productos, 10 listas, 830 tasas. |
| Copia de datos reales | **Sí, sin anonimizar.** Entorno efímero provisto por gerencia. |
| Quién toca Odoo y qué se equivoca | Pendiente del usuario. **No bloquea**: la Fase 3 se ejecuta ordenada por la severidad estimada y esa columna se reordena cuando llegue la información. |

## Estado por fase

| # | Tarea | Estado |
|---|---|---|
| 0 | Rotar credencial de producción | pendiente (requiere al usuario) |
| 1.1 | Inventario de fallbacks silenciosos | en curso |
| 1.2 | Cobertura de los caminos de dinero | pendiente |
| 1.3 | Integridad de las tablas | pendiente |
| 1.4 | Conciliación espejo vs Odoo | pendiente |
| 1.5 | Qué prueba cada partida del balance | pendiente |
| 2.1 | Ausencia de dato ≠ número | pendiente |
| 2.2 | Invariantes al escribir | pendiente |
| 2.3 | Alertas | pendiente |
| 2.4 | Sacar caminos de dinero de app.py | pendiente |
| 3 | Escenarios de error humano | pendiente |
| 4 | Estrés y fallas | pendiente |
| 5 | Dashboard | pendiente |
| 6 | Bugs y deuda | pendiente |

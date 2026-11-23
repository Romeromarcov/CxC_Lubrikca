# Runbook Operativo: Piloto Controlado y Go-Live App Móvil + Portal de Clientes (Lubrikca)

## 1. Arquitectura Desplegada (Fases 1–4)
- **Backend unificado (`CxC_Lubrikca`)**: `/api/v1/*` con aislamiento estricto de audiencias (`lubrikca-seller-app` vs `lubrikca-client-portal`), idempotencia (`sync_idempotency_keys`), push outbox anti-loop (`notificaciones_push_outbox`), suscripciones Web Push (`web_push_suscripciones`) y bitácora unificada (`accesos_bitacora`).
- **App Móvil Vendedores (`frontend/apps/mobile-vendedor`)**: React Native + Expo SDK 52 + SQLite WAL offline-first, validador SUDEBAN offline (`BANCOS_SUDEBAN` `0102`–`0191`), detección de duplicados y máquina Outbox anti-loop (`400`/`403`/`404`/`409`/`422` -> `CONFLICT_USER_ACTION`).
- **Portal de Clientes PWA (`frontend/apps/portal-clientes` servida en `/portal`)**: Estado de cuenta dual **Ruta BCV** vs **Ruta Binance**, recompra rápida, cuentas filtradas por ruta, trazabilidad 5 pasos y gestión de equipo para rol `titular`.

---

## 2. Activación de Storage Permanente Cloudflare R2 / S3
1. Crear un bucket privado en Cloudflare R2 (o S3 compatible) sin acceso público anónimo.
2. Configurar en Railway (`staging_V1` primero, luego `production` cuando el usuario autorice):
   - `S3_ENDPOINT_URL`: `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`
   - `S3_BUCKET`: `lubrikca-comprobantes-prod` (o `-staging`)
   - `S3_ACCESS_KEY_ID` y `S3_SECRET_ACCESS_KEY`
   - `S3_REGION`: `auto`
3. Verificar en **Configuración -> 🔄 Motor -> Observabilidad Piloto** que `Storage Comprobantes` muestre `Cloudflare R2 / S3`.
4. Migrar comprobantes históricos desde almacenamiento local al bucket (primero dry-run, luego aplicar):
   ```bash
   python scripts/migrar_adjuntos_r2.py
   python scripts/migrar_adjuntos_r2.py --apply
   ```

---

## 3. Protocolo de Piloto en `staging_V1` (2–3 Vendedores + 3–5 Clientes)
1. **Preparar cuentas**:
   - Verificar que los vendedores piloto tengan clave creada en `CxC` (`usuarios_plataforma`) y su teléfono instale la app apuntando a `EXPO_PUBLIC_CXC_API_URL=https://cxclubrikca-stagingv1.up.railway.app`.
   - Desde **Contactos -> 🌐 Portal Clientes**, emitir invitaciones de 48h (`titular`) a 3–5 clientes piloto y entregarles su enlace `/portal/activar?token=...`.
2. **Pruebas obligatorias de campo (modo avión / reconexión)**:
   - Buscar catálogo y stock en modo avión, crear 1 cotización y 1 pedido offline.
   - Intentar registrar un pago hacia banco ajeno (`0108 Provincial -> 0104 Venezolano de Crédito`): la app debe rechazarlo offline antes de encolar.
   - Registrar un pago válido con foto comprobante, activar datos móviles y verificar conciliación en `/pagos`.
   - Cambiar contraseña de una cuenta en **Configuración / Contactos** y confirmar revocación remota inmediata (`401` + limpieza de SQLite).

---

## 4. Criterios Cuantitativos de Salida para Go-Live
Consultar `GET /api/v1/observabilidad/metricas-piloto` (o tarjeta **Observabilidad Piloto** en `Configuración -> 🔄 Motor`):
1. **Tasa de éxito de sincronización sin intervención manual**: `>= 99.0%`.
2. **Pagos aprobados hacia cuenta destino ajena a Lubrikca**: **`0` (`0%`)**.
3. **Almacenamiento permanente**: `s3_configurado = true` (`Cloudflare R2 / S3`) antes de abrir a todos los clientes en producción.
4. **Cero loops de error**: `notificaciones_push_outbox.pendientes` y `outbox_operaciones` sin ciclos repetitivos ante `409`/`422`.

---

## 5. Checklist Controlado de Pase a Producción (`main`)
> Ejecutar únicamente cuando el usuario autorice promover `develop` a `main`:
1. Confirmar respaldo previo de PostgreSQL de producción en Railway.
2. Mergear PR `develop -> main` en `CxC_Lubrikca` y verificar logs de migraciones Alembic (`b8d1e3f5a7c9 -> c9e2f4a6b8d0 -> d0f3a5b7c9e1`) hasta `SUCCESS`.
3. Verificar `/healthz`, `/portal`, `/portal/manifest.webmanifest` y tarjeta **Observabilidad Piloto** en producción.

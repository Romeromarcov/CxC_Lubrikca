-- Rellena el hueco de tasas_historicas_auditoria del 05 al 11 de agosto 2026
-- (scraper caido ~8 dias). BCV/EUR: fuentes reales verificadas (finanzasdigital.com,
-- TCambio.app). Binance: no existe API retroactiva de Binance P2P -- se usa el
-- mismo estimador ya documentado en scripts/cargar_tasas_historicas.py
-- (diferencial de mercado 17.2% sobre BCV), igual que ya se hizo para el hueco
-- de feb-mar 2026 en esta misma tabla. Todo queda en una sola transaccion.

BEGIN;

-- 05 y 06 de agosto ya tenian BCV/EUR reales cargados -- solo completa Binance.
UPDATE tasas_historicas_auditoria
SET tasa_binance_promedio_diario = 885.041894,
    diferencial_bcv_binance_pct = '17.20%',
    notas = notas || ' | Binance estimado (diferencial 1.172, backfill agosto 2026)'
WHERE fecha = '2026-08-05';

UPDATE tasas_historicas_auditoria
SET tasa_binance_promedio_diario = 885.914917,
    diferencial_bcv_binance_pct = '17.20%',
    notas = notas || ' | Binance estimado (diferencial 1.172, backfill agosto 2026)'
WHERE fecha = '2026-08-06';

-- 07 de agosto (viernes) -- BCV/EUR reales.
INSERT INTO tasas_historicas_auditoria
    (fecha, tasa_bcv_usd, tasa_bcv_euro, tasa_binance_promedio_diario,
     diferencial_bcv_binance_pct, fuente, notas)
VALUES (
    '2026-08-07', 756.708300, 871.890000, 886.862128, '17.20%',
    'BCV real (finanzasdigital.com / TCambio.app, verificado) + Binance estimado',
    'BCV-EUR real (backfill agosto 2026, finanzasdigital.com/TCambio.app verificado) | Binance estimado (diferencial 1.172, backfill agosto 2026)'
)
ON CONFLICT (fecha) DO NOTHING;

-- 08 de agosto (sabado) -- BCV no publica, se arrastra la tasa del viernes 07.
INSERT INTO tasas_historicas_auditoria
    (fecha, tasa_bcv_usd, tasa_bcv_euro, tasa_binance_promedio_diario,
     diferencial_bcv_binance_pct, fuente, notas)
VALUES (
    '2026-08-08', 756.708300, 871.890000, 886.862128, '17.20%',
    'BCV real (sabado, arrastra tasa del viernes 07-ago) + Binance estimado',
    'Sabado sin publicacion BCV -- se arrastra la tasa del 07-ago-2026 (mismo criterio ya usado para fines de semana en esta tabla) | Binance estimado (diferencial 1.172, backfill agosto 2026)'
)
ON CONFLICT (fecha) DO NOTHING;

-- 09 de agosto (domingo) -- idem.
INSERT INTO tasas_historicas_auditoria
    (fecha, tasa_bcv_usd, tasa_bcv_euro, tasa_binance_promedio_diario,
     diferencial_bcv_binance_pct, fuente, notas)
VALUES (
    '2026-08-09', 756.708300, 871.890000, 886.862128, '17.20%',
    'BCV real (domingo, arrastra tasa del viernes 07-ago) + Binance estimado',
    'Domingo sin publicacion BCV -- se arrastra la tasa del 07-ago-2026 (mismo criterio ya usado para fines de semana en esta tabla) | Binance estimado (diferencial 1.172, backfill agosto 2026)'
)
ON CONFLICT (fecha) DO NOTHING;

-- 10 de agosto (lunes) -- BCV/EUR reales.
INSERT INTO tasas_historicas_auditoria
    (fecha, tasa_bcv_usd, tasa_bcv_euro, tasa_binance_promedio_diario,
     diferencial_bcv_binance_pct, fuente, notas)
VALUES (
    '2026-08-10', 757.540600, 875.210000, 887.837583, '17.20%',
    'BCV real (finanzasdigital.com / TCambio.app, verificado) + Binance estimado',
    'BCV-EUR real (backfill agosto 2026, finanzasdigital.com/TCambio.app verificado) | Binance estimado (diferencial 1.172, backfill agosto 2026)'
)
ON CONFLICT (fecha) DO NOTHING;

-- 11 de agosto (martes) -- BCV/EUR reales.
INSERT INTO tasas_historicas_auditoria
    (fecha, tasa_bcv_usd, tasa_bcv_euro, tasa_binance_promedio_diario,
     diferencial_bcv_binance_pct, fuente, notas)
VALUES (
    '2026-08-11', 761.216700, 879.340000, 892.145972, '17.20%',
    'BCV real (finanzasdigital.com / TCambio.app, verificado) + Binance estimado',
    'BCV-EUR real (backfill agosto 2026, finanzasdigital.com/TCambio.app verificado) | Binance estimado (diferencial 1.172, backfill agosto 2026)'
)
ON CONFLICT (fecha) DO NOTHING;

-- Verificacion (se muestra al ejecutar con psql -f).
SELECT fecha, tasa_bcv_usd, tasa_bcv_euro, tasa_binance_promedio_diario, fuente
FROM tasas_historicas_auditoria
WHERE fecha BETWEEN '2026-08-01' AND '2026-08-14'
ORDER BY fecha;

COMMIT;

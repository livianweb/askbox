-- generateContent con la clave del proyecto: gemini-flash-lite-latest responde 200.
-- 2.0-flash, 2.0-flash-lite y 1.5-flash-8b responden 404.
-- gemini-2.5-flash (sin lite) ya no está para cuentas nuevas.
-- gemini-2.5-flash-lite y gemini-3.1-flash-lite sí aparecen en models.list; se reactivan
-- detrás del alias latest.

INSERT INTO model_catalog (provider, slug, tier, priority, max_output_tokens, enabled, supports_vision)
VALUES
    ('gemini', 'gemini-flash-lite-latest', 'S', 1, 8192, true, true),
    ('gemini', 'gemini-2.5-flash-lite', 'S', 8, 8192, true, true),
    ('gemini', 'gemini-flash-latest', 'S', 12, 8192, true, true),
    ('gemini', 'gemini-3.1-flash-lite', 'S', 40, 8192, true, true)
ON CONFLICT (provider, slug, tier) DO UPDATE SET
    enabled = true,
    priority = EXCLUDED.priority,
    max_output_tokens = EXCLUDED.max_output_tokens,
    supports_vision = true;

UPDATE model_catalog SET enabled = false
WHERE provider = 'gemini'
  AND slug IN (
    'gemini-2.0-flash-lite',
    'gemini-2.0-flash',
    'gemini-1.5-flash-8b',
    'gemini-2.5-flash'
  );

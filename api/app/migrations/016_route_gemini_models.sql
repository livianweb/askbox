-- Ids que generateContent acepta para el router local.
-- gemini-2.5-flash-lite (y los 3.x de la semilla) responden 404 con la clave de este proyecto.
INSERT INTO model_catalog (provider, slug, tier, priority, max_output_tokens, enabled, supports_vision)
VALUES
    ('gemini', 'gemini-2.0-flash-lite', 'S', 5, 8192, true, true),
    ('gemini', 'gemini-2.0-flash', 'S', 15, 8192, true, true),
    ('gemini', 'gemini-1.5-flash-8b', 'S', 25, 8192, true, true)
ON CONFLICT (provider, slug, tier) DO UPDATE SET
    enabled = true,
    priority = EXCLUDED.priority,
    max_output_tokens = EXCLUDED.max_output_tokens,
    supports_vision = true;

UPDATE model_catalog SET enabled = false
WHERE provider = 'gemini'
  AND slug IN (
    'gemini-2.5-flash-lite',
    'gemini-3.1-flash-lite',
    'gemini-3.5-flash-lite',
    'gemini-3.5-flash',
    'gemini-3.6-flash',
    'gemini-3.7-flash'
  );

-- OpenCode Zen: los modelos *free* de la consola/TUI no son utilizables vía API externa
-- (gateway askbox); deshabilitar peer_chain. deepseek-v4.1-flash es de pago según docs Zen.
-- Activar filas de pago (enabled=true) cuando haya créditos en la cuenta Zen.

UPDATE model_catalog SET enabled = false
WHERE provider = 'opencode_zen'
  AND slug IN (
    'big-pickle',
    'mimo-v2.6-flash-free',
    'ling-3.1-flash-free',
    'space-bunny-free',
    'mimo-v2.5-free',
    'ling-3.0-flash-fin-free',
    'nemotron-3.5-lightning-free',
    'longcat-2.5-preview-free',
    'nemotron-3-ultra-free'
  );

UPDATE model_catalog SET
    enabled = false,
    cost_in_per_mtok = 0.30,
    cost_out_per_mtok = 1.20
WHERE provider = 'opencode_zen' AND slug = 'deepseek-v4.1-flash';

-- Slugs de pago (chat/completions); enabled=false hasta fondos — editar en BD o re-migrar.
INSERT INTO model_catalog (provider, slug, tier, priority, max_output_tokens, cost_in_per_mtok, cost_out_per_mtok, enabled) VALUES
    ('opencode_zen', 'deepseek-v4-flash', 'S', 70, 8192, 0.14, 0.28, false),
    ('opencode_zen', 'glm-5.3-flash',     'S', 75, 8192, 0.15, 0.50, false)
ON CONFLICT (provider, slug, tier) DO UPDATE SET
    cost_in_per_mtok = EXCLUDED.cost_in_per_mtok,
    cost_out_per_mtok = EXCLUDED.cost_out_per_mtok,
    enabled = EXCLUDED.enabled,
    priority = EXCLUDED.priority;

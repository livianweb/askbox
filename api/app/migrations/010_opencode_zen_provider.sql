-- OpenCode Zen (chat completions OpenAI-compatible) + catálogo de modelos free.

ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS api_keys_provider_check;
ALTER TABLE api_keys ADD CONSTRAINT api_keys_provider_check
    CHECK (provider IN ('gemini', 'openrouter', 'opencode_zen'));

ALTER TABLE model_catalog DROP CONSTRAINT IF EXISTS model_catalog_provider_check;
ALTER TABLE model_catalog ADD CONSTRAINT model_catalog_provider_check
    CHECK (provider IN ('gemini', 'openrouter', 'opencode_zen'));

-- Slugs según https://opencode.ai/docs/zen/ (free / chat-completions). Editable en BD.
INSERT INTO model_catalog (provider, slug, tier, priority, max_output_tokens, cost_in_per_mtok, cost_out_per_mtok) VALUES
    ('opencode_zen', 'big-pickle',                 'S', 15, 8192, 0, 0),
    ('opencode_zen', 'mimo-v2.6-flash-free',       'S', 25, 8192, 0, 0),
    ('opencode_zen', 'ling-3.1-flash-free',        'S', 35, 8192, 0, 0),
    ('opencode_zen', 'space-bunny-free',           'S', 45, 8192, 0, 0),
    ('opencode_zen', 'deepseek-v4.1-flash',        'S', 55, 8192, 0, 0),
    ('opencode_zen', 'mimo-v2.5-free',             'M', 15, 8192, 0, 0),
    ('opencode_zen', 'ling-3.0-flash-fin-free',    'M', 25, 8192, 0, 0),
    ('opencode_zen', 'nemotron-3.5-lightning-free','M', 35, 8192, 0, 0),
    ('opencode_zen', 'longcat-2.5-preview-free',   'L', 15, 8192, 0, 0),
    ('opencode_zen', 'nemotron-3-ultra-free',      'L', 25, 8192, 0, 0)
ON CONFLICT (provider, slug, tier) DO NOTHING;

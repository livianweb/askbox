-- Proveedor nativo DeepSeek (https://api.deepseek.com). No es OpenRouter
-- ni el tag local de Ollama deepseek-r1:14b.

ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS api_keys_provider_check;
ALTER TABLE api_keys ADD CONSTRAINT api_keys_provider_check
    CHECK (provider IN ('gemini', 'openrouter', 'deepseek'));

ALTER TABLE model_catalog DROP CONSTRAINT IF EXISTS model_catalog_provider_check;
ALTER TABLE model_catalog ADD CONSTRAINT model_catalog_provider_check
    CHECK (provider IN ('gemini', 'openrouter', 'deepseek'));

-- deepseek-chat: clase M (balanced). Tarifa cache-miss de la era V3 publicada
-- por DeepSeek: 0.27 USD entrada / 1.10 USD salida por millón de tokens.
-- deepseek-reasoner: clase L (strong). Era R1: 0.55 / 2.19.
-- Prioridad 40: después de Gemini (10–30) y antes de los modelos free de
-- OpenRouter en la misma clase. Sin visión: los adjuntos siguen en Gemini.
-- La página de precios de 2026 lista deepseek-flash y deepseek-v4-pro; estos
-- ids se guardan tal cual porque el cliente los pide de forma explícita.
INSERT INTO model_catalog (
    provider, slug, tier, priority, max_output_tokens,
    cost_in_per_mtok, cost_out_per_mtok, enabled, supports_vision
) VALUES
    ('deepseek', 'deepseek-chat',     'M', 40, 8192, 0.2700, 1.1000, true, false),
    ('deepseek', 'deepseek-reasoner', 'L', 40, 8192, 0.5500, 2.1900, true, false)
ON CONFLICT (provider, slug, tier) DO UPDATE SET
    priority = EXCLUDED.priority,
    max_output_tokens = EXCLUDED.max_output_tokens,
    cost_in_per_mtok = EXCLUDED.cost_in_per_mtok,
    cost_out_per_mtok = EXCLUDED.cost_out_per_mtok,
    enabled = true,
    supports_vision = false;

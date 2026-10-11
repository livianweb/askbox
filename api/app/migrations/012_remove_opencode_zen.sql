-- Revierte efectos operativos de 010/011: quita OpenCode Zen del catálogo y restricciones.

DELETE FROM api_keys WHERE provider = 'opencode_zen';
DELETE FROM model_catalog WHERE provider = 'opencode_zen';

ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS api_keys_provider_check;
ALTER TABLE api_keys ADD CONSTRAINT api_keys_provider_check
    CHECK (provider IN ('gemini', 'openrouter'));

ALTER TABLE model_catalog DROP CONSTRAINT IF EXISTS model_catalog_provider_check;
ALTER TABLE model_catalog ADD CONSTRAINT model_catalog_provider_check
    CHECK (provider IN ('gemini', 'openrouter'));

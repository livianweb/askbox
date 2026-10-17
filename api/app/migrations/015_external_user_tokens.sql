-- Tokens de usuario externo emitidos por un token de servicio (scope tokens:write).
-- No toca model_catalog. El secreto sigue siendo solo token_hash; last4 es metadato público.

ALTER TABLE project_access_tokens
    ADD COLUMN IF NOT EXISTS external_user_id text,
    ADD COLUMN IF NOT EXISTS last4 text;

CREATE INDEX IF NOT EXISTS project_access_tokens_external_user_idx
    ON project_access_tokens (project_id, external_user_id)
    WHERE external_user_id IS NOT NULL;

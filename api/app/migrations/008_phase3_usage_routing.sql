-- Fase 3: uso diario, tokens en requests, estado exhausted en claves

ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS api_keys_status_check;
ALTER TABLE api_keys ADD CONSTRAINT api_keys_status_check
    CHECK (status IN ('active', 'invalid', 'disabled', 'exhausted'));

ALTER TABLE requests
    ADD COLUMN IF NOT EXISTS input_tokens integer,
    ADD COLUMN IF NOT EXISTS output_tokens integer,
    ADD COLUMN IF NOT EXISTS estimated_cost_usd numeric(14, 6);

CREATE TABLE IF NOT EXISTS usage_daily (
    project_id           uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    day                  date NOT NULL,
    requests_ok          integer NOT NULL DEFAULT 0,
    requests_error       integer NOT NULL DEFAULT 0,
    cache_hits           integer NOT NULL DEFAULT 0,
    input_tokens         bigint NOT NULL DEFAULT 0,
    output_tokens        bigint NOT NULL DEFAULT 0,
    estimated_cost_usd   numeric(14, 6) NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, day)
);
CREATE INDEX IF NOT EXISTS usage_daily_project_day_idx ON usage_daily(project_id, day DESC);

-- Fase 2: access tokens por proyecto y plantillas de prompt versionadas

CREATE TABLE project_access_tokens (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    label        text NOT NULL DEFAULT '',
    token_hash   text NOT NULL UNIQUE,
    prefix       text NOT NULL,
    scopes       text[] NOT NULL DEFAULT '{}',
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    revoked_at   timestamptz
);
CREATE INDEX project_access_tokens_project_idx ON project_access_tokens(project_id);
CREATE INDEX project_access_tokens_active_hash_idx ON project_access_tokens(token_hash) WHERE revoked_at IS NULL;

CREATE TABLE project_prompts (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id    uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name          text NOT NULL,
    version       integer NOT NULL CHECK (version >= 1),
    system_prompt text NOT NULL DEFAULT '',
    user_template text NOT NULL,
    default_tier  text NOT NULL DEFAULT 'auto'
        CHECK (default_tier IN ('auto', 'fast', 'balanced', 'strong')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, name, version)
);
CREATE INDEX project_prompts_lookup_idx ON project_prompts(project_id, name, version DESC);

ALTER TABLE requests
    ADD COLUMN IF NOT EXISTS access_token_id uuid REFERENCES project_access_tokens(id) ON DELETE SET NULL;

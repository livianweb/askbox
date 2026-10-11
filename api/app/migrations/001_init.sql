-- Fase 1: esquema base askbox

CREATE TABLE users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email         text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- token_hash = sha256 del token de sesión (el token en claro solo vive en la cookie/cliente)
CREATE TABLE sessions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash   text NOT NULL UNIQUE,
    kind         text NOT NULL DEFAULT 'web' CHECK (kind IN ('web', 'api')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sessions_user_idx ON sessions(user_id);

CREATE TABLE projects (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name              text NOT NULL,
    default_tier      text NOT NULL DEFAULT 'auto' CHECK (default_tier IN ('auto', 'fast', 'balanced', 'strong')),
    allow_system_keys boolean NOT NULL DEFAULT false,
    cache_ttl_seconds integer NOT NULL DEFAULT 0 CHECK (cache_ttl_seconds >= 0),
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, name)
);

CREATE TABLE api_keys (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id           uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    provider             text NOT NULL CHECK (provider IN ('gemini', 'openrouter')),
    label                text NOT NULL DEFAULT '',
    ciphertext           bytea NOT NULL,
    nonce                bytea NOT NULL,
    key_version          smallint NOT NULL DEFAULT 1,
    last4                text NOT NULL,
    status               text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'invalid', 'disabled')),
    cooldown_until       timestamptz,
    consecutive_failures integer NOT NULL DEFAULT 0,
    last_used_at         timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX api_keys_project_idx ON api_keys(project_id, provider);

-- Traza de cada POST /v1/ask. No se guarda el prompt: solo longitud y hash truncado.
CREATE TABLE requests (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid REFERENCES users(id) ON DELETE SET NULL,
    project_id     uuid REFERENCES projects(id) ON DELETE SET NULL,
    tier_requested text NOT NULL,
    tier_used      text,
    status         text NOT NULL CHECK (status IN ('ok', 'error')),
    provider       text,
    model_used     text,
    key_id         uuid,
    key_source     text,                 -- 'project' | 'system'
    prompt_chars   integer NOT NULL,
    prompt_hash    text NOT NULL,
    response_chars integer,
    latency_ms     integer,
    cache_hit      boolean NOT NULL DEFAULT false,
    attempts       integer NOT NULL DEFAULT 0,
    error_code     text,
    error_message  text,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX requests_user_created_idx ON requests(user_id, created_at DESC);
CREATE INDEX requests_project_created_idx ON requests(project_id, created_at DESC);

-- Catálogo de modelos: el runtime lee de aquí (nada hardcodeado en código).
-- tier: S (rápido/barato), M (equilibrado), L (fuerte).
CREATE TABLE model_catalog (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider          text NOT NULL CHECK (provider IN ('gemini', 'openrouter')),
    slug              text NOT NULL,
    tier              text NOT NULL CHECK (tier IN ('S', 'M', 'L')),
    priority          integer NOT NULL DEFAULT 100,
    enabled           boolean NOT NULL DEFAULT true,
    max_output_tokens integer NOT NULL DEFAULT 8192,
    cost_in_per_mtok  numeric(12, 4),
    cost_out_per_mtok numeric(12, 4),
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, slug, tier)
);

-- Seed inicial (editable en BD sin redeploy). Orden por priority dentro de cada tier.
INSERT INTO model_catalog (provider, slug, tier, priority, max_output_tokens) VALUES
    ('gemini', 'gemini-2.5-pro',        'L', 10, 8192),
    ('gemini', 'gemini-3.7-flash',      'M', 10, 8192),
    ('gemini', 'gemini-3.6-flash',      'M', 20, 8192),
    ('gemini', 'gemini-3.5-flash',      'M', 30, 8192),
    ('gemini', 'gemini-3.5-flash-lite', 'S', 10, 8192),
    ('gemini', 'gemini-3.1-flash-lite', 'S', 20, 8192),
    ('gemini', 'gemini-2.5-flash-lite', 'S', 30, 8192),
    ('openrouter', 'google/gemma-3-27b-it:free',                    'S', 10, 4096),
    ('openrouter', 'meta-llama/llama-3.3-70b-instruct:free',        'M', 10, 4096),
    ('openrouter', 'qwen/qwen3-32b:free',                           'M', 20, 4096),
    ('openrouter', 'mistralai/mistral-small-3.1-24b-instruct:free', 'S', 20, 4096);

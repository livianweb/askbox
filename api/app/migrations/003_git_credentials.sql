-- Token de GitHub (PAT) por usuario, cifrado con AES-256-GCM (ASKBOX_MASTER_KEY).
-- Nunca se guarda en claro: solo ciphertext + nonce + last4 (metadato seguro para la UI).
CREATE TABLE git_credentials (
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider    text NOT NULL CHECK (provider IN ('github')),
    ciphertext  bytea NOT NULL,
    nonce       bytea NOT NULL,
    key_version smallint NOT NULL DEFAULT 1,
    last4       text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, provider)
);

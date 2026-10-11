-- Fase 4: jobs batch (arq) y metadatos de ítems

CREATE TABLE IF NOT EXISTS batch_jobs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id        uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status            text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    tier              text NOT NULL DEFAULT 'auto',
    max_output_tokens integer NOT NULL DEFAULT 512,
    total_items       integer NOT NULL DEFAULT 0,
    completed_items   integer NOT NULL DEFAULT 0,
    failed_items      integer NOT NULL DEFAULT 0,
    error_message     text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    started_at        timestamptz,
    finished_at       timestamptz
);
CREATE INDEX IF NOT EXISTS batch_jobs_user_created_idx ON batch_jobs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS batch_jobs_status_idx ON batch_jobs(status) WHERE status IN ('pending', 'running');

CREATE TABLE IF NOT EXISTS batch_job_items (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          uuid NOT NULL REFERENCES batch_jobs(id) ON DELETE CASCADE,
    idx             integer NOT NULL,
    prompt          text NOT NULL,
    system_prompt   text,
    status          text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'ok', 'error')),
    result_text     text,
    request_id      uuid REFERENCES requests(id) ON DELETE SET NULL,
    error_code      text,
    error_message   text,
    latency_ms      integer,
    created_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    UNIQUE (job_id, idx)
);
CREATE INDEX IF NOT EXISTS batch_job_items_job_idx ON batch_job_items(job_id, idx);

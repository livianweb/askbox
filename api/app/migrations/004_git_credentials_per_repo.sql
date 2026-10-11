-- Token de GitHub por repositorio (antes: uno global por usuario).
-- El token global antiguo (si existe) se asocia al repo principal 'askbox' (DEFAULT de la columna);
-- su ciphertext no cambia: para 'askbox' el AAD sigue siendo el histórico (`user:<id>`).
ALTER TABLE git_credentials ADD COLUMN repo text NOT NULL DEFAULT 'askbox';
ALTER TABLE git_credentials DROP CONSTRAINT git_credentials_pkey;
ALTER TABLE git_credentials ADD PRIMARY KEY (user_id, provider, repo);

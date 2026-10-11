-- Permite guardar el token de Cloudflare Tunnel cifrado (misma tabla que GitHub PAT).
ALTER TABLE git_credentials DROP CONSTRAINT IF EXISTS git_credentials_provider_check;
ALTER TABLE git_credentials ADD CONSTRAINT git_credentials_provider_check
    CHECK (provider IN ('github', 'cloudflare_tunnel'));

-- Token de API Cloudflare (cuenta + DNS) cifrado, distinto del connector tunnel.
ALTER TABLE git_credentials DROP CONSTRAINT IF EXISTS git_credentials_provider_check;
ALTER TABLE git_credentials ADD CONSTRAINT git_credentials_provider_check
    CHECK (provider IN ('github', 'cloudflare_tunnel', 'cloudflare_api'));

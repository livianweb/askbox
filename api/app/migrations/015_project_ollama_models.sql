-- Preferencias por proyecto de qué modelos Ollama entran en el enrutado automático.
-- {} = usar el default del servidor (solo el slot de código activo, salvo OLLAMA_DEFAULT_ACTIVE_MODELS).
-- No borra modelos del daemon Ollama: solo filtra la selección de mode=allin y provider=ollama auto.

ALTER TABLE projects
    ADD COLUMN ollama_models jsonb NOT NULL DEFAULT '{}'::jsonb;

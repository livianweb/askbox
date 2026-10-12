-- Capacidad multimodal (imagen/PDF) por fila del catálogo; el router filtra peers cuando hay adjuntos.

ALTER TABLE model_catalog
    ADD COLUMN IF NOT EXISTS supports_vision boolean NOT NULL DEFAULT false;

-- Gemini (semilla 001_init): modelos con visión nativa en la API.
UPDATE model_catalog SET supports_vision = true
WHERE provider = 'gemini'
  AND slug IN (
    'gemini-2.5-pro',
    'gemini-3.7-flash',
    'gemini-3.6-flash',
    'gemini-3.5-flash',
    'gemini-3.5-flash-lite',
    'gemini-3.1-flash-lite',
    'gemini-2.5-flash-lite'
  );

-- OpenRouter: solo slugs multimodales presentes en catálogo (Gemma 3 IT).
UPDATE model_catalog SET supports_vision = true
WHERE provider = 'openrouter'
  AND slug = 'google/gemma-3-27b-it:free';

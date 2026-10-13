-- En la misma clase de potencia, Gemini va antes que OpenRouter.
-- El tier fast probaba los modelos gratis de OpenRouter antes que gemini-3.5-flash.

UPDATE model_catalog SET priority = 40
WHERE provider = 'openrouter' AND slug = 'google/gemma-3-27b-it:free';

UPDATE model_catalog SET priority = 50
WHERE provider = 'openrouter' AND slug = 'mistralai/mistral-small-3.1-24b-instruct:free';

UPDATE model_catalog SET priority = 90
WHERE provider = 'openrouter' AND slug = 'meta-llama/llama-3.3-70b-instruct:free';

UPDATE model_catalog SET priority = 100
WHERE provider = 'openrouter' AND slug = 'qwen/qwen3-32b:free';

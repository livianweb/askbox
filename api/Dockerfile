FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
# git: necesario para la pantalla de configuración Git (app/gitops.py) si se monta el repo
# en ASKBOX_REPO_PATH. Sin ese montaje, el endpoint responde "no disponible" igualmente.
# tesseract-ocr(+spa, +osd) y poppler-utils (pdftotext, pdftoppm, pdfinfo): motor de POST /v1/ocr.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    tesseract-ocr tesseract-ocr-spa tesseract-ocr-osd poppler-utils \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# System deps (minimal); keep slim. Add build tools only if needed.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Instala solo dependencias de runtime para el servicio en Cloud Run.
COPY requirements.runtime.txt ./
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.runtime.txt

COPY . .

EXPOSE 8080

CMD ["gunicorn", "-b", ":8080", "bot:app"]


# ── Stage 1: Converteer model (eenmalig, met PyTorch) ────────────────────────
FROM python:3.12-slim AS converter

WORKDIR /build

# PyTorch CPU-only
RUN pip install --no-cache-dir --timeout=300 \
    torch>=2.6.0 --index-url https://download.pytorch.org/whl/cpu

# CTranslate2 + deps apart (voorkomt timeout)
RUN pip install --no-cache-dir --timeout=300 \
    ctranslate2>=4.4.0 \
    transformers>=4.45.0 \
    sentencepiece==0.2.0 \
    sacremoses==0.1.1

# Download en converteer het model naar CTranslate2 INT8 (via CLI)
RUN ct2-transformers-converter \
    --model Helsinki-NLP/opus-mt-en-nl \
    --output_dir /build/ct2_model \
    --quantization int8

# ── Stage 2: Runtime (slank, zonder PyTorch) ─────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Geconverteerd model uit stage 1
COPY --from=converter /build/ct2_model /models/opus-mt-en-nl-ct2

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "3"]

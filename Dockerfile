# The aligner: stable-ts (Whisper) forced alignment, CPU only.
FROM python:3.11-slim

# ffmpeg decodes the uploaded recordings (mp3, m4a, wav ...).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only torch AND torchaudio first, together, so pip does not pull the
# multi-GB CUDA builds (stable-ts needs torchaudio; a PyPI one is CUDA-linked
# and fails to load on a CPU box with "libcudart.so not found").
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the model into the image so the first job does not download it.
# Cached under /app (not /root): Hugging Face Spaces run the container as a
# non-root user (uid 1000), which cannot read /root/.cache.
ARG WHISPER_MODEL=base
ENV WHISPER_MODEL=${WHISPER_MODEL} \
    XDG_CACHE_HOME=/app/.cache
RUN python -c "import stable_whisper, os; stable_whisper.load_model(os.environ['WHISPER_MODEL'])" \
    && chmod -R a+rX /app/.cache

COPY app.py .

# Railway provides PORT; default for local runs.
ENV PORT=8080
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]

# Use official lightweight Python 3.11 image
FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies (ffmpeg for audio processing support, libopus for WebRTC)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libopus-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-cache HuggingFace SentenceTransformer model weights at build time (prevents startup timeouts)
# into a shared, world-readable cache so the unprivileged runtime user can load them
ENV HF_HOME=/opt/hf-cache
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Copy application files
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY make_call.py ./

# Run as an unprivileged user; brochure uploads are written under /app/data
RUN useradd --create-home --uid 10001 qwickcall && mkdir -p /app/data && chown -R qwickcall /app/data \
    && chmod -R a+rX /opt/hf-cache
USER qwickcall

# Expose FastAPI server port
EXPOSE 8001

# Run uvicorn server in production mode
# --proxy-headers makes request.client the real caller behind a trusted reverse proxy (used by rate limiting);
# narrow FORWARDED_ALLOW_IPS to your proxy's address in production.
ENV FORWARDED_ALLOW_IPS="127.0.0.1"
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8001 --proxy-headers --forwarded-allow-ips \"$FORWARDED_ALLOW_IPS\""]

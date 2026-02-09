FROM python:3.10-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir flask

# Build from repo root: docker build -f deploy/relay-service/Dockerfile .
COPY src/backend/relay_service.py /app/relay_service.py

WORKDIR /app

ENV RELAY_SERVICE_PORT=5020
ENV MEDIAMTX_HOST=localhost:8555
ENV USE_NVENC=false
ENV RELAY_FPS=0.5
ENV LOG_DIR=/app/logs

EXPOSE 5020

CMD ["python3", "relay_service.py"]

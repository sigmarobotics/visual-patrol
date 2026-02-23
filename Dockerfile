FROM python:3.10-slim
LABEL org.opencontainers.image.source=https://github.com/sigma-snaken/visual-patrol
# Prevent Python from writing pyc files to disc
ENV PYTHONDONTWRITEBYTECODE=1
# Prevent Python from buffering stdout and stderr
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies if needed (e.g. for some python packages)
# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    python3-dev \
    cmake \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    ffmpeg \
    gosu \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for faster dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy requirements first for better caching
COPY src/backend/requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

# Copy the source code
COPY src /app/src

# Download Chart.js for frontend (After COPY to avoid overwrite)
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /app/src/frontend/static/js && \
    curl -L https://cdn.jsdelivr.net/npm/chart.js -o /app/src/frontend/static/js/chart.min.js && \
    curl -L https://cdn.jsdelivr.net/npm/marked/marked.min.js -o /app/src/frontend/static/js/marked.min.js

# Download fonts for PDF generation (CJK + monospace)
RUN mkdir -p /app/src/backend/fonts && \
    curl -L "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/TraditionalChinese/NotoSansCJKtc-Regular.otf" \
         -o /app/src/backend/fonts/NotoSansCJKtc-Regular.otf && \
    curl -L "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/TraditionalChinese/NotoSansCJKtc-Bold.otf" \
         -o /app/src/backend/fonts/NotoSansCJKtc-Bold.otf && \
    curl -L "https://github.com/IBM/plex/raw/master/IBM-Plex-Mono/fonts/complete/otf/IBMPlexMono-Regular.otf" \
         -o /app/src/backend/fonts/IBMPlexMono-Regular.otf

# Prefer IPv4 over IPv6 (prevents 40s delay on Gemini API calls)
RUN echo "precedence ::ffff:0:0/96 100" > /etc/gai.conf

# Set locale
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# Set working directory to backend where app.py resides
WORKDIR /app/src/backend

# Create non-root user (UID 1000 to match typical host user for volume mounts)
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g 1000 -r -s /bin/false appuser && \
    mkdir -p /app/data /app/logs && \
    chown -R appuser:appuser /app/data /app/logs

# Copy entrypoint script (fixes volume permissions then drops to appuser)
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Expose the Flask port
EXPOSE 5000

# Start as root; entrypoint fixes volume ownership then drops to appuser
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "app.py"]
# Production Deployment

See the full [Deployment Guide](../docs/deployment.md) for setup instructions, multi-robot configuration, mediamtx RTSP relay setup, and troubleshooting.

## Quick Start

```bash
mkdir -p ~/visual-patrol && cd ~/visual-patrol

curl -LO https://raw.githubusercontent.com/sigmarobotics/visual-patrol/main/deploy/docker-compose.prod.yaml
curl -LO https://raw.githubusercontent.com/sigmarobotics/visual-patrol/main/deploy/nginx.conf

vim docker-compose.prod.yaml   # Edit robot IPs and ports

docker compose -f docker-compose.prod.yaml pull
docker compose -f docker-compose.prod.yaml up -d
```

## Update

```bash
docker compose -f docker-compose.prod.yaml pull
docker compose -f docker-compose.prod.yaml up -d
```

## RTSP Relay Service (Jetson)

CI builds multi-arch images to GHCR. Pull and run:

```bash
docker pull ghcr.io/sigmarobotics/visual-patrol-relay:latest
docker compose -f deploy/docker-compose.prod.yaml up -d rtsp-relay
```

See [Relay Service Setup](relay-service/JETSON_SETUP.md) for details including JPS VLM patch.

## JPS VLM Patch

VILA JPS requires a patched `streaming.py` to add `h264parse` for NvMMLite decoder compatibility:

```bash
cp deploy/vila-jps/streaming_patched.py /code/vila-jps/streaming_patched.py
cd /code/vila-jps && docker compose restart jps_vlm
```

See [Relay Service Setup](relay-service/JETSON_SETUP.md) for details.

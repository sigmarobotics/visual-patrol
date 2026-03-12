# Jetson RTSP Relay Service 部署說明

## 背景

測試階段架構：
- **VP (Visual Patrol)**: 在開發機 (WSL2) 上執行
- **Relay Service + mediamtx + VILA JPS**: 在 Jetson 上執行

VP 透過 gRPC 擷取機器人相機畫面，以 HTTP POST 送至 Jetson 上的 Relay Service。Relay Service 轉碼後推送至 mediamtx，供 VILA JPS 分析。

```
開發機 (WSL2)                      Jetson (192.168.50.35)
┌──────────────────────┐          ┌─────────────────────────────────┐
│ VP Flask Backend     │          │                                 │
│  FrameFeederThread   │  HTTP    │ Relay Service (:5020)           │
│  gRPC frame grab     │ ──────> │  ffmpeg transcode → mediamtx    │
│  → POST /frame       │         │                                 │
│                      │         │ mediamtx (:8555)                │
│                      │         │  → VILA JPS (:5010/:5016)       │
└──────────────────────┘         └─────────────────────────────────┘
```

### Relay 轉碼模式

Relay Service 對所有來源都進行轉碼（非 passthrough），確保輸出 H264 Baseline profile
的乾淨串流，使 Jetson 的 NvMMLite 硬體解碼器能正確解析：

- **robot_camera**: JPEG stdin → libx264/NVENC (5 fps) → RTSP
- **external_rtsp**: 來源 RTSP → fps=5 → libx264/NVENC → RTSP（非 `-c:v copy`）

## 前置條件

Jetson 上已有：
- Docker + NVIDIA runtime (`--runtime=nvidia`)
- mediamtx 在 `/code/mediamtx/` 執行中 (port 8555)
- VILA JPS 在 `/code/vila-jps/` 執行中 (port 5010/5016)

## 步驟

### 1. 部署 Relay Service（使用 CI 建置的映像）

CI 自動建置多架構 (amd64 + arm64) 映像至 GHCR：

```bash
# 拉取最新映像
docker pull ghcr.io/sigmarobotics/visual-patrol-relay:latest

# 啟動（首次或更新）
docker rm -f visual_patrol_rtsp_relay 2>/dev/null
docker run -d --name visual_patrol_rtsp_relay \
  --network=host \
  -e TZ=Asia/Taipei \
  -e RELAY_SERVICE_PORT=5020 \
  -e MEDIAMTX_HOST=localhost:8555 \
  -e USE_NVENC=false \
  --restart=unless-stopped \
  ghcr.io/sigmarobotics/visual-patrol-relay:latest
```

> **關於 NVENC**: 目前 CI 使用 `python:3.10-slim` 基礎映像，不含 Jetson GPU 支援。
> 設定 `USE_NVENC=false` 使用 libx264 軟體編碼。外部相機的轉碼在 CPU 上仍可正常運作。

### 2. JPS VLM streaming.py Patch

VILA JPS 的 `jetson_utils.videoSource` 建立的 GStreamer pipeline 缺少 `h264parse` 元素，
導致 `nvv4l2decoder` 無法解析從 mediamtx relay 過來的 H264 串流
（錯誤：`Stream format not found, dropping the frame`）。

Patch 以 GStreamer Python bindings 取代 `jetson_utils.videoSource`，
建立含 `h264parse` 的自訂 pipeline：

```
rtspsrc (TCP) → rtph264depay → h264parse → nvv4l2decoder → nvvidconv → appsink
```

部署方式：

```bash
# 複製 patch 檔案至 JPS 目錄
cp deploy/vila-jps/streaming_patched.py /code/vila-jps/streaming_patched.py

# 確認 JPS compose.yaml 有 volume mount（已設定則跳過）：
# volumes:
#   - ./streaming_patched.py:/jetson-services/inference/vlm/src/mmj_utils/mmj_utils/streaming.py

# 重啟 JPS
cd /code/vila-jps && docker compose restart jps_vlm
```

### 3. 確認 mediamtx 執行中

```bash
# 檢查 mediamtx 是否在 port 8555 監聽
ss -tlnp | grep 8555

# 如果沒有，啟動它
cd /code/mediamtx && docker compose up -d
```

### 4. 驗證

```bash
# Relay 健康檢查
curl http://localhost:5020/health
# 預期回應: {"status":"ok"}

# JPS 健康檢查
curl http://localhost:5010/api/v1/health/ready
# 預期回應: {"detail":"healthy"}

# 測試外部 RTSP relay
curl -X POST http://localhost:5020/relays \
  -H 'Content-Type: application/json' \
  -d '{"key":"robot-a/external","type":"external_rtsp","source_url":"rtsp://admin:pass@192.168.50.45:554/live/profile.1"}'

# 檢查串流就緒
curl "http://localhost:5020/relays/robot-a%2Fexternal/ready?timeout=15"

# 註冊至 JPS
curl -X POST http://localhost:5010/api/v1/live-stream \
  -H 'Content-Type: application/json' \
  -d '{"liveStreamUrl":"rtsp://localhost:8555/robot-a/external"}'

# 設定 alert rules
curl -X POST http://localhost:5010/api/v1/alerts \
  -H 'Content-Type: application/json' \
  -d '{"alerts":["is there any people"]}'

# 停止測試 relay
curl -X POST http://localhost:5020/relays/stop_all
```

## 更新流程

```bash
# 拉取最新映像（CI 自動建置）
docker pull ghcr.io/sigmarobotics/visual-patrol-relay:latest

# 重新啟動
docker rm -f visual_patrol_rtsp_relay
docker run -d --name visual_patrol_rtsp_relay \
  --network=host \
  -e TZ=Asia/Taipei \
  -e RELAY_SERVICE_PORT=5020 \
  -e MEDIAMTX_HOST=localhost:8555 \
  -e USE_NVENC=false \
  --restart=unless-stopped \
  ghcr.io/sigmarobotics/visual-patrol-relay:latest

# 更新 JPS patch（如有更新）
cd /code/visual-patrol && git pull
cp deploy/vila-jps/streaming_patched.py /code/vila-jps/streaming_patched.py
cd /code/vila-jps && docker compose restart jps_vlm
```

## API 參考

| 方法 | 端點 | 說明 |
|------|------|------|
| `GET` | `/health` | 健康檢查 |
| `GET` | `/relays` | 列出所有 relay 狀態 |
| `POST` | `/relays` | 啟動 relay。Body: `{"key":"robot-a/camera","type":"robot_camera"}` 或 `{"key":"robot-a/external","type":"external_rtsp","source_url":"rtsp://..."}` |
| `POST` | `/relays/<key>/frame` | 送出一幀 JPEG（binary body）。僅 robot_camera 類型。 |
| `DELETE` | `/relays/<key>` | 停止指定 relay |
| `GET` | `/relays/<key>/ready?timeout=15` | 阻塞式串流就緒檢查 |
| `POST` | `/relays/stop_all` | 停止所有 relay |

## 疑難排解

| 問題 | 解決方案 |
|------|----------|
| `Stream format not found` | 確認 JPS 有套用 streaming_patched.py（含 `h264parse`） |
| `gstDecoder::Capture() timeout` | 確認 relay service 有轉碼（非 `-c:v copy`）；確認 JPS patch 有效 |
| health check 無回應 | 檢查 port 5020 是否被占用：`ss -tlnp \| grep 5020` |
| ffmpeg 立即退出 | 檢查 mediamtx 是否執行中：`ss -tlnp \| grep 8555` |
| NVENC 編碼失敗 | 設 `USE_NVENC=false` 退回軟體編碼 |
| 串流 ready 但 JPS 收不到 | 確認 mediamtx 和 JPS 在同一 host network；用 `gst-launch-1.0` 測試 |
| `gst-launch` 解碼失敗 | 確認 pipeline 含 `h264parse`：`... ! rtph264depay ! h264parse ! nvv4l2decoder ! ...` |
| 外部相機 No route to host | 確認相機 IP 正確且從 Jetson 可 ping 到 |

# VILA JPS (Jetson Platform Services) 部署指南

## 概述

VILA JPS 是 NVIDIA 的 Jetson 平台服務，提供基於 VLM (Vision Language Model) 的即時影像分析。Visual Patrol 使用 JPS 進行巡檢期間的即時監控 (Edge AI)，透過 RTSP 串流將攝影機畫面送至 JPS 分析，並以 WebSocket 接收警報事件。

### 架構

```
Visual Patrol Backend                    Jetson
┌──────────────────────┐          ┌─────────────────────────────────┐
│ frame_hub.py         │  RTSP    │ mediamtx (:8555)                │
│  gRPC poll → ffmpeg  │ ──────> │  /raw/{robot_id}/camera          │
│  push (0.5fps)       │         │       ↓                          │
│                      │         │ Relay Service (:5020)             │
│ relay_manager.py     │  HTTP   │  ffmpeg transcode → mediamtx     │
│  start/stop relay    │ ──────> │  /{robot_id}/camera               │
│                      │         │       ↓                          │
│ edge_ai_service.py   │  REST   │ VILA JPS (:5010)                 │
│  register stream     │ ──────> │  VLM inference (VILA 1.5)        │
│  set alert rules     │         │       ↓                          │
│                      │   WS    │ JPS WebSocket (:5016)            │
│  receive alerts      │ <────── │  alert events                    │
└──────────────────────┘         └─────────────────────────────────┘
```

## 前置條件

- **硬體**: NVIDIA Jetson AGX Orin (64GB) 或 Orin NX (16GB)
  - AGX Orin 64GB: 可跑 13B 模型
  - Orin NX 16GB: 建議 3B 模型
- **軟體**: JetPack 6.0+ (L4T R36.x), Docker + NVIDIA runtime
- **網路**: Jetson 與 Visual Patrol 後端在同一區網

## 安裝步驟

### 1. 安裝 JPS AI Services

JPS 使用 NVIDIA 提供的預建 Docker 映像，從 NGC (NVIDIA GPU Cloud) 拉取：

```bash
# 建立工作目錄
mkdir -p /code/vila-jps && cd /code/vila-jps

# 下載 JPS AI 服務的 compose 設定
# 參考: https://docs.nvidia.com/jetson/jps/setup.html
# 實際 compose.yaml 依 NVIDIA 官方文件取得

# 拉取並啟動
docker compose pull
docker compose up -d
```

JPS AI 服務包含：
- **jps_vlm**: VLM 推論服務 (VILA 模型)
- **jps_api**: REST API 閘道 (port 5010)
- **jps_ws**: WebSocket 警報服務 (port 5016)

### 2. 安裝 mediamtx

mediamtx 是輕量 RTSP 伺服器，用於接收和分發影像串流：

```bash
mkdir -p /code/mediamtx && cd /code/mediamtx

# docker-compose.yml 範例
cat > docker-compose.yml << 'EOF'
services:
  mediamtx:
    image: bluenviron/mediamtx:latest
    network_mode: host
    restart: unless-stopped
EOF

docker compose up -d
```

mediamtx 預設監聽 port 8554 (RTSP) 和 8555。確認使用 port 8555 (Visual Patrol 的預設 `JETSON_MEDIAMTX_PORT`)。

### 3. 套用 streaming_patched.py

VILA JPS 內建的 `jetson_utils.videoSource` 建立的 GStreamer pipeline 缺少 `h264parse` 元素，導致 `nvv4l2decoder` 無法正確解析 H264 串流。

此 patch 以 GStreamer Python bindings 建立自訂 pipeline，加入 `h264parse`：

```
rtspsrc (TCP) → rtph264depay → h264parse → nvv4l2decoder → nvvidconv → BGRx appsink
```

部署方式：

```bash
# 複製 patch 至 JPS 目錄
cp streaming_patched.py /code/vila-jps/streaming_patched.py

# 在 JPS 的 compose.yaml 中加入 volume mount：
# services:
#   jps_vlm:
#     volumes:
#       - ./streaming_patched.py:/jetson-services/inference/vlm/src/mmj_utils/mmj_utils/streaming.py

# 重啟 JPS VLM 服務
cd /code/vila-jps && docker compose restart jps_vlm
```

### 4. 驗證

```bash
# JPS 健康檢查
curl http://localhost:5010/api/v1/health/ready
# 預期: {"detail":"healthy"}

# mediamtx 檢查
ss -tlnp | grep 8555
```

## 模型選擇

### 可用模型

JPS 使用 VILA 1.5 系列 VLM，支援以下模型：

| 模型 | 大小 | VRAM 需求 | 推論速度 | 適用硬體 |
|------|------|-----------|----------|----------|
| `Efficient-Large-Model/VILA1.5-3b` | ~3.7GB (INT4) | ~6GB | ~45 tok/s | Orin Nano/NX |
| `Efficient-Large-Model/VILA1.5-3b-s2` | ~3.7GB (INT4) | ~6GB | ~45 tok/s | Orin Nano/NX |
| `Efficient-Large-Model/VILA1.5-8b` | ~5GB (INT4) | ~10GB | ~29 tok/s | Orin NX 16GB |
| `Efficient-Large-Model/VILA1.5-13b` | ~8GB (INT4) | ~16GB | ~21 tok/s | AGX Orin 64GB |
| `Efficient-Large-Model/VILA1.5-40b` | ~25GB (INT4) | ~40GB | ~8 tok/s | AGX Orin 64GB |

**建議：**
- **AGX Orin 64GB (預設)**: `VILA1.5-13b` — 最佳精確度/速度平衡
- **Orin NX 16GB**: `VILA1.5-8b` — 記憶體受限時的最佳選擇
- **Orin Nano 8GB**: `VILA1.5-3b` — 唯一可用選項

### 量化

所有模型使用 **INT4 AWQ** 量化，大幅降低 VRAM 使用量同時維持推論品質。模型在首次啟動時自動從 Hugging Face 下載並快取。

### 切換模型

修改 JPS 的設定檔 `chat_server_config.json`：

```json
{
  "model": "Efficient-Large-Model/VILA1.5-13b",
  "max_tokens": 512,
  "temperature": 0.2
}
```

或透過 JPS compose.yaml 的環境變數設定：

```yaml
services:
  jps_vlm:
    environment:
      - VLM_MODEL=Efficient-Large-Model/VILA1.5-13b
```

修改後重啟服務：

```bash
cd /code/vila-jps && docker compose restart jps_vlm
```

首次使用新模型時需要下載，視網路速度可能需 5-30 分鐘。

## JPS API 參考

### REST API (port 5010)

| 方法 | 端點 | 說明 |
|------|------|------|
| `GET` | `/api/v1/health/ready` | 健康檢查 |
| `POST` | `/api/v1/live-stream` | 註冊 RTSP 串流 |
| `DELETE` | `/api/v1/live-stream` | 取消註冊串流 |
| `POST` | `/api/v1/alerts` | 設定警報規則 |
| `GET` | `/api/v1/alerts` | 查詢目前警報規則 |

### 串流註冊

```bash
# 註冊串流 (注意欄位名稱是 liveStreamUrl)
curl -X POST http://localhost:5010/api/v1/live-stream \
  -H 'Content-Type: application/json' \
  -d '{"liveStreamUrl": "rtsp://localhost:8555/robot-a/camera"}'
```

**重要**: API 欄位名稱是 `liveStreamUrl`，不是 `url`。

### 警報規則

```bash
# 設定 yes/no 類型的警報規則
curl -X POST http://localhost:5010/api/v1/alerts \
  -H 'Content-Type: application/json' \
  -d '{"alerts": ["Is there a person?", "Is there fire or smoke?"]}'
```

規則為 yes/no 問題，JPS 對每一幀影像進行 VLM 推論，回答 yes 時觸發警報。

### WebSocket 警報 (port 5016)

```python
import websocket
ws = websocket.WebSocketApp("ws://localhost:5016")
# 收到 JSON 格式的警報事件
```

JPS 限制：**最多同時 1 個串流**。Visual Patrol 前端使用單選按鈕 (radio buttons) 讓使用者選擇機器人攝影機或外部 RTSP 串流。

## 與 Visual Patrol 的整合

### 設定

在 Visual Patrol Web UI 的 **Settings > VILA/Edge AI** 分頁設定：

1. **Jetson Host**: 填入 Jetson IP 位址 (例：`192.168.50.35`)
2. **Stream Source**: 選擇機器人攝影機或外部 RTSP
3. **Alert Rules**: 設定 yes/no 警報規則 (最多 10 條)
4. **Enable Edge AI**: 啟用即時監控

### 運作流程

巡檢啟動時 (若啟用 Edge AI)：

1. `frame_hub` 啟動 ffmpeg RTSP push (0.5fps) 至 mediamtx `/raw/{robot_id}/camera`
2. `relay_manager` 請求 Relay Service 啟動轉碼 (至 `/{robot_id}/camera`)
3. `edge_ai_service` 向 JPS 註冊串流 + 設定警報規則
4. `edge_ai_service` 建立 WebSocket 連線監聽警報
5. 巡檢期間持續監控，觸發警報時擷取證據畫面
6. 巡檢結束：WS 斷線 → 取消串流註冊 → 停止 relay → 停止 RTSP push

### RTSP 串流預覽

使用者可透過 VLC 等播放器觀看即時串流：

```
rtsp://{jetson_host}:8555/{robot_id}/camera
```

## 疑難排解

| 問題 | 解決方案 |
|------|----------|
| JPS 健康檢查失敗 | 確認 JPS 容器正常運行：`docker compose logs jps_vlm` |
| `Stream format not found` | 確認已套用 `streaming_patched.py` (含 `h264parse`) |
| 模型下載失敗 | 確認 Jetson 有網路連線；可手動下載模型至快取目錄 |
| 記憶體不足 (OOM) | 換用較小模型 (13b → 8b → 3b) |
| 串流延遲高 | 正常行為 — ffmpeg push 0.5fps + VLM 推論約 2-5 秒延遲 |
| WebSocket 連線中斷 | `edge_ai_service` 會自動重連；檢查 JPS WS 服務狀態 |
| 無法註冊串流 | 確認 JPS 沒有其他已註冊串流 (最多 1 個)；先 DELETE 再 POST |
| relay 啟動失敗 | 確認 `RELAY_SERVICE_URL` 已設定且 relay service 運行中 |

## 檔案說明

```
deploy/vila-jps/
├── README.md                  # 本文件
└── streaming_patched.py       # JPS VLM 的 GStreamer pipeline patch
                               # 加入 h264parse 修正 NvMMLite 解碼問題
```

## 參考連結

- [NVIDIA Jetson Platform Services](https://docs.nvidia.com/jetson/jps/)
- [VILA 1.5 模型](https://huggingface.co/collections/Efficient-Large-Model/vila-on-pre-training-for-visual-language-models-65d8022a3a52cd9bcd62698e)
- [mediamtx RTSP Server](https://github.com/bluenviron/mediamtx)
- [Relay Service 部署](../relay-service/JETSON_SETUP.md)

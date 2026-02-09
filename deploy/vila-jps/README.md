# VILA JPS (Jetson Platform Services) 部署指南

## 概述

VILA JPS 是 NVIDIA 的 Jetson 平台服務，提供基於 VLM (Vision Language Model) 的即時影像分析。Visual Patrol 使用 JPS 進行巡檢期間的即時監控 (Edge AI)，透過 RTSP 串流將攝影機畫面送至 JPS 分析，並以 WebSocket 接收警報事件。

### 架構

```
Visual Patrol Backend                    Jetson
┌──────────────────────┐          ┌─────────────────────────────────┐
│ frame_hub.py         │  RTSP    │ mediamtx (:8555)                │
│  gRPC poll → ffmpeg  │ ──────> │  /{robot_id}/camera (直推 2fps)  │
│  push (2fps)         │         │       ↓                          │
│                      │         │ VILA JPS (:5010)                 │
│ edge_ai_service.py   │  REST   │  VLM inference (VILA 1.5)        │
│  register stream     │ ──────> │       ↓                          │
│  set alert rules     │         │ JPS WebSocket (:5016)            │
│                      │   WS    │  alert events                    │
│  receive alerts      │ <────── │                                  │
│                      │         │ Prometheus metrics (:5012)        │
│  poll alert status   │ <────── │  alert_status gauge per rule     │
└──────────────────────┘         └─────────────────────────────────┘
```

**機器人攝影機**直接由 frame_hub 推送至 mediamtx 最終路徑 `/{robot_id}/camera`，不經過 relay 轉碼。
**外部 RTSP** 仍透過 relay service 轉碼至 `/{robot_id}/external`。

## 前置條件

- **硬體**: NVIDIA Jetson Orin NX (16GB)
  - Orin NX 16GB: 可跑 3B 或 7B 模型
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

JPS 使用 VILA 1.5 系列 VLM。在 Jetson Orin NX 16GB 上可執行以下模型：

| 模型 | 適用硬體 | 說明 |
|------|----------|------|
| `Efficient-Large-Model/VILA1.5-3b` | Orin NX 16GB | 較快，精確度較低 |
| `Efficient-Large-Model/VILA-7b` | Orin NX 16GB | **目前使用**，精確度/速度平衡 |

模型在首次啟動時自動從 Hugging Face 下載並快取至 `/data/models/huggingface`。

### 切換模型

修改 JPS 的設定檔 `chat_server_config.json`：

```json
{
  "model": "Efficient-Large-Model/VILA-7b",
  "log_level": "INFO",
  "print_stats": true
}
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
| `GET` | `/api/v1/live-stream` | 列出已註冊串流 |
| `POST` | `/api/v1/live-stream` | 註冊 RTSP 串流 |
| `DELETE` | `/api/v1/live-stream/{stream_id}` | 取消註冊串流 |
| `POST` | `/api/v1/alerts` | 設定警報規則 |
| `POST` | `/api/v1/chat/completions` | 聊天推論 |

### Prometheus Metrics (port 5012)

JPS 在 port 5012 暴露 Prometheus 格式的 alert metrics，包含每個 rule 的**即時判斷值**（1.0 = yes, 0.0 = no）：

```bash
curl http://localhost:5012/metrics | grep alert_status
# alert_status{alert_number="r0",alert_string="is there any people"} 1.0
# alert_status{alert_number="r1",alert_string="is there any fire"} 0.0
```

Visual Patrol 的 Test Edge AI 功能會輪詢此 endpoint 以顯示每個 rule 的即時狀態。

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

**注意**: 首次設定 alert rules 時，VILA 模型需要 warm-up（約 30 秒），timeout 建議設為 60 秒。

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

1. **機器人攝影機**: `frame_hub` 啟動 ffmpeg RTSP push (2fps) 直接至 mediamtx `/{robot_id}/camera`
2. **外部 RTSP**: `relay_manager` 請求 Relay Service 啟動轉碼 (至 `/{robot_id}/external`)
3. `edge_ai_service` 向 JPS 註冊串流 + 設定警報規則
4. `edge_ai_service` 建立 WebSocket 連線監聽警報
5. 巡檢期間持續監控，觸發警報時擷取證據畫面
6. 巡檢結束：WS 斷線 → 取消串流註冊 → 停止 push/relay

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
| 記憶體不足 (OOM) | 換用 3B 模型 |
| 首次設定 alerts 超時 | VILA 模型 cold start 約需 30 秒；timeout 建議設 60 秒 |
| 串流延遲高 | 正常行為 — ffmpeg push 2fps + VLM 推論約 2-5 秒延遲 |
| WebSocket 連線中斷 | `edge_ai_service` 會自動重連；檢查 JPS WS 服務狀態 |
| 無法註冊串流 | 確認 JPS 沒有其他已註冊串流 (最多 1 個)；先 DELETE 再 POST |

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

# 前端文件

## 概述

前端為單頁應用程式 (SPA)，使用原生 JavaScript ES modules 建構。無需建置步驟或打包工具 -- 瀏覽器透過 `<script type="module">` 直接載入模組。

## 檔案結構

```
src/frontend/
|-- templates/
|   +-- index.html              # 主要 SPA 頁面 (所有視圖在同一檔案)
+-- static/
    |-- favicon.png              # 應用程式 logo
    |-- css/
    |   +-- style.css            # 所有樣式 (~48KB)
    +-- js/
        |-- app.js               # 進入點、分頁切換、機器人選擇器
        |-- state.js             # 共享可變狀態 (singleton)
        |-- map.js               # Canvas 渲染、座標轉換
        |-- controls.js          # 方向鍵手動控制
        |-- ai.js                # AI 測試面板、結果解析
        |-- points.js            # 巡檢點 CRUD、表格渲染
        |-- patrol.js            # 巡檢啟動/停止、狀態輪詢
        |-- schedule.js          # 排程巡檢管理
        |-- history.js           # 巡檢歷史、詳細彈窗
        |-- reports.js           # 多日分析報告、日期範圍選擇器
        |-- settings.js          # 設定載入/儲存、時鐘、即時監控測試
        |-- stats.js             # Token 使用量圖表 (Chart.js)
        |-- chart.min.js         # Chart.js (vendored，建置時下載)
        +-- marked.min.js        # Marked.js (vendored，建置時下載)
```

## 模組相依圖

```
state.js  (不匯入任何模組)
    ^
    |--- map.js
    |--- controls.js
    |--- ai.js
    |--- points.js  ---> map.js, ai.js
    |--- patrol.js  ---> ai.js
    |--- schedule.js
    |--- history.js ---> ai.js
    |--- reports.js
    |--- settings.js
    |--- stats.js   (不匯入 state，直接使用 DOM)
    |
app.js  (匯入以上所有模組，進入點)
```

## 共享狀態 (`state.js`)

所有跨模組狀態存放在單一匯出的 `state` 物件中：

```javascript
const state = {
    robotPose: { x, y, theta },     // 機器人目前位置
    mapInfo: { resolution, width, height, origin_x, origin_y },
    isMapLoaded: false,
    mapImage: new Image(),           // 已載入的 PNG 地圖
    canvasScale: 1,                  // CSS canvas 縮放因子

    isDragging: false,               // 點擊移動的拖曳狀態
    dragStart: null,
    dragCurrent: null,

    currentPatrolPoints: [],         // 從 /api/{id}/points 載入
    highlightedPoint: null,          // 地圖懸停高亮

    currentSettingsTimezone: 'UTC',
    currentIdleStreamEnabled: true,

    selectedRobotId: null,           // 目前選擇的機器人
    availableRobots: [],             // 所有已註冊的機器人

    _intervals: { ... },            // SetInterval ID，用於清除
};
```

同時匯出 `escapeHtml()` 工具函式以防止 XSS。

## 分頁系統

應用程式有 6 個分頁：**Patrol**、**Control** (預設)、**History**、**Reports**、**Tokens**、**Settings**。

所有分頁視圖同時存在於 DOM 中。`switchTab(name)` 負責顯示/隱藏：

```javascript
window.switchTab = function(tabName) {
    // 隱藏所有視圖
    // 顯示目標視圖
    // 為資料密集的分頁載入資料 (history, reports, tokens, settings)
    // 在 Control 和 Patrol 視圖之間搬移地圖 canvas
};
```

地圖 canvas (`#map-canvas`) 使用 `prepend()` / `appendChild()` 在 Control 和 Patrol 面板之間實體搬移，避免維護兩個獨立的 canvas。

## 主要模式

### API 呼叫

所有 API 呼叫對機器人專屬端點使用 `state.selectedRobotId`：

```javascript
// 機器人專屬
fetch(`/api/${state.selectedRobotId}/state`)

// 全域 (無機器人前綴)
fetch('/api/settings')
```

### Window 函式曝露

HTML 中的行內 `onclick` 處理器透過 `window` 參照函式：

```javascript
// 在 initPoints() 中
window.updatePoint = updatePoint;
window.deletePoint = deletePoint;
```

```html
<button onclick="deletePoint('${id}')">Delete</button>
```

### 機器人選擇器

`fetchRobots()` 每 5 秒執行一次，更新機器人下拉選單和連線指示燈。選擇的機器人變更時，`onRobotChanged()` 觸發：

1. `resetMap()` -- 清除並重新載入地圖
2. `loadPoints()` -- 取得新機器人的巡檢點位
3. `loadSchedule()` -- 取得排程巡檢
4. `refreshCameraStreams()` -- 將鏡頭 `<img>` 標籤指向新機器人的串流

### 輪詢間隔

| 間隔 | 目標 | 頻率 |
|------|------|------|
| `statePolling` | 機器人位置、電量、地圖資訊 | 100ms |
| `patrolPolling` | 巡檢狀態、檢查結果 | 1s |
| `robotFetch` | 機器人列表 (下拉選單更新) | 5s |
| `clock` | 標頭時鐘顯示 | 1s |
| `scheduleDisplay` | 下次巡檢時間顯示 | 60s |

所有間隔儲存在 `state._intervals` 中，避免重複註冊。

## 模組細節

### `map.js` -- 地圖 Canvas

在 HTML5 canvas 上渲染機器人的環境地圖：

- 從 `/api/{id}/map` 載入地圖 PNG
- 以方向箭頭繪製機器人位置
- 懸停時顯示巡檢點高亮
- 處理點擊移動 (click) 和位姿移動 (拖曳指定方向)
- 世界座標 (公尺) 和像素空間之間的座標轉換：
  - `worldToPixelX/Y()`：世界座標轉 canvas 像素
  - `pixelToWorldX/Y()`：canvas 像素轉世界座標
- 以 100ms 輪詢機器人狀態並重繪

### `controls.js` -- 手動控制

簡單的方向鍵控制，用於手動移動機器人：

- 前進/後退：0.1m 增量
- 左轉/右轉：約 10 度旋轉
- 返回基地按鈕
- 緊急停止 (取消指令)

### `ai.js` -- AI 測試

測試目前鏡頭畫面的 AI 辨識：

- 發送提示詞至 `/api/{id}/test_ai`
- 解析結構化 JSON 回應 (`is_NG`、`Description`)
- 匯出 `parseAIResponse()` 和 `renderAIResultHTML()` 供其他模組使用

### `points.js` -- 巡檢點管理

巡檢點位的完整 CRUD：

- 在機器人目前位置新增點位
- 行內編輯名稱、提示詞、啟用狀態
- 刪除點位
- 在 Patrol 視圖中透過上/下按鈕排序
- 匯入/匯出為 JSON 檔案
- 從 Kachaka 機器人匯入已儲存的位置
- 測試點位：移動機器人至該點，然後執行 AI 測試

渲染三個獨立的表格視圖：
1. 快速表格 (Control 視圖) -- 名稱、提示詞、測試、刪除
2. Patrol 視圖表格 -- 名稱、排序按鈕、啟用核取方塊
3. 詳細表格 (目前 UI 未使用) -- 含座標的完整資訊

### `patrol.js` -- 巡檢控制

- 啟動/停止巡檢按鈕
- 每 1 秒輪詢巡檢狀態
- 顯示最新 AI 分析結果
- 顯示目前巡檢結果的可捲動歷史
- 管理鏡頭串流 (巡檢期間啟用，閒置時選擇性啟用)
- **即時警報面板**：當即時監控啟用時，每秒輪詢 `GET /api/{id}/patrol/edge_ai_alerts`，以紅色主題的可折疊面板顯示觸發的警報，含計數徽章。警報顯示規則、串流來源、時間戳記和證據圖片。

### `schedule.js` -- 排程巡檢

- 使用時間選擇器新增排程巡檢
- 切換啟用/停用
- 刪除排程
- 在 Patrol 視圖標頭顯示「下次巡檢」倒計時

### `history.js` -- 巡檢歷史

- 列出所有過去的巡檢記錄，以卡片形式呈現，標題 "Run #N"
- 有影片的巡檢顯示影片圖示
- 機器人篩選下拉選單
- 點擊檢視含 AI 摘要和巡檢圖片的詳細彈窗
- 即時警報區段在詳細彈窗中顯示 (含 stream_source 標籤)
- 下載巡檢 PDF
- 使用 `marked.js` 渲染 Markdown 報告內容

### `reports.js` -- 分析報告 (獨立分頁)

- 獨立的 Reports 分頁，含日期範圍選擇器
- 機器人篩選下拉選單
- 產生多日 AI 分析報告
- 下載分析報告 PDF
- 使用 `marked.js` 渲染 Markdown 報告內容

### `settings.js` -- 設定面板

透過 `/api/settings` 載入和儲存所有系統設定。包含 3 個子分頁：

#### General 子分頁
- **時區**：下拉選擇 (UTC, Asia/Taipei, Asia/Tokyo, 等)
- **Turbo 模式**：啟用/停用核取方塊
- **閒置串流**：控制非巡檢時是否顯示鏡頭畫面
- **Telegram 通知**：啟用/停用、Bot Token、User ID、訊息提示詞

#### Gemini AI 子分頁
- **API 金鑰**：輸入框 (敏感，遮罩顯示)
- **模型**：Gemini 模型識別碼
- **系統提示詞**：AI 巡檢的角色提示詞
- **報告提示詞**：單次及多日報告提示詞
- **錄影**：啟用/停用及影片分析提示詞

#### VILA/Edge AI 子分頁
- **啟用即時監控**：核取方塊
- **串流來源**：單選按鈕 (Robot Camera / External RTSP) -- JPS 最多 1 個串流
- **Jetson Host**：IP 位址輸入框 (自動衍生 JPS、mediamtx、relay、WS URL)
- **外部 RTSP URL**：選擇 External RTSP 時顯示
- **警報規則**：文字區域 (每行一條，最多 10 條)
- **測試按鈕**：使用 JPS 流程 (relay --> mediamtx --> JPS --> WebSocket) 進行快速測試

同時：
- 顯示已註冊的機器人列表
- 管理標頭時鐘 (使用已設定的時區)

### `stats.js` -- Token 使用量 (Tokens 分頁)

- 分頁名稱為 **Tokens** (非 Stats)
- 從 `/api/stats/token_usage` 取得每日 token 使用量
- 以 Chart.js 堆疊長條圖渲染 (輸入、輸出 token)
- 含機器人篩選的日期範圍選擇器
- 以百萬為單位顯示 token 總量並附定價資訊
- 摘要卡片顯示所選期間的彙總統計

## 第三方函式庫

| 函式庫 | 版本 | 用途 |
|--------|------|------|
| Chart.js | Latest (CDN) | Token 使用量統計圖表 |
| marked.js | Latest (CDN) | AI 報告的 Markdown 渲染 |

兩者皆在 Docker 建置時下載並打包為靜態檔案。透過 `<script>` 標籤以傳統腳本 (非 ES modules) 方式在 `app.js` 之前載入。

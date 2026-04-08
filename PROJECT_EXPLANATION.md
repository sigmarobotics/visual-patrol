# Sigma Patrol 專案說明

這是一個名為 **Sigma Patrol** 的自主機器人巡邏系統，整合了 **Kachaka 機器人** 與 **Google Gemini Vision AI**，用於智慧環境監控與異常偵測。

## 核心功能

1.  **自主巡邏 (Autonomous Patrol)**：使用者可定義巡邏點 (waypoints)，讓機器人自動導航至指定位置。
2.  **AI 智慧檢測 (AI-Powered Inspection)**：利用 Gemini Vision 分析巡邏中拍攝的影像，偵測異常狀況（如：跌倒、入侵者、危險物品）。
3.  **錄影與分析 (Video Recording)**：支援巡邏過程錄影，並進行 AI 影片分析。
4.  **即時儀表板 (Real-time Dashboard)**：提供網頁介面，顯示即時地圖、機器人位置、電池狀態及雙鏡頭畫面。
5.  **排程巡邏 (Scheduled Patrols)**：可設定定時巡邏任務（包含特定星期幾）。
6.  **多日分析報告 (Multi-day Analysis Reports)**：生成指定日期範圍的 AI 分析報告。
7.  **PDF 報告生成 (Unified PDF Reports)**：伺服器端生成包含 Markdown 支援（表格、列表、程式碼區塊）與中文字型支援的 PDF 報告。
8.  **手動控制 (Manual Control)**：透過網頁介面手動遙控機器人。

## 技術架構

*   **後端 (Backend)**：使用 **Python Flask** 框架開發。
    *   `app.py`：主要的 API 伺服器入口。
    *   `robot_service.py`：負責與 Kachaka 機器人溝通（移動、相機）。
    *   `ai_service.py`：整合 Google Gemini API 進行影像與文字分析。
    *   `patrol_service.py`：管理巡邏邏輯與排程。
    *   `pdf_service.py`：使用 ReportLab 生成 PDF 報告。
*   **前端 (Frontend)**：HTML/CSS/JavaScript 單頁應用程式 (SPA)，位於 `src/frontend`。
*   **資料庫 (Database)**：使用 SQLite (`data/report/report.db`) 儲存巡邏記錄、檢測結果與設定。
*   **部署 (Deployment)**：支援 Docker 與 Docker Compose 快速部署。

## 快速開始

專案支援 Docker 部署：
```bash
docker-compose up -d
```
服務預設運行於 `http://localhost:5000`。

## 設定

使用者需在設定頁面輸入 **Google Gemini API Key** 並設定 **機器人 IP** 即可開始使用。

---
這隻程式的主要目的是將實體機器人 (Kachaka) 與強大的生成式 AI (Gemini) 結合，實現自動化的安全巡邏與報告生成。

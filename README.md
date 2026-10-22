# twse-report-rag

台灣上市公司年報 AI 問答助理：用中文提問，系統從年報 PDF 檢索相關段落與財務表格，串流回答並附上引用來源（公司、年度、頁碼）。完全在本機執行，不需要任何 API key。

目前收錄 5 家公司的 2025 年度（民國 114 年度）股東會年報：台達電（2308）、鴻海（2317）、台積電（2330）、廣達（2382）、聯發科（2454）。

## 快速開始

使用預先建好的資料庫（seed），不需要 PDF，也不需要跑 ingestion。

### 需要先安裝

- **Docker**：[Docker Desktop](https://www.docker.com/products/docker-desktop/) 或 [OrbStack](https://orbstack.dev/)（Mac），裝好後要先開啟。
- **Ollama**：[ollama.com/download](https://ollama.com/download)，請用最新版（舊版下載不了 `qwen3.5:2b`）。Ollama 直接裝在主機上，不放進 Docker：Mac 的 Docker 用不到 GPU。

### 步驟

```bash
git clone https://github.com/kimi0217/twse-report-rag.git
cd twse-report-rag

# 1. 下載模型（embedding 約 1.2 GB、LLM 約 3 GB）
ollama pull bge-m3
ollama pull qwen3.5:2b

# 2. 設定檔（預設值即可直接使用）
cp .env.example .env

# 3. 下載資料庫 seed（約 10 MB，會檢查 SHA-256）
sh scripts/fetch_seed.sh

# 4. 啟動 db、backend、frontend
docker compose up -d --build
```

打開 <http://localhost:8080>，點範例問題或直接輸入問題。

- **第一次啟動**：db 會自動還原 seed，約需 1～2 分鐘，完成前 backend 不會啟動。可以用 `docker compose ps` 確認三個服務都是 running（db、backend 會顯示 healthy）。
- **第一次提問**：Ollama 要先載入模型，約 15 秒；之後每題約 5～15 秒。
- **後端 API**：<http://localhost:8000>（例如 `curl localhost:8000/companies`）。

### 常見問題

- **網頁顯示「無法連線到後端服務」**：backend 還在等 db 還原 seed，等一下再重新整理；或用 `docker compose logs backend` 查看錯誤。
- **回答「回答時發生錯誤」，訊息提到連線失敗**：主機上的 Ollama 沒有在執行。開啟 Ollama app，或執行 `ollama serve`。
- **在下載 seed 之前就先執行了 `docker compose up`**：db 只會在第一次啟動時還原 seed，要刪掉資料 volume 重來：

  ```bash
  docker compose down -v
  sh scripts/fetch_seed.sh
  docker compose up -d
  ```

- **port 被占用**：在 `.env` 修改 `FRONTEND_PORT`（預設 8080）、`BACKEND_PORT`（8000）、`DB_PORT`（5433）。
- **停止服務**：`docker compose down`（資料會保留）；`docker compose down -v` 會連資料庫一起刪除。

## 從原始資料重建

從證交所下載年報 PDF，自己跑一次完整的資料處理流程：前置檢查 → 抽財務表格 → 切 chunk、算 embedding、寫入資料庫。資料處理腳本在主機的 Python venv 執行，資料寫進 docker compose 的 db。

需要 Python 3.11 以上，以及「快速開始」裡的 Docker、Ollama 與 `bge-m3` 模型。

```bash
# 1. Python 環境
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-ingest.txt

# 2. 啟動資料庫，並把 .env 的設定載入目前的 shell（腳本從環境變數讀設定）
cp -n .env.example .env
docker compose up -d db
set -a; . ./.env; set +a

# 3. 從證交所文件伺服器下載 5 家公司的 2025 年報到 data/pdfs/（約 24 MB）
python scripts/download_reports.py

# 4. 前置檢查：確認 PDF 抽得出正確的中文（結果在 reports/）
python scripts/check_pdf_text.py data/pdfs/

# 5. 抽財務表格到 data/tables/（.md 給人看、.jsonl 給 ingestion）
python scripts/extract_financial_tables.py data/pdfs/

# 6. 切 chunk、算 embedding、寫入資料庫（第一次約 4～5 分鐘；embedding 會快取，重跑很快）
python scripts/ingest.py data/pdfs/

# 7. 啟動 backend 與 frontend
docker compose up -d --build
```

- 證交所的檔案跟各公司官網上的年報是同一份（已用 SHA-256 比對），頁碼與評估題目一致。
- 證交所下載失敗時，可以到[公開資訊觀測站](https://mops.twse.com.tw/)手動下載中文版股東會年報，存成 `data/pdfs/<股票代號>_2025.pdf`。
- ingestion 可以重跑：同一家公司同一年度的舊資料會先刪除再寫入，不會重複。
- 資料處理的判斷標準與遇到的問題見 [`docs/pdf-preflight.md`](docs/pdf-preflight.md)。

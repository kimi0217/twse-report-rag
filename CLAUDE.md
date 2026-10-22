# twse-report-rag

台灣上市櫃公司年報 AI 問答助理。使用者用中文提問，系統從年報 PDF 檢索相關段落與財務表格，串流回答並附上引用來源（公司、年度、頁碼）。

這是**求職作品集**，開發時程 1～2 天。優先順序：能跑通 > 程式碼清楚好講解 > 功能多。

## 目前進度

- [x] PDF 前置檢查：`scripts/check_pdf_text.py`、`scripts/extract_financial_tables.py`、`scripts/textnorm.py`（說明見 `docs/pdf-preflight.md`）
- [ ] ingestion：PDF 內文 + 表格 → chunk → embedding → PostgreSQL (pgvector)
- [ ] 後端 API：FastAPI，`/companies`、`/ask`（SSE 串流）
- [ ] 前端：React 聊天頁 + 引用卡片
- [ ] 評估：`eval/` 問題集 + hit@k
- [ ] Docker 化（docker compose 一鍵啟動）、GitHub Actions、README（含 demo GIF）

完成一項就把這裡打勾。

## 技術選型（完全本機、完全免費、無 API key）

| 用途 | 選擇 | 備註 |
|---|---|---|
| Embedding | Ollama `bge-m3`（1024 維） | 中文表現好；1024 低於 pgvector HNSW 索引的 2000 維上限 |
| LLM | Ollama `qwen3.5:2b` | demo 用的最小可用模型；確切 tag 以 ollama.com/library 為準 |
| 資料庫 | Docker 跑 `pgvector/pgvector:pg16` | |
| 部署 | 不上雲端 | README 放 demo GIF，並提供 `docker compose up` 一鍵本機啟動 |

注意事項：

- **Ollama 直接裝在主機上，不要放進 Docker**（Mac 的 Docker 用不到 GPU）。容器內的後端用 `OLLAMA_BASE_URL=http://host.docker.internal:11434` 連線；直接在 venv 跑時用 `http://localhost:11434`。
- **關閉 Qwen 的思考模式**（Ollama API 傳 `think: false`），小模型思考很慢，而且思考內容不該串流給使用者。
- **Qwen 容易輸出簡體中文**：system prompt 要求使用繁體中文，回答再用 OpenCC（`s2twp`）轉換一次當保險。
- 小模型容易不照指示，prompt 要短、規則要明確；引用編號由程式附上 sources，不要完全依賴模型自己標。
- 模型名稱、Ollama 位址都用環境變數（`EMBED_MODEL`、`LLM_MODEL`、`OLLAMA_BASE_URL`），程式包一層介面，以後要換成雲端 API 不用改業務邏輯。換 embedding 模型時一定要重跑 ingestion。

## 架構

```
data/pdfs/            年報 PDF（不進 git），檔名格式：<股票代號>_<年度>.pdf，例如 2330_2025.pdf
data/tables/          extract_financial_tables.py 的輸出（.md 給人看、.jsonl 給 ingestion）
scripts/              離線資料處理（textnorm、檢查、表格抽取、ingest.py）
backend/              FastAPI 服務
frontend/             React + Vite + TypeScript + Tailwind
eval/                 questions.jsonl 與 run_eval.py
tests/                pytest；測試用 PDF 由 tests/make_sample_pdfs.py 產生
docker-compose.yml    db (pgvector/pgvector:pg16)、backend、frontend
```

資料流：**離線**跑 ingestion 寫進資料庫；**線上**服務只讀資料庫做檢索與回答，網站沒有上傳功能。

## 資料模型

```
chunks(
  id          serial primary key,
  ticker      text,      -- 2330
  company     text,      -- 台積電
  year        int,       -- 2025
  page        int,       -- 1-based，對應 PDF 頁碼
  kind        text,      -- 'text' | 'table'
  title       text,      -- 表格名稱或章節名稱，可為空
  content     text,      -- 已經過 normalize_text()
  embedding   vector(1024)
)
```

- 內文 chunk：依段落切，約 300～500 字，相鄰 chunk 重疊約 50 字；不要跨頁，才能保證頁碼正確。
- 表格 chunk：直接讀 `data/tables/*.jsonl`，**一張表一個 chunk，不可再切**；content 開頭放表名（含單位）。
- ingestion 時，內文頁若已被抽成表格，內文版本仍保留（之後評估再決定要不要去重）。

## API

- `GET /health`
- `GET /companies` → `[{ticker, company, years: [..]}]`
- `POST /ask` body `{question, tickers?: string[], year?: int}` → SSE 串流
  - `event: token` 逐段文字
  - `event: sources` 最後送出 `[{ticker, company, year, page, kind, title, snippet}]`
  - `event: error` 發生錯誤時

## 開發規則

- **所有從 PDF 來的文字都要經過 `scripts/textnorm.normalize_text()`**，使用者的問題在檢索前也要。
- **不使用 LangChain / LlamaIndex**：chunking、檢索、prompt 組裝自己寫，這是面試要講解的核心。
- 檢索結果不足或相關性太低時，回答「年報中查無相關資料」，不可臆測。prompt 要求模型只根據提供的段落回答，並標註引用編號。
- 所有設定走環境變數，維護 `.env.example`；**不 commit 任何 key 或 `.env`**。
- 後端用 async；request/response 一律用 Pydantic model。
- 註解與 docstring 用繁體中文，說明「為什麼」而不是「做什麼」；變數與函式名稱用英文。
- 每個功能都要有對應的 pytest；不需要真的呼叫外部 API 的測試，要用 mock/fake 取代。

## 工作方式

- 一次只做一個小步驟，完成後跑測試，確認通過再進下一步。
- 每個可運作的小步驟就 commit 一次，訊息用英文 Conventional Commits（`feat:`、`fix:`、`test:`、`docs:`、`chore:`），例如 `feat: add pdf ingestion script`。
- 不確定的設計決策先提出選項讓我決定，不要自己做大範圍改動。
- 不要新增計畫以外的功能（登入、上傳、多使用者等都不在 MVP 範圍）。

## 常用指令

```bash
ollama pull bge-m3
ollama pull qwen3.5:2b
docker run -d --name twse-db -e POSTGRES_PASSWORD=postgres -p 5432:5432 pgvector/pgvector:pg16

pip install -r requirements-ingest.txt
python scripts/check_pdf_text.py data/pdfs/
python scripts/extract_financial_tables.py data/pdfs/
python -m pytest -q
```

（後端、前端、docker-compose 完成後把指令補在這裡。）

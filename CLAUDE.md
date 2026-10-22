# twse-report-rag

台灣上市櫃公司年報 AI 問答助理。使用者用中文提問，系統從年報 PDF 檢索相關段落與財務表格，串流回答並附上引用來源（公司、年度、頁碼）。

這是**求職作品集**，開發時程 1～2 天。優先順序：能跑通 > 程式碼清楚好講解 > 功能多。

## 目前進度

- [x] PDF 前置檢查：`scripts/check_pdf_text.py`、`scripts/extract_financial_tables.py`、`common/textnorm.py`（說明見 `docs/pdf-preflight.md`）
- [x] 年報資料：2308、2317、2330、2382、2454 的 2025 年報；財務表格抽取品質修正（PyMuPDF 重抽、內容篩選、表名、續表、錯字表）
- [x] 2412 中華電淘汰（內文約 17% 字被字型換成罕見字），原檔留在 `data/rejected/`，改用 2382 廣達；`check_pdf_text.py` 新增罕見字比例指標
- [x] ingestion（內文）：`scripts/ingest.py`、`common/embeddings.py`；5 家約 1,780 個 text chunk（表格範圍內的內文已去重），bge-m3 (1024 維)、HNSW cosine
- [x] ingestion（表格）：`data/tables/*.jsonl` → kind='table' chunk，一張表一個 chunk，共 15 張（最大約 700 token）
- [x] 後端 API：FastAPI，`/health`、`/companies`、`/ask`（SSE 串流）；`backend/app/`
- [x] 前端：React 聊天頁 + 引用卡片（`frontend/`，Vite + TypeScript + Tailwind v4；Vitest 測 SSE 解析與引用拆解）
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
data/tables/          extract_financial_tables.py 的輸出（.md 給人看、.jsonl 給 ingestion），不進 git
data/char_fixes.json  依 PDF 檔名設定的錯字替換表（進 git）
data/rejected/        前置檢查 FAIL 的年報，留作 README 的證據（不進 git，ingestion 不讀）
common/               scripts/ 與 backend/ 共用：textnorm、embeddings、companies
scripts/              離線資料處理（檢查、表格抽取、ingest.py）
config/companies.json 股票代號 → 公司名稱與別名（新增公司要加在這裡；別名用來從問題認出公司）
data/embeddings_cache.jsonl  embedding 快取（依「模型 + 文字」hash），不進 git
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
- ingestion 時，表格 bbox 範圍內的內文跳過（避免攤平的數字跟表格重複）；表格外的說明文字保留。未抽成表格的非財務表仍會攤平留在內文，見 `docs/known-issues.md`。

## API

- `GET /health`
- `GET /companies` → `[{ticker, company, years: [..]}]`
- `POST /ask` body `{question, tickers?: string[], year?: int}` → SSE 串流
  - `event: token` 逐段文字
  - `event: sources` 最後送出 `[{index, ticker, company, year, page, kind, title, snippet, content}]`（index 對應回答裡的 [n]；content 是完整 chunk，前端展開卡片用）
  - `event: error` 發生錯誤時

## 開發規則

- **所有從 PDF 來的文字都要經過 `common/textnorm.normalize_text()`**，使用者的問題在檢索前也要。
- **PDF 文字一律套用 `data/char_fixes.json`**：用 `load_char_fixes(pdf檔名)` 取得對照表，傳給 `normalize_text(text, char_fixes)`。這是字型把字對應錯（例如中華電「新」→「倝」），NFKC 修不好，只能依檔名人工維護；只能一字換一字。發現新錯字就加進這個檔，再重跑表格抽取與 ingestion。使用者的問題不套用。
- 表格 chunk 規則（`extract_financial_tables.py` 已實作）：項目欄須含財務項目；表名含「治理」「道德」「永續」「氣候」者排除；不收文字對齊（text-fallback）表格；頁首無表名的表沿用上一頁表名加「（續）」；jsonl 的 `tool` 記錄抽取工具（`pdfplumber` / `pymupdf`）。
- **不使用 LangChain / LlamaIndex**：chunking、檢索、prompt 組裝自己寫，這是面試要講解的核心。
- 檢索結果不足或相關性太低時，回答「年報中查無相關資料」，不可臆測。程式層：最相近 chunk 距離超過 `NO_ANSWER_DISTANCE` 就不呼叫 LLM；問題提到已知公司（含別名）時自動只檢索該公司。prompt 要求模型只根據提供的段落回答，並標註引用編號。
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
docker run -d --name twse-pg -e POSTGRES_USER=twse -e POSTGRES_PASSWORD=twse -e POSTGRES_DB=twse \
  -p 5432:5432 -v twse-pg-data:/var/lib/postgresql/data pgvector/pgvector:pg16
docker exec twse-pg psql -U twse -d twse -c "CREATE DATABASE twse_test;"   # pytest 整合測試用

pip install -r requirements-ingest.txt
python scripts/check_pdf_text.py data/pdfs/
python scripts/extract_financial_tables.py data/pdfs/
python scripts/ingest.py data/pdfs/          # 需要 pgvector 容器與 Ollama (bge-m3)
python -m pytest -q                          # 資料庫整合測試用 TEST_DATABASE_URL（twse_test），連不上會跳過
```

後端（在專案根目錄）：

```bash
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --port 8000
```

前端（在 `frontend/`；開發時 Vite 把 `/api/*` 轉給 `localhost:8000`，可用 `BACKEND_URL` 改）：

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
npm test           # Vitest
npm run build      # 型別檢查 + 打包
```

（docker-compose 完成後把指令補在這裡。）

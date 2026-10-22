# PDF 前置檢查

開始寫 ingestion 之前，先確認年報 PDF 抽得出正確的中文，並把財務表格整理成 markdown。

## 步驟

```bash
pip install -r requirements-ingest.txt

# 1. 把年報 PDF 放進 data/pdfs/，檔名建議用「股票代號_年度.pdf」，例如 2330_2025.pdf
#    下載：公開資訊觀測站 → 電子書 → 年報

# 2. 檢查抽字品質
python scripts/check_pdf_text.py data/pdfs/
#    ❌ FAIL 的公司直接換掉；⚠️ WARN 的打開 reports/text_samples/ 跟原檔比對幾頁

# 3. 抽財務概況表格
python scripts/extract_financial_tables.py data/pdfs/
#    打開 data/tables/*.md 檢查。抽錯或漏抽的，用 --pages 手動指定頁碼重跑：
python scripts/extract_financial_tables.py data/pdfs/2330_2025.pdf --pages 120-135

# 測試
python -m pytest -q
```

## 判斷標準

| 結果 | 意思 | 處理 |
|---|---|---|
| 空白頁比例高 | 掃描檔，沒有文字層 | 換公司（OCR 不在 MVP 範圍） |
| 亂碼比例高 / 中文比例低 | 字型缺 ToUnicode 對照，或 Big5 被誤解碼 | 換公司 |
| 有中文但常見詞命中很少 | 字被對應到錯的字碼 | 換公司 |
| 康熙部首 / 相容字 | 「金」變成 ⾦ (U+2FA6) 等 | 可修：ingestion 一律過 `textnorm.normalize_text()`（NFKC） |

## README「遇到的問題」素材

依實際跑的結果改寫，數字填真的：

- **中文抽字品質不一**：寫了前置檢查腳本，用空白頁比例、亂碼比例、常見詞命中率三個指標自動判斷，X 家公司中淘汰了 Y 家。
- **康熙部首字碼**：部分 PDF 把「金」「長」抽成康熙部首區的字，肉眼一樣但字碼不同，造成關鍵字搜尋失敗。ingestion 前統一做 NFKC 正規化。
- **財務表格被切碎**：純文字抽取會把表格攤平成一串數字，失去「哪個數字屬於哪一年」的對應。改用 pdfplumber 只抽財務概況頁的表格，轉成 markdown，每張表當一個獨立 chunk，表名放進 metadata。
- **目錄頁誤判**：目錄也含「財務概況」等關鍵字，用點線（……）與「目錄」字樣排除。

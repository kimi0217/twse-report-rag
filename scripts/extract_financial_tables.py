"""從年報「財務概況」相關頁面抽出表格，轉成 markdown。

用法：
    python scripts/extract_financial_tables.py data/pdfs/
    python scripts/extract_financial_tables.py data/pdfs/2330_2025.pdf --pages 120-135

輸出（每個 PDF 一組）：
    data/tables/<檔名>.md     方便人工檢查
    data/tables/<檔名>.jsonl  一張表一行，之後 ingestion 直接讀，每張表當一個 chunk

做法：
1. 先用 PyMuPDF 快速掃全部頁面，找出含財務關鍵字的頁（跳過目錄頁）。
2. 只對這些頁用 pdfplumber 抽表格（pdfplumber 慢，不要整本跑）。
3. 先用「格線」策略；抽不到才退回「文字對齊」策略，並在輸出標記為 fallback。
4. 表格上方的那一行文字當作表名，存成 metadata。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pdfplumber
import pymupdf

sys.path.insert(0, str(Path(__file__).parent))
from textnorm import normalize_text  # noqa: E402

DEFAULT_KEYWORDS = [
    "財務概況",
    "簡明資產負債表",
    "簡明綜合損益表",
    "簡明損益表",
    "財務分析",
    "財務狀況",
    "財務績效",
]
TOC_HINT = re.compile(r"目\s*錄|\.{6,}|…{3,}")
TEXT_STRATEGY = {"vertical_strategy": "text", "horizontal_strategy": "text"}


def parse_pages(spec: str) -> list[int]:
    """'3,10-12' → [2, 9, 10, 11]（轉成 0-based）"""
    out: list[int] = []
    for part in spec.split(","):
        a, _, b = part.strip().partition("-")
        out += range(int(a) - 1, int(b or a))
    return out


def find_candidate_pages(path: Path, keywords: list[str]) -> list[int]:
    hits = []
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc):
            text = normalize_text(page.get_text("text"))
            if any(k in text for k in keywords) and not TOC_HINT.search(text):
                hits.append(i)
    return hits


def clean_cell(cell: str | None) -> str:
    if cell is None:
        return ""
    return normalize_text(cell).replace("\n", "").replace("|", "\\|")


def clean_table(rows: list[list[str | None]]) -> list[list[str]]:
    table = [[clean_cell(c) for c in row] for row in rows]
    table = [r for r in table if any(r)]
    if not table:
        return []
    width = max(len(r) for r in table)
    table = [r + [""] * (width - len(r)) for r in table]
    keep = [j for j in range(width) if any(r[j] for r in table)]
    table = [[r[j] for j in keep] for r in table]
    # 表頭的合併儲存格：pdfplumber 只在第一格放字，其餘是空的 → 往右補
    header = table[0]
    for j in range(1, len(header)):
        if not header[j]:
            header[j] = header[j - 1]
    return table


def to_markdown(table: list[list[str]]) -> str:
    header, *body = table
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


def guess_title(page: pdfplumber.page.Page, top: float) -> str:
    if top < 5:
        return ""
    region = page.crop((0, max(0, top - 60), page.width, top))
    lines = [ln.strip() for ln in (region.extract_text() or "").splitlines() if ln.strip()]
    return normalize_text(lines[-1]) if lines else ""


def is_useful(table: list[list[str]]) -> bool:
    """至少 2 列 2 欄，而且要有數字，避免把排版框線誤認成表格。"""
    if len(table) < 2 or len(table[0]) < 2:
        return False
    return any(re.search(r"\d", c) for r in table for c in r)


def extract(path: Path, pages: list[int]) -> list[dict]:
    results = []
    with pdfplumber.open(path) as pdf:
        for i in pages:
            if i >= len(pdf.pages):
                continue
            page = pdf.pages[i]
            found = page.find_tables()
            method = "lines"
            if not found:
                found = page.find_tables(TEXT_STRATEGY)
                method = "text-fallback"
            k = 0
            for t in found:
                table = clean_table(t.extract())
                if not is_useful(table):
                    continue
                k += 1
                results.append(
                    {
                        "file": path.name,
                        "page": i + 1,
                        "table_index": k,
                        "title": guess_title(page, t.bbox[1]),
                        "method": method,
                        "rows": len(table),
                        "cols": len(table[0]),
                        "markdown": to_markdown(table),
                    }
                )
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path, help="PDF 檔或資料夾")
    ap.add_argument("--pages", help="手動指定頁碼，例如 120-135,140（只適用單一檔案）")
    ap.add_argument("--keywords", help="自訂關鍵字，逗號分隔")
    ap.add_argument("--out", type=Path, default=Path("data/tables"))
    args = ap.parse_args()

    files: list[Path] = []
    for p in args.paths:
        files += sorted(p.glob("*.pdf")) if p.is_dir() else [p]
    keywords = args.keywords.split(",") if args.keywords else DEFAULT_KEYWORDS
    args.out.mkdir(parents=True, exist_ok=True)

    for f in files:
        pages = parse_pages(args.pages) if args.pages else find_candidate_pages(f, keywords)
        tables = extract(f, pages)
        fallback = sum(t["method"] == "text-fallback" for t in tables)

        md = [f"# {f.name} 財務表格\n"]
        for t in tables:
            note = "（文字對齊模式，請人工確認）" if t["method"] == "text-fallback" else ""
            md.append(f"## 第 {t['page']} 頁・表 {t['table_index']}：{t['title'] or '（無標題）'}{note}\n")
            md.append(t["markdown"] + "\n")
        (args.out / f"{f.stem}.md").write_text("\n".join(md), encoding="utf-8")
        with open(args.out / f"{f.stem}.jsonl", "w", encoding="utf-8") as fp:
            for t in tables:
                fp.write(json.dumps(t, ensure_ascii=False) + "\n")

        page_list = ", ".join(str(p + 1) for p in pages[:15]) + (" …" if len(pages) > 15 else "")
        print(f"{f.name}: 候選頁 {len(pages)} 頁 [{page_list}] → 抽出 {len(tables)} 張表"
              + (f"（其中 {fallback} 張是 fallback，要人工看）" if fallback else ""))
    print(f"\n輸出：{args.out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())

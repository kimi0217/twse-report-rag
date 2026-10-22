"""從年報「財務概況」相關頁面抽出表格，轉成 markdown。

用法：
    python scripts/extract_financial_tables.py data/pdfs/
    python scripts/extract_financial_tables.py data/pdfs/2330_2025.pdf --pages 120-135

輸出（每個 PDF 一組）：
    data/tables/<檔名>.md     方便人工檢查
    data/tables/<檔名>.jsonl  一張表一行，之後 ingestion 直接讀，每張表當一個 chunk

做法：
1. 先用 PyMuPDF 快速掃全部頁面，找出含財務關鍵字的頁（跳過目錄頁）。
2. 只對這些頁用 pdfplumber 的「格線」策略抽表格（pdfplumber 慢，不要整本跑）。
   「文字對齊」策略常把兩欄排版的內文誤認成表格，一律不用。
3. 品質檢查：項目欄大多空白、只剩數字的表格，改用 PyMuPDF find_tables() 重抽；
   重抽還是不合格就丟掉。jsonl 的 tool 欄位記錄最後用哪個工具。
4. 只留含財務項目（營業收入、資產、負債…）的表格，排除 ESG、公司治理等表。
5. 表名取表格正上方、同一欄位範圍內最近的文字行（跨頁版 PDF 一頁有左右兩張表）。
   頁首第一張表沒有表名時，沿用上一頁最後一張表的表名加「（續）」。
6. 表名含「治理」「道德」「永續」「氣候」的表丟掉：項目欄偶爾會提到資產、負債，躲過第 4 步。
7. 依 data/char_fixes.json 修正個別 PDF 的錯字（字型對應錯誤，NFKC 修不好）。
8. 表名前面補「合併」「個體」：台積電用「及子公司」表示合併報表，embedding 分不出兩張表，
   問「合併營收」時個體表反而排在前面。判斷不出來就不補，不猜。
9. jsonl 記錄表格 bbox，ingestion 用來跳過表格範圍內的內文，避免同一張表以攤平的數字重複出現。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pdfplumber
import pymupdf

# 讓 scripts/ 直接執行時也找得到 common/ 套件
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.textnorm import load_char_fixes, normalize_text  # noqa: E402

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
# 用來判斷「這是財務表格」：關鍵字出現在表格內容，而不是頁面上
FINANCIAL_TERMS = ["營業收入", "營業毛利", "營業利益", "淨利", "資產", "負債", "權益", "每股盈餘", "現金流量", "現金餘額"]
NUMERIC_LINE = re.compile(r"[\d,.\s()%$\-]+")
# PyMuPDF 會把部分字型的千分位逗號抽成獨立一行：「1630955164\n, , ,」
DETACHED_COMMAS = re.compile(r"(\(?)(-?\d+)(\)?)\n[,\s]+")
TITLE_SEARCH_HEIGHT = 80
# 表名常比表格左緣多凸出一點（「7.1 合併財務報表」的 7 會被切掉）；跨頁版左右兩表間距約 100pt，20pt 不會越界
TITLE_SEARCH_MARGIN = 20
EXCLUDED_TITLE_WORDS = ["治理", "道德", "永續", "氣候"]
CONTINUED = "（續）"


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


def _fix_detached_commas(cell: str) -> str:
    m = DETACHED_COMMAS.fullmatch(cell.strip())
    if not m:
        return cell
    left, digits, right = m.groups()
    return f"{left}{int(digits):,}{right}"


def clean_cell(cell: str | None, char_fixes: dict[int, str] | None = None) -> str:
    if cell is None:
        return ""
    return normalize_text(_fix_detached_commas(cell), char_fixes).replace("\n", "").replace("|", "\\|")


def clean_table(rows: list[list[str | None]], char_fixes: dict[int, str] | None = None) -> list[list[str]]:
    table = [[clean_cell(c, char_fixes) for c in row] for row in rows]
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


def pick_title(lines_above: list[str], char_fixes: dict[int, str] | None = None) -> str:
    """lines_above 由上到下排列；最後一行離表格最近。

    純數字行（頁碼、上一張表的殘行）跳過；優先選含「表」「分析」的行。
    「合併」「個體」決定數字是哪一份報表，選中的行沒有就把那一行補在前面，
    否則問「合併營收」時可能拿到個體報表的數字。
    """
    lines = [ln for ln in (normalize_text(x, char_fixes) for x in lines_above) if ln and not NUMERIC_LINE.fullmatch(ln)]
    if not lines:
        return ""
    nearest_first = lines[::-1]
    title = next((ln for ln in nearest_first if "表" in ln or "分析" in ln), nearest_first[0])
    scope = next((ln for ln in nearest_first if "合併" in ln or "個體" in ln), None)
    if scope and scope != title:
        title = f"{scope} {title}"
    unit = next((ln for ln in nearest_first if ln.startswith("單位")), None)
    if unit and unit not in title:
        title = f"{title}（{unit}）"
    return title


def title_lines_above(page: pymupdf.Page, bbox: tuple[float, float, float, float]) -> list[str]:
    # 只看表格自己的 x 範圍：跨頁版一頁左右各一張表，全頁寬會抓到隔壁表的標題
    x0, top, x1, _ = bbox
    if top < 5:
        return []
    clip = pymupdf.Rect(x0 - TITLE_SEARCH_MARGIN, max(0, top - TITLE_SEARCH_HEIGHT), x1 + TITLE_SEARCH_MARGIN, top)
    return page.get_text("text", clip=clip, sort=True).splitlines()


def is_useful(table: list[list[str]]) -> bool:
    """至少 2 列 2 欄，而且要有數字，避免把排版框線誤認成表格。"""
    if len(table) < 2 or len(table[0]) < 2:
        return False
    return any(re.search(r"\d", c) for r in table for c in r)


def is_financial(table: list[list[str]]) -> bool:
    # 只看項目欄：ESG 表的描述欄常提到「資產」「現金流量」，看整張表會誤收
    labels = "".join(r[0] for r in table)
    return any(term in labels for term in FINANCIAL_TERMS)


def has_labels(table: list[list[str]]) -> bool:
    """有數字的列裡，項目欄（第一欄）要過半有字；否則數字對不回項目，進 RAG 只會誤導。"""
    numeric_rows = [r for r in table[1:] if any(re.search(r"\d", c) for c in r[1:])]
    if not numeric_rows:
        return False
    labeled = sum(bool(r[0]) for r in numeric_rows)
    return labeled * 2 > len(numeric_rows)


def _overlap(a: tuple, b: tuple) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(w, 0) * max(h, 0)


def is_excluded_title(title: str) -> bool:
    return any(w in title for w in EXCLUDED_TITLE_WORDS)


def label_scopes(titles: list[str]) -> list[str]:
    """同一頁的表名依序傳入，回傳補上「合併」「個體」的表名。

    個體只在兩種情況判定：表名明寫「個體」，或同頁另一張已判定為合併、這張沒有任何標記。
    """
    def scope(title: str) -> str | None:
        if "合併" in title or "及子公司" in title:
            return "合併"
        if "個體" in title:
            return "個體"
        return None

    scopes = [scope(t) for t in titles]
    if "合併" in scopes:
        scopes = [s or "個體" for s in scopes]
    return [f"{s} {t}" if s and not t.startswith(s) else t for s, t in zip(scopes, titles)]


def continued_title(prev_title: str) -> str:
    # 跨三頁以上的表，上一頁已經是「…（續）」，不要變成「（續）（續）」
    return prev_title.removesuffix(CONTINUED) + CONTINUED


def reextract_with_pymupdf(
    page: pymupdf.Page, bbox: tuple, char_fixes: dict[int, str] | None = None
) -> tuple[list[list[str]], tuple] | None:
    """在同一頁找跟 pdfplumber 表格重疊最多的那張 PyMuPDF 表格。"""
    candidates = [t for t in page.find_tables().tables if _overlap(t.bbox, bbox) > 0]
    if not candidates:
        return None
    best = max(candidates, key=lambda t: _overlap(t.bbox, bbox))
    return clean_table(best.extract(), char_fixes), tuple(best.bbox)


def extract(
    path: Path,
    pages: list[int],
    stats: dict[str, int] | None = None,
    char_fixes: dict[int, str] | None = None,
) -> list[dict]:
    """stats 會累計丟棄原因，讓 CLI 印出來，方便判斷篩選規則是否太嚴。"""
    stats = stats if stats is not None else {}
    results = []
    last_title_by_page: dict[int, str] = {}
    with pdfplumber.open(path) as pdf, pymupdf.open(path) as doc:
        for i in pages:
            if i >= len(pdf.pages):
                continue
            k = 0
            page_tables: list[dict] = []
            for t in pdf.pages[i].find_tables():
                table, bbox, tool = clean_table(t.extract(), char_fixes), t.bbox, "pdfplumber"
                if not is_useful(table):
                    continue
                # 先修項目欄再判斷是不是財務表：項目欄空白時財務關鍵字也一起不見了
                if not has_labels(table):
                    retry = reextract_with_pymupdf(doc[i], bbox, char_fixes)
                    if retry is None or not is_useful(retry[0]) or not has_labels(retry[0]):
                        stats["no_labels"] = stats.get("no_labels", 0) + 1
                        continue
                    (table, bbox), tool = retry, "pymupdf"
                    stats["pymupdf"] = stats.get("pymupdf", 0) + 1
                if not is_financial(table):
                    stats["not_financial"] = stats.get("not_financial", 0) + 1
                    continue
                title = pick_title(title_lines_above(doc[i], bbox), char_fixes)
                if not title and k == 0 and last_title_by_page.get(i - 1):
                    title = continued_title(last_title_by_page[i - 1])
                if is_excluded_title(title):
                    stats["excluded_title"] = stats.get("excluded_title", 0) + 1
                    continue
                k += 1
                page_tables.append(
                    {
                        "file": path.name,
                        "page": i + 1,
                        "table_index": k,
                        "title": title,
                        "tool": tool,
                        "bbox": [round(x, 1) for x in bbox],
                        "rows": len(table),
                        "cols": len(table[0]),
                        "markdown": to_markdown(table),
                    }
                )
            # 整頁的表都確定後才補合併／個體：「沒標記的判為個體」要看同頁其他表
            for t, labeled in zip(page_tables, label_scopes([t["title"] for t in page_tables])):
                t["title"] = labeled
            if page_tables:
                last_title_by_page[i] = page_tables[-1]["title"]
            results += page_tables
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path, help="PDF 檔或資料夾")
    ap.add_argument("--pages", help="手動指定頁碼，例如 120-135,140（只適用單一檔案）")
    ap.add_argument("--keywords", help="自訂關鍵字，逗號分隔")
    ap.add_argument("--out", type=Path, default=Path("data/tables"))
    ap.add_argument("--char-fixes", type=Path, default=Path("data/char_fixes.json"), help="依檔名設定的錯字替換表")
    args = ap.parse_args()

    files: list[Path] = []
    for p in args.paths:
        files += sorted(p.glob("*.pdf")) if p.is_dir() else [p]
    keywords = args.keywords.split(",") if args.keywords else DEFAULT_KEYWORDS
    args.out.mkdir(parents=True, exist_ok=True)

    for f in files:
        pages = parse_pages(args.pages) if args.pages else find_candidate_pages(f, keywords)
        stats: dict[str, int] = {}
        tables = extract(f, pages, stats, load_char_fixes(f.name, args.char_fixes))

        md = [f"# {f.name} 財務表格\n"]
        for t in tables:
            note = "（PyMuPDF 重抽）" if t["tool"] == "pymupdf" else ""
            md.append(f"## 第 {t['page']} 頁・表 {t['table_index']}：{t['title'] or '（無標題）'}{note}\n")
            md.append(t["markdown"] + "\n")
        (args.out / f"{f.stem}.md").write_text("\n".join(md), encoding="utf-8")
        with open(args.out / f"{f.stem}.jsonl", "w", encoding="utf-8") as fp:
            for t in tables:
                fp.write(json.dumps(t, ensure_ascii=False) + "\n")

        page_list = ", ".join(str(p + 1) for p in pages[:15]) + (" …" if len(pages) > 15 else "")
        print(f"{f.name}: 候選頁 {len(pages)} 頁 [{page_list}] → 抽出 {len(tables)} 張表"
              f"（PyMuPDF 重抽 {stats.get('pymupdf', 0)}；丟棄：非財務 {stats.get('not_financial', 0)}、"
              f"項目欄空白 {stats.get('no_labels', 0)}、排除表名 {stats.get('excluded_title', 0)}）")
    print(f"\n輸出：{args.out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())

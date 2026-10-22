"""把年報 PDF 內文切 chunk、算 embedding，寫進 PostgreSQL (pgvector)。

用法：
    python scripts/ingest.py data/pdfs/
    python scripts/ingest.py data/pdfs/2330_2025.pdf

這一步只處理內文（kind='text'）；表格 chunk 另外由 data/tables/*.jsonl 寫入。

做法：
1. 檔名 <股票代號>_<年度>.pdf → ticker、year；公司名稱查 config/companies.json。
2. 每頁取文字區塊當段落。跨頁版（寬 > 高）先切左右兩半各自抽，先左後右，
   否則雙欄文字會依 PDF 繪製順序交錯。頁碼一律用 PDF 頁碼。
3. 每個區塊套用該 PDF 的 char_fixes 再 normalize_text()。太短的頁（封面、圖片頁）與目錄頁跳過。
4. 段落依序接成 300～500 字的 chunk，相鄰重疊約 50 字；不跨頁，頁碼才保證正確。
5. 送去 embedding 的文字前面加「公司 年度 年報｜章節」，資料庫存原文：
   年報內文多寫「本公司」，不加公司名，問「台積電研發費用」時向量對不到正確公司。
6. 每份 PDF 一個 transaction：先刪該公司該年度的 text chunk 再寫入，重跑不會重複，也不動表格 chunk。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pymupdf
from pgvector.psycopg import register_vector

sys.path.insert(0, str(Path(__file__).parent))
from embeddings import CachedEmbedder, Embedder, get_embedder  # noqa: E402
from textnorm import load_char_fixes, normalize_text  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPANIES = ROOT / "config" / "companies.json"
DEFAULT_CACHE = ROOT / "data" / "embeddings_cache.jsonl"
DEFAULT_DB = "postgresql://twse:twse@localhost:5432/twse"

MIN_CHUNK = 300
MAX_CHUNK = 500
OVERLAP = 50
# 每頁最後剩下不到這個長度的零頭併進前一塊；單獨成塊的話資訊太少，檢索時只會變雜訊
MIN_TAIL = 100
MIN_PAGE_CHARS = 100
# 單獨成塊的章節標題、頁尾殘字；太短沒有檢索價值
MIN_KEEP = 50
SENTENCE_END = "。！？；!?;\n"
# 區塊以這些字結尾才算段落結束；否則是排版折行（台積電一行一個區塊），要跟下一個區塊接起來
PARAGRAPH_END = "。！？；：!?;:」）)"
TOC_HINT = re.compile(r"目\s*錄|\.{6,}|…{3,}")
NUMERIC_ONLY = re.compile(r"[\d\s,.\-–—/()（）%]+")
# 年報的法定章節名稱；只認「編號 + 這些名稱開頭」的段落當章節標題。
# 只看編號格式（「1.」「壹、」）會誤判日期、製程節點、條列項目
CHAPTER_NAMES = [
    "致股東報告書", "公司簡介", "公司概況", "公司治理", "募資情形", "營運概況", "財務概況",
    "財務狀況及財務績效", "特別記載事項", "企業永續", "資本及股份", "關係企業",
]
CHAPTER = re.compile(
    r"^(?:[壹貳參肆伍陸柒捌玖拾]{1,2}|\d{1,2})\s*[、‧.．]?\s*"
    rf"((?:{'|'.join(CHAPTER_NAMES)})[^。，,:：?？]{{0,30}}?)"
    r"(?:\s*\d{1,3})?$"  # 章節扉頁的小目錄會在標題後面接頁碼：「伍、營運概況 086」
)

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS chunks (
    id        serial PRIMARY KEY,
    ticker    text NOT NULL,
    company   text NOT NULL,
    year      int  NOT NULL,
    page      int  NOT NULL,
    kind      text NOT NULL CHECK (kind IN ('text', 'table')),
    title     text,
    content   text NOT NULL,
    embedding vector({dim}) NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_ticker_year ON chunks (ticker, year);
"""


@dataclass
class Chunk:
    page: int
    title: str
    content: str


# --- 讀 PDF ---


def parse_filename(path: Path) -> tuple[str, int]:
    m = re.fullmatch(r"(\d{4,6})_(\d{4})", path.stem)
    if not m:
        raise ValueError(f"檔名要是 <股票代號>_<年度>.pdf：{path.name}")
    return m.group(1), int(m.group(2))


def load_companies(path: Path = DEFAULT_COMPANIES) -> dict[str, str]:
    return json.loads(path.read_text(encoding="utf-8"))


def page_regions(page: pymupdf.Page) -> list[pymupdf.Rect]:
    r = page.rect
    if r.width <= r.height:
        return [r]
    mid = r.x0 + r.width / 2
    return [pymupdf.Rect(r.x0, r.y0, mid, r.y1), pymupdf.Rect(mid, r.y0, r.x1, r.y1)]


def page_paragraphs(page: pymupdf.Page, char_fixes: dict[int, str] | None = None) -> list[str]:
    paragraphs: list[str] = []
    for clip in page_regions(page):
        open_para = False  # 上一個區塊還沒結束，換半頁時不續接
        for x0, y0, x1, y1, text, _no, block_type in page.get_text("blocks", clip=clip):
            if block_type != 0:  # 圖片區塊
                continue
            # 區塊內的換行是排版折行，不是段落；中文直接接起來
            para = normalize_text(text.replace("\n", ""), char_fixes)
            if not para or NUMERIC_ONLY.fullmatch(para):
                continue
            if open_para and not chapter_title(para):
                paragraphs[-1] += para
            else:
                paragraphs.append(para)
            open_para = not para.endswith(tuple(PARAGRAPH_END)) and not chapter_title(para)
    return paragraphs


def chapter_title(paragraph: str) -> str | None:
    # 只留章節名稱、去掉編號：各家編號格式不同（壹、/1./1），名稱才是檢索有用的部分
    m = CHAPTER.match(paragraph)
    return m.group(1).strip() if m else None


# --- 切 chunk ---


def _cut_point(text: str) -> int:
    """在 MIN_CHUNK～MAX_CHUNK 之間找最後一個句尾；找不到就硬切在 MAX_CHUNK。"""
    window = text[MIN_CHUNK:MAX_CHUNK]
    ends = [i for i, c in enumerate(window) if c in SENTENCE_END]
    return MIN_CHUNK + ends[-1] + 1 if ends else MAX_CHUNK


def _overlap_tail(text: str) -> str:
    """取最後約 OVERLAP 字，盡量從句子開頭接起，讓下一塊讀起來不會從半句開始。"""
    window = text[-(OVERLAP + 30) :]
    for i, c in enumerate(window[: -20]):
        if c in SENTENCE_END:
            return window[i + 1 :].lstrip()
    return text[-OVERLAP:]


def chunk_paragraphs(paragraphs: list[str]) -> list[str]:
    chunks: list[str] = []
    buf, carried = "", 0  # carried：buf 開頭有多少字是上一塊的重疊

    def emit(text: str) -> None:
        nonlocal buf, carried
        chunks.append(text.strip())
        tail = _overlap_tail(text)
        buf, carried = tail, len(tail)

    for para in paragraphs:
        if len(buf) - carried >= MIN_CHUNK and len(buf) + 1 + len(para) > MAX_CHUNK:
            emit(buf)
        buf = f"{buf}\n{para}" if buf else para
        while len(buf) > MAX_CHUNK:
            cut = _cut_point(buf)
            rest = buf[cut:]
            emit(buf[:cut])  # buf 變成重疊的尾巴
            buf += rest
    leftover = buf[carried:].strip()
    if leftover:
        if chunks and len(leftover) < MIN_TAIL:
            chunks[-1] = f"{chunks[-1]}\n{leftover}"
        else:
            chunks.append(buf.strip())
    return chunks


def pdf_chunks(path: Path, char_fixes: dict[int, str] | None = None) -> list[Chunk]:
    out: list[Chunk] = []
    section = ""
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc):
            paragraphs = page_paragraphs(page, char_fixes)
            text = "".join(paragraphs)
            if len(text) < MIN_PAGE_CHARS or TOC_HINT.search(text):
                continue
            # 章節標題出現在頁中間時，標題之前的段落屬於舊章節：依標題把段落分組再各自切
            groups: list[tuple[str, list[str]]] = [(section, [])]
            for para in paragraphs:
                title = chapter_title(para)
                if title:
                    section = title
                    groups.append((section, []))
                groups[-1][1].append(para)
            for title, paras in groups:
                out += [Chunk(i + 1, title, c) for c in chunk_paragraphs(paras) if len(c) >= MIN_KEEP]
    return out


def embedding_text(company: str, year: int, chunk: Chunk) -> str:
    head = f"{company} {year} 年報" + (f"｜{chunk.title}" if chunk.title else "")
    return f"{head}\n{chunk.content}"


# --- 資料庫 ---


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or os.environ.get("DATABASE_URL", DEFAULT_DB))


def ensure_schema(conn: psycopg.Connection, dim: int) -> None:
    conn.execute(SCHEMA.format(dim=dim))
    register_vector(conn)
    # 表已存在時 CREATE TABLE IF NOT EXISTS 不會改維度；換了 embedding 模型要明確報錯，而不是寫入時才炸
    existing = conn.execute(
        "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
    ).fetchone()[0]
    if existing != dim:
        raise RuntimeError(f"chunks.embedding 是 vector({existing})，但 EMBEDDING_DIM={dim}；換模型要先重建資料表")
    conn.commit()


def ingest_pdf(
    conn: psycopg.Connection,
    path: Path,
    embedder: Embedder,
    companies: dict[str, str],
    char_fixes_path: Path | None = None,
) -> list[Chunk]:
    ticker, year = parse_filename(path)
    if ticker not in companies:
        raise KeyError(f"{ticker} 不在公司對照表，請加進 config/companies.json")
    company = companies[ticker]
    fixes = load_char_fixes(path.name, char_fixes_path) if char_fixes_path else load_char_fixes(path.name)
    chunks = pdf_chunks(path, fixes)
    vectors = embedder.embed([embedding_text(company, year, c) for c in chunks])

    with conn.transaction():
        conn.execute("DELETE FROM chunks WHERE ticker = %s AND year = %s AND kind = 'text'", (ticker, year))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (ticker, company, year, page, kind, title, content, embedding)"
                " VALUES (%s, %s, %s, %s, 'text', %s, %s, %s)",
                [
                    (ticker, company, year, c.page, c.title or None, c.content, v)
                    for c, v in zip(chunks, vectors)
                ],
            )
    return chunks


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path, help="PDF 檔或資料夾")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    args = ap.parse_args()

    files: list[Path] = []
    for p in args.paths:
        files += sorted(p.glob("*.pdf")) if p.is_dir() else [p]
    companies = load_companies()
    embedder = CachedEmbedder(get_embedder(), args.cache)
    dim = int(os.environ.get("EMBEDDING_DIM", "1024"))

    start = time.perf_counter()
    with connect() as conn:
        ensure_schema(conn, dim)
        print(f"{'檔案':<16}{'chunk':>7}{'平均字數':>9}{'最短':>6}{'最長':>6}{'秒':>8}")
        for f in files:
            t0 = time.perf_counter()
            chunks = ingest_pdf(conn, f, embedder, companies)
            lengths = [len(c.content) for c in chunks]
            print(f"{f.name:<18}{len(chunks):>7}{statistics.mean(lengths):>10.0f}{min(lengths):>7}{max(lengths):>7}"
                  f"{time.perf_counter() - t0:>9.1f}")
    print(f"\n總耗時 {time.perf_counter() - start:.1f} 秒；embedding 快取命中 {embedder.hits}、新算 {embedder.misses}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

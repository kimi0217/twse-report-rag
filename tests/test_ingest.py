import json
import os
import sys
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import ingest  # noqa: E402
from common.embeddings import CachedEmbedder  # noqa: E402
from common.textnorm import normalize_text  # noqa: E402
from ingest import (  # noqa: E402
    MAX_CHUNK,
    MIN_CHUNK,
    Chunk,
    chapter_title,
    chunk_paragraphs,
    embedding_text,
    page_paragraphs,
    page_regions,
    MAX_TABLE_CHARS,
    load_table_boxes,
    parse_filename,
    pdf_chunks,
    table_chunks,
)

FONT = "china-t"
SENTENCE = "本公司持續投入先進製程研發，提升產能利用率與營運效率。"  # 27 字


class FakeEmbedder:
    """不呼叫 Ollama：用文字長度與 hash 產生固定向量，並記錄被呼叫了幾次。"""

    model = "fake"
    dim = 8

    def __init__(self) -> None:
        self.calls = 0
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        self.texts += texts
        return [[float(len(t))] + [float((hash(t) >> i) & 1) for i in range(self.dim - 1)] for t in texts]


def _pdf(path: Path, pages: list[dict]) -> Path:
    """pages: [{"size": (w, h), "texts": [(x, y, w, h, text), ...]}]，依清單順序繪製。"""
    doc = pymupdf.open()
    for spec in pages:
        page = doc.new_page(width=spec["size"][0], height=spec["size"][1])
        for x, y, w, h, text in spec["texts"]:
            page.insert_textbox(pymupdf.Rect(x, y, x + w, y + h), text, fontname=FONT, fontsize=10)
    doc.save(path)
    return path


# --- 檔名與章節 ---


def test_parse_filename():
    assert parse_filename(Path("2330_2025.pdf")) == ("2330", 2025)
    with pytest.raises(ValueError):
        parse_filename(Path("tsmc.pdf"))


@pytest.mark.parametrize(
    "para, title",
    [
        ("壹、致股東報告書", "致股東報告書"),
        ("4. 營運概況", "營運概況"),
        ("伍、營運概況 086", "營運概況"),  # 章節扉頁小目錄後面的頁碼要去掉
        ("2月10日(民國114年第一次常會)", None),
        ("3. 持續強化高效能運算", None),  # 編號條列，不是法定章節
        ("營運概況", None),  # 沒有編號：目錄頁或內文提到章節名稱
    ],
)
def test_chapter_title(para, title):
    assert chapter_title(para) == title


def test_embedding_text_prefix():
    assert embedding_text("台積電", 2025, Chunk(3, "營運概況", "內文")) == "台積電 2025 年報｜營運概況\n內文"
    assert embedding_text("台積電", 2025, Chunk(3, "", "內文")) == "台積電 2025 年報\n內文"


# --- 切 chunk ---


def test_chunks_respect_length_and_overlap():
    paragraphs = [SENTENCE * 3 for _ in range(20)]  # 每段 81 字，共 1620 字
    chunks = chunk_paragraphs(paragraphs)
    assert len(chunks) >= 3
    assert all(len(c) <= MAX_CHUNK for c in chunks)
    assert all(len(c) >= MIN_CHUNK for c in chunks[:-1])
    for prev, nxt in zip(chunks, chunks[1:]):
        head = nxt.split("\n")[0]
        assert prev.endswith(head[-20:])  # 下一塊開頭是上一塊的尾巴


def test_long_paragraph_split_at_sentence_end():
    chunks = chunk_paragraphs([SENTENCE * 40])  # 單一段落 1080 字
    assert all(len(c) <= MAX_CHUNK for c in chunks)
    assert all(c.endswith("。") for c in chunks)


def test_short_tail_merged_into_previous():
    chunks = chunk_paragraphs([SENTENCE * 15, "補充說明。"])  # 405 字 + 5 字零頭
    assert len(chunks) == 1
    assert chunks[0].endswith("補充說明。")


def test_short_page_text_is_single_chunk():
    assert chunk_paragraphs([SENTENCE * 5]) == [SENTENCE * 5]


# --- PDF 頁面處理 ---


def test_page_regions_split_landscape_only():
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    doc.new_page(width=1190, height=842)
    assert len(page_regions(doc[0])) == 1
    left, right = page_regions(doc[1])
    assert left.x1 == right.x0 == 595


def test_spread_page_reads_left_before_right(tmp_path):
    # 故意先畫右半邊，而且兩半交錯繪製：仍要整個左半頁讀完才讀右半頁。
    # 半頁內維持繪製順序，不依座標排序：台積電的半頁裡還有兩欄，依 y 排序會讓兩欄段落交錯
    path = _pdf(tmp_path / "2330_2025.pdf", [{
        "size": (1190, 842),
        "texts": [
            (650, 50, 480, 60, "右半頁第一段。"),
            (50, 50, 480, 60, "左半頁第一段。"),
            (650, 300, 480, 60, "右半頁第二段。"),
            (50, 300, 480, 60, "左半頁第二段。"),
        ],
    }])
    with pymupdf.open(path) as doc:
        assert page_paragraphs(doc[0]) == ["左半頁第一段。", "左半頁第二段。", "右半頁第一段。", "右半頁第二段。"]


def test_wrapped_lines_joined_into_paragraph(tmp_path):
    # 台積電的 PDF 一行一個文字區塊：沒以句尾標點結束的區塊要跟下一個接起來，不能留下「強\n勁」
    path = _pdf(tmp_path / "x.pdf", [{"size": (595, 842), "texts": [
        (50, 50, 480, 20, "人工智慧相關的需求在全年間保持強"),
        (50, 70, 480, 20, "勁，非AI相關的終端市場則已觸底。"),
        (50, 90, 480, 20, "下一段落。"),
    ]}])
    with pymupdf.open(path) as doc:
        assert page_paragraphs(doc[0]) == ["人工智慧相關的需求在全年間保持強勁,非AI相關的終端市場則已觸底。", "下一段落。"]


def test_char_fixes_applied_to_page_text(tmp_path):
    # 內建測試字型沒有「倝」的字形，改用字型裡有的字模擬錯字
    path = _pdf(tmp_path / "x.pdf", [{"size": (595, 842), "texts": [(50, 50, 480, 60, "單位：新台幣仟元")]}])
    with pymupdf.open(path) as doc:
        assert page_paragraphs(doc[0], str.maketrans({"仟": "千"})) == ["單位:新台幣千元"]


def test_pdf_chunks_skip_short_and_toc_pages_and_carry_section(tmp_path):
    body = SENTENCE * 6
    path = _pdf(tmp_path / "2330_2025.pdf", [
        {"size": (595, 842), "texts": [(50, 50, 480, 60, "台積公司 2025 年報")]},  # 封面：太短
        {"size": (595, 842), "texts": [(50, 50, 480, 300, "目錄\n壹、致股東報告書 ........ 3\n" + body)]},
        {"size": (595, 842), "texts": [(50, 50, 480, 30, "壹、致股東報告書"), (50, 100, 480, 400, body)]},
        {"size": (595, 842), "texts": [(50, 50, 480, 400, body)]},  # 沒有標題：沿用上一頁章節
    ])
    chunks = pdf_chunks(path)
    assert [c.page for c in chunks] == [3, 4]
    assert [c.title for c in chunks] == ["致股東報告書", "致股東報告書"]


# --- embedding 快取 ---


def test_cached_embedder_skips_known_texts(tmp_path):
    cache = tmp_path / "cache.jsonl"
    fake = FakeEmbedder()
    first = CachedEmbedder(fake, cache).embed(["甲", "乙", "甲"])
    assert fake.texts == ["甲", "乙"]  # 同一批重複的文字只算一次

    fake2 = FakeEmbedder()
    again = CachedEmbedder(fake2, cache).embed(["乙", "甲", "丙"])  # 重新載入快取檔
    assert fake2.texts == ["丙"]
    assert again[:2] == [first[1], first[0]]


def test_cached_embedder_batches(tmp_path):
    fake = FakeEmbedder()
    CachedEmbedder(fake, tmp_path / "c.jsonl", batch_size=2).embed(["a", "b", "c", "d", "e"])
    assert fake.calls == 3


# --- 資料庫整合測試（連不上本機 twse_test 就跳過）---


@pytest.fixture
def db():
    psycopg = pytest.importorskip("psycopg")
    url = os.environ.get("TEST_DATABASE_URL", "postgresql://twse:twse@localhost:5432/twse_test")
    try:
        conn = psycopg.connect(url, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip("連不上測試資料庫")
    conn.execute("DROP TABLE IF EXISTS chunks")
    conn.commit()
    ingest.ensure_schema(conn, FakeEmbedder.dim)
    yield conn
    conn.close()


def _count(conn, kind: str) -> int:
    return conn.execute("SELECT count(*) FROM chunks WHERE kind = %s", (kind,)).fetchone()[0]


def test_ingest_is_idempotent_and_keeps_tables(db, tmp_path):
    path = _pdf(tmp_path / "2330_2025.pdf", [{"size": (595, 842), "texts": [(50, 50, 480, 600, SENTENCE * 30)]}])
    db.execute(
        "INSERT INTO chunks (ticker, company, year, page, kind, title, content, embedding)"
        " VALUES ('2330', '台積電', 2025, 64, 'table', '表', '內容', %s)",
        ([0.0] * FakeEmbedder.dim,),
    )
    db.commit()
    companies = {"2330": "台積電"}

    first = ingest.ingest_pdf(db, path, FakeEmbedder(), companies, tables_dir=tmp_path)
    ingest.ingest_pdf(db, path, FakeEmbedder(), companies, tables_dir=tmp_path)

    assert _count(db, "text") == len(first) > 1
    assert _count(db, "table") == 1  # 重跑內文不能刪到表格 chunk
    row = db.execute("SELECT company, year, page FROM chunks WHERE kind = 'text' LIMIT 1").fetchone()
    assert row == ("台積電", 2025, 1)


def test_ingest_rejects_unknown_ticker(db, tmp_path):
    path = _pdf(tmp_path / "9999_2025.pdf", [{"size": (595, 842), "texts": [(50, 50, 480, 600, SENTENCE * 10)]}])
    with pytest.raises(KeyError):
        ingest.ingest_pdf(db, path, FakeEmbedder(), {"2330": "台積電"}, tables_dir=tmp_path)


def test_schema_dimension_mismatch_raises(db):
    with pytest.raises(RuntimeError):
        ingest.ensure_schema(db, FakeEmbedder.dim + 1)


# --- 表格 chunk ---


def _jsonl(path: Path, tables: list[dict]) -> Path:
    path.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tables), encoding="utf-8")
    return path


TABLE = {
    "file": "2330_2025.pdf", "page": 65, "table_index": 2, "tool": "pymupdf",
    "title": "台積公司及子公司財務報表（單位:新台幣仟元）",
    "markdown": "| 項目 | 民國114年 |\n|---|---|\n| 營業收入淨額 | 3,809,054,272 |",
}


def test_table_chunk_content_starts_with_title(tmp_path):
    [c] = table_chunks(_jsonl(tmp_path / "2330_2025.jsonl", [TABLE]))
    assert (c.page, c.title) == (65, TABLE["title"])
    assert c.content == f"{TABLE['title']}\n\n{TABLE['markdown']}"
    assert embedding_text("台積電", 2025, c).startswith(f"台積電 2025 年報｜{TABLE['title']}\n")


def test_table_without_title_keeps_markdown_only(tmp_path):
    [c] = table_chunks(_jsonl(tmp_path / "2330_2025.jsonl", [{**TABLE, "title": ""}]))
    assert c.content == TABLE["markdown"]


def test_oversized_table_raises_instead_of_splitting(tmp_path):
    big = {**TABLE, "markdown": "| 項目 |\n" + "| 營業收入 |\n" * (MAX_TABLE_CHARS // 8)}
    with pytest.raises(ValueError, match="超過"):
        table_chunks(_jsonl(tmp_path / "2330_2025.jsonl", [big]))


def test_text_and_table_reingest_do_not_touch_each_other(db, tmp_path):
    pdf = _pdf(tmp_path / "2330_2025.pdf", [{"size": (595, 842), "texts": [(50, 50, 480, 600, SENTENCE * 30)]}])
    # jsonl 放另一個資料夾：這裡只測 kind 互不影響，不測表格範圍去重
    tables = tmp_path / "tables"
    tables.mkdir()
    jsonl = _jsonl(tables / "2330_2025.jsonl", [TABLE, {**TABLE, "page": 64, "table_index": 1}])
    companies = {"2330": "台積電"}

    texts = ingest.ingest_pdf(db, pdf, FakeEmbedder(), companies, tables_dir=tmp_path)
    ingest.ingest_tables(db, jsonl, FakeEmbedder(), companies)
    ingest.ingest_tables(db, jsonl, FakeEmbedder(), companies)  # 重跑表格
    assert (_count(db, "text"), _count(db, "table")) == (len(texts), 2)

    ingest.ingest_pdf(db, pdf, FakeEmbedder(), companies, tables_dir=tmp_path)  # 重跑內文
    assert (_count(db, "text"), _count(db, "table")) == (len(texts), 2)
    row = db.execute("SELECT title, page FROM chunks WHERE kind = 'table' ORDER BY page DESC LIMIT 1").fetchone()
    assert row == (TABLE["title"], 65)


# --- 表格範圍內的內文去重 ---


def _page_with_table(tmp_path: Path) -> Path:
    # 上方說明文字在表格外；下方一行模擬 PDF 文字層裡攤平的表格數字
    return _pdf(tmp_path / "2382_2025.pdf", [{"size": (595, 842), "texts": [
        (50, 50, 480, 200, SENTENCE * 6),
        (50, 300, 480, 30, "每股盈餘(元)15.4919.453.96 25.56%"),
    ]}])


def test_load_table_boxes(tmp_path):
    path = _jsonl(tmp_path / "2382_2025.jsonl", [{**TABLE, "page": 1, "bbox": [40, 290, 560, 340]}, TABLE])
    boxes = load_table_boxes(path)
    assert list(boxes) == [1]  # 沒有 bbox 的舊格式資料略過
    assert load_table_boxes(tmp_path / "missing.jsonl") == {}


def test_text_inside_table_bbox_skipped(tmp_path):
    path = _page_with_table(tmp_path)
    with pymupdf.open(path) as doc:
        assert len(page_paragraphs(doc[0])) == 2
        kept = page_paragraphs(doc[0], table_boxes=[pymupdf.Rect(40, 290, 560, 340)])
    assert kept == [normalize_text(SENTENCE * 6)]


def test_ingest_pdf_uses_table_boxes(db, tmp_path):
    path = _page_with_table(tmp_path)
    _jsonl(tmp_path / "2382_2025.jsonl", [{**TABLE, "page": 1, "bbox": [40, 290, 560, 340]}])
    chunks = ingest.ingest_pdf(db, path, FakeEmbedder(), {"2382": "廣達"}, tables_dir=tmp_path)
    assert chunks and not any("15.49" in c.content for c in chunks)

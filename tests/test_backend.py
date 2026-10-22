import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from backend.app.convert import StreamConverter, to_traditional  # noqa: E402
from backend.app.main import Deps, create_app  # noqa: E402
from backend.app.prompt import NO_ANSWER, SYSTEM_PROMPT, build_messages, order_context  # noqa: E402
from backend.app.retrieval import PgRetriever, RetrievedChunk  # noqa: E402
from backend.app.settings import Settings  # noqa: E402
from common.companies import Company, find_tickers, load_companies  # noqa: E402

COMPANIES = {
    "2330": Company("2330", "台積電", ("台積公司", "TSMC")),
    "2382": Company("2382", "廣達", ("廣達電腦", "Quanta")),
}


def _chunk(kind="text", distance=0.2, page=7, ticker="2330", company="台積電", title=None, content="內文段落"):
    return RetrievedChunk(ticker, company, 2025, page, kind, title, content, distance)


class FakeEmbedder:
    model = "fake"

    def __init__(self):
        self.texts = []

    async def embed(self, texts):
        self.texts += texts
        return [[0.0] * 4 for _ in texts]


class FakeRetriever:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    async def search(self, vector, tickers, year, k):
        self.calls.append({"tickers": tickers, "year": year, "k": k})
        return self.chunks[:k]

    async def companies(self):
        return [("2330", "台積電", [2025]), ("2382", "廣達", [2024, 2025])]


class FakeLLM:
    def __init__(self, pieces=("台积电", "合并营业收入", "为 3,809,054,272 ", "新台币仟元 [1]。")):
        self.pieces = pieces
        self.messages = None

    async def stream(self, messages):
        self.messages = messages
        for p in self.pieces:
            yield p


class BrokenLLM(FakeLLM):
    async def stream(self, messages):
        yield "部分"
        raise RuntimeError("ollama 掛了")


def _client(chunks, llm=None, settings=None):
    deps = Deps(settings or Settings(), FakeEmbedder(), FakeRetriever(chunks), llm or FakeLLM(), COMPANIES)
    return TestClient(create_app(deps)), deps


def _events(resp):
    """把 SSE 回應拆成 [(event, data), ...]。"""
    out = []
    for block in resp.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


# --- /health、/companies ---


def test_health():
    client, _ = _client([])
    with client:
        assert client.get("/health").json() == {"status": "ok"}


def test_companies():
    client, _ = _client([])
    with client:
        assert client.get("/companies").json() == [
            {"ticker": "2330", "company": "台積電", "years": [2025]},
            {"ticker": "2382", "company": "廣達", "years": [2024, 2025]},
        ]


# --- /ask ---


def test_ask_streams_tokens_then_sources():
    chunks = [_chunk("text", 0.20, page=7), _chunk("table", 0.22, page=65, title="合併 台積公司及子公司財務報表")]
    client, deps = _client(chunks)
    with client:
        events = _events(client.post("/ask", json={"question": "台積電合併營收？"}))
    kinds = [e for e, _ in events]
    assert kinds[-1] == "sources" and set(kinds[:-1]) == {"token"}
    answer = "".join(d["text"] for e, d in events if e == "token")
    assert answer == "台積電合併營業收入為 3,809,054,272 新台幣仟元 [1]。"  # 簡轉繁，「台」不變「臺」
    sources = events[-1][1]
    # 表格排第一，編號跟 context 的 [1][2] 一致
    assert [(s["index"], s["kind"], s["page"]) for s in sources] == [(1, "table", 65), (2, "text", 7)]
    assert sources[1]["content"] == "內文段落"  # 完整內容給前端展開卡片
    assert deps.llm.messages[1]["content"].index("[1] 台積電 2025 p65（表格）") < deps.llm.messages[1]["content"].index("[2]")


def test_far_chunks_answer_without_calling_llm():
    llm = FakeLLM()
    client, _ = _client([_chunk(distance=0.42)], llm=llm, settings=Settings(no_answer_distance=0.33))
    with client:
        events = _events(client.post("/ask", json={"question": "長榮航空 2025 年營收是多少？"}))
    assert events == [("token", {"text": NO_ANSWER}), ("sources", [])]
    assert llm.messages is None


def test_empty_retrieval_answers_no_data():
    client, _ = _client([])
    with client:
        assert _events(client.post("/ask", json={"question": "台積電營收"}))[0] == ("token", {"text": NO_ANSWER})


def test_tickers_detected_from_question_alias():
    client, deps = _client([_chunk()])
    with client:
        client.post("/ask", json={"question": "TSMC 和廣達電腦的營收"})
    assert deps.retriever.calls[0]["tickers"] == ["2330", "2382"]


def test_explicit_filters_passed_to_retrieval():
    client, deps = _client([_chunk()])
    with client:
        client.post("/ask", json={"question": "營收是多少", "tickers": ["2382"], "year": 2025})
    assert deps.retriever.calls[0] == {"tickers": ["2382"], "year": 2025, "k": 8}


def test_unknown_company_not_filtered():
    client, deps = _client([_chunk()])
    with client:
        client.post("/ask", json={"question": "長榮航空的營收"})
    assert deps.retriever.calls[0]["tickers"] is None  # 交給距離門檻判斷


def test_question_normalized_before_embedding():
    client, deps = _client([_chunk()])
    with client:
        client.post("/ask", json={"question": "台積電營收１２３"})
    assert deps.embedder.texts == ["台積電營收123"]


def test_llm_failure_sends_error_event():
    client, _ = _client([_chunk()], llm=BrokenLLM())
    with client:
        events = _events(client.post("/ask", json={"question": "台積電營收"}))
    assert events[-1][0] == "error"
    assert "ollama 掛了" in events[-1][1]["message"]


def test_empty_question_rejected():
    client, _ = _client([])
    with client:
        assert client.post("/ask", json={"question": ""}).status_code == 422


# --- prompt 與 context ---


def test_order_context_puts_tables_first_keeping_distance_order():
    chunks = [_chunk("text", 0.1, page=1), _chunk("table", 0.3, page=2), _chunk("text", 0.2, page=3),
              _chunk("table", 0.4, page=4)]
    assert [c.page for c in order_context(chunks)] == [2, 4, 1, 3]


def test_prompt_rules_and_context_labels():
    messages = build_messages("台積電營收？", [_chunk("table", title="合併 財報", content="| 營業收入 | 1 |")])
    assert messages[0]["content"] == SYSTEM_PROMPT
    for rule in ["只能根據", "表格", "單位", NO_ANSWER, "繁體中文"]:
        assert rule in SYSTEM_PROMPT
    assert "[1] 台積電 2025 p7（表格）｜合併 財報\n| 營業收入 | 1 |" in messages[1]["content"]
    assert messages[1]["content"].endswith("問題：台積電營收？")


# --- 繁簡轉換 ---


def test_stream_converter_keeps_phrases_across_tokens():
    conv = StreamConverter()
    # 「软件」被切成兩個 token：逐 token 轉會變成「軟件」，暫存到標點再轉才會是「軟體」
    out = conv.feed("我们的软") + conv.feed("件，") + conv.feed("网络服务") + conv.flush()
    assert out == "我們的軟體，網路服務"


def test_to_traditional_keeps_tai():
    assert to_traditional("台积电与新台币") == "台積電與新台幣"


def test_stream_converter_flushes_long_text_without_punctuation():
    conv = StreamConverter()
    assert conv.feed("一" * 25) == "一" * 25


# --- 公司別名 ---


def test_find_tickers_with_aliases_and_codes():
    companies = load_companies()
    assert find_tickers("TSMC 營收", companies) == ["2330"]
    assert find_tickers("tsmc 營收", companies) == ["2330"]
    assert find_tickers("2382 每股盈餘", companies) == ["2382"]
    assert find_tickers("股價 23300 元", companies) == []
    assert find_tickers("長榮航空營收", companies) == []


def test_companies_config_has_aliases():
    companies = load_companies()
    assert set(companies) == {"2308", "2317", "2330", "2382", "2454"}
    assert all(c.aliases for c in companies.values())


# --- PgRetriever 整合測試（連不上本機 twse_test 就跳過）---


def test_pg_retriever_filters_in_sql():
    psycopg = pytest.importorskip("psycopg")
    import ingest

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://twse:twse@localhost:5432/twse_test")
    try:
        conn = psycopg.connect(url, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip("連不上測試資料庫")
    with conn:
        conn.execute("DROP TABLE IF EXISTS chunks")
        conn.commit()
        ingest.ensure_schema(conn, 3)
        rows = [("2330", "台積電", 2025, [1.0, 0.0, 0.0]), ("2382", "廣達", 2025, [0.9, 0.1, 0.0]), ("2330", "台積電", 2024, [1.0, 0.05, 0.0])]
        for t, c, y, v in rows:
            conn.execute(
                "INSERT INTO chunks (ticker, company, year, page, kind, content, embedding) VALUES (%s,%s,%s,1,'text','x',%s)",
                (t, c, y, v),
            )
        conn.commit()

    async def run():
        r = PgRetriever(url)
        await r.open()
        try:
            all_hits = await r.search([1.0, 0.0, 0.0], None, None, 8)
            only_2382 = await r.search([1.0, 0.0, 0.0], ["2382"], None, 8)
            only_2025 = await r.search([1.0, 0.0, 0.0], None, 2025, 8)
            companies = await r.companies()
        finally:
            await r.close()
        return all_hits, only_2382, only_2025, companies

    all_hits, only_2382, only_2025, companies = asyncio.run(run())
    assert [h.ticker for h in all_hits][0] == "2330" and all_hits[0].distance == pytest.approx(0)
    assert {h.ticker for h in only_2382} == {"2382"}
    assert {h.year for h in only_2025} == {2025}
    assert companies == [("2330", "台積電", [2024, 2025]), ("2382", "廣達", [2025])]


def test_table_unit_surfaced_in_context():
    messages = build_messages("營收？", [_chunk("table", title="合併 台積公司及子公司財務報表（單位:新台幣仟元）")])
    assert "（表格）｜合併 台積公司及子公司財務報表（單位:新台幣仟元）\n金額單位：新台幣仟元\n" in messages[1]["content"]
    assert "金額單位" not in build_messages("營收？", [_chunk("text")])[1]["content"]

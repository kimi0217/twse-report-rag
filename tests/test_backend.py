import asyncio
import json

import httpx
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from backend.app.convert import StreamConverter, to_traditional  # noqa: E402
from backend.app.main import Deps, create_app, no_answer_threshold  # noqa: E402
from backend.app.llm import OllamaChat, OutputLimitReached  # noqa: E402
from backend.app.prompt import NO_ANSWER, SYSTEM_PROMPT, TRUNCATED_NOTE, build_messages  # noqa: E402
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
    # 依距離排序（不再表格優先），編號跟 context 的 [1][2] 一致
    assert [(s["index"], s["kind"], s["page"]) for s in sources] == [(1, "text", 7), (2, "table", 65)]
    assert sources[0]["content"] == "內文段落"  # 完整內容給前端展開卡片
    context = deps.llm.messages[1]["content"]
    assert context.index("[1] 台積電 2025 p7（內文）") < context.index("[2] 台積電 2025 p65（表格）")


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


def test_context_keeps_retrieval_order():
    chunks = [_chunk("text", 0.1, page=1), _chunk("table", 0.3, page=2), _chunk("text", 0.2, page=3)]
    content = build_messages("營收？", chunks)[1]["content"]
    assert content.index("p1（內文）") < content.index("p2（表格）") < content.index("p3（內文）")


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


# --- 拒答門檻依問題是否提到已知公司 ---


def test_threshold_loosened_only_when_question_names_company():
    s = Settings(no_answer_distance=0.33, no_answer_distance_with_company=0.5)
    assert no_answer_threshold(s, "TSMC 去年稅後淨利？", COMPANIES) == 0.5
    assert no_answer_threshold(s, "大立光的毛利率？", COMPANIES) == 0.33


def test_named_company_answers_at_mid_distance():
    llm = FakeLLM()
    settings = Settings(no_answer_distance=0.33, no_answer_distance_with_company=0.5)
    client, _ = _client([_chunk(distance=0.42)], llm=llm, settings=settings)
    with client:
        events = _events(client.post("/ask", json={"question": "台積電 2 奈米量產了嗎？"}))
    assert llm.messages is not None and events[-1][0] == "sources"


def test_ui_ticker_filter_does_not_loosen_threshold():
    # 前端勾選公司不代表問題跟年報有關：「秋天的詩」即使只勾台積電也要拒答
    llm = FakeLLM()
    settings = Settings(no_answer_distance=0.33, no_answer_distance_with_company=0.5)
    client, _ = _client([_chunk(distance=0.42)], llm=llm, settings=settings)
    with client:
        events = _events(client.post("/ask", json={"question": "寫一首秋天的詩", "tickers": ["2330"]}))
    assert events == [("token", {"text": NO_ANSWER}), ("sources", [])]
    assert llm.messages is None


# --- 輸出上限與整體逾時 ---


class LimitedLLM(FakeLLM):
    """模擬 Ollama 回報 done_reason=length：先輸出一段，再丟出 OutputLimitReached。"""

    async def stream(self, messages):
        self.messages = messages
        yield "董事長是蔡明介，蔡明介，蔡明介"
        raise OutputLimitReached()


class EndlessLLM(FakeLLM):
    """模擬停不下來的生成：一直輸出，直到被整體逾時中止。"""

    async def stream(self, messages):
        self.messages = messages
        while True:
            yield "重複，"
            await asyncio.sleep(0.01)


class SlowEmbedder(FakeEmbedder):
    async def embed(self, texts):
        await asyncio.sleep(5)
        return await super().embed(texts)


def test_output_limit_appends_truncation_note_and_keeps_sources():
    client, _ = _client([_chunk()], llm=LimitedLLM())
    with client:
        events = _events(client.post("/ask", json={"question": "聯發科董事長？"}))
    tokens = [d["text"] for e, d in events if e == "token"]
    assert tokens[-1] == TRUNCATED_NOTE and "蔡明介" in "".join(tokens)
    assert events[-1][0] == "sources" and len(events[-1][1]) == 1


def test_endless_generation_stopped_by_total_timeout():
    client, _ = _client([_chunk()], llm=EndlessLLM(), settings=Settings(ask_timeout_seconds=0.3))
    with client:
        events = _events(client.post("/ask", json={"question": "聯發科董事長？"}))
    tokens = [d["text"] for e, d in events if e == "token"]
    assert tokens[-1] == TRUNCATED_NOTE and len(tokens) > 1
    assert events[-1][0] == "sources"


def test_timeout_before_answer_sends_error():
    deps = Deps(Settings(ask_timeout_seconds=0.2), SlowEmbedder(), FakeRetriever([_chunk()]), FakeLLM(), COMPANIES)
    with TestClient(create_app(deps)) as client:
        events = _events(client.post("/ask", json={"question": "台積電營收"}))
    assert events == [("error", {"message": "回答逾時（超過 0.2 秒）"})]


def _ollama_chat_with(lines, seen_bodies):
    def handler(request):
        seen_bodies.append(json.loads(request.content))
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())

    llm = OllamaChat("qwen", "http://ollama", 0.1, max_tokens=64)
    llm._client = httpx.AsyncClient(base_url="http://ollama", transport=httpx.MockTransport(handler))
    return llm


async def _collect(llm):
    out = []
    try:
        async for t in llm.stream([{"role": "user", "content": "hi"}]):
            out.append(t)
    except OutputLimitReached:
        out.append("<limit>")
    return out


def test_ollama_chat_sends_num_predict_and_detects_length_stop():
    bodies = []
    lines = [{"message": {"content": "甲"}, "done": False}, {"message": {"content": "乙"}, "done": True, "done_reason": "length"}]
    assert asyncio.run(_collect(_ollama_chat_with(lines, bodies))) == ["甲", "乙", "<limit>"]
    assert bodies[0]["options"]["num_predict"] == 64 and bodies[0]["think"] is False


def test_ollama_chat_normal_stop_not_truncated():
    lines = [{"message": {"content": "甲"}, "done": True, "done_reason": "stop"}]
    assert asyncio.run(_collect(_ollama_chat_with(lines, []))) == ["甲"]

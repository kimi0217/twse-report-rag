"""FastAPI 服務：/health、/companies、/ask（SSE）。

啟動（在專案根目錄）：
    uvicorn backend.app.main:app --port 8000

線上服務只讀資料庫做檢索與回答；資料由 scripts/ingest.py 離線寫入。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from backend.app.convert import StreamConverter
from backend.app.llm import LLM, OllamaChat, OutputLimitReached
from backend.app.prompt import NO_ANSWER, TRUNCATED_NOTE, build_messages
from backend.app.retrieval import PgRetriever, RetrievedChunk, Retriever
from backend.app.schemas import AskRequest, CompanyOut, Health, Source
from backend.app.settings import Settings
from common.companies import Company, find_tickers, load_companies
from common.embeddings import AsyncEmbedder, get_async_embedder
from common.textnorm import normalize_text

log = logging.getLogger(__name__)


@dataclass
class Deps:
    settings: Settings
    embedder: AsyncEmbedder
    retriever: Retriever
    llm: LLM
    companies: dict[str, Company]


def sse(event: str, data: object) -> str:
    # data 一律 JSON：回答裡的換行如果直接寫進 data 欄位，會被 SSE 當成欄位結束
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def to_sources(chunks: list[RetrievedChunk], snippet_chars: int) -> list[Source]:
    return [
        Source(
            index=i, ticker=c.ticker, company=c.company, year=c.year, page=c.page,
            kind=c.kind, title=c.title, snippet=c.content[:snippet_chars], content=c.content,
        )
        for i, c in enumerate(chunks, 1)
    ]


def no_answer_threshold(settings: Settings, question: str, companies: dict[str, Company]) -> float:
    """問題文字裡提到已知公司（含別名）才放寬；前端勾選的公司篩選不算，那不代表問題本身跟年報有關。"""
    if find_tickers(question, companies):
        return settings.no_answer_distance_with_company
    return settings.no_answer_distance


def resolve_tickers(req: AskRequest, question: str, companies: dict[str, Company]) -> list[str] | None:
    """使用者沒指定公司時，用問題裡的公司名稱／別名自動篩選，避免拿別家公司的段落回答。"""
    if req.tickers:
        return req.tickers
    return find_tickers(question, companies) or None


async def answer_events(req: AskRequest, deps: Deps) -> AsyncIterator[str]:
    s = deps.settings
    # 整個 /ask 共用一個截止時間：檢索階段逾時回 error；回答生成中逾時就截斷收尾，照常送出 sources
    deadline = asyncio.get_running_loop().time() + s.ask_timeout_seconds
    try:
        question = normalize_text(req.question)
        tickers = resolve_tickers(req, question, deps.companies)
        async with asyncio.timeout_at(deadline):
            [vector] = await deps.embedder.embed([question])
            chunks = await deps.retriever.search(vector, tickers, req.year, s.top_k)

        # 程式層的查無資料：最相近的段落都不夠近，就不讓 LLM 有機會拿不相關的段落硬湊答案
        threshold = no_answer_threshold(s, question, deps.companies)
        if not chunks or min(c.distance for c in chunks) > threshold:
            log.info("no answer: tickers=%s best=%s", tickers, chunks and min(c.distance for c in chunks))
            yield sse("token", {"text": NO_ANSWER})
            yield sse("sources", [])
            return

        # context 依向量距離排序：評估發現表格優先時，答案所在的內文被擠到後面，小模型就回答查無資料
        conv = StreamConverter()
        truncated = False
        try:
            async with asyncio.timeout_at(deadline):
                async for piece in deps.llm.stream(build_messages(question, chunks)):
                    if text := conv.feed(piece):
                        yield sse("token", {"text": text})
        except (OutputLimitReached, TimeoutError):
            # 小模型偶爾重複輸出停不下來：保留已輸出的部分並標明截斷，比讓前端一直轉圈好
            truncated = True
        if text := conv.flush():
            yield sse("token", {"text": text})
        if truncated:
            log.warning("answer truncated: %s", req.question)
            yield sse("token", {"text": TRUNCATED_NOTE})
        yield sse("sources", [src.model_dump() for src in to_sources(chunks, s.snippet_chars)])
    except TimeoutError:
        log.warning("retrieval timeout: %s", req.question)
        yield sse("error", {"message": f"回答逾時（超過 {s.ask_timeout_seconds:g} 秒）"})
    except Exception as exc:  # 串流已經開始，HTTP 狀態碼改不了，只能用 error 事件通知前端
        log.exception("ask failed")
        yield sse("error", {"message": f"{type(exc).__name__}: {exc}"})


def create_app(deps: Deps | None = None) -> FastAPI:
    """測試時傳入 fake 的 Deps；正式啟動時在 lifespan 裡建立真的連線。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if deps is not None:
            app.state.deps = deps
            yield
            return
        settings = Settings.from_env()
        retriever = PgRetriever(settings.database_url)
        await retriever.open()
        embedder = get_async_embedder()
        llm = OllamaChat(settings.llm_model, settings.ollama_url, settings.llm_temperature, settings.llm_max_tokens)
        app.state.deps = Deps(settings, embedder, retriever, llm, load_companies())
        try:
            yield
        finally:
            await retriever.close()
            await embedder.aclose()
            await llm.aclose()

    app = FastAPI(title="twse-report-rag", lifespan=lifespan)

    @app.get("/health")
    async def health() -> Health:
        return Health()

    @app.get("/companies")
    async def companies(request: Request) -> list[CompanyOut]:
        rows = await request.app.state.deps.retriever.companies()
        return [CompanyOut(ticker=t, company=c, years=list(y)) for t, c, y in rows]

    @app.post("/ask")
    async def ask(req: AskRequest, request: Request) -> StreamingResponse:
        return StreamingResponse(
            answer_events(req, request.app.state.deps),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()

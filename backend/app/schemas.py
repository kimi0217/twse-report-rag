from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    tickers: list[str] | None = None
    year: int | None = None


class Source(BaseModel):
    """順序與 context 的 [1]…[n] 編號一致，前端用 index 對應回答裡的引用。"""

    index: int
    ticker: str
    company: str
    year: int
    page: int
    kind: Literal["text", "table"]
    title: str | None
    snippet: str
    # 完整 chunk 內容，前端點卡片展開用；只在串流最後送一次，8 個 chunk 約數 KB
    content: str


class CompanyOut(BaseModel):
    ticker: str
    company: str
    years: list[int]


class Health(BaseModel):
    status: Literal["ok"] = "ok"

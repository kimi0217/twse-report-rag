"""向量檢索：問題向量 → pgvector cosine 距離前 k 名，tickers / year 在 SQL 裡篩選。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pgvector.psycopg import register_vector_async
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

SEARCH_SQL = """
SELECT ticker, company, year, page, kind, title, content, embedding <=> %(vec)s::vector AS distance
FROM chunks
WHERE (%(tickers)s::text[] IS NULL OR ticker = ANY(%(tickers)s))
  AND (%(year)s::int IS NULL OR year = %(year)s)
ORDER BY embedding <=> %(vec)s::vector
LIMIT %(k)s
"""

COMPANIES_SQL = """
SELECT ticker, company, array_agg(DISTINCT year ORDER BY year)
FROM chunks GROUP BY ticker, company ORDER BY ticker
"""


@dataclass(frozen=True)
class RetrievedChunk:
    ticker: str
    company: str
    year: int
    page: int
    kind: str
    title: str | None
    content: str
    distance: float


class Retriever(Protocol):
    async def search(
        self, vector: list[float], tickers: list[str] | None, year: int | None, k: int
    ) -> list[RetrievedChunk]: ...

    async def companies(self) -> list[tuple[str, str, list[int]]]: ...


async def _configure(conn: AsyncConnection) -> None:
    await register_vector_async(conn)
    # HNSW 先取 ef_search 個候選再套 WHERE；只篩一家公司時候選常不到 k 筆。
    # iterative_scan 讓 pgvector 在篩完不夠時繼續往下掃（pgvector 0.8+）
    await conn.execute("SET hnsw.iterative_scan = strict_order")
    await conn.commit()


class PgRetriever:
    def __init__(self, database_url: str) -> None:
        self.pool = AsyncConnectionPool(database_url, configure=_configure, open=False, min_size=1, max_size=5)

    async def open(self) -> None:
        await self.pool.open()

    async def close(self) -> None:
        await self.pool.close()

    async def search(
        self, vector: list[float], tickers: list[str] | None, year: int | None, k: int
    ) -> list[RetrievedChunk]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(SEARCH_SQL, {"vec": vector, "tickers": tickers, "year": year, "k": k})
            return [RetrievedChunk(*row) for row in await cur.fetchall()]

    async def companies(self) -> list[tuple[str, str, list[int]]]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(COMPANIES_SQL)
            return [tuple(row) for row in await cur.fetchall()]

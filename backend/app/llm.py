"""LLM 介面：業務邏輯只認 `LLM.stream()`；換模型改環境變數 LLM_MODEL。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Protocol

import httpx


class LLM(Protocol):
    def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]: ...


class OllamaChat:
    def __init__(self, model: str, base_url: str, temperature: float, timeout: float = 120.0) -> None:
        self.model = model
        self.temperature = temperature
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        body = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            # qwen3.5 預設會先輸出思考過程：實測一題 150 秒、關掉 7 秒，串流問答一定要關
            "think": False,
            "options": {"temperature": self.temperature},
        }
        async with self._client.stream("POST", "/api/chat", json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if data.get("error"):
                    raise RuntimeError(data["error"])
                if text := data.get("message", {}).get("content"):
                    yield text

    async def aclose(self) -> None:
        await self._client.aclose()

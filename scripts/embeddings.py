"""Embedding 介面：業務邏輯只認 `Embedder.embed()`，換供應商或模型改環境變數就好。

環境變數：
    EMBEDDING_PROVIDER  目前只有 ollama
    EMBEDDING_MODEL     預設 bge-m3
    OLLAMA_URL          預設 http://localhost:11434

`CachedEmbedder` 依「模型 + 文字」的 hash 快取到 jsonl：
ingestion 重跑時內容沒變的 chunk 不用重算，本機 CPU 跑 bge-m3 一份年報要好幾分鐘。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Protocol

import httpx

BATCH_SIZE = 32


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    def __init__(self, model: str, base_url: str, timeout: float = 300.0) -> None:
        self.model = model
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.post("/api/embed", json={"model": self.model, "input": texts})
        resp.raise_for_status()
        return resp.json()["embeddings"]


def get_embedder() -> Embedder:
    provider = os.environ.get("EMBEDDING_PROVIDER", "ollama")
    model = os.environ.get("EMBEDDING_MODEL", "bge-m3")
    if provider == "ollama":
        return OllamaEmbedder(model, os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    raise ValueError(f"不支援的 EMBEDDING_PROVIDER：{provider}")


def cache_key(model: str, text: str) -> str:
    # key 含模型名稱：換模型時舊向量維度、語意空間都不同，不能誤用
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


class CachedEmbedder:
    def __init__(self, inner: Embedder, path: Path, batch_size: int = BATCH_SIZE) -> None:
        self.inner = inner
        self.model = inner.model
        self.path = path
        self.batch_size = batch_size
        self.hits = 0
        self.misses = 0
        self._cache: dict[str, list[float]] = {}
        if path.exists():
            with open(path, encoding="utf-8") as fp:
                for line in fp:
                    if line.strip():
                        row = json.loads(line)
                        self._cache[row["key"]] = row["embedding"]

    def embed(self, texts: list[str]) -> list[list[float]]:
        keys = [cache_key(self.model, t) for t in texts]
        missing = list({k: t for k, t in zip(keys, texts) if k not in self._cache}.items())
        self.hits += len(texts) - len(missing)
        self.misses += len(missing)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for i in range(0, len(missing), self.batch_size):
            batch = missing[i : i + self.batch_size]
            vectors = self.inner.embed([t for _, t in batch])
            # 每批寫完就落檔：中途中斷，已算好的不會白費
            with open(self.path, "a", encoding="utf-8") as fp:
                for (k, _), v in zip(batch, vectors):
                    self._cache[k] = v
                    fp.write(json.dumps({"key": k, "embedding": v}) + "\n")
        return [self._cache[k] for k in keys]

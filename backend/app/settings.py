"""所有設定走環境變數（見 .env.example），程式裡只放本機開發的預設值。"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql://twse:twse@localhost:5432/twse"
    ollama_url: str = "http://localhost:11434"
    llm_model: str = "qwen3.5:2b"
    llm_temperature: float = 0.1
    top_k: int = 8
    # 最相近 chunk 的 cosine 距離超過這個值就直接回答查無資料、不呼叫 LLM。
    # 0.33 是用 13 題實測訂的（相關 0.19～0.31、資料庫外公司 0.35～0.52），評估階段要重新校準
    no_answer_distance: float = 0.33
    snippet_chars: int = 120

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        return cls(
            database_url=env.get("DATABASE_URL", cls.database_url),
            ollama_url=env.get("OLLAMA_BASE_URL", cls.ollama_url),
            llm_model=env.get("LLM_MODEL", cls.llm_model),
            llm_temperature=float(env.get("LLM_TEMPERATURE", cls.llm_temperature)),
            top_k=int(env.get("TOP_K", cls.top_k)),
            no_answer_distance=float(env.get("NO_ANSWER_DISTANCE", cls.no_answer_distance)),
        )

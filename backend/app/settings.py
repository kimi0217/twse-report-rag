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
    # 問題提到資料庫裡的公司時改用這個門檻：評估發現別名題（英文公司名）與跨公司題的距離天生偏高
    # （0.34～0.49），會被 0.33 誤拒；有提到已知公司時檢索已限定在該公司，誤用別家資料的風險低很多
    no_answer_distance_with_company: float = 0.5
    snippet_chars: int = 120
    # 輸出上限：主評估集 4 個版本、104 則正常回答最長 554 字（約 460 token），留約 2 倍餘裕。
    # 沒有上限時小模型偶爾會重複輸出停不下來（held-out 第一次驗收卡了 21 分鐘）
    llm_max_tokens: int = 1024
    # 每次 /ask 的整體時間上限：主評估集最慢一題 26.7 秒（含首次載入模型約 15 秒），留約 2 倍
    ask_timeout_seconds: float = 60.0

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
            llm_max_tokens=int(env.get("LLM_MAX_TOKENS", cls.llm_max_tokens)),
            ask_timeout_seconds=float(env.get("ASK_TIMEOUT_SECONDS", cls.ask_timeout_seconds)),
            no_answer_distance_with_company=float(
                env.get("NO_ANSWER_DISTANCE_WITH_COMPANY", cls.no_answer_distance_with_company)
            ),
        )

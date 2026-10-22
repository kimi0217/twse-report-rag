"""公司對照表：股票代號 → 名稱與別名（config/companies.json）。

別名用來在問題裡認出公司：問「TSMC 營收」時只在台積電的 chunk 裡檢索，
就算向量排名被別家的營收段落擠掉，也不會拿別家的數字來回答。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from common.textnorm import normalize_text

DEFAULT_COMPANIES = Path(__file__).resolve().parents[1] / "config" / "companies.json"


@dataclass(frozen=True)
class Company:
    ticker: str
    name: str
    aliases: tuple[str, ...] = field(default=())

    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


def load_companies(path: Path = DEFAULT_COMPANIES) -> dict[str, Company]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {t: Company(t, v["name"], tuple(v.get("aliases", ()))) for t, v in raw.items()}


def find_tickers(question: str, companies: dict[str, Company]) -> list[str]:
    """回傳問題裡提到的公司代號，依代號排序。英文別名不分大小寫；代號前後不能接其他數字（避免「23300 元」）。"""
    text = normalize_text(question).casefold()
    found = []
    for ticker, c in companies.items():
        if re.search(rf"(?<!\d){ticker}(?!\d)", text) or any(n.casefold() in text for n in c.names()):
            found.append(ticker)
    return sorted(found)

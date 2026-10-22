"""組 context 與 prompt。"""

from __future__ import annotations

import re

from backend.app.retrieval import RetrievedChunk

UNIT = re.compile(r"單位\s*[:：]\s*([^）)\s]+)")

NO_ANSWER = "年報中查無相關資料"
TRUNCATED_NOTE = "（回答過長，已截斷）"

SYSTEM_PROMPT = f"""你是台灣上市公司年報問答助理。請嚴格遵守：
1. 只能根據使用者提供的「年報段落」回答，不可使用段落以外的知識，也不可推測。
2. 數字以表格段落為準，照抄數字，並在數字後面寫上該表格標示的「金額單位」，不要自行換算。
   例如金額單位是「新台幣仟元」，3,809,054,272 要寫成「新台幣 3,809,054,272 仟元」，不能省略「仟」。
3. 句子後面用 [編號] 標註依據的段落，例如 [1]。
4. 段落裡沒有答案，或段落講的不是問題問的公司，只回答「{NO_ANSWER}」，不要多寫。
5. 用繁體中文回答，簡潔直接。"""


def format_context(chunks: list[RetrievedChunk]) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        label = "表格" if c.kind == "table" else "內文"
        head = f"[{i}] {c.company} {c.year} p{c.page}（{label}）" + (f"｜{c.title}" if c.title else "")
        # 小模型常忽略藏在表名括號裡的單位，把金額錯讀成「元」（差一千倍），所以單獨拉成一行
        if c.kind == "table" and (m := UNIT.search(c.title or "")):
            head += f"\n金額單位：{m.group(1)}"
        parts.append(f"{head}\n{c.content}")
    return "\n\n".join(parts)


def build_messages(question: str, chunks: list[RetrievedChunk]) -> list[dict[str, str]]:
    user = f"年報段落：\n\n{format_context(chunks)}\n\n問題：{question}"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]

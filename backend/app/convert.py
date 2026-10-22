"""LLM 串流輸出轉繁體（OpenCC s2twp）。

s2twp 依詞轉換（「软件」→「軟體」），逐 token 轉會把詞切斷，所以先暫存到標點或一定長度再轉。
s2twp 會把「台」轉成「臺」；年報與公司名稱都用「台」（台積電、新台幣），轉完再換回來。
"""

from __future__ import annotations

import opencc

BREAKS = set("，。！？；：、\n,.!?;:)）」")
MAX_BUFFER = 20

_cc = opencc.OpenCC("s2twp")


def to_traditional(text: str) -> str:
    return _cc.convert(text).replace("臺", "台")


class StreamConverter:
    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> str:
        """回傳可以送出的已轉換文字；還沒到斷點的部分留在 buffer。"""
        self._buf += text
        cut = max((i for i, c in enumerate(self._buf) if c in BREAKS), default=-1) + 1
        if cut == 0 and len(self._buf) >= MAX_BUFFER:
            cut = len(self._buf)
        ready, self._buf = self._buf[:cut], self._buf[cut:]
        return to_traditional(ready) if ready else ""

    def flush(self) -> str:
        ready, self._buf = self._buf, ""
        return to_traditional(ready) if ready else ""

"""PDF 抽出文字的共用正規化工具。

台灣年報 PDF 常見的問題：
- 字型把「金、長、高」等字對應到「康熙部首」區塊 (U+2F00–U+2FDF) 或
  「CJK 相容字」(U+F900–U+FAFF)。肉眼看一樣，但字碼不同，
  會讓關鍵字搜尋與 embedding 失準。NFKC 正規化可以把它們轉回標準字。
- 全形英數字（ＡＢＣ１２３）也會被 NFKC 轉成半形，方便比對數字。
"""

import re
import unicodedata

_MULTI_SPACE = re.compile(r"[ \t　]+")
_MULTI_NEWLINE = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("�", "")
    text = _MULTI_SPACE.sub(" ", text)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    return text.strip()

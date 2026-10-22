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
# 中文排版用字距對齊（「股　本」「差　異」），抽字後變成字間空白，
# 會讓「股本」這類關鍵字比對不到；中文本來就不用空白分詞，刪掉不會傷到語意。
_CJK = r"\u3400-\u4dbf\u4e00-\u9fff"
_CJK_GAP = re.compile(rf"(?<=[{_CJK}]) +(?=[{_CJK}])")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("�", "")
    text = _MULTI_SPACE.sub(" ", text)
    text = _CJK_GAP.sub("", text)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    return text.strip()

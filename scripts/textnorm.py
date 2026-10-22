"""PDF 抽出文字的共用正規化工具。

台灣年報 PDF 常見的問題：
- 字型把「金、長、高」等字對應到「康熙部首」區塊 (U+2F00–U+2FDF) 或
  「CJK 相容字」(U+F900–U+FAFF)。肉眼看一樣，但字碼不同，
  會讓關鍵字搜尋與 embedding 失準。NFKC 正規化可以把它們轉回標準字。
- 全形英數字（ＡＢＣ１２３）也會被 NFKC 轉成半形，方便比對數字。
- 少數 PDF 的字型把某個字對應到完全不同的字（中華電年報把「新」抽成「倝」）。
  這種錯誤每個 PDF 不同、NFKC 也修不好，只能依檔名人工設定替換表：data/char_fixes.json。
"""

import json
import re
import unicodedata
from pathlib import Path

DEFAULT_CHAR_FIXES = Path(__file__).resolve().parents[1] / "data" / "char_fixes.json"

_MULTI_SPACE = re.compile(r"[ \t　]+")
_MULTI_NEWLINE = re.compile(r"\n{3,}")
# 中文排版用字距對齊（「股　本」「差　異」），抽字後變成字間空白，
# 會讓「股本」這類關鍵字比對不到；中文本來就不用空白分詞，刪掉不會傷到語意。
_CJK = r"\u3400-\u4dbf\u4e00-\u9fff"
_CJK_GAP = re.compile(rf"(?<=[{_CJK}]) +(?=[{_CJK}])")


def load_char_fixes(pdf_name: str, path: Path = DEFAULT_CHAR_FIXES) -> dict[int, str]:
    """回傳可直接給 str.translate 用的對照表；檔案不存在或沒設定這個 PDF 就回傳空表。"""
    if not path.exists():
        return {}
    table = json.loads(path.read_text(encoding="utf-8")).get(pdf_name, {})
    bad = [k for k in table if len(k) != 1]
    if bad:
        # translate 只能一字換一字；多字替換容易誤傷正常文字，乾脆不支援
        raise ValueError(f"{path}: {pdf_name} 的替換來源必須是單一字元：{bad}")
    return str.maketrans(table)


def normalize_text(text: str, char_fixes: dict[int, str] | None = None) -> str:
    text = unicodedata.normalize("NFKC", text)
    if char_fixes:
        text = text.translate(char_fixes)
    text = text.replace("�", "")
    text = _MULTI_SPACE.sub(" ", text)
    text = _CJK_GAP.sub("", text)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    return text.strip()

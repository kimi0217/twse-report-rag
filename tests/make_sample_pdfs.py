"""產生測試用的假年報 PDF，模擬三種情況：正常、掃描檔、Big5 亂碼。

康熙部首問題無法用產生的 PDF 重現（寫入時會被轉回標準字），改在 test_pdf_tools.py 直接測字串。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

FONT = "china-t"  # PyMuPDF 內建繁體中文字型

BODY = (
    "本公司致力於半導體製造服務，致股東報告書說明年度營業結果。\n"
    "董事會依公司治理守則運作，財務報告經會計師查核簽證。\n"
    "本年度營業收入較上年度成長，研究發展支出持續增加。"
)


def _text_page(doc: pymupdf.Document, text: str, font: str = FONT) -> pymupdf.Page:
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 545, 400), text, fontname=font, fontsize=12)
    return page


def _ruled_table(page: pymupdf.Page, top: float, rows: list[list[str]]) -> None:
    x0, col_w, row_h = 50, 140, 24
    width = col_w * len(rows[0])
    shape = page.new_shape()
    for r in range(len(rows) + 1):
        y = top + r * row_h
        shape.draw_line((x0, y), (x0 + width, y))
    for c in range(len(rows[0]) + 1):
        x = x0 + c * col_w
        shape.draw_line((x, top), (x, top + row_h * len(rows)))
    shape.finish(color=(0, 0, 0), width=0.6)
    shape.commit()
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            page.insert_text((x0 + 6 + c * col_w, top + 16 + r * row_h), cell, fontname=FONT, fontsize=10)


def make_good(path: Path) -> None:
    doc = pymupdf.open()
    _text_page(doc, "目　錄\n壹、致股東報告書 ........ 1\n陸、財務概況 ........ 5")
    for _ in range(3):
        _text_page(doc, BODY)
    page = _text_page(doc, "陸、財務概況\n一、最近五年度簡明資產負債表及綜合損益表")
    page.insert_text((50, 175), "簡明綜合損益表（單位：新台幣仟元）", fontname=FONT, fontsize=11)
    _ruled_table(
        page,
        185,
        [
            ["項目", "2024 年", "2025 年"],
            ["營業收入", "2,894,307", "3,809,054"],
            ["營業毛利", "1,624,354", "2,282,415"],
            ["本期淨利", "1,173,268", "1,717,893"],
        ],
    )
    _text_page(doc, BODY)
    doc.save(path)


def make_scanned(path: Path) -> None:
    """把正常頁面轉成圖片再貼回去，模擬掃描檔（沒有文字層）。"""
    src = pymupdf.open()
    for _ in range(4):
        _text_page(src, BODY)
    doc = pymupdf.open()
    for p in src:
        pix = p.get_pixmap(dpi=72)
        new = doc.new_page(width=p.rect.width, height=p.rect.height)
        new.insert_image(new.rect, pixmap=pix)
    doc.save(path)


def make_big5_mojibake(path: Path) -> None:
    """Big5 位元組被當 Latin-1 解碼的樣子，模擬抽字失敗。"""
    junk = BODY.encode("big5").decode("latin-1")
    doc = pymupdf.open()
    for _ in range(4):
        _text_page(doc, junk, font="helv")
    doc.save(path)


def make_all(out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "good": out / "good.pdf",
        "scanned": out / "scanned.pdf",
        "mojibake": out / "mojibake.pdf",
    }
    make_good(paths["good"])
    make_scanned(paths["scanned"])
    make_big5_mojibake(paths["mojibake"])
    return paths


if __name__ == "__main__":
    for name, p in make_all(Path("tests/fixtures")).items():
        print(name, p)

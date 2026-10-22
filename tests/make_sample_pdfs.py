"""產生測試用的假年報 PDF，模擬正常、掃描檔、Big5 亂碼、字型對應錯誤等情況。

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


def _ruled_table(page: pymupdf.Page, top: float, rows: list[list[str]], x0: float = 50) -> None:
    col_w, row_h = 140, 24
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


def make_spread(path: Path) -> None:
    """跨頁版：一頁左右各一張表（像台積電年報），另加一張非財務表。

    左右表名不同，用來確認表名只從表格自己的 x 範圍找。
    """
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_text((50, 60), "財務狀況分析", fontname=FONT, fontsize=12)
    page.insert_text((50, 90), "合併財務報表", fontname=FONT, fontsize=11)
    page.insert_text((650, 90), "個體財務報表", fontname=FONT, fontsize=11)
    page.insert_text((650, 110), "單位：新台幣仟元", fontname=FONT, fontsize=9)
    rows = [["項目", "114年", "113年"], ["資產總額", "7,933,023", "6,691,938"], ["負債總額", "2,472,228", "2,368,362"]]
    _ruled_table(page, 120, rows, x0=50)
    _ruled_table(page, 120, [rows[0], ["資產總額", "7,276,214", "6,037,249"], ["負債總額", "1,856,618", "1,748,704"]], x0=650)
    page.insert_text((50, 330), "董事會成員出席情形", fontname=FONT, fontsize=11)
    _ruled_table(page, 340, [["姓名", "出席次數", "出席率"], ["王小明", "6", "100%"]], x0=50)
    doc.save(path)


def make_continued(path: Path) -> None:
    """一張表跨兩頁（第 2 頁頁首的表沒有表名），第 3 頁是表名含「永續」、項目欄卻有「資產」的表。"""
    doc = pymupdf.open()
    rows = [["項目", "114年", "113年"], ["流動資產", "117,371", "112,968"], ["資產總額", "535,871", "534,491"]]
    page = doc.new_page()
    page.insert_text((50, 100), "合併財務狀況比較分析表", fontname=FONT, fontsize=11)
    _ruled_table(page, 110, rows)
    page = doc.new_page()
    _ruled_table(page, 40, [rows[0], ["負債總額", "135,081", "136,386"], ["權益總額", "400,790", "398,104"]])
    page = doc.new_page()
    page.insert_text((50, 100), "永續相關風險之財務影響表", fontname=FONT, fontsize=11)
    _ruled_table(page, 110, [["項目", "影響金額", "說明"], ["擱淺資產", "6,000", "氣候風險"]])
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


# 中華電 2025 年報實際抽出的錯字：「聯合國企業與人權指導原則」→「耡合國企噟和人奙䭷㼫原則」
REMAPPED = str.maketrans({"聯": "耡", "業": "噟", "權": "奙", "司": "莄", "報": "顺", "務": "袙"})


def make_remapped(path: Path) -> None:
    """字型對應表壞掉：字是合法中文字碼，但被換成罕見字，亂碼比例與關鍵詞都抓不到。"""
    doc = pymupdf.open()
    for _ in range(4):
        _text_page(doc, BODY.translate(REMAPPED))
    doc.save(path)


def make_all(out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "good": out / "good.pdf",
        "scanned": out / "scanned.pdf",
        "mojibake": out / "mojibake.pdf",
        "spread": out / "spread.pdf",
        "continued": out / "continued.pdf",
        "remapped": out / "remapped.pdf",
    }
    make_good(paths["good"])
    make_scanned(paths["scanned"])
    make_big5_mojibake(paths["mojibake"])
    make_spread(paths["spread"])
    make_continued(paths["continued"])
    make_remapped(paths["remapped"])
    return paths


if __name__ == "__main__":
    for name, p in make_all(Path("tests/fixtures")).items():
        print(name, p)

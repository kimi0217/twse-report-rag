"""檢查年報 PDF 能不能正確抽出中文。

用法：
    python scripts/check_pdf_text.py data/pdfs/
    python scripts/check_pdf_text.py data/pdfs/2330_2025.pdf --sample 30

對每個 PDF 抽樣頁面，計算幾個指標並給出 OK / WARN / FAIL，
同時把抽樣文字存到 reports/text_samples/，方便肉眼比對原檔。

判斷依據：
- empty_ratio     沒有文字的頁面比例。很高代表是掃描檔，需要 OCR → 建議換公司。
- garbled_ratio   亂碼字元比例：U+FFFD、私有區 (PUA)、Big5 被當 Latin-1 解碼的殘骸。
                  偏高代表字型缺 ToUnicode 對照表，文字救不回來 → 建議換公司。
- cjk_ratio       中文字佔所有非空白字元的比例。年報正常應該很高。
- keyword_hits    年報必有的詞（公司、股東、董事…）出現幾個。
                  有中文但一個都沒命中，代表字被對應到錯的字碼。
- kangxi_count    康熙部首 / 相容字的數量。可以用 NFKC 修好，只是提醒。
- rare_ratio      中文字裡「罕見字」的比例：不在 Big5 常用字區 (0xA440–0xC67E) 或無法用 Big5 編碼的字。
                  字型對應表壞掉時，抽出的仍是合法中文字碼（「聯合國」變「耡合國」），
                  亂碼比例和關鍵詞都抓不到；但錯字幾乎都落在罕見字。正常年報約 0.05%。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

# 讓 scripts/ 直接執行時也找得到 common/ 套件
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.textnorm import normalize_text  # noqa: E402

KEYWORDS = ["公司", "股東", "董事", "營業", "財務", "年度", "會計", "報告"]

CJK = re.compile(r"[一-鿿㐀-䶿]")
KANGXI = re.compile(r"[⼀-⿟⺀-⻿豈-﫿]")
PUA = re.compile(r"[-]")
# Big5 位元組被當成 Latin-1 解碼時會出現的字元，例如「¤½¥q」
LATIN1_JUNK = re.compile(r"[¡-ÿ]")
CID = re.compile(r"\(cid:\d+\)")

MIN_CHARS_PER_PAGE = 30
BIG5_COMMON = range(0xA440, 0xC67F)
# 2025 年報實測：正常 4 家 0.02%～0.06%，字型對應壞掉的中華電 14.7%；門檻取兩者之間並留寬裕
RARE_WARN = 0.005
RARE_FAIL = 0.02


@dataclass
class Report:
    file: str
    pages: int
    sampled: int
    empty_ratio: float = 0.0
    garbled_ratio: float = 0.0
    cjk_ratio: float = 0.0
    keyword_hits: int = 0
    kangxi_count: int = 0
    rare_ratio: float = 0.0
    avg_chars: float = 0.0
    verdict: str = "OK"
    reasons: list[str] = field(default_factory=list)


def sample_pages(n_pages: int, n_sample: int) -> list[int]:
    if n_pages <= n_sample:
        return list(range(n_pages))
    step = n_pages / n_sample
    return sorted({int(i * step) for i in range(n_sample)})


def is_rare_char(c: str) -> bool:
    try:
        b = c.encode("big5")
    except UnicodeEncodeError:
        return True
    return len(b) != 2 or int.from_bytes(b, "big") not in BIG5_COMMON


def rare_char_ratio(text: str) -> float:
    cjk = CJK.findall(text)
    return sum(map(is_rare_char, cjk)) / len(cjk) if cjk else 0.0


def check_pdf(path: Path, n_sample: int, out_dir: Path) -> Report:
    doc = pymupdf.open(path)
    pages = sample_pages(doc.page_count, n_sample)
    rep = Report(file=path.name, pages=doc.page_count, sampled=len(pages))

    raw_all, empty, dump = [], 0, []
    for i in pages:
        text = doc[i].get_text("text")
        if len(text.strip()) < MIN_CHARS_PER_PAGE:
            empty += 1
        raw_all.append(text)
        dump.append(f"===== page {i + 1} =====\n{text}")
    doc.close()

    raw = "".join(raw_all)
    chars = [c for c in raw if not c.isspace()]
    total = max(len(chars), 1)

    garbled = (
        raw.count("�")
        + len(PUA.findall(raw))
        + len(LATIN1_JUNK.findall(raw))
        + len(CID.findall(raw)) * 5
    )
    rep.empty_ratio = round(empty / max(len(pages), 1), 3)
    rep.garbled_ratio = round(garbled / total, 4)
    rep.cjk_ratio = round(len(CJK.findall(raw)) / total, 3)
    rep.kangxi_count = len(KANGXI.findall(raw))
    rep.avg_chars = round(len(chars) / max(len(pages), 1), 1)
    normalized = normalize_text(raw)
    rep.keyword_hits = sum(1 for k in KEYWORDS if k in normalized)
    # 用 NFKC 之後的文字算：康熙部首轉回標準字後就不算罕見字，那是另一個可修的問題
    rep.rare_ratio = round(rare_char_ratio(normalized), 4)

    fail, warn = rep.reasons, []
    if rep.empty_ratio > 0.5:
        fail.append(f"{rep.empty_ratio:.0%} 抽樣頁沒有文字，可能是掃描檔（需要 OCR）")
    if rep.garbled_ratio > 0.02:
        fail.append(f"亂碼字元比例 {rep.garbled_ratio:.1%}，字型可能缺少對照表")
    if rep.cjk_ratio < 0.2 and rep.empty_ratio <= 0.5:
        fail.append(f"中文字只佔 {rep.cjk_ratio:.0%}，抽出的可能不是正確中文")
    if rep.cjk_ratio >= 0.2 and rep.keyword_hits < 3:
        fail.append(f"常見詞只命中 {rep.keyword_hits}/{len(KEYWORDS)}，字可能被對應到錯的字碼")
    if rep.rare_ratio > RARE_FAIL:
        fail.append(f"罕見字比例 {rep.rare_ratio:.1%}，字型對應表可能壞掉（字被換成別的字）")

    if not fail:
        if 0.2 < rep.empty_ratio <= 0.5:
            warn.append(f"{rep.empty_ratio:.0%} 抽樣頁幾乎沒文字（可能是圖片頁或封面）")
        if 0.005 < rep.garbled_ratio <= 0.02:
            warn.append(f"少量亂碼 {rep.garbled_ratio:.2%}，抽查樣本確認")
        if RARE_WARN < rep.rare_ratio <= RARE_FAIL:
            warn.append(f"罕見字比例 {rep.rare_ratio:.2%}，抽查樣本看有沒有錯字")
        if rep.kangxi_count:
            warn.append(f"有 {rep.kangxi_count} 個康熙部首/相容字，ingestion 時要做 NFKC 正規化")
        rep.reasons = warn

    rep.verdict = "FAIL" if fail else ("WARN" if warn else "OK")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{path.stem}.txt").write_text("\n".join(dump), encoding="utf-8")
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path, help="PDF 檔或資料夾")
    ap.add_argument("--sample", type=int, default=20, help="每個檔案抽樣幾頁（預設 20）")
    ap.add_argument("--out", type=Path, default=Path("reports"), help="報告輸出資料夾")
    args = ap.parse_args()

    files: list[Path] = []
    for p in args.paths:
        files += sorted(p.glob("*.pdf")) if p.is_dir() else [p]
    if not files:
        print("找不到 PDF", file=sys.stderr)
        return 1

    reports = [check_pdf(f, args.sample, args.out / "text_samples") for f in files]

    icon = {"OK": "✅", "WARN": "⚠️ ", "FAIL": "❌"}
    print(f"{'檔案':<28}{'頁數':>6}{'空白頁':>8}{'亂碼':>8}{'中文':>7}{'關鍵詞':>7}{'罕見字':>8}  結果")
    for r in reports:
        print(
            f"{r.file:<30}{r.pages:>6}{r.empty_ratio:>9.0%}{r.garbled_ratio:>9.2%}"
            f"{r.cjk_ratio:>8.0%}{r.keyword_hits:>5}/{len(KEYWORDS)}{r.rare_ratio:>10.2%}  {icon[r.verdict]} {r.verdict}"
        )
        for reason in r.reasons:
            print(f"    - {reason}")

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "pdf_check.json").write_text(
        json.dumps([asdict(r) for r in reports], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n抽樣文字：{args.out / 'text_samples'}/   完整報告：{args.out / 'pdf_check.json'}")
    return 1 if any(r.verdict == "FAIL" for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())

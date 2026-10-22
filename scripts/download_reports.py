"""從證交所文件伺服器（doc.twse.com.tw）下載年報 PDF。

用法：
    python scripts/download_reports.py                 # config/companies.json 的全部公司，2025 年度
    python scripts/download_reports.py 2330 2454 --year 2025 --out data/pdfs

為什麼用證交所：各公司官網的網址格式不一，台積電官網還有 Cloudflare 機器人驗證；
證交所的流程每家都一樣：
1. 年報清單頁（step=1）列出該年度上傳的檔案，中文股東會年報的檔名以 F04.pdf 結尾（FE4 是英文版）。
2. 檔案頁（step=9）回傳一個 HTML，裡面才有真正的 PDF 連結。

年報在隔年才上傳，所以 2025 年度要查民國 115 年的清單。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.companies import load_companies  # noqa: E402

BASE = "https://doc.twse.com.tw"
# 證交所會擋掉沒有瀏覽器 User-Agent 的請求
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"}


def find_filename(client: httpx.Client, ticker: str, year: int) -> str | None:
    roc_year = year - 1911 + 1  # 年報隔年上傳
    resp = client.get(f"{BASE}/server-java/t57sb01",
                      params={"step": 1, "colorchg": 1, "co_id": ticker, "year": roc_year, "mtype": "F"})
    resp.raise_for_status()
    names = sorted(set(re.findall(rf"{year}_{ticker}_\d+F04\.pdf", resp.content.decode("big5", errors="replace"))))
    return names[-1] if names else None  # 有更正版時檔名日期較新


def find_pdf_url(client: httpx.Client, ticker: str, filename: str) -> str:
    resp = client.get(f"{BASE}/server-java/t57sb01",
                      params={"step": 9, "colorchg": 1, "co_id": ticker, "kind": "F", "filename": filename})
    resp.raise_for_status()
    m = re.search(r"href='(/pdf/[^']+\.pdf)'", resp.content.decode("big5", errors="replace"))
    if not m:
        raise RuntimeError(f"{ticker}: 檔案頁找不到 PDF 連結（可能遇到驗證或格式改變）")
    return BASE + m.group(1)


def download(client: httpx.Client, ticker: str, year: int, out: Path) -> Path:
    filename = find_filename(client, ticker, year)
    if not filename:
        raise RuntimeError(f"{ticker}: 證交所清單裡沒有 {year} 年度的中文年報（F04）")
    url = find_pdf_url(client, ticker, filename)
    resp = client.get(url)
    resp.raise_for_status()
    if not resp.content.startswith(b"%PDF"):
        raise RuntimeError(f"{ticker}: 下載到的不是 PDF（{resp.headers.get('content-type')}）")
    path = out / f"{ticker}_{year}.pdf"
    path.write_bytes(resp.content)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="*", help="股票代號；不填就下載 config/companies.json 的全部公司")
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--out", type=Path, default=Path("data/pdfs"))
    args = ap.parse_args()

    tickers = args.tickers or sorted(load_companies())
    args.out.mkdir(parents=True, exist_ok=True)
    failed = []
    with httpx.Client(headers=HEADERS, timeout=120, follow_redirects=True) as client:
        for t in tickers:
            try:
                path = download(client, t, args.year, args.out)
                print(f"{t}: {path}（{path.stat().st_size / 1e6:.1f} MB）")
            except Exception as exc:  # 一家失敗不影響其他家
                failed.append(t)
                print(f"{t}: 失敗：{exc}", file=sys.stderr)
    if failed:
        print(f"\n下載失敗：{', '.join(failed)}。請到公開資訊觀測站手動下載，存成 {args.out}/<代號>_{args.year}.pdf", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

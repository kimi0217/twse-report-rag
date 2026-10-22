import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import download_reports as dr  # noqa: E402

# 證交所頁面是 Big5；清單同時列出前一年度、英文版（FE4）與其他附件
LIST_HTML = (
    "<a>2024_2330_20250604F04.pdf</a><a>2025_2330_20260604FE4.pdf</a>"
    "<a>2025_2330_20260604F04.pdf</a><a>2025_2330_20260604F01.pdf</a>"
).encode("big5")
FILE_HTML = "電子檔案：<a href='/pdf/2025_2330_20260604F04_20261022_1.pdf'>下載</a>".encode("big5")


def _client(pdf_body=b"%PDF-1.6 fake", seen=None):
    def handler(request):
        if seen is not None:
            seen.append(dict(request.url.params))
        if request.url.path.startswith("/pdf/"):
            return httpx.Response(200, content=pdf_body)
        step = request.url.params["step"]
        return httpx.Response(200, content=LIST_HTML if step == "1" else FILE_HTML)

    return httpx.Client(base_url=dr.BASE, transport=httpx.MockTransport(handler))


def test_picks_chinese_report_of_the_year_from_next_year_listing():
    seen = []
    assert dr.find_filename(_client(seen=seen), "2330", 2025) == "2025_2330_20260604F04.pdf"
    assert seen[0]["year"] == "115"  # 2025 年度的年報在民國 115 年上傳


def test_download_follows_file_page_link(tmp_path):
    path = dr.download(_client(), "2330", 2025, tmp_path)
    assert path == tmp_path / "2330_2025.pdf"
    assert path.read_bytes().startswith(b"%PDF")


def test_non_pdf_response_rejected(tmp_path):
    with pytest.raises(RuntimeError, match="不是 PDF"):
        dr.download(_client(pdf_body=b"<html>Just a moment...</html>"), "2330", 2025, tmp_path)
    assert not (tmp_path / "2330_2025.pdf").exists()

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from check_pdf_text import KANGXI, check_pdf  # noqa: E402
from extract_financial_tables import extract, find_candidate_pages, parse_pages  # noqa: E402
from make_sample_pdfs import make_all  # noqa: E402
from textnorm import normalize_text  # noqa: E402


@pytest.fixture(scope="module")
def pdfs(tmp_path_factory):
    return make_all(tmp_path_factory.mktemp("pdfs"))


@pytest.mark.parametrize(
    "name, verdict",
    [("good", "OK"), ("scanned", "FAIL"), ("mojibake", "FAIL")],
)
def test_check_verdict(pdfs, tmp_path, name, verdict):
    assert check_pdf(pdfs[name], 20, tmp_path).verdict == verdict


def test_kangxi_is_detected_and_normalized():
    raw = "現⾦流量與⾧期借款"  # ⾦ ⾧
    assert KANGXI.findall(raw)
    assert normalize_text(raw) == "現金流量與長期借款"


def test_fullwidth_digits_normalized():
    assert normalize_text("營收１２３，４５６") == "營收123,456"


def test_toc_page_is_skipped(pdfs):
    # 第 1 頁是目錄（也含「財務概況」），只應該找到第 5 頁
    assert find_candidate_pages(pdfs["good"], ["財務概況"]) == [4]


def test_table_extracted_as_markdown(pdfs):
    tables = extract(pdfs["good"], [4])
    assert len(tables) == 1
    t = tables[0]
    assert t["method"] == "lines"
    assert "簡明綜合損益表" in t["title"]
    assert "| 營業收入 | 2,894,307 | 3,809,054 |" in t["markdown"]


def test_parse_pages():
    assert parse_pages("3,10-12") == [2, 9, 10, 11]

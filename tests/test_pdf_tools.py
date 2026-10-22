import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from check_pdf_text import KANGXI, check_pdf  # noqa: E402
import extract_financial_tables as eft  # noqa: E402
from extract_financial_tables import (  # noqa: E402
    _fix_detached_commas,
    extract,
    find_candidate_pages,
    has_labels,
    is_financial,
    parse_pages,
    pick_title,
)
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
    assert t["tool"] == "pdfplumber"
    assert "簡明綜合損益表" in t["title"]
    assert "| 營業收入 | 2,894,307 | 3,809,054 |" in t["markdown"]


def test_parse_pages():
    assert parse_pages("3,10-12") == [2, 9, 10, 11]


# --- textnorm：中文字間空白 ---


def test_space_between_cjk_removed():
    assert normalize_text("股 本") == "股本"
    assert normalize_text("差　異 說 明") == "差異說明"


def test_space_next_to_digits_kept():
    # 數字與中文之間的空白不是字距對齊造成的，保留才不會把「114 年」和前面的數字黏在一起
    assert normalize_text("民國 114 年 營收") == "民國 114 年營收"


# --- 表格內容篩選 ---


def test_is_financial_checks_label_column_only():
    assert is_financial([["項目", "114年"], ["資產總額", "1,000"]])
    # ESG 表：描述欄提到「資產」，但項目欄不是財務項目
    assert not is_financial([["風險", "描述"], ["乾旱", "影響不動產等資產價值 1,000"]])


def test_has_labels():
    good = [["項目", "114年"], ["營業收入", "1,000"], ["營業成本", "600"]]
    bad = [["項目", "114年"], ["", "1,000"], ["", "600"], ["營業毛利", "400"]]
    assert has_labels(good)
    assert not has_labels(bad)


# --- PyMuPDF 千分位修正 ---


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1630955164\n, , ,", "1,630,955,164"),
        ("(16676412)\n, ,", "(16,676,412)"),
        ("31%", "31%"),
        ("2025", "2025"),  # 年度沒有逗號殘行，不能被加上千分位
    ],
)
def test_fix_detached_commas(raw, expected):
    assert _fix_detached_commas(raw) == expected


# --- 表名 ---


def test_title_prefers_table_line_and_skips_numbers():
    lines = ["6.1.2 財務績效分析", "台積公司及子公司財務報表", "單位：新台幣仟元", "126 127"]
    assert pick_title(lines) == "台積公司及子公司財務報表（單位:新台幣仟元）"


def test_title_keeps_consolidated_scope():
    assert pick_title(["合併", "財務狀況比較分析"]) == "合併 財務狀況比較分析"


def test_title_empty_when_only_numbers():
    assert pick_title(["123", "4,567"]) == ""


# --- 跨頁版整合測試 ---


def test_spread_page_titles_follow_table_column(pdfs):
    tables = extract(pdfs["spread"], [0])
    titles = sorted(t["title"] for t in tables)
    # 董事會出席表不含財務項目 → 被丟掉；左右兩張表的表名不能互相搶
    assert len(tables) == 2
    assert titles == ["個體財務報表（單位:新台幣仟元）", "合併財務報表"]


def test_unlabeled_table_reextracted_with_pymupdf(pdfs, monkeypatch):
    # 合成 PDF 的 pdfplumber 結果是好的，所以模擬「第一次品質檢查失敗」，確認會走 PyMuPDF 重抽
    real = eft.has_labels
    calls = {"n": 0}

    def flaky(table):
        calls["n"] += 1
        return False if calls["n"] == 1 else real(table)

    monkeypatch.setattr(eft, "has_labels", flaky)
    tables = extract(pdfs["good"], [4])
    assert len(tables) == 1
    assert tables[0]["tool"] == "pymupdf"
    assert "| 營業收入 | 2,894,307 | 3,809,054 |" in tables[0]["markdown"]


def test_unlabeled_table_dropped_when_retry_fails(pdfs, monkeypatch):
    monkeypatch.setattr(eft, "has_labels", lambda table: False)
    stats: dict[str, int] = {}
    assert extract(pdfs["good"], [4], stats) == []
    assert stats["no_labels"] == 1

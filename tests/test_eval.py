import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from run_eval import Retrieved, check_answer, hit_rank  # noqa: E402


def _top(*items):
    return [Retrieved(t, p, "text", 0.2) for t, p in items]


def test_hit_rank_first_matching_page():
    assert hit_rank(_top(("2330", 9), ("2330", 65), ("2330", 7)), {"2330": [7, 65]}) == 2
    assert hit_rank(_top(("2330", 9)), {"2330": [7]}) is None


def test_hit_rank_wrong_company_same_page_not_hit():
    assert hit_rank(_top(("2454", 65)), {"2330": [65]}) is None


def test_hit_rank_cross_needs_every_company():
    expected = {"2330": [52], "2454": [68, 109]}
    assert hit_rank(_top(("2330", 52), ("2330", 3), ("2454", 109)), expected) == 3
    assert hit_rank(_top(("2330", 52), ("2330", 3)), expected) is None


def test_refusal_question_has_no_rank():
    assert hit_rank(_top(("2330", 1)), {}) is None


def test_check_number_and_unit_ignoring_commas_and_spaces():
    check = {"any": ["3,809,054,272仟元", "3兆8,090億"]}
    assert check_answer("合併營收為新台幣 3,809,054,272 仟元 [1]。", check)
    assert check_answer("約 3 兆 8,090 億元", check)
    assert not check_answer("新台幣 3,809,054,272 元", check)  # 少了「仟」差一千倍，要算錯


def test_check_all_groups():
    check = {"all": [["246,427,264", "6.5%"], ["148,306,389"]]}
    assert check_answer("台積電占營收 6.5%，聯發科 148,306,389 仟元", check)
    assert not check_answer("台積電 246,427,264 仟元", check)


def test_check_keywords_and_refusal():
    assert check_answer("董事長是蔡明介 [1]", {"keywords": ["蔡明介"]})
    assert not check_answer("雲端與消費智能", {"keywords": ["雲端", "消費", "電腦終端"]})
    assert check_answer("年報中查無相關資料", {"refuse": True})
    assert not check_answer("大立光毛利率 60%", {"refuse": True})

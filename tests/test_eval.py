import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from backend.app.settings import Settings  # noqa: E402
from run_eval import QuestionResult, Retrieved, check_answer, hit_rank, is_refusal, render, summarize  # noqa: E402


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


def test_refusal_types():
    assert is_refusal("refusal") and is_refusal("refusal_known")
    assert not is_refusal("numeric")


def test_check_ignores_markdown_bold_and_currency_position():
    assert check_answer("每股盈餘為 **19.45** 元。", {"any": ["19.45元"]})
    assert check_answer("資產總額為 **7,933,023,878** 新台幣仟元", {"any": ["7,933,023,878仟元"]})
    assert not check_answer("**7,933,023,878** 元", {"any": ["7,933,023,878仟元"]})  # 單位錯仍算錯


def test_partial_results_render_after_first_question():
    # 每題跑完就寫報告：只有 1 題（seen）時 unseen 組是空的，也要能產生報告
    r = QuestionResult(id="n01", question="q", type="numeric", answer_in="both", seen=True, tickers_used=["2330"],
                       top=[Retrieved("2330", 65, "table", 0.2)], best_distance=0.2, refused_by_threshold=False,
                       hit_rank=1, answer="3,809,054,272 仟元", answer_correct=True, truncated=False,
                       refused_by_answer=False, latency=1.0)
    summary = summarize([r], Settings(), with_llm=True)
    summary.update(complete=False, n_done=1, n_total=26)
    md = render({"x": summary}, {"n01": {"expected_pages": {"2330": [65]}, "expected_answer": "a"}})
    assert "已完成 1/26 題" in md

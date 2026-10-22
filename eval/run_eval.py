"""檢索與回答品質評估。

用法（在專案根目錄，需要 pgvector 容器與 Ollama）：
    python eval/run_eval.py --label baseline              # 只評估檢索與程式層拒答
    python eval/run_eval.py --label baseline --with-llm   # 再加上 LLM 回答正確率（較慢）

評估走的是跟 /ask 完全相同的程式：normalize_text → 別名認公司 → bge-m3 → PgRetriever 前 k 名，
--with-llm 時直接呼叫後端的 answer_events()，所以量到的就是線上行為。

結果依 label 存進 eval/results.json，再由全部 label 重新產生 eval/results.md，
之後做改進時用新的 label 跑一次，就能在同一份報告裡並排比較。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.llm import OllamaChat  # noqa: E402
from backend.app.main import Deps, answer_events, resolve_tickers  # noqa: E402
from backend.app.prompt import NO_ANSWER  # noqa: E402
from backend.app.retrieval import PgRetriever  # noqa: E402
from backend.app.schemas import AskRequest  # noqa: E402
from backend.app.settings import Settings  # noqa: E402
from common.companies import load_companies  # noqa: E402
from common.embeddings import get_async_embedder  # noqa: E402
from common.textnorm import normalize_text  # noqa: E402

QUESTIONS = ROOT / "eval" / "questions.jsonl"
RESULTS_JSON = ROOT / "eval" / "results.json"
RESULTS_MD = ROOT / "eval" / "results.md"
KS = (1, 3, 8)


@dataclass
class Retrieved:
    ticker: str
    page: int
    kind: str
    distance: float


@dataclass
class QuestionResult:
    id: str
    question: str
    type: str
    answer_in: str | None
    seen: bool
    tickers_used: list[str] | None
    top: list[Retrieved]
    best_distance: float | None
    refused_by_threshold: bool
    # 前 k 名裡「每家預期公司都至少命中一次」的最小 k；沒有就是 None。拒答題也是 None
    hit_rank: int | None
    answer: str | None = None
    answer_correct: bool | None = None
    refused_by_answer: bool | None = None
    latency: float | None = None
    error: str | None = None


# --- 判斷 ---


def hit_rank(top: list[Retrieved], expected: dict[str, list[int]]) -> int | None:
    """跨公司題要兩家都命中才算：取各家「第一個命中名次」的最大值。"""
    if not expected:
        return None
    firsts = []
    for ticker, pages in expected.items():
        ranks = [i for i, r in enumerate(top, 1) if r.ticker == ticker and r.page in pages]
        if not ranks:
            return None
        firsts.append(ranks[0])
    return max(firsts)


def _squash(text: str) -> str:
    # 「3,809,054,272 仟元」「3809054272仟元」視為相同；只拿掉逗號與空白，不做單位換算
    return re.sub(r"[,\s]", "", normalize_text(text))


def check_answer(answer: str, check: dict) -> bool:
    a = _squash(answer)
    if check.get("refuse"):
        return NO_ANSWER in answer
    if "any" in check:
        return any(_squash(x) in a for x in check["any"])
    if "all" in check:
        return all(any(_squash(x) in a for x in group) for group in check["all"])
    if "keywords" in check:
        return all(_squash(k) in a for k in check["keywords"])
    raise ValueError(f"不認得的 answer_check：{check}")


# --- 執行 ---


async def evaluate(questions: list[dict], with_llm: bool) -> tuple[list[QuestionResult], Settings]:
    settings = Settings.from_env()
    companies = load_companies()
    embedder = get_async_embedder()
    retriever = PgRetriever(settings.database_url)
    await retriever.open()
    llm = OllamaChat(settings.llm_model, settings.ollama_url, settings.llm_temperature) if with_llm else None
    deps = Deps(settings, embedder, retriever, llm, companies)
    results = []
    try:
        for q in questions:
            req = AskRequest(question=q["question"])
            text = normalize_text(req.question)
            tickers = resolve_tickers(req, text, companies)
            [vec] = await embedder.embed([text])
            chunks = await retriever.search(vec, tickers, None, settings.top_k)
            top = [Retrieved(c.ticker, c.page, c.kind, round(c.distance, 4)) for c in chunks]
            best = min((c.distance for c in chunks), default=None)
            r = QuestionResult(
                id=q["id"], question=q["question"], type=q["type"], answer_in=q["answer_in"], seen=q["seen"],
                tickers_used=tickers, top=top, best_distance=None if best is None else round(best, 4),
                refused_by_threshold=best is None or best > settings.no_answer_distance,
                hit_rank=hit_rank(top, q["expected_pages"]),
            )
            if with_llm:
                t0 = time.perf_counter()
                tokens, error = [], None
                async for event in answer_events(req, deps):
                    name, data = _parse_sse(event)
                    if name == "token":
                        tokens.append(data["text"])
                    elif name == "error":
                        error = data["message"]
                r.answer = "".join(tokens)
                r.error = error
                r.latency = round(time.perf_counter() - t0, 1)
                r.refused_by_answer = NO_ANSWER in r.answer
                r.answer_correct = error is None and check_answer(r.answer, q["answer_check"])
            results.append(r)
            print(f"{r.id} rank={r.hit_rank} best={r.best_distance}" + (f" correct={r.answer_correct} {r.latency}s" if with_llm else ""))
    finally:
        await retriever.close()
        await embedder.aclose()
        if llm:
            await llm.aclose()
    return results, settings


def _parse_sse(event: str) -> tuple[str, object]:
    lines = dict(line.split(": ", 1) for line in event.strip().splitlines())
    return lines["event"], json.loads(lines["data"])


# --- 統計 ---


def retrieval_metrics(rs: list[QuestionResult]) -> dict:
    rs = [r for r in rs if r.type != "refusal"]
    n = len(rs)
    if not n:
        return {"n": 0}
    out = {"n": n}
    for k in KS:
        out[f"hit@{k}"] = sum(r.hit_rank is not None and r.hit_rank <= k for r in rs) / n
    out["mrr"] = sum(1 / r.hit_rank for r in rs if r.hit_rank) / n
    return out


def refusal_metrics(rs: list[QuestionResult], attr: str) -> dict:
    refusal = [r for r in rs if r.type == "refusal"]
    answerable = [r for r in rs if r.type != "refusal"]
    return {
        "refusal_recall": sum(getattr(r, attr) for r in refusal) / len(refusal) if refusal else None,
        "false_refusal": sum(getattr(r, attr) for r in answerable) / len(answerable) if answerable else None,
        "n_refusal": len(refusal),
        "n_answerable": len(answerable),
    }


def summarize(results: list[QuestionResult], settings: Settings, with_llm: bool) -> dict:
    groups: dict[str, list[QuestionResult]] = {"全部": results}
    for key, label in (("type", "type"), ("answer_in", "answer_in")):
        for value in sorted({getattr(r, key) for r in results if getattr(r, key)}):
            groups[f"{label}={value}"] = [r for r in results if getattr(r, key) == value]
    summary = {
        "settings": {"top_k": settings.top_k, "no_answer_distance": settings.no_answer_distance, "llm_model": settings.llm_model},
        "retrieval": {
            scope: {g: retrieval_metrics(rs if scope == "all" else [r for r in rs if not r.seen]) for g, rs in groups.items()}
            for scope in ("all", "unseen")
        },
        "seen_vs_unseen": {
            "seen": retrieval_metrics([r for r in results if r.seen]),
            "unseen": retrieval_metrics([r for r in results if not r.seen]),
        },
        "refusal_threshold": refusal_metrics(results, "refused_by_threshold"),
        "questions": [asdict(r) for r in results],
    }
    if with_llm:
        summary["refusal_answer"] = refusal_metrics(results, "refused_by_answer")
        by_type = {}
        for t in sorted({r.type for r in results}):
            rs = [r for r in results if r.type == t]
            by_type[t] = {"n": len(rs), "correct": sum(bool(r.answer_correct) for r in rs) / len(rs)}
        summary["answer"] = {
            "by_type": by_type,
            "all": sum(bool(r.answer_correct) for r in results) / len(results),
            "unseen": _ratio([r for r in results if not r.seen]),
            "avg_latency": round(sum(r.latency for r in results) / len(results), 1),
        }
    return summary


def _ratio(rs: list[QuestionResult]) -> float | None:
    return sum(bool(r.answer_correct) for r in rs) / len(rs) if rs else None


# --- 報告 ---


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def retrieval_table(metrics: dict[str, dict]) -> list[str]:
    lines = ["| 分組 | 題數 | hit@1 | hit@3 | hit@8 | MRR |", "|---|---|---|---|---|---|"]
    for g, m in metrics.items():
        if not m.get("n"):
            continue
        lines.append(f"| {g} | {m['n']} | {pct(m['hit@1'])} | {pct(m['hit@3'])} | {pct(m['hit@8'])} | {m['mrr']:.2f} |")
    return lines


def render(all_results: dict[str, dict], questions: dict[str, dict]) -> str:
    out = ["# 評估結果", "",
           "由 `eval/run_eval.py` 產生，不要手動修改。題目見 `eval/questions.jsonl`（抽查表：`eval/questions_review.md`）。", "",
           "## 指標定義", "",
           "- **命中**：檢索到的 chunk 的（ticker, page）在 expected_pages 裡；跨公司題要每家公司都命中才算。",
           "- **hit@k**：前 k 名（依向量距離排序）有命中的題目比例；**MRR**：第一個命中名次的倒數平均，前 8 名沒命中記 0。檢索指標不含拒答題。",
           "- **拒答召回率**：拒答題被拒答的比例；**誤拒率**：可回答的題目被拒答的比例。",
           "  「門檻」是程式層判斷（最相近 chunk 距離 > NO_ANSWER_DISTANCE）；「回答」是 LLM 最終回答含「年報中查無相關資料」。",
           "- **回答正確**：去掉逗號與空白後，回答包含 answer_check 指定的數字＋單位（數字題）或全部關鍵字（文字題）；拒答題要回答查無資料。",
           "- 樣本只有 26 題，**1 題約等於 4%**；董事長題（t01、t06、t07、a02）列入所有寫出「董事長＋姓名」的頁，命中範圍較寬。", ""]

    labels = list(all_results)
    if len(labels) > 1:
        out += ["## 各版本比較（全部題目）", "",
                "| label | hit@1 | hit@3 | hit@8 | MRR | 拒答召回率（門檻） | 誤拒率（門檻） | 回答正確率 |", "|---|---|---|---|---|---|---|---|"]
        for lb in labels:
            s = all_results[lb]
            m = s["retrieval"]["all"]["全部"]
            rf = s["refusal_threshold"]
            ans = pct(s["answer"]["all"]) if "answer" in s else "—"
            out.append(f"| {lb} | {pct(m['hit@1'])} | {pct(m['hit@3'])} | {pct(m['hit@8'])} | {m['mrr']:.2f} | "
                       f"{pct(rf['refusal_recall'])} | {pct(rf['false_refusal'])} | {ans} |")
        out.append("")

    for lb in labels:
        s = all_results[lb]
        st = s["settings"]
        out += [f"## {lb}", "", f"設定：top_k={st['top_k']}、NO_ANSWER_DISTANCE={st['no_answer_distance']}、LLM={st['llm_model']}；執行時間 {s.get('run_at', '')}", ""]
        out += ["### 檢索（全部題目）", ""] + retrieval_table(s["retrieval"]["all"]) + [""]
        out += ["### 檢索（只看 seen=false）", ""] + retrieval_table(s["retrieval"]["unseen"]) + [""]
        sv = s["seen_vs_unseen"]
        out += ["### seen 與 unseen 比較", "", "| 組別 | 題數 | hit@1 | hit@3 | hit@8 | MRR |", "|---|---|---|---|---|---|"]
        for g in ("seen", "unseen"):
            m = sv[g]
            out.append(f"| {g} | {m['n']} | {pct(m['hit@1'])} | {pct(m['hit@3'])} | {pct(m['hit@8'])} | {m['mrr']:.2f} |")
        out.append("")

        out += ["### 拒答", "", "| 判斷方式 | 拒答召回率 | 誤拒率 |", "|---|---|---|"]
        rt = s["refusal_threshold"]
        out.append(f"| 門檻（{st['no_answer_distance']}） | {pct(rt['refusal_recall'])}（{rt['n_refusal']} 題） | {pct(rt['false_refusal'])}（{rt['n_answerable']} 題） |")
        if "refusal_answer" in s:
            ra = s["refusal_answer"]
            out.append(f"| 最終回答（含 LLM） | {pct(ra['refusal_recall'])} | {pct(ra['false_refusal'])} |")
        out.append("")

        if "answer" in s:
            a = s["answer"]
            out += ["### 回答正確率（--with-llm）", "", "| type | 題數 | 正確率 |", "|---|---|---|"]
            for t, m in a["by_type"].items():
                out.append(f"| {t} | {m['n']} | {pct(m['correct'])} |")
            out += [f"| **全部** | {len(s['questions'])} | **{pct(a['all'])}** |", "",
                    f"只看 seen=false：{pct(a['unseen'])}；平均每題耗時 {a['avg_latency']} 秒（LLM temperature 0.1，每次結果可能略有不同）。", ""]

        out += ["### 每題結果", "",
                "| id | type | 命中名次 | 最近距離 | 前 3 名（ticker p頁 kind 距離） | 回答正確 |", "|---|---|---|---|---|---|"]
        for r in s["questions"]:
            top3 = "<br>".join(f"{x['ticker']} p{x['page']} {x['kind']} {x['distance']:.3f}" for x in r["top"][:3]) or "—"
            rank = "拒答題" if r["type"] == "refusal" else (r["hit_rank"] or "✗")
            ok = "—" if r["answer_correct"] is None else ("✓" if r["answer_correct"] else "✗")
            out.append(f"| {r['id']} | {r['type']} | {rank} | {r['best_distance']} | {top3} | {ok} |")
        out.append("")

        failures = [r for r in s["questions"]
                    if (r["type"] != "refusal" and (r["hit_rank"] is None or r["hit_rank"] > 3))
                    or (r["type"] == "refusal" and not r["refused_by_threshold"])
                    or r["answer_correct"] is False]
        out += ["### 失敗題目", "", "檢索沒進前 3 名、拒答題沒被門檻擋下、或回答錯誤的題目。", ""]
        for r in failures:
            q = questions[r["id"]]
            exp = "；".join(f"{t} p{', '.join(map(str, p))}" for t, p in q["expected_pages"].items()) or "應拒答"
            out += [f"- **{r['id']}** {r['question']}（命中名次：{r['hit_rank'] or '✗'}，最近距離 {r['best_distance']}）",
                    f"  - 預期：{exp}；答案：{q['expected_answer']}",
                    "  - 前 3 名：" + "、".join(f"{x['ticker']} p{x['page']}（{x['kind']}，{x['distance']:.3f}）" for x in r["top"][:3])]
            if r["answer"] is not None:
                out.append(f"  - 回答：{r['answer'].replace(chr(10), ' ')}")
        out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", required=True, help="這次結果的名稱，例如 baseline")
    ap.add_argument("--with-llm", action="store_true", help="同時評估 LLM 回答（每題約數秒）")
    args = ap.parse_args()

    questions = [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines() if line.strip()]
    results, settings = asyncio.run(evaluate(questions, args.with_llm))
    summary = summarize(results, settings, args.with_llm)
    summary["run_at"] = time.strftime("%Y-%m-%d %H:%M")

    all_results = json.loads(RESULTS_JSON.read_text(encoding="utf-8")) if RESULTS_JSON.exists() else {}
    all_results[args.label] = summary
    RESULTS_JSON.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    RESULTS_MD.write_text(render(all_results, {q["id"]: q for q in questions}), encoding="utf-8")

    m = summary["retrieval"]["all"]["全部"]
    print(f"\n[{args.label}] hit@1 {pct(m['hit@1'])} hit@3 {pct(m['hit@3'])} hit@8 {pct(m['hit@8'])} MRR {m['mrr']:.2f}")
    if args.with_llm:
        print(f"回答正確率 {pct(summary['answer']['all'])}")
    print(f"寫入 {RESULTS_MD.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

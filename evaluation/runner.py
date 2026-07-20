"""
runner.py — 评测执行器
======================
运行: python -m evaluation.runner

对 benchmark 测试集执行多维度评测，生成报告。

评测维度:
  1. 关键词召回率 — 回答是否包含预期关键信息
  2. 引用完整性 — 是否标注了信息来源
  3. 答案充分性 — 回答长度是否合理
  4. 越界拒绝率 — 非领域问题是否正确拒绝
  5. 历史案例命中率 — 长期记忆是否召回
"""
import json
import time
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

BENCHMARK_FILE = Path(__file__).parent / "benchmark" / "eval_cases.json"
API_BASE = "http://localhost:8000/api/v1"


def load_cases() -> list[dict]:
    with open(BENCHMARK_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def compute_keyword_recall(answer: str, expected_keywords: list[str]) -> float:
    if not expected_keywords:
        return 1.0
    hits = sum(1 for kw in expected_keywords if kw.lower() in answer.lower())
    return hits / len(expected_keywords)


def has_citations(answer: str) -> bool:
    return any(marker in answer for marker in [
        "来源:", "SOP-", "来源：", "参考", "*来源", "**来源",
        "注塑机换模作业指导书", "操作手册", "报警码对照表",
        "设备点检标准", "安全操作规程", "安全培训大纲",
        "缺陷及处理", "保养与维修", "切削参数", "6S管理",
    ])


def is_refusal(answer: str) -> bool:
    refusal_patterns = ["无法回答", "文档中未找到", "抱歉", "我不是", "没有相关"]
    return any(p in answer for p in refusal_patterns)


def has_history_reference(answer: str) -> bool:
    return any(marker in answer for marker in ["历史案例", "历史维修", "之前", "曾经", "上次"])


async def run_single_case(client: httpx.AsyncClient, case: dict) -> dict:
    """运行单个测试用例，返回评测结果。"""
    t0 = time.time()
    try:
        r = await client.post(f"{API_BASE}/conversation/chat", json={
            "message": case["query"],
            "workshop_id": "workshop-a",
        }, timeout=60)
    except Exception as e:
        return {"id": case["id"], "error": str(e), "latency_ms": 0}

    # 提取纯文本回答
    answer = ""
    for line in r.text.split("\n"):
        if line.startswith("data:"):
            try:
                d = json.loads(line[6:])
                if d.get("type") == "text":
                    answer += d.get("content", "")
            except (json.JSONDecodeError, KeyError):
                pass

    latency = (time.time() - t0) * 1000

    # 计算指标
    kw_recall = compute_keyword_recall(answer, case.get("expected_keywords", []))
    cited = has_citations(answer)
    refusal = is_refusal(answer)
    history = has_history_reference(answer)
    length_ok = len(answer) >= case.get("min_answer_length", 0)

    # 判定通过
    passed = True
    failures = []

    if case.get("expected_refusal"):
        if not refusal:
            passed = False
            failures.append("应拒绝但未拒绝")
    else:
        if kw_recall < 0.5:
            passed = False
            failures.append(f"关键词召回={kw_recall:.0%}")
        if case.get("must_cite") and not cited:
            passed = False
            failures.append("缺少引用来源")
        if not length_ok:
            passed = False
            failures.append(f"回答过短 ({len(answer)} < {case['min_answer_length']})")

    if case.get("expects_history") and not history:
        # 不是硬性失败，但记录
        pass

    return {
        "id": case["id"],
        "category": case["category"],
        "difficulty": case["difficulty"],
        "query": case["query"][:60],
        "passed": passed,
        "failures": failures,
        "kw_recall": round(kw_recall, 3),
        "cited": cited,
        "answer_len": len(answer),
        "refusal": refusal,
        "history_ref": history,
        "latency_ms": round(latency, 0),
    }


async def main():
    cases = load_cases()
    print(f"\n{'='*60}")
    print(f"  评测执行中 — {len(cases)} 个测试用例")
    print(f"{'='*60}\n")

    async with httpx.AsyncClient() as client:
        # 确保种子已加载
        await client.post(f"{API_BASE}/admin/seed", timeout=120)

        results = []
        for i, case in enumerate(cases, 1):
            r = await run_single_case(client, case)
            results.append(r)
            status = "PASS" if r["passed"] else "FAIL"
            icon = "+" if r["passed"] else "!"
            cat = case["category"]
            query = case["query"][:50]
            print(f"  [{i:2d}/{len(cases)}] [{status}] [{cat:12s}] {query}...")
            if r.get("failures"):
                for f in r["failures"]:
                    print(f"         -> {f}")
            # 避免打太快
            await asyncio.sleep(0.3)

    # ── 汇总报告 ──
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed

    by_category = {}
    for r in results:
        cat = r["category"]
        if cat not in by_category:
            by_category[cat] = {"total": 0, "passed": 0}
        by_category[cat]["total"] += 1
        if r["passed"]:
            by_category[cat]["passed"] += 1

    by_difficulty = {}
    for r in results:
        d = r["difficulty"]
        if d not in by_difficulty:
            by_difficulty[d] = {"total": 0, "passed": 0}
        by_difficulty[d]["total"] += 1
        if r["passed"]:
            by_difficulty[d]["passed"] += 1

    avg_kw = sum(r["kw_recall"] for r in results) / total
    avg_latency = sum(r["latency_ms"] for r in results) / total
    citation_rate = sum(1 for r in results if r["cited"]) / total
    history_rate = sum(1 for r in results if r.get("history_ref")) / total
    refusal_rate = sum(1 for r in results if r.get("refusal")) / total

    print(f"\n{'='*60}")
    print(f"  评测报告")
    print(f"{'='*60}")
    print(f"  总用例: {total}  |  通过: {passed}  |  失败: {failed}  |  通过率: {passed/total:.1%}")
    print(f"\n  ── 指标 ──")
    print(f"  关键词召回率:  {avg_kw:.1%}")
    print(f"  引用覆盖率:    {citation_rate:.1%}")
    print(f"  平均延迟:      {avg_latency:.0f}ms")
    print(f"  历史案例命中:  {history_rate:.1%}")
    print(f"  越界拒绝率:    {refusal_rate:.1%}")
    print(f"\n  ── 按类别 ──")
    for cat, stats in sorted(by_category.items()):
        bar = "#" * stats["passed"] + "-" * (stats["total"] - stats["passed"])
        print(f"  [{cat:14s}] {bar} {stats['passed']}/{stats['total']}")
    print(f"\n  ── 按难度 ──")
    for d in ["easy", "medium", "hard"]:
        if d in by_difficulty:
            s = by_difficulty[d]
            print(f"  [{d:6s}] {s['passed']}/{s['total']}")

    # 保存报告
    report_path = Path(__file__).parent / "report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": {
                "total": total, "passed": passed, "failed": failed,
                "pass_rate": passed / total,
                "avg_kw_recall": avg_kw, "avg_latency_ms": avg_latency,
                "citation_rate": citation_rate,
            },
            "by_category": by_category,
            "by_difficulty": by_difficulty,
            "details": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n  详细报告: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())

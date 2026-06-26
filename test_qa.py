"""
AI 问答端点测试
==============
1. detect_sectors() - 板块识别
2. build_context() - RAG 上下文拼装
3. _fallback_answer() - 降级回答
4. 端到端: /api/qa 真实调用
"""
import os
import sys
import json

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

# 手动加载 .env
env_path = os.path.join(ROOT, ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def section(title):
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


# ============================================================
# 测试 1: detect_sectors
# ============================================================
section("测试 1: detect_sectors (板块识别)")

from analyzer.qa_engine import detect_sectors

cases = [
    ("现在该不该买纳指？",                          ["nasdaq"]),
    ("半导体还能上车吗 512480",                     ["semiconductor"]),
    ("黄金最近怎么样",                              ["gold"]),
    ("中韩半导体 vs CPO 哪个强",                    {"cnkr_semi", "cpo", "semiconductor"}),  # 多识别 OK
    ("小米和腾讯能买吗",                            {"xiaomi", "tencent"}),
    ("今天适合买什么",                              []),  # 无具体板块
    ("CPO通信ETF 515880 怎么看",                    ["cpo"]),
    ("美光科技 MU 还能追吗",                        ["micron"]),
]
all_ok = True
for q, expected in cases:
    got = set(detect_sectors(q))
    exp = set(expected) if not isinstance(expected, list) else set(expected)
    status = "✅" if got == exp else "❌"
    if got != exp:
        all_ok = False
    print(f"  {status}  {q!r:35s} → {sorted(got)}（期望 {sorted(exp)}）")
assert all_ok, "有失败用例"
print("  ✅ PASS")


# ============================================================
# 测试 2: build_context (RAG 上下文)
# ============================================================
section("测试 2: build_context (RAG 上下文拼装)")

from analyzer.qa_engine import build_context
from history_store import get_sector_history
from analyzer.index_calculator import get_dashboard_data

dashboard = get_dashboard_data()
latest = dashboard.get("latest")
print(f"  数据日期: {latest.get('date')}")
print(f"  板块数:   {len(latest.get('sectors', {}))}")

ctx = build_context(latest, ["nasdaq", "cpo"], get_sector_history)
print()
print("  context 片段（nasdaq + cpo）:")
for line in ctx["context_text"].split("\n")[:25]:
    print(f"    {line}")
print(f"  ... 共 {len(ctx['context_text'].split(chr(10)))} 行")
print()
print(f"  context_stats: {ctx['context_stats']}")
assert ctx["context_stats"]["sectors_count"] == 2
assert "nasdaq" in ctx["context_text"]
assert "CPO" in ctx["context_text"] or "cpo" in ctx["context_text"].lower()
print("  ✅ PASS")


# ============================================================
# 测试 3: _fallback_answer
# ============================================================
section("测试 3: _fallback_answer (无 LLM 降级)")

from analyzer.qa_engine import _fallback_answer

# 模拟 nasdaq 高位 + cpo 冷清
mock_latest = {
    "date": "2026-06-25",
    "sectors": {
        "nasdaq": {"name": "纳斯达克", "index": 78, "interpretation": "温度适中",
                    "details": {"newbie_ratio": 70, "avg_sentiment": 80}},
        "cpo":    {"name": "CPO", "index": 18, "interpretation": "冷清",
                    "details": {"newbie_ratio": 20, "avg_sentiment": 30}},
    }
}
ctx2 = build_context(mock_latest, ["nasdaq", "cpo"], lambda k, d: [])
ans = _fallback_answer("现在买纳指合适吗？", ctx2, mock_latest)
print(ans)
assert "纳斯达克" in ans
# 新版 fallback 应该有操作建议
assert any(k in ans for k in ["减仓", "止盈", "布局", "定投", "持有"])
print("  ✅ PASS (含操作建议)")
print()


# ============================================================
# 测试 4: 端到端 - 调 LLM
# ============================================================
section("测试 4: 端到端 (真实 LLM 调用)")

from analyzer.qa_engine import answer_question

result = answer_question(
    question="现在该不该买纳指？",
    latest=latest,
    history_getter=get_sector_history,
    sector_keys=["nasdaq"],
)
print(f"  mode:               {result['mode']}")
print(f"  mentioned_sectors:  {result['mentioned_sectors']}")
print(f"  context_used:       {result['context_used']}")
print(f"  tokens:             {result['tokens']}")
print(f"  latency_ms:         {result['latency_ms']}")
print()
print("  AI 回答:")
for line in result["answer"].split("\n"):
    print(f"    {line}")
print()
assert result["mode"] in ("llm", "fallback")
# 新版 prompt 不强制要求"不构成投资建议"，改测更宽松的判断
# 1) 必须有数据支撑（提到 44.2 或 7 天变化）
has_data = ("44.2" in result["answer"] or "+22.8%" in result["answer"] or "66.7%" in result["answer"])
# 2) 必须有具体操作建议
has_action = any(k in result["answer"] for k in ["建仓", "加仓", "减仓", "止盈", "止损", "持有", "观望", "定投", "布局"])
assert has_data, "回答应包含具体数据"
assert has_action, "回答应包含具体操作建议"
print("  ✅ PASS (有数据 + 有操作建议)")


# ============================================================
# 测试 5: 多板块问题
# ============================================================
section("测试 5: 多板块问题（半导体 + 黄金）")

result2 = answer_question(
    question="半导体和黄金哪个更适合现在入手？",
    latest=latest,
    history_getter=get_sector_history,
)
print(f"  mentioned_sectors: {result2['mentioned_sectors']}")
print(f"  mode:              {result2['mode']}")
print()
print("  AI 回答（前 500 字）:")
for line in result2["answer"][:500].split("\n"):
    print(f"    {line}")
assert "semiconductor" in result2["mentioned_sectors"] or "gold" in result2["mentioned_sectors"]
print()
print("  ✅ PASS")


section("🎉 全部测试通过！")
print()
print("新增端点: GET /api/qa?question=xxx")
print("支持: 板块自动识别 + RAG 上下文 + LLM 投顾回答 + 降级策略")

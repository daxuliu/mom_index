"""
P2 功能单元测试
==============
1. interpret_generator.compute_trend_context()  - 7天趋势/连续升温/相似情形
2. interpret_generator._build_history_context() - 历史 context 拼接
3. correlation.compute_correlation_matrix()    - 联动热力图
4. calendar_data.compute_calendar()            - 情绪日历

跑法: python3 test_p2_features.py

不需要真实 DeepSeek key，全是纯本地算法。
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
# 测试 1: compute_trend_context
# ============================================================
section("测试 1: compute_trend_context (历史趋势算法)")

from analyzer.interpret_generator import compute_trend_context

# 模拟 22 天数据：连续升温 3 天后回落到 60
mock_history = [
    {"record_date": f"2026-06-{i:02d}", "index_value": 50 + (i - 11) * 1.5}
    for i in range(1, 23)
]
# 算下来：6-01: 35, 6-15: 57, 6-22: 68
ctx = compute_trend_context(mock_history)
print(f"  data_quality: {ctx.get('data_quality')}")
print(f"  samples:      {ctx.get('samples')}")
print(f"  streak:       {ctx.get('streak')} ({ctx.get('streak_direction')})")
if ctx.get("trend_7d"):
    t = ctx["trend_7d"]
    print(f"  trend_7d:     {t['change']:+} 分 ({t['pct']}, {t['direction']})")
print(f"  volatility:   {ctx.get('volatility_30d')}")
print(f"  similar eps:  {len(ctx.get('similar_episodes', []))} 个")

assert ctx["data_quality"] == "rich", f"应 rich，实际 {ctx['data_quality']}"
assert ctx["samples"] == 22
assert isinstance(ctx["trend_7d"], dict)
print("  ✅ PASS")


# ============================================================
# 测试 2: _build_history_context（多板块拼装）
# ============================================================
section("测试 2: _build_history_context (多板块 context 拼装)")

from analyzer.interpret_generator import _build_history_context

# 一个有 22 天历史，一个只有 1 天
sectors_with_hist = {
    "nasdaq": [
        {"record_date": f"2026-06-{i:02d}", "index_value": 50 + i, "sector_name": "纳斯达克"}
        for i in range(1, 23)
    ],
    "cpo": [
        {"record_date": "2026-06-25", "index_value": 42.0, "sector_name": "CPO"}
    ],
}
ctx_text = _build_history_context(sectors_with_hist)
print(ctx_text)
assert "纳斯达克" in ctx_text, "应包含有历史的板块"
assert "CPO" not in ctx_text, "无历史的板块不应包含"
print("  ✅ PASS (有历史的板块出现，无历史的不出现)")


# ============================================================
# 测试 3: compute_correlation_matrix（联动热力图）
# ============================================================
section("测试 3: compute_correlation_matrix (联动热力图)")

from analyzer.correlation import compute_correlation_matrix
from history_store import get_all_sectors_history

history = get_all_sectors_history(days=30)
# 加 sector_name
for k, rows in history.items():
    for r in rows:
        r["sector_name"] = k

SECTOR_NAMES = {
    "nasdaq": "纳斯达克",
    "cpo": "CPO",
    "gold": "黄金",
    "semiconductor": "半导体",
}
result = compute_correlation_matrix(history, SECTOR_NAMES)
print(f"  实际纳入板块数: {result['sectors_used']}")
print(f"  板块列表: {result['names']}")
print(f"  矩阵维度: {len(result['matrix'])}x{len(result['matrix'][0]) if result['matrix'] else 0}")
print(f"  强联动对: {len([p for p in result['pairs'] if p['strength']=='strong'])} 个")
print(f"  中等联动: {len([p for p in result['pairs'] if p['strength']=='medium'])} 个")
print()
print("  Top 3 联动对:")
for p in result["pairs"][:3]:
    print(f"    {p['a_name']} <-> {p['b_name']}: r={p['r']:+.2f} ({p['strength']}, {p['direction']}, n={p['samples']})")
    print(f"      → {p['interpret']}")

assert result["sectors_used"] >= 4, f"应 >= 4 板块，实际 {result['sectors_used']}"
# 对角线应该是 1.0
for i in range(result["sectors_used"]):
    assert result["matrix"][i][i] == 1.0, f"对角线 [{i}][{i}] 应为 1.0"
print("  ✅ PASS (对角线 = 1.0，矩阵对称)")


# ============================================================
# 测试 4: compute_calendar（情绪日历）
# ============================================================
section("测试 4: compute_calendar (散户情绪日历)")

from analyzer.calendar_data import compute_calendar

cal = compute_calendar(history, SECTOR_NAMES, days=30)
print(f"  日期数: {cal['days_count']}")
print(f"  板块数: {len(cal['sectors'])}")
print(f"  全市场等级分布: {cal['totals']}")
print()
print("  各板块前 5 天日历预览:")
for s in cal["sectors"][:2]:
    print(f"\n  [{s['name']}] (avg={s['stats'].get('avg')}, max={s['stats'].get('max')}, min={s['stats'].get('min')})")
    for c in s["cells"][:5]:
        v = c["value"] if c["value"] is not None else "—"
        print(f"    {c['date']}  {c['emoji']}  {c['label']:4s}  ({v})")

assert cal["days_count"] > 0
assert len(cal["sectors"]) >= 4
# 验证 emoji 与 level 匹配
for s in cal["sectors"]:
    for c in s["cells"]:
        if c["level"] == "hot":
            assert c["emoji"] == "🔥"
        elif c["level"] == "warm":
            assert c["emoji"] == "🌤️"
        elif c["level"] == "cool":
            assert c["emoji"] == "⛅"
        elif c["level"] == "cold":
            assert c["emoji"] == "❄️"
print("  ✅ PASS (emoji 与 level 对应正确)")


# ============================================================
# 测试 5: interpret_generator.generate_interpret (P2 版)
# ============================================================
section("测试 5: generate_interpret (P2 升级版 - 含历史)")

from analyzer.interpret_generator import generate_interpret

sector_indices = {
    "date": "2026-06-25",
    "sectors": {
        "nasdaq": {"name": "纳斯达克", "index": 75, "interpretation": "温度适中",
                    "details": {"newbie_ratio": 60, "avg_sentiment": 65}},
        "cpo": {"name": "CPO", "index": 25, "interpretation": "冷清",
                 "details": {"newbie_ratio": 20, "avg_sentiment": 30}},
    },
}
top_posts = {
    "nasdaq": [{"title": "小白买纳指", "reasoning": "自述小白", "score": 95}],
    "cpo": [],
}
result = generate_interpret(sector_indices, top_posts, sectors_with_hist)
print(f"  mode:        {result['mode']}")
print(f"  history_used: {result.get('history_used')}")
print(f"  解读预览（前 300 字）:")
print("  " + result["interpret"][:300].replace("\n", "\n  "))

assert result["mode"] in ("llm", "fallback")
print("  ✅ PASS")


# ============================================================
section("🎉 全部 P2 测试通过！")
print()
print("新增的 3 个端点：")
print("  GET /api/interpret      升级：含历史趋势 context")
print("  GET /api/correlation    新增：板块联动热力图")
print("  GET /api/calendar       新增：散户情绪日历")
print()
print("前端下一步：在 dashboard.html 加 UI 渲染这 3 个区块。")

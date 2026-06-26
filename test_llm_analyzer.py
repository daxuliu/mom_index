"""
LLM 分析器单元测试
==================
本文件**只测关键词模式**（不依赖真实 DeepSeek key）。
测 LLM 模式请用 test_llm_live.py（需要 .env 里有 DEEPSEEK_API_KEY）。
跑法: python test_llm_analyzer.py
"""
import os
import sys

# 强制走关键词模式（即使有 key 也不调 LLM，专注测关键词逻辑）
os.environ.pop("DEEPSEEK_API_KEY", None)
sys.path.insert(0, os.path.dirname(__file__))

from analyzer.llm_analyzer import (
    analyze_post, analyze_sector, llm_available,
    AnalysisResult,
)


def test_keyword_mode():
    """测试关键词模式（无 LLM key）"""
    assert not llm_available(), "应未启用 LLM（测试时强制 unset key）"

    cases = [
        {
            "name": "典型小白帖（身份自述 + 决策依赖 + 情绪恐慌）",
            "post": {
                "id": "1",
                "title": "小白第一次买纳指ETF，求大佬们看看还能不能上车",
                "content": "我刚入门什么都不懂，听同事说最近涨得不错就买了 1000 块，求各位大哥帮忙看看还能涨吗？好慌啊已经跌了 5% 了，割肉吗？",
                "platform": "guba",
            },
            "expect_min_score": 40,
            "expect_level_in": ["偏小白", "纯小白"],
        },
        {
            "name": "典型专业帖（专业术语 + 风险意识 + 长期视角）",
            "post": {
                "id": "2",
                "title": "纳指 ETF 估值分析：当前 PE 偏高，建议定投",
                "content": "从 ROE 和估值面看，纳指 100 ETF 的溢价率已经到 3%，建议结合定投计划分批建仓。仅供参考，不构成投资建议。",
                "platform": "guba",
            },
            "expect_max_score": 30,
            "expect_level_in": ["专业投资者", "偏专业", "中间派"],
        },
        {
            "name": "决策依赖型（该不该买）",
            "post": {
                "id": "3",
                "title": "中韩半导体还能买吗？现在入手来得及吗？",
                "content": "看着芯片涨得这么猛，心动想买一点，但不知道会不会追高，麻烦大家给点建议？",
                "platform": "guba",
            },
            "expect_min_score": 30,
            "expect_level_in": ["中间派", "偏小白", "纯小白"],
        },
        {
            "name": "过度乐观型（梭哈/稳赚）",
            "post": {
                "id": "4",
                "title": "满仓干了兄弟们！",
                "content": "梭哈就完事了，躺赚，稳赚不赔，明天就起飞，暴富就在今天！",
                "platform": "guba",
            },
            "expect_min_score": 20,
            "expect_level_in": ["中间派", "偏小白", "纯小白"],
        },
        {
            "name": "垃圾帖",
            "post": {
                "id": "5",
                "title": "签到",
                "content": "我是冲着金条来的",
                "platform": "guba",
            },
            "expect_level": "垃圾帖",
        },
    ]

    ok = 0
    for i, c in enumerate(cases, 1):
        r = analyze_post(c["post"], "test_sector", use_llm=False)
        assert r.analysis_mode == "keyword", f"应走关键词模式, 实际: {r.analysis_mode}"
        print(f"\n[Case {i}] {c['name']}")
        print(f"  标题: {c['post']['title']}")
        print(f"  分数: {r.newbie_score}, 等级: {r.level}, 置信度: {r.newbie_confidence}")
        print(f"  小白信号: {r.matched_newbie[:2]}")
        print(f"  专业信号: {r.matched_pro[:2]}")
        print(f"  推理: {r.reasoning[:120]}")
        print(f"  模式: {r.analysis_mode}")

        # 断言
        if "expect_min_score" in c:
            assert r.newbie_score >= c["expect_min_score"], \
                f"分数应 ≥{c['expect_min_score']}，实际 {r.newbie_score}"
        if "expect_max_score" in c:
            assert r.newbie_score <= c["expect_max_score"], \
                f"分数应 ≤{c['expect_max_score']}，实际 {r.newbie_score}"
        if "expect_level" in c:
            assert r.level == c["expect_level"], \
                f"等级应为 {c['expect_level']}，实际 {r.level}"
        if "expect_level_in" in c:
            assert r.level in c["expect_level_in"], \
                f"等级应在 {c['expect_level_in']}，实际 {r.level}"
        ok += 1
        print(f"  ✅ 通过")
    return ok


def test_sector_batch():
    """测试批量分析"""
    posts = [
        {"id": "a", "title": "小白求助：新手第一次买 ETF", "content": "刚入股市什么都不懂", "platform": "guba"},
        {"id": "b", "title": "估值面分析纳指 ETF 折溢价", "content": "从 PE 和 ROE 看当前估值偏高", "platform": "guba"},
        {"id": "c", "title": "今天冲不冲？", "content": "梭哈稳赚", "platform": "guba"},
        {"id": "d", "title": "定投计划分享", "content": "每月定投 5000，长期持有 10 年", "platform": "guba"},
    ]
    results = analyze_sector(posts, "test", use_llm=False)
    assert len(results) == 4
    # 验证排序：分数从高到低
    scores = [r.newbie_score for r in results]
    assert scores == sorted(scores, reverse=True), f"应按分数降序，实际 {scores}"
    print(f"\n[批量] 4 条帖子分数: {scores}")
    print(f"  排序: ✅")
    return 1


def test_fallback_message():
    """测试 LLM 不可用时降级"""
    # 显式调 use_llm=True 但 env 没 key
    r = analyze_post(
        {"id": "x", "title": "测试帖子", "content": "小白测试"},
        "test", use_llm=True,
    )
    assert r.analysis_mode == "keyword", f"应走 keyword 模式，实际 {r.analysis_mode}"
    print(f"\n[降级] 无 key 时 use_llm=True → 模式: {r.analysis_mode} ✅")
    return 1


def test_interpret_fallback():
    """测试 interpret 降级"""
    from analyzer.interpret_generator import generate_interpret
    sector_indices = {
        "date": "2026-06-25",
        "sectors": {
            "nasdaq": {"name": "纳斯达克", "index": 75, "interpretation": "🟠 高度警惕",
                       "details": {"newbie_ratio": 28, "avg_sentiment": 70}},
            "gold": {"name": "黄金", "index": 15, "interpretation": "🔵 极度冷清",
                     "details": {"newbie_ratio": 5, "avg_sentiment": 30}},
            "cpo": {"name": "CPO", "index": 45, "interpretation": "🟡 开始升温",
                    "details": {"newbie_ratio": 15, "avg_sentiment": 55}},
        },
    }
    result = generate_interpret(sector_indices, top_posts_by_sector={})
    assert "interpret" in result and len(result["interpret"]) > 50
    assert result["mode"] == "fallback", f"无 key 应降级, 实际 {result['mode']}"
    print(f"\n[解读降级] mode: {result['mode']}")
    print(f"  文本前 200 字: {result['interpret'][:200]}")
    return 1


def main():
    print("=" * 60)
    print("   宝妈指数 LLM 分析器 — 单元测试")
    print("=" * 60)

    total = 0
    passed = 0
    for fn, name in [
        (test_keyword_mode, "关键词模式"),
        (test_sector_batch, "批量分析"),
        (test_fallback_message, "LLM 降级消息"),
        (test_interpret_fallback, "解读降级"),
    ]:
        print(f"\n--- {name} ---")
        try:
            n = fn()
            passed += n
            total += n
            print(f"  ✅ {n} 个 case 通过")
        except AssertionError as e:
            print(f"  ❌ 断言失败: {e}")
            total += 1
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  ❌ 异常: {e}")
            total += 1

    print("\n" + "=" * 60)
    print(f"   通过: {passed}/{total}")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

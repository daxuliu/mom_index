"""
LLM 模式活体测试
================
需要 .env 里有 DEEPSEEK_API_KEY，**真正调一次** DeepSeek API。
对比 test_llm_analyzer.py（只测关键词模式，不调 LLM）。

跑法:
    python3 test_llm_live.py
"""
import os
import sys

# 手动加载 .env（不依赖 python-dotenv）
env_path = os.path.join(os.path.dirname(__file__), ".env")
loaded = {}
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            loaded[k] = v
            os.environ.setdefault(k, v)
else:
    print(f"❌ 找不到 .env: {env_path}")
    sys.exit(1)

api_key = loaded.get("DEEPSEEK_API_KEY", "")
if not api_key or api_key.startswith("sk-你的"):
    print("❌ .env 里 DEEPSEEK_API_KEY 还没填，或还是占位符")
    print(f"   当前: {api_key[:15] if api_key else '(空)'}")
    sys.exit(1)
if not api_key.startswith("sk-"):
    print(f"⚠️  key 格式异常（前 4 位: {api_key[:4]}），DeepSeek key 通常以 sk- 开头")

print(f"✅ key 加载成功（前 7 位: {api_key[:7]}...，长度: {len(api_key)}）")
_model = os.environ.get("DEEPSEEK_MODEL") or "deepseek-v4-flash（默认）"
print(f"✅ 模型: {_model}")
print()

sys.path.insert(0, os.path.dirname(__file__))
from analyzer.llm_analyzer import analyze_post, llm_available

if not llm_available():
    print("❌ llm_available() 返回 False，请检查 .env")
    sys.exit(1)


def _short(text, n=100):
    return text if len(text) <= n else text[:n] + "..."


def _print_result(label, r):
    print(f"=== {label} ===")
    print(f"  模式:     {r.analysis_mode}    {'✅' if r.analysis_mode == 'llm' else '❌'}")
    print(f"  小白分:   {r.newbie_score}")
    print(f"  置信度:   {r.newbie_confidence}")
    print(f"  等级:     {r.level}")
    print(f"  意图:     {r.intent} (强度 {r.intent_strength})")
    print(f"  情绪:     {r.sentiment_score}")
    print(f"  耗时:     {r.llm_latency_ms} ms")
    print(f"  理由:     {_short(r.reasoning)}")
    print()


# ---- 测试 1: 典型小白帖 ----
r1 = analyze_post(
    post={
        "id": "live-001",
        "title": "小白第一次买纳指ETF，求大佬们看看还能不能上车",
        "content": "我刚入门什么都不懂，听同事说最近涨得不错就买了 1000 块，求各位大哥帮忙看看还能涨吗？好慌啊已经跌了 5% 了，割肉吗？",
        "platform": "guba",
    },
    sector="NASDAQ",
    use_llm=True,
)
_print_result("测试 1: 典型小白帖", r1)

# ---- 测试 2: 典型专业帖 ----
r2 = analyze_post(
    post={
        "id": "live-002",
        "title": "纳指 ETF 估值分析：当前 PE 偏高，建议定投",
        "content": "从 ROE 和估值面看，纳指 100 ETF 的溢价率已经到 3%，建议结合定投计划分批建仓。仅供参考，不构成投资建议。",
        "platform": "guba",
    },
    sector="NASDAQ",
    use_llm=True,
)
_print_result("测试 2: 典型专业帖", r2)

# ---- 结论 ----
print("=== 结论 ===")
if r1.analysis_mode == "llm" and r2.analysis_mode == "llm":
    print("🎉 LLM 模式完全跑通！")
    print(f"   小白帖分: {r1.newbie_score}（期望高，>= 60）")
    print(f"   专业帖分: {r2.newbie_score}（期望低，<= 40）")
    sep = r1.newbie_score - r2.newbie_score
    print(f"   分差:     {sep:+.1f}（期望大，> 20）")
    if r1.newbie_score > r2.newbie_score:
        print("✅ LLM 区分能力正确（小白 > 专业）")
    else:
        print("⚠️  LLM 没正确区分，可能是 prompt 需要调优")
else:
    print("❌ LLM 模式未启用")
    print(f"   r1: {r1.analysis_mode}, r2: {r2.analysis_mode}")

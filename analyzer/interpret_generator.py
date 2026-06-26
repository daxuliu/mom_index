"""
AI 指数解读生成器
================
基于当日 11 板块指数 + 代表性小白帖，调用 LLM 生成一段人类可读的
"今天市场情绪怎么样、为什么"的中文解读（300-500 字）。

降级策略：LLM 不可用时，用规则拼接出一个简化版解读。
"""
from typing import Dict, List, Optional
import json
import os
import time
import logging

try:
    from openai import OpenAI
    _OPENAI_SDK_AVAILABLE = True
except ImportError:
    OpenAI = None
    _OPENAI_SDK_AVAILABLE = False

logger = logging.getLogger(__name__)


_INTERPRET_SYSTEM = """你是「宝妈指数」项目的市场分析师。你的风格：
- **专业但接地气**：用普通投资者能听懂的话，但保持数据严谨
- **有判断有温度**：敢说"市场过热/过冷"，但不要用绝对化的口吻
- **不构成投资建议**：每次结尾提醒"以上仅为情绪分析，不构成投资建议"
- **格式**：纯文本，2-4 段，每段 3-5 句

参考的指数含义：
- 0-20 极度冷清：市场情绪冰点，往往是机会区
- 20-40 正常区间：理性占主导
- 40-60 开始升温：需警惕非理性声音
- 60-80 高度警惕：菜市场大妈都在讨论
- 80-100 极度狂热：擦鞋童理论触发点
"""


def _get_client():
    if not _OPENAI_SDK_AVAILABLE:
        return None
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        return OpenAI(
            api_key=api_key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            timeout=int(os.environ.get("DEEPSEEK_TIMEOUT", "30")),
        )
    except Exception as e:
        logger.warning(f"[interpret] DeepSeek 客户端初始化失败: {e}")
        return None


def _build_prompt(sector_indices: Dict, top_posts_by_sector: Dict) -> str:
    """拼装 prompt：指数概况 + 代表性小白帖"""
    lines = []
    lines.append("【今日板块情绪指数概览】")
    lines.append(f"日期: {sector_indices.get('date', 'N/A')}")
    lines.append("")

    # 板块按指数从高到低排
    sectors = sector_indices.get("sectors", {}) or {}
    sorted_sectors = sorted(
        sectors.items(),
        key=lambda kv: kv[1].get("index", 0) if isinstance(kv[1], dict) else 0,
        reverse=True,
    )
    for key, data in sorted_sectors:
        if not isinstance(data, dict):
            continue
        name = data.get("name", key)
        idx = data.get("index", 0)
        interp = data.get("interpretation", "")
        details = data.get("details", {})
        ratio = details.get("newbie_ratio", 0)
        avg_sent = details.get("avg_sentiment", 50)
        lines.append(f"- {name}: {idx} 分 — {interp} (小白占比 {ratio}%, 平均情绪 {avg_sent})")

    # Top 小白帖
    lines.append("")
    lines.append("【各板块代表性小白帖】")
    for key, posts in top_posts_by_sector.items():
        if not posts:
            continue
        name = sectors.get(key, {}).get("name", key) if isinstance(sectors.get(key), dict) else key
        lines.append(f"\n[{name}]")
        for i, p in enumerate(posts[:2], 1):
            title = p.get("title", "")[:60]
            reasoning = p.get("reasoning", "")[:100]
            score = p.get("score", 0)
            lines.append(f"  {i}. [{score}分] {title} — {reasoning}")

    lines.append("")
    lines.append("请基于以上数据，写一段 200-400 字的『今日市场情绪解读』，覆盖：")
    lines.append("1. 整体冷热判断（哪些板块过热、哪些过冷）")
    lines.append("2. 典型小白帖反映了什么群体情绪")
    lines.append("3. 对投资者的实操意义（不构成投资建议）")
    return "\n".join(lines)


def _fallback_interpret(sector_indices: Dict) -> str:
    """降级方案：基于规则拼接解读"""
    sectors = sector_indices.get("sectors", {}) or {}
    if not sectors:
        return "暂无数据。"

    sorted_sectors = sorted(
        sectors.items(),
        key=lambda kv: kv[1].get("index", 0) if isinstance(kv[1], dict) else 0,
        reverse=True,
    )

    hot = [(k, v) for k, v in sorted_sectors if isinstance(v, dict) and v.get("index", 0) >= 60]
    cold = [(k, v) for k, v in sorted_sectors if isinstance(v, dict) and v.get("index", 0) <= 20]

    lines = [f"📊 今日（{sector_indices.get('date', 'N/A')}）宝妈指数概览："]

    if hot:
        names = "、".join(v.get("name", k) for k, v in hot[:3])
        lines.append(f"🔥 热度前三：{names}——这些板块的小白讨论最为活跃，")
        lines.append("非理性声音占比较高，**需警惕追高风险**。")

    if cold:
        names = "、".join(v.get("name", k) for k, v in cold[:3])
        lines.append(f"\n❄️ 冷度前三：{names}——市场关注度极低，")
        lines.append("往往是机会区（逆向思维：别人恐惧我贪婪），但也可能是基本面问题。")

    if not hot and not cold:
        lines.append("\n整体市场情绪处于正常区间，没有明显过热或过冷板块。")
        lines.append("保持既定策略，不被短期噪音干扰。")

    lines.append("\n⚠️ 以上仅为情绪分析，不构成投资建议。投资有风险，决策需谨慎。")
    return "\n".join(lines)


def generate_interpret(sector_indices: Dict, top_posts_by_sector: Optional[Dict] = None) -> Dict:
    """
    生成今日指数解读。

    Args:
        sector_indices: 形如 {date, sectors: {key: {name, index, interpretation, details}}}
        top_posts_by_sector: 形如 {key: [post, post, ...]}（可选，没有就只用指数）

    Returns:
        {interpret: str, mode: "llm"|"fallback", latency_ms: int, tokens: int}
    """
    top_posts_by_sector = top_posts_by_sector or {}
    client = _get_client()

    if client is None:
        return {
            "interpret": _fallback_interpret(sector_indices),
            "mode": "fallback",
            "latency_ms": 0,
            "tokens": 0,
        }

    prompt = _build_prompt(sector_indices, top_posts_by_sector)
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            messages=[
                {"role": "system", "content": _INTERPRET_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,    # 解读需要点创造性
            max_tokens=800,
        )
        latency = int((time.time() - t0) * 1000)
        text = resp.choices[0].message.content.strip()
        usage = resp.usage
        tokens = (usage.total_tokens if usage else 0) or 0
        return {
            "interpret": text,
            "mode": "llm",
            "latency_ms": latency,
            "tokens": tokens,
        }
    except Exception as e:
        logger.warning(f"[interpret] LLM 调用失败，降级: {e}")
        return {
            "interpret": _fallback_interpret(sector_indices),
            "mode": "fallback",
            "latency_ms": int((time.time() - t0) * 1000),
            "tokens": 0,
            "error": str(e)[:200],
        }

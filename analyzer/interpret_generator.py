"""
AI 指数解读生成器 (P2 升级版)
============================
基于当日 11 板块指数 + 代表性小白帖 + **近 7/30 天历史趋势**，
调用 LLM 生成一段人类可读的"今天市场情绪怎么样、为什么"的中文解读。

P2 升级点：
- ✅ 引入历史趋势 context：连续升温/降温天数、7 天变化、相似情形检索
- ✅ 让 LLM 引用历史数据生成"过去 N 天从 X 升到 Y"、"历史上类似情形..." 类解读
- ✅ 降级策略：无 LLM / 无历史数据时，自动用规则拼接

降级策略：LLM 不可用时，用规则拼接出一个简化版解读。
"""
from typing import Dict, List, Optional, Tuple
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
- **善用历史**：优先引用「历史趋势 context」里的数据，让解读有纵深
- **不构成投资建议**：每次结尾提醒"以上仅为情绪分析，不构成投资建议"
- **格式**：纯文本，3-5 段，每段 3-5 句，可用 🔥/❄️/📈/📉/⚠️ 等 emoji 增强可读性

参考的指数含义：
- 0-20 极度冷清：市场情绪冰点，往往是机会区
- 20-40 正常区间：理性占主导
- 40-60 开始升温：需警惕非理性声音
- 60-80 高度警惕：菜市场大妈都在讨论
- 80-100 极度狂热：擦鞋童理论触发点

历史趋势 context 字段说明（务必善用）：
- `trend_7d`: 近 7 天指数变化（数值 + 方向 up/down/flat + 百分比）
- `streak`: 连续升温/降温天数（正数=连续升温，负数=连续降温，0=平稳）
- `volatility_30d`: 近 30 天标准差（数值越大=波动越剧烈）
- `similar_episodes`: 历史上相似情形的列表（指数接近 + 当时后续表现）
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


# ============================================================
# 历史趋势分析（无需 LLM，本地算法）
# ============================================================

def compute_trend_context(history: List[Dict]) -> Dict:
    """
    从历史记录里算出"近 7 天趋势 / 连续升温天数 / 30 天波动 / 相似情形"。

    Args:
        history: 按日期降序排列的板块历史，元素形如
            {"record_date": "2026-06-25", "index_value": 65.3, ...}

    Returns:
        {
            "trend_7d": {"change": +12.3, "direction": "up", "pct": "+18.7%"},
            "streak": 3,                            # 连续升温 3 天
            "volatility_30d": 8.5,                  # 近 30 天标准差
            "similar_episodes": [                   # 历史上相似情形
                {"date": "2026-05-10", "index": 63.1,
                 "followup_7d": -5.2, "followup_30d": -8.3},
                ...
            ],
            "data_quality": "rich" | "limited" | "none"
        }
    """
    if not history:
        return {"data_quality": "none"}

    # 统一字段名（兼容 record_date / date；index / index_value 两种命名）
    norm = []
    for h in history:
        d = h.get("record_date") or h.get("date")
        v = h.get("index") or h.get("index_value")
        if v is None and isinstance(h.get("details"), dict):
            v = h["details"].get("newbie_score")
        if d is None or v is None:
            continue
        try:
            norm.append({"date": d, "value": float(v)})
        except (TypeError, ValueError):
            continue

    # 升序排列（方便前后对比）
    norm.sort(key=lambda x: x["date"])

    if len(norm) < 2:
        return {"data_quality": "limited" if norm else "none"}

    latest = norm[-1]
    latest_val = latest["value"]

    # ----- trend_7d: 与 7 天前的对比 -----
    trend_7d = None
    if len(norm) >= 8:
        ref = norm[-8]
        change = latest_val - ref["value"]
        pct = (change / ref["value"] * 100) if ref["value"] != 0 else 0
        if abs(change) < 1.5:
            direction = "flat"
        elif change > 0:
            direction = "up"
        else:
            direction = "down"
        trend_7d = {
            "ref_date": ref["date"],
            "ref_value": round(ref["value"], 1),
            "change": round(change, 1),
            "pct": f"{pct:+.1f}%",
            "direction": direction,
        }

    # ----- streak: 连续升温/降温天数 -----
    streak = 0
    direction = None
    for i in range(len(norm) - 1, 0, -1):
        diff = norm[i]["value"] - norm[i - 1]["value"]
        if abs(diff) < 0.5:  # 当日平稳，不算 streak
            break
        d = "up" if diff > 0 else "down"
        if direction is None:
            direction = d
            streak = 1
        elif d == direction:
            streak += 1
        else:
            break

    # ----- volatility_30d: 近 30 天标准差 -----
    values_30d = [x["value"] for x in norm[-30:]]
    if len(values_30d) >= 2:
        mean = sum(values_30d) / len(values_30d)
        variance = sum((v - mean) ** 2 for v in values_30d) / len(values_30d)
        volatility_30d = round(variance ** 0.5, 1)
    else:
        volatility_30d = None

    # ----- similar_episodes: 历史上相似情形（±10 分以内） -----
    similar = []
    if len(norm) >= 10:
        target = latest_val
        for i in range(len(norm) - 1):  # 排除最新一条本身
            ep = norm[i]
            if abs(ep["value"] - target) <= 10:
                # 计算后续 7/30 天表现（向前看）
                f7 = None
                f30 = None
                if i + 7 < len(norm):
                    f7 = round(norm[i + 7]["value"] - ep["value"], 1)
                if i + 30 < len(norm):
                    f30 = round(norm[i + 30]["value"] - ep["value"], 1)
                similar.append({
                    "date": ep["date"],
                    "index": round(ep["value"], 1),
                    "diff": round(ep["value"] - target, 1),
                    "followup_7d": f7,
                    "followup_30d": f30,
                })
        # 按日期降序，最多取 5 个
        similar = sorted(similar, key=lambda x: x["date"], reverse=True)[:5]

    # ----- data_quality -----
    if len(norm) >= 14 and similar:
        quality = "rich"
    elif len(norm) >= 7:
        quality = "limited"
    else:
        quality = "minimal"

    return {
        "trend_7d": trend_7d,
        "streak": streak if direction == "up" else (-streak if direction == "down" else 0),
        "streak_direction": direction or "flat",
        "volatility_30d": volatility_30d,
        "similar_episodes": similar,
        "data_quality": quality,
        "samples": len(norm),
    }


def _build_history_context(sectors_with_history: Dict[str, List[Dict]]) -> str:
    """
    把多板块的历史 trend 拼成 LLM prompt 的一段。
    sectors_with_history: {sector_key: history_list}

    只输出 samples >= 7 的板块（数据太少没意义）。
    """
    lines = []
    any_rich = False
    for key, hist in sectors_with_history.items():
        ctx = compute_trend_context(hist)
        if ctx.get("samples", 0) < 7:
            # 数据太少，跳过
            continue
        if ctx.get("data_quality") == "rich":
            any_rich = True
        name = hist[0].get("sector_name", key) if hist else key
        lines.append(f"  - {name} ({ctx['data_quality']}, {ctx.get('samples', 0)} 天数据):")
        if ctx.get("trend_7d"):
            t = ctx["trend_7d"]
            lines.append(
                f"    7 天变化: {t['change']:+} 分 ({t['pct']}, 方向: {t['direction']})"
            )
        if ctx.get("streak") and ctx["streak"] != 0:
            lines.append(
                f"    连续 {'升温' if ctx['streak'] > 0 else '降温'} "
                f"{abs(ctx['streak'])} 天"
            )
        if ctx.get("volatility_30d") is not None:
            v = ctx["volatility_30d"]
            v_level = "高波动" if v > 15 else ("中等" if v > 8 else "平稳")
            lines.append(f"    30 天波动: {v} 分（{v_level}）")
        if ctx.get("similar_episodes"):
            lines.append(f"    历史上相似情形（指数±10 分以内，共 {len(ctx['similar_episodes'])} 次）:")
            for ep in ctx["similar_episodes"][:3]:
                f7 = ep.get("followup_7d")
                f30 = ep.get("followup_30d")
                f7_s = f"{f7:+.1f}" if f7 is not None else "数据不足"
                f30_s = f"{f30:+.1f}" if f30 is not None else "数据不足"
                lines.append(
                    f"      · {ep['date']}（{ep['index']} 分）→ 7 天后 {f7_s}，30 天后 {f30_s}"
                )

    if not lines:
        return "（暂无历史趋势数据）"
    header = "【历史趋势 context - 重要数据源】" if any_rich else "【历史趋势 context（数据有限）】"
    return f"{header}\n" + "\n".join(lines)


def _build_prompt(sector_indices: Dict, top_posts_by_sector: Dict,
                  history_context: str = "") -> str:
    """拼装 prompt：指数概况 + 代表性小白帖 + 历史趋势 context"""
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

    # 历史趋势
    if history_context:
        lines.append("")
        lines.append(history_context)

    lines.append("")
    lines.append("请基于以上数据，写一段 300-500 字的『今日市场情绪解读』，覆盖：")
    lines.append("1. 整体冷热判断（哪些板块过热、哪些过冷）")
    lines.append("2. **必须引用历史趋势 context**：连续升温/降温、7 天变化、历史上相似情形（这是你区别于普通解读的核心价值）")
    lines.append("3. 典型小白帖反映了什么群体情绪")
    lines.append("4. 对投资者的实操意义（不构成投资建议）")
    return "\n".join(lines)


def _fallback_interpret(sector_indices: Dict, history_context: str = "") -> str:
    """降级方案：基于规则拼接解读（含历史 trend）"""
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
        top_idx = hot[0][1].get("index", 0)
        lines.append(f"🔥 热度前三：{names}（最高 {top_idx} 分）——这些板块的小白讨论最为活跃，")
        lines.append("非理性声音占比较高，**需警惕追高风险**。")

    if cold:
        names = "、".join(v.get("name", k) for k, v in cold[:3])
        lines.append(f"\n❄️ 冷度前三：{names}——市场关注度极低，")
        lines.append("往往是机会区（逆向思维：别人恐惧我贪婪），但也可能是基本面问题。")

    if not hot and not cold:
        lines.append("\n整体市场情绪处于正常区间，没有明显过热或过冷板块。")
        lines.append("保持既定策略，不被短期噪音干扰。")

    # 历史 trend（如有）
    if history_context and "暂无" not in history_context:
        lines.append("\n📈 **历史趋势**（节选自降级规则）：")
        lines.append("```")
        lines.append(history_context)
        lines.append("```")

    lines.append("\n⚠️ 以上仅为情绪分析，不构成投资建议。投资有风险，决策需谨慎。")
    return "\n".join(lines)


def generate_interpret(sector_indices: Dict,
                       top_posts_by_sector: Optional[Dict] = None,
                       sectors_with_history: Optional[Dict[str, List[Dict]]] = None) -> Dict:
    """
    生成今日指数解读 (P2 升级版)。

    Args:
        sector_indices: 形如 {date, sectors: {key: {name, index, interpretation, details}}}
        top_posts_by_sector: 形如 {key: [post, post, ...]}（可选，没有就只用指数）
        sectors_with_history: 形如 {sector_key: [history_row, ...]}（P2 新增，用于 trend context）

    Returns:
        {interpret: str, mode: "llm"|"fallback", latency_ms: int, tokens: int, history_used: bool}
    """
    top_posts_by_sector = top_posts_by_sector or {}
    sectors_with_history = sectors_with_history or {}

    # 拼历史 trend context
    history_context = _build_history_context(sectors_with_history) if sectors_with_history else ""
    history_used = bool(history_context and "暂无" not in history_context)

    client = _get_client()

    if client is None:
        return {
            "interpret": _fallback_interpret(sector_indices, history_context),
            "mode": "fallback",
            "latency_ms": 0,
            "tokens": 0,
            "history_used": history_used,
        }

    prompt = _build_prompt(sector_indices, top_posts_by_sector, history_context)
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            messages=[
                {"role": "system", "content": _INTERPRET_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,    # 解读需要点创造性
            max_tokens=1000,   # P2 升级：从 800 → 1000（容纳历史引用）
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
            "history_used": history_used,
        }
    except Exception as e:
        logger.warning(f"[interpret] LLM 调用失败，降级: {e}")
        return {
            "interpret": _fallback_interpret(sector_indices, history_context),
            "mode": "fallback",
            "latency_ms": int((time.time() - t0) * 1000),
            "tokens": 0,
            "history_used": history_used,
            "error": str(e)[:200],
        }

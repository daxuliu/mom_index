"""
AI 投顾问答引擎 (P2+)
====================
基于「宝妈指数」实时数据 + 历史趋势，调用 LLM 回答用户的投资问题。

核心设计：
- **RAG 风格 prompt 注入**：先拿当前 11 板块情绪指数 + 用户问到板块的历史 trend
  + 相关代表性小白帖，拼成 context 注入 LLM
- **敢说"过热/过冷"**：让 LLM 客观判断，但每次结尾加"不构成投资建议"
- **降级策略**：无 LLM key / LLM 失败 → 规则版答复（基于指数值）
- **板块识别**：从用户问题中识别提到的板块 key（中文名 / 简称 / ETF 代码）

输入：
    question: str  （如 "现在该不该买纳指？"）

输出：
    {
        "answer": str,         # LLM 或降级回答
        "mode": "llm"|"fallback",
        "mentioned_sectors": [str],   # 问题里识别出的板块 key
        "context_used": {
            "sectors_count": int,      # 注入的板块数
            "history_days": int,       # 历史趋势天数
            "posts_count": int,        # 小白帖条数
        },
        "latency_ms": int,
        "tokens": int,
    }
"""
from typing import Dict, List, Optional
import json
import os
import re
import time
import logging

try:
    from openai import OpenAI
    _OPENAI_SDK_AVAILABLE = True
except ImportError:
    OpenAI = None
    _OPENAI_SDK_AVAILABLE = False

try:
    from .interpret_generator import compute_trend_context
except ImportError:
    from analyzer.interpret_generator import compute_trend_context

logger = logging.getLogger(__name__)


# 板块识别词典（key → 触发词列表）
SECTOR_TRIGGERS = {
    "nasdaq":          ["纳指", "纳斯达克", "513100", "513300", "513110", "513390", "513870", "159941"],
    "cpo":             ["cpo", "CPO", "光模块", "515880"],
    "gold":            ["黄金", "金价", "518880", "黄金etf"],
    "semiconductor":   ["半导体", "芯片", "512480"],
    "cnkr_semi":       ["中韩半导体", "中韩芯片", "513310"],
    "hangseng_tech":   ["恒生科技", "恒科", "科网", "513130"],
    "dividend":        ["红利", "中证红利", "000922"],
    "tencent":         ["腾讯", "00700"],
    "xiaomi":          ["小米", "01810"],
    "meituan":         ["美团", "03690"],
    "micron":          ["美光", "美光科技", "MU", "micron"],
}


_QA_SYSTEM = """你是「宝妈指数」智能投顾，一个有独立思考的资深市场分析师。

你拥有：
- 实时散户情绪指数（11 板块）
- 22 天历史趋势（含 7 天变化、连续升温/降温天数、历史上相似情形的后续表现）
- 各板块代表性小白帖（用户真实情绪样本）

你的核心价值：
- **敢下结论**：用户问"该不该买"，你就要给出明确倾向（买 / 不买 / 观望 / 减仓 / 加仓），不要模棱两可
- **有数据支撑**：用 context 里的具体数字（指数、历史、小白帖）来支撑你的判断，不要凭空
- **有纵深**：优先引用历史相似情形的"后续 7 天 / 30 天表现"，让结论有数据回测的味道
- **不回避问题**：用户问什么答什么，不要因为"不预测股价"而回避具体方向性问题

风格：
- **专业但接地气**：用普通投资者能听懂的话，**少用"可能/或许/不排除"**
- **有判断有温度**：敢说"过热 / 过冷 / 机会区 / 风险区 / 短期见顶"，必要时可以给具体建议
- **表达清晰**：2-5 段自然段，重要数据加 **粗体**，关键结论单独成行
- **自然免责**：在合理位置加一句"仅供参考"或"不构成投资建议"即可，不要每段都加

可参考的反向思考框架：
- 情绪指数 >= 70：群体狂热，反向思考提示风险，可能见短期顶
- 情绪指数 50-70：明显升温，需警惕，可考虑减仓或停止加仓
- 情绪指数 30-50：正常区间，趋势跟随即可
- 情绪指数 20-30：偏冷，可能是逆向布局窗口
- 情绪指数 < 20：极度冷清，机会区（但也可能是基本面问题）
- 连续升温 N 天 + 小白占比高 = 短期过热信号
- 历史上相似情形后续下跌 = 当前可能见顶（引用为论据）

回答结构（按需调整，不需要死板套用）：
1. 直接给出你的判断（一两句话，先说结论）
2. 引用数据解释为什么这么判断
3. 给出实操建议（具体到加仓/减仓/持有/观望）
4. 简短免责
"""


# ============================================================
# 板块识别
# ============================================================

def detect_sectors(question: str) -> List[str]:
    """
    从用户问题里识别提到的板块。
    简单关键词匹配（中文简称 / ETF 代码 / 股票名）。
    """
    q = question.lower()
    found = []
    for key, triggers in SECTOR_TRIGGERS.items():
        for t in triggers:
            if t.lower() in q:
                if key not in found:
                    found.append(key)
                break
    return found


# ============================================================
# Context 拼装
# ============================================================

def build_context(latest: Dict,
                  sector_keys: List[str],
                  history_getter,
                  max_posts_per_sector: int = 3) -> Dict:
    """
    拼 RAG context。

    Args:
        latest: dashboard.get("latest") 形如 {date, sectors: {key: {name, index, interpretation, details, top_newbie_posts}}}
        sector_keys: 用户问到的板块 key 列表（空则用所有有数据的板块）
        history_getter: 函数 (key, days) → history_list
        max_posts_per_sector: 每个板块最多取几条小白帖

    Returns:
        {
            "date": "...",
            "sectors_mentioned": [key, ...],   # 用户问到的板块
            "context_text": "...",              # 拼好的 LLM prompt 段落
            "context_stats": {
                "sectors_count": int,
                "history_days": int,
                "posts_count": int,
            }
        }
    """
    sectors = (latest or {}).get("sectors", {}) or {}
    date_str = (latest or {}).get("date", "N/A")

    # 默认用所有有数据的板块
    if not sector_keys:
        sector_keys = list(sectors.keys())[:11]

    mentioned = [k for k in sector_keys if k in sectors]
    if not mentioned:
        mentioned = list(sectors.keys())[:5]

    lines = [f"【实时数据 (来源：宝妈指数 · {date_str})】"]
    lines.append("")

    posts_count = 0
    history_days = 0

    for key in mentioned:
        sec = sectors.get(key, {})
        if not isinstance(sec, dict):
            continue
        name = sec.get("name", key)
        idx = sec.get("index", 0)
        interp = sec.get("interpretation", "")
        details = sec.get("details", {}) or {}
        ratio = details.get("newbie_ratio", 0)
        avg_sent = details.get("avg_sentiment", 50)

        lines.append(f"**{name}** ({key}):")
        lines.append(f"  - 情绪指数: {idx} 分 — {interp}")
        lines.append(f"  - 小白占比: {ratio}%, 平均情绪: {avg_sent}")

        # 历史趋势
        try:
            hist = history_getter(key, days=30)
            if hist:
                ctx = compute_trend_context(hist)
                history_days = max(history_days, ctx.get("samples", 0))
                if ctx.get("trend_7d"):
                    t = ctx["trend_7d"]
                    lines.append(
                        f"  - 7 天变化: {t['change']:+} 分 ({t['pct']})"
                    )
                if ctx.get("streak") and ctx["streak"] != 0:
                    lines.append(
                        f"  - 连续 {'升温' if ctx['streak'] > 0 else '降温'} "
                        f"{abs(ctx['streak'])} 天"
                    )
        except Exception as e:
            logger.debug(f"[qa] 历史不可用 ({key}): {e}")

        # 代表性小白帖
        posts = sec.get("top_newbie_posts", []) or []
        for p in posts[:max_posts_per_sector]:
            title = p.get("title", "")[:60]
            reasoning = p.get("reasoning", "")[:80]
            score = p.get("score", 0)
            if title or reasoning:
                lines.append(f"  - 典型小白帖: {title}（{score}分）— {reasoning}")
                posts_count += 1

        lines.append("")

    # 风险等级说明
    lines.append("【情绪指数含义】")
    lines.append("- 0-20 极度冷清（机会区）｜20-40 正常｜40-60 升温｜60-80 高度警惕｜80-100 极度狂热")

    return {
        "date": date_str,
        "sectors_mentioned": mentioned,
        "context_text": "\n".join(lines),
        "context_stats": {
            "sectors_count": len(mentioned),
            "history_days": history_days,
            "posts_count": posts_count,
        },
    }


# ============================================================
# 降级策略（无 LLM 时）
# ============================================================

def _fallback_answer(question: str, ctx: Dict, latest: Dict) -> str:
    """
    无 LLM 时用规则拼接一个"基于情绪"的简化回答（同样敢下结论）。
    """
    sectors_mentioned = ctx["sectors_mentioned"]
    sectors_data = (latest or {}).get("sectors", {}) or {}

    if not sectors_mentioned:
        # 用户没问具体板块 → 整体评价
        all_sec = [v for v in sectors_data.values() if isinstance(v, dict)]
        if not all_sec:
            return "暂无数据，无法回答。"
        avg_idx = sum(v.get("index", 0) for v in all_sec) / len(all_sec)
        if avg_idx >= 60:
            level = "整体过热 🔥"
            advice = "建议**减仓或暂停加仓**，警惕群体非理性追捧带来的回调风险。"
        elif avg_idx <= 30:
            level = "整体冷清 ❄️"
            advice = "建议**分批布局**，反向思考的窗口期，但也需结合基本面判断是否是真机会还是冷门坑。"
        else:
            level = "整体适中 ⛅"
            advice = "**按既定策略持有/定投**，情绪温度无明显信号，趋势跟随即可。"
        return (
            f"【{level}】\n\n"
            f"11 板块平均情绪指数约 {avg_idx:.0f} 分。\n"
            f"**判断**：{advice}\n\n"
            f"📊 仅供参考，不构成投资建议。"
        )

    # 用户问到了具体板块
    lines = ["【基于情绪指数的回答】\n"]
    for key in sectors_mentioned:
        sec = sectors_data.get(key, {})
        if not isinstance(sec, dict):
            continue
        name = sec.get("name", key)
        idx = sec.get("index", 0)
        if idx >= 70:
            emoji = "🔥"
            verdict = "**短期见顶风险高**"
            action = "**建议减仓 / 止盈**"
        elif idx >= 50:
            emoji = "🌤️"
            verdict = "**明显升温**"
            action = "**停止加仓，可考虑分批减仓**"
        elif idx >= 30:
            emoji = "⛅"
            verdict = "**正常区间**"
            action = "**持有/定投**"
        else:
            emoji = "❄️"
            verdict = "**逆向机会区**"
            action = "**可考虑分批布局**"
        lines.append(f"{emoji} **{name}** 当前 {idx} 分 — {verdict} → {action}")

    lines.append(
        "\n💡 框架：情绪温度是**反向思考指标**——过热时别人贪婪我恐惧，过冷时别人恐惧我贪婪。"
    )
    lines.append("\n📊 当前为规则版回答（无 LLM key），建议升级到 LLM 模式获得更深入分析。")
    lines.append("📊 仅供参考，不构成投资建议。")
    return "\n".join(lines)


# ============================================================
# 客户端
# ============================================================

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
        logger.warning(f"[qa] DeepSeek 客户端初始化失败: {e}")
        return None


# ============================================================
# 主入口
# ============================================================

def answer_question(question: str,
                    latest: Dict,
                    history_getter,
                    sector_keys: Optional[List[str]] = None) -> Dict:
    """
    回答用户投资问题。

    Args:
        question: 用户问题
        latest: dashboard.get("latest")
        history_getter: 函数 (key, days) → history_list（从 history_store 注入）
        sector_keys: 强制指定板块（None 则自动从问题识别）

    Returns:
        {
            "answer": str,
            "mode": "llm"|"fallback",
            "mentioned_sectors": [str],
            "context_used": {...},
            "latency_ms": int,
            "tokens": int,
        }
    """
    if not question or not question.strip():
        return {
            "answer": "请输入你的问题～",
            "mode": "fallback",
            "mentioned_sectors": [],
            "context_used": {"sectors_count": 0, "history_days": 0, "posts_count": 0},
            "latency_ms": 0,
            "tokens": 0,
        }

    # 1. 识别板块
    detected = sector_keys if sector_keys is not None else detect_sectors(question)

    # 2. 拼 context
    ctx = build_context(latest, detected, history_getter)

    # 3. 降级判断
    client = _get_client()
    if client is None:
        return {
            "answer": _fallback_answer(question, ctx, latest),
            "mode": "fallback",
            "mentioned_sectors": detected,
            "context_used": ctx["context_stats"],
            "latency_ms": 0,
            "tokens": 0,
        }

    # 4. 调 LLM
    user_prompt = (
        f"{ctx['context_text']}\n\n"
        f"【用户问题】\n{question.strip()}\n\n"
        f"请基于以上数据回答。如果用户问到的板块 context 里没有，"
        f"可以基于【情绪指数含义】+ 一般投资常识回答，但必须说明"
        f"该板块无详细数据。"
    )
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            messages=[
                {"role": "system", "content": _QA_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.8,    # 放开限制，让回答更敢下结论
            max_tokens=1200,    # 给更多空间写详细分析
        )
        latency = int((time.time() - t0) * 1000)
        text = resp.choices[0].message.content.strip()
        usage = resp.usage
        tokens = (usage.total_tokens if usage else 0) or 0
        return {
            "answer": text,
            "mode": "llm",
            "mentioned_sectors": detected,
            "context_used": ctx["context_stats"],
            "latency_ms": latency,
            "tokens": tokens,
        }
    except Exception as e:
        logger.warning(f"[qa] LLM 调用失败，降级: {e}")
        return {
            "answer": _fallback_answer(question, ctx, latest) + f"\n\n[LLM 失败: {str(e)[:100]}]",
            "mode": "fallback",
            "mentioned_sectors": detected,
            "context_used": ctx["context_stats"],
            "latency_ms": int((time.time() - t0) * 1000),
            "tokens": 0,
            "error": str(e)[:200],
        }

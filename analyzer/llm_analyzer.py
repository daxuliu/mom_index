"""
宝妈指数 LLM 分析引擎
====================

双模式：
- **LLM 模式**（推荐）：调用 DeepSeek-V4-Flash，对每条股吧/小红书帖子做深度判定
- **关键词模式**（降级）：纯本地基于关键词匹配，无 API key 也能跑

启用方法：
    export DEEPSEEK_API_KEY="sk-xxx"     # 一次性，进程内有效
    # 或 .env 文件（python-dotenv 自动加载）

判断标准（来自 README）：
    小白信号 = 身份自述/知识求助/决策依赖/情绪恐慌/跟风行为/过度乐观/短期思维/金额极小/术语缺失/互动异常
    专业信号 = 专业术语/策略思维/数据引用/风险意识/长期视角/冷静表达
"""
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
import json
import os
import time
import logging

# ============================================================
# 可选依赖：openai SDK（用于 DeepSeek 调用）
# ============================================================
try:
    from openai import OpenAI
    _OPENAI_SDK_AVAILABLE = True
except ImportError:
    OpenAI = None
    _OPENAI_SDK_AVAILABLE = False

logger = logging.getLogger(__name__)


# ============================================================
# 信号定义库（关键词降级模式用）
# ============================================================

@dataclass
class Signal:
    """单个判定信号"""
    name: str
    weight: float          # 权重 (-5 到 +5, 正=小白, 负=专业)
    description: str       # 人类可读的描述

# ---- 小白信号 (正向) ----
NEWBIE_SIGNALS = [
    Signal("身份自述", 8, "明确自称小白/新手/刚入门/宝妈"),
    Signal("知识求助", 6, "在问基础问题（怎么买/在哪看/什么意思）"),
    Signal("决策依赖", 7, "请求他人替自己做投资决策（该不该/要不要/能不能）"),
    Signal("情绪恐慌", 5, "表达明显的恐惧/焦虑/后悔情绪"),
    Signal("跟风行为", 6, "提及跟别人买/听说/博主推荐/朋友说"),
    Signal("过度乐观", 4, "非理性乐观（梭哈/稳赚/必涨/躺赚）"),
    Signal("短期思维", 3, "关注明天/后天/今天涨跌，非长期视角"),
    Signal("金额极小", 2, "讨论几百几千块的投资，试水心态"),
    Signal("术语缺失", 4, "全文无任何专业术语（PE/PB/ETF/溢价率等）"),
    Signal("互动异常", 3, "大量emoji/感叹号/问号，情绪化表达"),
]

# ---- 专业信号 (负向) ----
PRO_SIGNALS = [
    Signal("专业术语", -5, "使用PE/PB/ROE/基本面/技术面/估值等专业词汇"),
    Signal("策略思维", -4, "讨论定投/仓位/分散/对冲/止损等策略"),
    Signal("数据引用", -4, "引用具体数据/财报/宏观经济指标"),
    Signal("风险意识", -3, "明确提及风险/仅供参考/不构成建议"),
    Signal("长期视角", -3, "讨论长期趋势/定投计划/年度收益"),
    Signal("冷静表达", -2, "理性分析，客观陈述，无情绪化表达"),
]

# 关键词匹配规则
NEWBIE_KEYWORDS = {
    "身份自述": ["小白", "新手", "新人", "刚入", "第一次", "菜鸟", "萌新", "宝妈", "全职妈妈", "学生党"],
    "知识求助": ["不懂", "请教", "各位大哥", "大佬", "请问", "有没有人", "谁知道", "求助", "怎么买", "在哪看", "什么意思"],
    "决策依赖": ["该不该", "要不要", "能不能", "可以吗", "行不行", "靠谱吗", "还能上车吗", "现在入手", "还会涨吗", "还会跌吗"],
    "情绪恐慌": ["好慌", "救命", "完了", "哭了", "怕了", "吓死", "心态崩", "太惨", "亏死了", "割肉", "后悔", "早知道"],
    "跟风行为": ["跟着买的", "别人推荐", "博主说", "听说", "朋友说", "同事买", "群里说", "都在买"],
    "过度乐观": ["冲", "梭哈", "稳赚", "必涨", "躺赚", "满仓干", "起飞", "暴富"],
    "短期思维": ["明天涨", "今天跌", "后天走势", "今天买"],
}

PRO_KEYWORDS = {
    "专业术语": ["PE", "PB", "ROE", "溢价率", "折价", "估值", "基本面", "技术面", "MACD", "KDJ", "ETF", "联接", "LOF"],
    "策略思维": ["定投", "仓位", "分散", "对冲", "止损", "止盈", "网格", "轮动", "资产配置"],
    "数据引用": ["季报", "年报", "GDP", "CPI", "非农", "美联储", "加息", "降息", "收益率", "年化"],
    "风险意识": ["仅供参考", "不构成建议", "个人观点", "理性投资", "风险自担", "谨慎"],
    "长期视角": ["长期持有", "定投计划", "年度", "养老", "十年"],
    "冷静表达": ["分析", "观点", "看法", "逻辑", "原因在于"],
}

# ---- 买入/卖出意图关键词 ----
BUY_KEYWORDS = [
    "上车", "冲", "梭哈", "all in", "满仓", "抄底", "加仓", "买入", "买了", "入手", "已入",
    "追", "杀入", "建仓", "补仓", "定投", "已上车",
    "还能买吗", "还能上车吗", "可以买吗", "能不能买", "要不要入",
    "想买", "想入", "心动", "看着眼馋", "忍不住",
    "后悔没买", "错过", "买少了", "早知道就买了", "再不买",
]

SELL_KEYWORDS = [
    "割肉", "割", "止损", "清仓", "减仓", "出货", "卖了", "出了", "跑了", "走人",
    "不玩了", "离场", "下车", "赎回",
    "要不要割", "要不要走", "该不该卖", "还能留吗", "要不要清",
    "想卖", "想走", "想割", "想跑",
    "亏了", "亏麻了", "亏惨了", "深套", "套牢", "后悔买了", "被套",
    "跌麻了", "跌惨了", "血亏", "亏死", "跌死", "跌崩",
]


# ============================================================
# 分析引擎
# ============================================================

@dataclass
class AnalysisResult:
    """单条帖子的完整分析结果"""
    post_id: str
    title: str
    platform: str
    sector: str
    url: str = ""           # 原帖链接（股吧/小红书/抖音）
    content: str = ""       # 帖子正文摘要

    # 分数
    newbie_score: float = 0.0       # 小白总分 (0-100)
    newbie_confidence: str = "low"   # 置信度: high/medium/low

    # 命中信号
    matched_newbie: List[Tuple[str, str, float]] = field(default_factory=list)
    matched_pro: List[Tuple[str, str, float]] = field(default_factory=list)

    # 判定
    level: str = "未判定"      # 纯小白/偏小白/中间派/偏专业/专业
    reasoning: str = ""        # 人类可读的推理过程
    sentiment_score: float = 0  # -1(恐慌) ~ +1(贪婪)
    intent: str = "neutral"     # buy/sell/neutral — 买入/卖出意图
    intent_strength: float = 0  # 0~1 意图强度

    # 用于前端展示
    key_signals: List[str] = field(default_factory=list)

    # 元数据
    analysis_mode: str = "keyword"   # "llm" / "keyword" / "keyword_fallback"
    llm_latency_ms: int = 0           # LLM 调用耗时（毫秒）


# ============================================================
# DeepSeek 客户端（懒加载）
# ============================================================

_DEEPSEEK_CLIENT: Optional["OpenAI"] = None
_DEEPSEEK_KEY: Optional[str] = None
_DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
# 默认走 deepseek-v4-flash（性价比第一，$0.14/1M input）
# 2026-07-24 后 deepseek-chat alias 会弃用，提前切到 v4-flash
# 备选：deepseek-v4-pro（旗舰，强推理，但贵 3 倍）


def _get_deepseek_client() -> Optional["OpenAI"]:
    """懒加载 DeepSeek 客户端，失败返回 None 走降级"""
    global _DEEPSEEK_CLIENT, _DEEPSEEK_KEY
    if _DEEPSEEK_CLIENT is not None:
        return _DEEPSEEK_CLIENT
    if not _OPENAI_SDK_AVAILABLE:
        return None
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        _DEEPSEEK_CLIENT = OpenAI(
            api_key=api_key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            timeout=int(os.environ.get("DEEPSEEK_TIMEOUT", "30")),
        )
        _DEEPSEEK_KEY = api_key
        logger.info(f"[LLM] DeepSeek 客户端初始化成功, model={_DEEPSEEK_MODEL}")
        return _DEEPSEEK_CLIENT
    except Exception as e:
        logger.warning(f"[LLM] DeepSeek 客户端初始化失败: {e}")
        return None


def llm_available() -> bool:
    """是否启用了 LLM 模式（key 存在 + SDK 可用）"""
    return _get_deepseek_client() is not None


# ============================================================
# DeepSeek 调用
# ============================================================

_SYSTEM_PROMPT = """你是「宝妈指数」项目的 A 股散户情绪分析专家。你的任务是判断下面这条社交媒体帖子（来自股吧/小红书/雪球等）到底属于"小白发言"还是"专业分析"。

# 小白典型特征
- 身份自述：自称小白/新手/刚入门/宝妈/学生党
- 知识求助：问基础问题（怎么买/在哪看/什么意思）
- 决策依赖：请求他人替自己做投资决策（该不该/要不要/能不能）
- 情绪恐慌/过度乐观：表达明显的恐惧/后悔 或 梭哈/稳赚/必涨
- 跟风行为：提及跟别人买/博主说/朋友推荐
- 短期思维：只关注明天/今天涨跌
- 术语缺失：全文找不到任何专业术语

# 专业典型特征
- 使用专业术语：PE/PB/ROE/估值/技术面/溢价率/折价 等
- 策略思维：讨论定投/仓位/分散/对冲/止损
- 数据引用：引用财报/宏观数据/经济指标
- 风险意识：明确写"仅供参考/不构成建议"
- 长期视角：定投计划/长期持有
- 冷静表达：理性分析，无情绪化

# 输出格式（**严格 JSON**）
{
  "is_newbie": true | false,                    // 是否小白
  "score": 0-100,                                // 小白分数（0=绝对专业，100=绝对小白）
  "confidence": "high" | "medium" | "low",       // 判定置信度
  "level": "纯小白" | "偏小白" | "中间派" | "偏专业" | "专业投资者",
  "signals": [                                   // 命中的信号，最多 5 个
    {"name": "信号名", "weight": 8, "evidence": "原文中的具体片段"}
  ],
  "reasoning": "中文 1-2 句话解释判定逻辑",
  "sentiment": 0.0-1.0,                          // 情绪：0=极度恐慌/割肉，0.5=中性，1=极度贪婪/梭哈
  "intent": "buy" | "sell" | "neutral",          // 买入/卖出/观望意图
  "intent_strength": 0.0-1.0                     // 意图强度
}

# 评分建议
- 0-15: 专业投资者
- 15-30: 偏专业
- 30-50: 中间派
- 50-75: 偏小白
- 75-100: 纯小白
- 同时命中 ≥3 个小白信号 → 至少 60 分
- 同时命中 ≥2 个专业信号 → 至少 -10 分

**重要**：只返回 JSON，不要任何解释或代码块标记。"""


def _call_deepseek(title: str, content: str, sector: str) -> Tuple[Dict, int]:
    """调用 DeepSeek 分析单条帖子。失败抛异常由调用方降级。"""
    client = _get_deepseek_client()
    if client is None:
        raise RuntimeError("DeepSeek 客户端不可用")

    user_prompt = f"""【板块】{sector}
【标题】{title}

【正文】
{content if content else '(无正文)'}

请以 JSON 输出你的判定："""

    t0 = time.time()
    resp = client.chat.completions.create(
        model=_DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,       # 低温度让判定更稳定
        max_tokens=600,
        response_format={"type": "json_object"},
    )
    latency = int((time.time() - t0) * 1000)

    raw = resp.choices[0].message.content.strip()
    data = json.loads(raw)
    return data, latency


def _llm_to_result(data: Dict, post: Dict, sector: str, latency_ms: int) -> AnalysisResult:
    """把 LLM 输出的 dict 转成 AnalysisResult"""
    result = AnalysisResult(
        post_id=post.get("id", ""),
        title=post.get("title", "")[:80],
        platform=post.get("platform", "unknown"),
        sector=sector,
        url=post.get("url", ""),
        content=post.get("content", "")[:300],
        analysis_mode="llm",
        llm_latency_ms=latency_ms,
    )

    # 1. 基础分数
    score = float(data.get("score", 50))
    result.newbie_score = max(0.0, min(100.0, score))

    # 2. 置信度
    conf = data.get("confidence", "medium")
    if conf not in ("high", "medium", "low"):
        conf = "medium"
    result.newbie_confidence = conf

    # 3. 等级
    level = data.get("level", "")
    valid_levels = {"纯小白", "偏小白", "中间派", "偏专业", "专业投资者", "未判定", "垃圾帖"}
    if level in valid_levels:
        result.level = level
    else:
        # 用分数反推
        s = result.newbie_score
        if s >= 75: result.level = "纯小白"
        elif s >= 50: result.level = "偏小白"
        elif s >= 30: result.level = "中间派"
        elif s >= 15: result.level = "偏专业"
        else: result.level = "专业投资者"

    # 4. 信号
    signals = data.get("signals", []) or []
    for sig in signals[:5]:
        name = sig.get("name", "")
        weight = float(sig.get("weight", 0))
        evidence = sig.get("evidence", "")
        if weight > 0:
            result.matched_newbie.append((name, evidence, weight))
        else:
            result.matched_pro.append((name, evidence, abs(weight)))

    # 5. 推理
    result.reasoning = data.get("reasoning", "")[:300]

    # 6. 情绪
    sent = float(data.get("sentiment", 0.5))
    result.sentiment_score = max(-1.0, min(1.0, sent * 2 - 1))   # [0,1] → [-1,1]

    # 7. 意图
    intent = data.get("intent", "neutral")
    if intent not in ("buy", "sell", "neutral"):
        intent = "neutral"
    result.intent = intent
    result.intent_strength = max(0.0, min(1.0, float(data.get("intent_strength", 0.0))))

    # 8. 关键信号（用于前端卡片）
    for name, evidence, weight in result.matched_newbie[:3]:
        result.key_signals.append(f"「{name}」{evidence} (权重 {weight:+.0f})")
    for name, evidence, weight in result.matched_pro[:2]:
        result.key_signals.append(f"「{name}」{evidence} (权重 -{weight:.0f})")

    return result


# ============================================================
# 关键词降级（保留旧逻辑）
# ============================================================

def _keyword_analyze(post: Dict, sector: str) -> AnalysisResult:
    """纯关键词匹配分析（无 LLM 调用，作为降级）"""
    title = post.get("title", "")
    content = post.get("content", "")
    full_text = f"{title} {content}" if content else title

    # 0. 垃圾过滤
    SPAM_PATTERNS = [
        "我是冲着金条来的",
        "金条来的，你呢",
        "领金条",
        "签到",
        "打卡",
        "广告",
    ]
    for spam in SPAM_PATTERNS:
        if spam in full_text:
            result = AnalysisResult(
                post_id=post.get("id", ""),
                title=title[:80],
                platform=post.get("platform", "unknown"),
                sector=sector,
                newbie_score=0,
                newbie_confidence="high",
                level="垃圾帖",
                reasoning=f"检测到垃圾/活动帖（命中: 「{spam}」），已过滤，不计入指数。",
                analysis_mode="keyword",
            )
            return result

    result = AnalysisResult(
        post_id=post.get("id", ""),
        title=title[:80],
        platform=post.get("platform", "unknown"),
        sector=sector,
        url=post.get("url", ""),
        content=post.get("content", "")[:300],
        analysis_mode="keyword",
    )

    # 1. 逐信号匹配
    matched_newbie = []
    matched_pro = []

    for signal in NEWBIE_SIGNALS:
        keywords = NEWBIE_KEYWORDS.get(signal.name, [])
        matched_kws = [kw for kw in keywords if kw.lower() in full_text.lower()]
        if matched_kws:
            matched_newbie.append((signal.name, signal.description, signal.weight, matched_kws))

    for signal in PRO_SIGNALS:
        keywords = PRO_KEYWORDS.get(signal.name, [])
        matched_kws = [kw for kw in keywords if kw.lower() in full_text.lower()]
        if matched_kws:
            matched_pro.append((signal.name, signal.description, signal.weight, matched_kws))

    # 2. 额外特征
    extra_score = 0
    extra_reasons = []

    if len(title) < 12 and any(kw in title for kw in ["涨", "跌", "买", "卖"]):
        extra_score += 3
        extra_reasons.append("标题极短+情绪化，典型小白特征")

    if title.endswith("吗") or title.endswith("呢") or title.endswith("？"):
        extra_score += 2
        extra_reasons.append("以问句结尾，在寻求答案")

    # 3. 计算总分
    total_newbie = sum(s[2] for s in matched_newbie) + extra_score
    total_pro = abs(sum(s[2] for s in matched_pro))

    raw_score = total_newbie - total_pro * 0.8
    result.newbie_score = max(0, min(100, raw_score * 4 + 10))

    # 4. 置信度
    total_signals = len(matched_newbie) + len(matched_pro)
    if total_signals >= 4:
        result.newbie_confidence = "high"
    elif total_signals >= 2:
        result.newbie_confidence = "medium"
    else:
        result.newbie_confidence = "low"

    # 5. 判定等级
    s = result.newbie_score
    if s >= 50:
        result.level = "纯小白"
    elif s >= 35:
        result.level = "偏小白"
    elif s >= 20:
        result.level = "中间派"
    elif s >= 10:
        result.level = "偏专业"
    else:
        result.level = "专业投资者"

    # 6. 推理文本
    result.reasoning = _generate_reasoning(
        title, matched_newbie, matched_pro, extra_reasons, result
    )

    # 7. 情绪分析
    result.sentiment_score = _analyze_sentiment(full_text)

    # 8. 买入/卖出意图
    buy_count = sum(1 for kw in BUY_KEYWORDS if kw in full_text)
    sell_count = sum(1 for kw in SELL_KEYWORDS if kw in full_text)

    if buy_count > sell_count:
        result.intent = "buy"
        result.intent_strength = min(1.0, buy_count / 5)
    elif sell_count > buy_count:
        result.intent = "sell"
        result.intent_strength = min(1.0, sell_count / 5)
    else:
        result.intent = "neutral"
        result.intent_strength = 0

    # 9. 关键信号摘要
    result.key_signals = []
    for name, desc, weight, kws in matched_newbie[:3]:
        result.key_signals.append(f"「{name}」{desc} (命中: {', '.join(kws[:2])})")
    for name, desc, weight, kws in matched_pro[:2]:
        result.key_signals.append(f"「{name}」{desc} (命中: {', '.join(kws[:2])})")

    result.matched_newbie = [(n, d, w) for n, d, w, _ in matched_newbie]
    result.matched_pro = [(n, d, w) for n, d, w, _ in matched_pro]

    return result


def _generate_reasoning(
    title: str,
    matched_newbie: List[Tuple],
    matched_pro: List[Tuple],
    extra_reasons: List[str],
    result: AnalysisResult,
) -> str:
    """生成人类可读的推理文本"""
    parts = []
    parts.append(f"帖子「{title[:40]}...」")

    if not matched_newbie and not matched_pro:
        parts.append("未命中明确的信号词，内容较短或信息不足。")
        parts.append("根据有限信息判定为中间派。")
        return " ".join(parts)

    if matched_newbie:
        signal_descs = [f"{name}({weight}分)" for name, desc, weight, kws in matched_newbie]
        parts.append(f"命中{len(matched_newbie)}个小信号: {', '.join(signal_descs)}。")

    if matched_pro:
        signal_descs = [f"{name}({weight}分)" for name, desc, weight, kws in matched_pro]
        parts.append(f"命中{len(matched_pro)}个专业信号: {', '.join(signal_descs)}。")

    if extra_reasons:
        parts.extend(extra_reasons)

    parts.append(f"综合得分{result.newbie_score}分，")
    parts.append(f"判定为「{result.level}」")
    parts.append(f"(置信度: {result.newbie_confidence})。")

    return " ".join(parts)


def _analyze_sentiment(text: str) -> float:
    """情绪分析: -1(恐慌) ~ +1(贪婪)"""
    greed_words = ["冲", "梭哈", "稳赚", "必涨", "躺赚", "满仓", "抄底", "起飞", "暴涨", "翻倍", "赚了", "盈利"]
    fear_words = ["割肉", "止损", "亏", "跌惨", "暴跌", "崩盘", "完了", "套牢", "深套", "亏了", "赔了", "大跌"]

    greed = sum(1 for w in greed_words if w in text)
    fear = sum(1 for w in fear_words if w in text)

    total = greed + fear
    if total == 0:
        return 0.0
    return round((greed - fear) / total, 2)


# ============================================================
# 公共 API
# ============================================================

def analyze_post(post: Dict, sector: str, *, use_llm: bool = True) -> AnalysisResult:
    """
    分析单条帖子。

    Args:
        post: 帖子 dict（至少包含 title，可选 content/platform/url/id）
        sector: 板块名
        use_llm: 是否尝试用 LLM（默认 True，没 key 自动降级）

    Returns:
        AnalysisResult（包含 analysis_mode 字段标明走的哪个模式）
    """
    if use_llm and llm_available():
        try:
            title = post.get("title", "")
            content = post.get("content", "")
            data, latency = _call_deepseek(title, content, sector)
            return _llm_to_result(data, post, sector, latency)
        except Exception as e:
            logger.warning(f"[LLM] 调用失败，降级到关键词模式: {e}")
            result = _keyword_analyze(post, sector)
            result.analysis_mode = "keyword_fallback"
            result.reasoning = f"⚠️ LLM 调用失败（{type(e).__name__}），已降级到关键词分析。\n\n{result.reasoning}"
            return result
    return _keyword_analyze(post, sector)


def analyze_sector(posts: List[Dict], sector: str, *, use_llm: bool = True) -> List[AnalysisResult]:
    """分析一个板块的所有帖子"""
    results = []
    for post in posts:
        result = analyze_post(post, sector, use_llm=use_llm)
        results.append(result)
    results.sort(key=lambda r: r.newbie_score, reverse=True)
    return results


def analyze_all(sector_data: Dict[str, List[Dict]], *, use_llm: bool = True) -> Dict[str, List[AnalysisResult]]:
    """分析所有板块"""
    mode = "LLM" if (use_llm and llm_available()) else "关键词"
    all_results = {}
    for sector, posts in sector_data.items():
        logger.info(f"  [{mode}] 分析 {sector}: {len(posts)} 条帖子...")
        all_results[sector] = analyze_sector(posts, sector, use_llm=use_llm)
    return all_results

"""
板块联动分析 (P2)
================
计算各板块历史情绪指数的两两相关性（Pearson），用于热力图展示。
对每个板块对，输出：
- 相关系数 r (-1 ~ +1)
- 强度标签 (strong / medium / weak)
- 方向 (positive / negative)
- 样本天数
- 一句话解读
"""
from typing import Dict, List, Tuple
import math


def _to_series(history: List[Dict]) -> List[Tuple[str, float]]:
    """把 history 转成 [(date, value), ...] 升序

    兼容两种字段命名：
    - index (来自 _sector_row_to_dict)
    - index_value (来自 SQLite 原生行)
    """
    norm = []
    for h in history:
        d = h.get("record_date") or h.get("date")
        v = h.get("index") or h.get("index_value")
        if v is None and isinstance(h.get("details"), dict):
            v = h["details"].get("newbie_score")
        if d is None or v is None:
            continue
        try:
            norm.append((d, float(v)))
        except (TypeError, ValueError):
            continue
    norm.sort(key=lambda x: x[0])
    return norm


def _align(series_a: List[Tuple[str, float]],
           series_b: List[Tuple[str, float]]) -> Tuple[List[float], List[float], int]:
    """按日期对齐两条序列，只保留共同日期"""
    map_a = dict(series_a)
    map_b = dict(series_b)
    common = sorted(set(map_a) & set(map_b))
    a = [map_a[d] for d in common]
    b = [map_b[d] for d in common]
    return a, b, len(common)


def pearson(a: List[float], b: List[float]) -> float:
    """Pearson 相关系数（手写，避免依赖 numpy）"""
    n = len(a)
    if n < 2 or len(b) != n:
        return 0.0
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n)) / n
    var_a = sum((x - mean_a) ** 2 for x in a) / n
    var_b = sum((x - mean_b) ** 2 for x in b) / n
    if var_a <= 0 or var_b <= 0:
        return 0.0
    return cov / math.sqrt(var_a * var_b)


def _strength_label(r: float) -> str:
    """相关性强度"""
    ar = abs(r)
    if ar >= 0.7:
        return "strong"
    if ar >= 0.4:
        return "medium"
    if ar >= 0.2:
        return "weak"
    return "none"


def _interpret(r: float, name_a: str, name_b: str) -> str:
    """给一段人话解读"""
    s = _strength_label(r)
    if s == "none":
        return f"{name_a} 与 {name_b} 几乎无联动"
    direction = "同步" if r > 0 else "反向"
    cn = {
        "strong": "强",
        "medium": "中等",
        "weak": "弱",
    }[s]
    if r > 0:
        return f"{name_a} 与 {name_b} **{cn}联动**（同步涨跌，r={r:+.2f}）"
    else:
        return f"{name_a} 与 {name_b} **{cn}反向**（此消彼长，r={r:+.2f}）"


def compute_correlation_matrix(sectors_history: Dict[str, List[Dict]],
                                sector_names: Dict[str, str] = None) -> Dict:
    """
    计算所有有数据板块的两两 Pearson 相关性。

    Args:
        sectors_history: {sector_key: [history_row, ...]}
        sector_names: {sector_key: 中文名}（可选）

    Returns:
        {
            "matrix": [[r_aa, r_ab, ...], ...],   # N x N 方阵
            "labels": ["nasdaq", "cpo", ...],     # 板块 key
            "names":  ["纳斯达克", "CPO", ...],    # 中文名
            "pairs":  [                            # 强联动对
                {"a": "nasdaq", "b": "cpo", "r": 0.82,
                 "strength": "strong", "direction": "positive",
                 "interpret": "...", "samples": 20},
                ...
            ],
            "sectors_used": N,   # 实际参与计算的板块数
            "min_samples": 7,    # 至少要多少天数据才纳入
        }
    """
    sector_names = sector_names or {}
    MIN_SAMPLES = 7

    # 过滤：历史 >= MIN_SAMPLES 的板块才纳入
    series = {}
    for k, hist in sectors_history.items():
        s = _to_series(hist)
        if len(s) >= MIN_SAMPLES:
            series[k] = s

    keys = list(series.keys())
    n = len(keys)
    if n == 0:
        return {
            "matrix": [],
            "labels": [],
            "names": [],
            "pairs": [],
            "sectors_used": 0,
            "min_samples": MIN_SAMPLES,
        }

    # 算 N x N 矩阵
    matrix = [[0.0] * n for _ in range(n)]
    pairs = []
    for i in range(n):
        for j in range(n):
            if i == j:
                matrix[i][j] = 1.0
                continue
            a_vals, b_vals, common = _align(series[keys[i]], series[keys[j]])
            r = pearson(a_vals, b_vals)
            matrix[i][j] = round(r, 3)
            # 只记录 i<j 的对（去重）
            if i < j:
                strength = _strength_label(r)
                direction = "positive" if r > 0 else "negative"
                name_a = sector_names.get(keys[i], keys[i])
                name_b = sector_names.get(keys[j], keys[j])
                pairs.append({
                    "a": keys[i],
                    "b": keys[j],
                    "a_name": name_a,
                    "b_name": name_b,
                    "r": round(r, 3),
                    "strength": strength,
                    "direction": direction,
                    "interpret": _interpret(r, name_a, name_b),
                    "samples": common,
                })

    # 按相关性绝对值降序
    pairs.sort(key=lambda x: abs(x["r"]), reverse=True)

    return {
        "matrix": matrix,
        "labels": keys,
        "names": [sector_names.get(k, k) for k in keys],
        "pairs": pairs,
        "sectors_used": n,
        "min_samples": MIN_SAMPLES,
    }

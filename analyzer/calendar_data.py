"""
散户情绪日历数据 (P2)
====================
把板块每日指数转成"emoji 日历"：每行一个板块，每列一天，
格子用 🔥/🌤️/❄️/⛅ 表示当天情绪等级。

等级划分（基于指数值）：
- hot  (>= 70)  → 🔥  狂热
- warm (50-70)  → 🌤️  升温
- cool (30-50)  → ⛅  平淡
- cold (< 30)   → ❄️  冷清
"""
from typing import Dict, List
from datetime import datetime, timedelta


# 等级阈值
THRESHOLDS = [
    (70, "hot",  "🔥", "狂热"),
    (50, "warm", "🌤️", "升温"),
    (30, "cool", "⛅", "平淡"),
    (0,  "cold", "❄️", "冷清"),
]


def _classify(value: float) -> Dict:
    """根据指数值分类"""
    for min_val, label, emoji, cn in THRESHOLDS:
        if value >= min_val:
            return {"level": label, "emoji": emoji, "label": cn}
    return {"level": "cold", "emoji": "❄️", "label": "冷清"}


def compute_calendar(sectors_history: Dict[str, List[Dict]],
                     sector_names: Dict[str, None] = None,
                     days: int = 30) -> Dict:
    """
    生成情绪日历数据。

    Args:
        sectors_history: {sector_key: [history_row, ...]}
        sector_names: {sector_key: 中文名}
        days: 取最近 N 天

    Returns:
        {
            "days": ["2026-06-01", "2026-06-02", ...],   # 日期升序
            "sectors": [
                {
                    "key": "nasdaq",
                    "name": "纳斯达克",
                    "cells": [
                        {"date": "2026-06-01", "value": 65.3,
                         "level": "warm", "emoji": "🌤️"},
                        ...
                    ],
                    "stats": {
                        "hot": 3, "warm": 10, "cool": 12, "cold": 5,
                        "avg": 45.6, "max": 78.2, "min": 12.0,
                    }
                },
                ...
            ],
            "totals": {
                "hot": 8, "warm": 25, ...   # 全市场每日合计
            }
        }
    """
    sector_names = sector_names or {}

    # 1. 找所有日期（升序），取最近 N 天
    all_dates = set()
    for hist in sectors_history.values():
        for h in hist:
            d = h.get("record_date") or h.get("date")
            if d:
                all_dates.add(d)
    all_dates = sorted(all_dates)
    if not all_dates:
        return {"days": [], "sectors": [], "totals": {}}

    # 取最近 N 天
    days_list = all_dates[-days:]

    # 2. 每个板块转 cells
    sectors_out = []
    totals = {"hot": 0, "warm": 0, "cool": 0, "cold": 0}

    for key, hist in sectors_history.items():
        # 索引化（按日期找值，兼容 index / index_value 两种命名）
        by_date = {}
        for h in hist:
            d = h.get("record_date") or h.get("date")
            v = h.get("index") or h.get("index_value")
            if v is None and isinstance(h.get("details"), dict):
                v = h["details"].get("newbie_score")
            if d and v is not None:
                try:
                    by_date[d] = float(v)
                except (TypeError, ValueError):
                    pass

        cells = []
        stats = {"hot": 0, "warm": 0, "cool": 0, "cold": 0,
                 "values": []}

        for d in days_list:
            v = by_date.get(d)
            if v is None:
                cells.append({"date": d, "value": None,
                              "level": "none", "emoji": "·",
                              "label": "无数据"})
            else:
                cls = _classify(v)
                cells.append({"date": d, "value": round(v, 1), **cls})
                stats[cls["level"]] += 1
                stats["values"].append(v)

        if stats["values"]:
            stats["avg"] = round(sum(stats["values"]) / len(stats["values"]), 1)
            stats["max"] = round(max(stats["values"]), 1)
            stats["min"] = round(min(stats["values"]), 1)
        else:
            stats["avg"] = stats["max"] = stats["min"] = None
        stats.pop("values", None)

        sectors_out.append({
            "key": key,
            "name": sector_names.get(key, key),
            "cells": cells,
            "stats": stats,
        })

        # 累计 totals
        for lvl in ("hot", "warm", "cool", "cold"):
            totals[lvl] += stats.get(lvl, 0)

    # 按板块名排序
    sectors_out.sort(key=lambda x: x["name"])

    return {
        "days": days_list,
        "sectors": sectors_out,
        "totals": totals,
        "days_count": len(days_list),
    }

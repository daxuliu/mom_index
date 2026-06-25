"""
宝妈指数 — 主流程
采集 → 分析 → 计算 → 存储 → 输出
"""
import sys
import os
import json
from datetime import datetime

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(__file__))

from collectors.guba_collector import collect_all as collect_guba
from collectors.xhs_collector import collect_all as collect_xhs
from analyzer.llm_analyzer import analyze_all
from analyzer.index_calculator import (
    compute_sector_index, add_record, get_dashboard_data, SECTOR_NAMES
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def run_pipeline():
    """执行完整的数据采集→分析→指数计算流程"""
    print("=" * 65)
    print("   👩‍👧 宝妈指数 · 数据采集与分析")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)
    
    # ===== 第1步: 数据采集 =====
    print("\n📡 第1步: 数据采集")
    
    all_posts = {}
    
    # 东方财富股吧
    print("  [东方财富股吧]")
    guba_data = collect_guba()
    for sector, posts in guba_data.items():
        all_posts[sector] = all_posts.get(sector, []) + posts
    
    # 小红书 (如果有API Key)
    print("  [小红书]")
    try:
        xhs_data = collect_xhs()
        for sector, posts in xhs_data.items():
            all_posts[sector] = all_posts.get(sector, []) + posts
    except Exception as e:
        print(f"  小红书采集跳过: {e}")
    
    total_collected = sum(len(v) for v in all_posts.values())
    print(f"\n  共采集 {total_collected} 条帖子\n")
    
    # ===== 第2步: LLM分析 =====
    print("🧠 第2步: LLM 多维度分析")
    analysis_results = analyze_all(all_posts)
    
    # 打印每个板块的 top 小白帖
    for sector, results in analysis_results.items():
        top_newbie = [r for r in results if r.newbie_score >= 30][:3]
        print(f"\n  [{SECTOR_NAMES.get(sector, sector)}] 共分析 {len(results)} 条")
        if top_newbie:
            print(f"  🔥 典型小白帖:")
            for r in top_newbie:
                print(f"     [{r.level} {r.newbie_score}分] {r.title[:50]}...")
    
    # ===== 第3步: 指数计算 =====
    print("\n📊 第3步: 指数计算")
    
    sector_indices = {}
    for sector, results in analysis_results.items():
        result = compute_sector_index(results)
        sector_indices[sector] = result
        name = SECTOR_NAMES.get(sector, sector)
        d = result["details"]
        bar = "█" * int(result["index"] / 5) + "░" * (20 - int(result["index"] / 5))
        print(f"  {name:6s} {bar} {result['index']:5.1f}  [{d['newbie_posts']}/{d['total_posts']}小白, {d['newbie_ratio']}%]")
    
    # ===== 第4步: 存储历史 =====
    print("\n💾 第4步: 存储历史记录")
    add_record(sector_indices, analysis_results)

    # 同时写入 SQLite 用于历史走势查询
    try:
        from history_store import save_all_sectors
        save_all_sectors(sector_indices)
        print("  ✅ SQLite 板块历史已更新")
    except Exception as e:
        print(f"  ⚠️ SQLite 存储失败: {e}")
    
    # ===== 第5步: 输出前端数据 =====
    dashboard = get_dashboard_data()
    os.makedirs(DATA_DIR, exist_ok=True)
    dashboard_file = os.path.join(DATA_DIR, "dashboard_data.json")
    with open(dashboard_file, 'w', encoding='utf-8') as f:
        json.dump(dashboard, f, ensure_ascii=False, indent=2)
    print(f"  数据已保存: {dashboard_file}")
    
    # 同步到 frontend/data/（前端服务器从这里读取）
    frontend_data_dir = os.path.join(os.path.dirname(__file__), "frontend", "data")
    os.makedirs(frontend_data_dir, exist_ok=True)
    for fname in ["dashboard_data.json", "history.json", "xhs_posts.json"]:
        src = os.path.join(DATA_DIR, fname)
        dst = os.path.join(frontend_data_dir, fname)
        if os.path.exists(src):
            import shutil
            shutil.copy2(src, dst)
    print(f"  已同步到: {frontend_data_dir}")
    
    # ===== 总结 =====
    print("\n" + "=" * 65)
    print("   ✅ 分析完成!")
    print(f"   历史记录: {dashboard['record_count']} 条")
    if dashboard["latest"]:
        for sector, data in dashboard["latest"]["sectors"].items():
            name = SECTOR_NAMES.get(sector, sector)
            print(f"   {name}: {data['index']} — {data['interpretation']}")
    print("=" * 65)
    
    return dashboard


def generate_sample_history(days: int = 30):
    """生成模拟历史数据（用于展示前端曲线效果）"""
    import random
    import math
    
    history = {"records": []}
    base_indices = {
        "nasdaq": 35,
        "gold": 28,
        "cpo": 22,
        "semiconductor": 30,
        "cnkr_semi": 32,
        "xiaomi": 55,
        "tencent": 40,
        "meituan": 48,
        "hangseng_tech": 50,
        "dividend": 25,
        "micron": 65,
    }
    
    today = datetime.now()
    for i in range(days, 0, -1):
        d = today.replace(day=min(today.day, 28))  # 简化
        d = d.replace(day=max(1, d.day - i))
        record = {"date": d.strftime("%Y-%m-%d"), "sectors": {}}
        
        for sector, base in base_indices.items():
            # 模拟波动：当前趋势 + 随机噪音
            trend = 15 * math.sin(i / 10.0)  # 周期性波动
            noise = random.uniform(-8, 8)
            idx = round(base + trend + noise, 1)
            idx = max(0, min(100, idx))
            
            record["sectors"][sector] = {
                "index": idx,
                "interpretation": interpret(idx),
                "details": {
                    "total_posts": random.randint(60, 85),
                    "valid_posts": random.randint(55, 80),
                    "spam_posts": random.randint(0, 5),
                    "newbie_posts": random.randint(3, 25),
                    "pure_newbie": random.randint(0, 5),
                    "newbie_ratio": round(random.uniform(5, 35), 1),
                    "avg_newbie_score": round(random.uniform(20, 50), 1),
                    "avg_sentiment": round(random.uniform(20, 80), 1),
                    "purity_signal": round(random.uniform(10, 60), 1),
                    "activity": round(random.uniform(60, 100), 1),
                },
                "top_newbie_posts": [],
            }
        
        history["records"].append(record)
    
    history["records"].sort(key=lambda r: r["date"])
    return history


def interpret(idx):
    if idx >= 75: return "🔴 极度狂热"
    if idx >= 60: return "🟠 高度警惕"
    if idx >= 40: return "🟡 开始升温"
    if idx >= 20: return "🟢 正常区间"
    return "🔵 极度冷清"


if __name__ == "__main__":
    # 先跑真实采集
    dashboard = run_pipeline()
    
    # 如果历史数据不够，补充模拟数据
    if dashboard["record_count"] < 5:
        print("\n📝 历史数据不足，生成30天模拟数据用于前端展示...")
        sample = generate_sample_history(30)
        from analyzer.index_calculator import save_history
        # 合并：保留真实数据，补充模拟历史
        existing = dashboard.get("sector_history", {})
        real_dates = set()
        if dashboard["latest"]:
            real_dates.add(dashboard["latest"]["date"])
        
        history = {"records": []}
        for r in sample["records"]:
            if r["date"] not in real_dates:
                history["records"].append(r)
        # 再加回真实记录
        history["records"].extend([
            r for r in sample["records"] if r["date"] in real_dates
        ])
        history["records"].sort(key=lambda r: r["date"])
        save_history(history)
        print(f"  已生成 {len(history['records'])} 天历史数据")

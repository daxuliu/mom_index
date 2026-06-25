"""
小红书 Playwright 采集器 — 反检测升级版
隐身指纹 + 人类延迟 + Chrome profile 复用登录态
"""
import asyncio
import json
import os
import re
from datetime import datetime
from typing import List, Dict

from playwright.async_api import async_playwright
from .anti_detection import get_anti_detection

_ad = get_anti_detection()

SEARCH_KEYWORDS = {
    "nasdaq":     ["美股怎么买", "纳斯达克新手", "纳指还能买吗"],
    "gold":       ["黄金怎么买", "黄金亏了", "黄金还能涨吗"],
    "cpo":        ["CPO是什么", "CPO还能买吗"],
    "semiconductor": ["芯片还能上车吗", "半导体新手"],
}

OUTPUT_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "xhs_posts.json")


async def search_xhs(keyword: str, limit: int = 10) -> List[Dict]:
    """搜索小红书 — 隐身模式 + 延迟"""
    posts = []

    async with async_playwright() as p:
        # 使用真实 Chrome 的持久化 profile
        user_data = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
        tmp_profile = os.path.expandvars(r"%TEMP%\pw_xhs_profile")

        # 复制 profile 避免锁冲突
        if os.path.exists(tmp_profile):
            import shutil
            shutil.rmtree(tmp_profile, ignore_errors=True)
        os.makedirs(os.path.join(tmp_profile, "Default"), exist_ok=True)

        browser = await p.chromium.launch_persistent_context(
            user_data_dir=tmp_profile,
            headless=True,
            args=_ad.get_playwright_launch_args(),
            viewport={"width": 1366, "height": 768},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            device_scale_factor=1,
        )

        # 注入隐身脚本
        for script in _ad.get_stealth_scripts():
            await browser.add_init_script(script)

        page = await browser.new_page()

        try:
            search_url = f"https://www.xiaohongshu.com/search_result?keyword={keyword}&type=51"
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

            # 等页面渲染
            await asyncio.sleep(3)

            html = await page.content()

            # 检查是否被风控
            if "安全限制" in html or "300012" in html:
                print(f"    ⚠️ XHS 风控拦截: {keyword}")
                return posts

            if "登录" in html[:2000] and "手机号" in html[:2000]:
                print(f"    ⚠️ XHS 需要登录: {keyword}")
                return posts

            # 提取笔记
            note_ids = re.findall(r'"note_id":"([^"]+)"', html)
            titles = re.findall(r'"display_title":"([^"]+)"', html)

            for i, title in enumerate(titles[:limit]):
                note_id = note_ids[i] if i < len(note_ids) else ""
                posts.append({
                    "id": note_id or f"xhs_{hash(title)}",
                    "title": title[:100],
                    "url": f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else "",
                    "platform": "xiaohongshu",
                    "keyword": keyword,
                    "collected_at": datetime.now().isoformat(),
                })

            print(f"    '{keyword}' → {len(posts)}条")

        except Exception as e:
            print(f"    ❌ {keyword}: {e}")

        finally:
            await browser.close()
            import shutil
            shutil.rmtree(tmp_profile, ignore_errors=True)

    return posts


def collect_all() -> Dict[str, List[Dict]]:
    """采集所有板块 — 每个搜索之间加人类延迟"""
    result = {}
    for sector_key, keywords in SEARCH_KEYWORDS.items():
        all_notes = []
        for kw in keywords:
            try:
                notes = asyncio.run(search_xhs(kw, limit=8))
                all_notes.extend(notes)
                # 搜索之间模拟人类延迟（关键：防止验证码）
                _ad.sleep_like_human("search")
            except Exception as e:
                print(f"  [XHS-{sector_key}] '{kw}' 失败: {e}")

        # 去重
        seen = set()
        unique = []
        for n in all_notes:
            if n["title"] not in seen:
                seen.add(n["title"])
                unique.append(n)
        result[sector_key] = unique

    return result


if __name__ == "__main__":
    data = collect_all()
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    total = sum(len(v) for v in data.values())
    print(f"\n共采集 {total} 条 XHS 帖子 → {OUTPUT_FILE}")

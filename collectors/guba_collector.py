"""
东方财富股吧采集器 — 反检测升级版
支持板块: 纳斯达克ETF, 黄金ETF, 通信ETF(CPO), 半导体ETF
"""
import re
import html as html_mod
import requests
import time
from datetime import datetime
from typing import List, Dict, Optional

from .anti_detection import get_anti_detection

SECTORS = {
    "nasdaq":     {"name": "纳斯达克", "code": "of159941", "etf": "513100"},
    "gold":       {"name": "黄金",     "code": "of518880", "etf": "518880"},
    "cpo":        {"name": "CPO通信",  "code": "of515880", "etf": "515880"},
    "semiconductor": {"name": "半导体", "code": "of512480", "etf": "512480"},
    "cnkr_semi":  {"name": "中韩半导体", "code": "sh513310", "etf": "513310"},
    "xiaomi":     {"name": "小米集团", "code": "hk01810", "etf": "01810"},
    "tencent":    {"name": "腾讯控股", "code": "hk00700", "etf": "00700"},
    "meituan":    {"name": "美团",     "code": "hk03690", "etf": "03690"},
    "hangseng_tech": {"name": "恒生科技", "code": "sh513130", "etf": "513130"},
    "dividend":    {"name": "中证红利", "code": "of000922", "etf": "000922"},
    "micron":      {"name": "美光科技", "code": "usMU", "etf": "MU"},
}

# PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}  # 临时禁用
PROXY = {}

_ad = get_anti_detection()


def fetch_board(code: str) -> str:
    """获取股吧页面HTML — 使用反检测请求头"""
    url = f"https://guba.eastmoney.com/list,{code}.html"
    headers = _ad.get_common_headers(referer="https://guba.eastmoney.com")
    resp = requests.get(url, headers=headers, proxies=PROXY, timeout=15)
    resp.encoding = 'utf-8'
    return resp.text


def parse_posts(html_content: str) -> List[Dict]:
    """解析帖子列表 — 兼容东财新版(标题在 a 标签内)和旧版(标题在 title 属性)"""
    # 兼容两种格式: 优先匹配 title 属性，没有则取 a 标签文本
    title_with_attr = re.compile(
        r'<a[^>]*href="(/news,[^"]*)"[^>]*title="([^"]*)"[^>]*>',
        re.DOTALL
    )
    title_in_tag = re.compile(
        r'<a[^>]*href="(/news,[^"]*)"[^>]*>([^<]{4,})</a>',
        re.DOTALL
    )

    titles = []
    seen_urls = set()
    # 先匹配 title 属性
    for url, title in title_with_attr.findall(html_content):
        if url not in seen_urls:
            titles.append((url, title))
            seen_urls.add(url)
    # 再补充 a 标签文本（兼容新版 HTML）
    for url, title in title_in_tag.findall(html_content):
        if url not in seen_urls:
            titles.append((url, title))
            seen_urls.add(url)

    # 阅读/回复/作者/日期 (容错: 老版字段在新版可能被 class 名微调)
    read_pattern = re.compile(r'<cite[^>]*class="[^"]*l1[^"]*"[^>]*>(.*?)</cite>', re.DOTALL)
    reply_pattern = re.compile(r'<cite[^>]*class="[^"]*l2[^"]*"[^>]*>(.*?)</cite>', re.DOTALL)
    author_pattern = re.compile(r'<cite[^>]*class="[^"]*l4[^"]*"[^>]*>.*?<a[^>]*>(.*?)</a>', re.DOTALL)
    date_pattern = re.compile(r'<cite[^>]*class="[^"]*l5[^"]*"[^>]*>(.*?)</cite>', re.DOTALL)

    reads = read_pattern.findall(html_content)
    replies = reply_pattern.findall(html_content)
    authors = author_pattern.findall(html_content)
    dates = date_pattern.findall(html_content)

    posts = []
    for i, (url, title) in enumerate(titles):
        title = html_mod.unescape(title.strip())
        if not title or title == '点击开始搜索' or len(title) < 4:
            continue
        posts.append({
            "id": f"guba_{url.split(',')[-1].replace('.html','')}",
            "title": title,
            "url": f"https://guba.eastmoney.com{url}",
            "platform": "guba",
            "author": authors[i].strip() if i < len(authors) else "未知",
            "reads": reads[i].strip() if i < len(reads) else "0",
            "replies": replies[i].strip() if i < len(replies) else "0",
            "date": dates[i].strip() if i < len(dates) else "未知",
            "collected_at": datetime.now().isoformat(),
        })
    return posts


def collect_all() -> Dict[str, List[Dict]]:
    """采集所有板块 — 带人类延迟防触发风控"""
    result = {}
    for sector_key, cfg in SECTORS.items():
        try:
            html = fetch_board(cfg["code"])
            posts = parse_posts(html)
            result[sector_key] = posts
            print(f"  [{cfg['name']}] 采集到 {len(posts)} 条帖子")
            # 板块之间加延迟
            _ad.sleep_like_human("scroll")
        except Exception as e:
            print(f"  [{cfg['name']}] 采集失败: {e}")
            result[sector_key] = []
    return result


if __name__ == "__main__":
    data = collect_all()
    for k, v in data.items():
        print(f"{k}: {len(v)} posts")

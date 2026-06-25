"""
Vercel Serverless 入口
- /api/query     智能识别代码 + 在线抓取 + LLM 分析 + 指数计算
- /api/etf-nav  跨境 ETF T-1 净值（东方财富）
- /api/health   健康检查
- 其他路径       → 静态文件 (public/)
"""
import os
import sys
import re
import time
import json
import urllib.request
import datetime

# 把项目根目录加入 path, 让 api_server 等模块可被导入
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from flask import Flask, request, jsonify, Response  # noqa: E402

# 复用本地 api_server 的 query_stock
from api_server import query_stock  # noqa: E402

app = Flask(__name__, static_folder=None)

# ==================== ETF T-1 净值缓存 ====================
__etf_nav_cache: dict = {}
__ETF_NAV_TTL = 4 * 3600  # 4 小时

# 跨境 ETF 完整列表（20 只，纳指12+标普4+日经4）
ETF_LIST = [
    ('513100', 'sh', 'NASDAQ100', '纳指ETF国泰'),
    ('513300', 'sh', 'NASDAQ100', '纳指ETF华夏'),
    ('513110', 'sh', 'NASDAQ100', '纳指ETF华泰柏瑞'),
    ('513390', 'sh', 'NASDAQ100', '纳指ETF博时'),
    ('513870', 'sh', 'NASDAQ100', '纳指ETF富国'),
    ('159941', 'sz', 'NASDAQ100', '纳指ETF广发'),
    ('159632', 'sz', 'NASDAQ100', '纳指ETF华安'),
    ('159660', 'sz', 'NASDAQ100', '纳指ETF汇添富'),
    ('159659', 'sz', 'NASDAQ100', '纳指ETF招商'),
    ('159696', 'sz', 'NASDAQ100', '纳指ETF易方达'),
    ('159501', 'sz', 'NASDAQ100', '纳指ETF嘉实'),
    ('159513', 'sz', 'NASDAQ100', '纳指ETF大成'),
    ('513500', 'sh', 'SP500', '标普500ETF博时'),
    ('513650', 'sh', 'SP500', '标普500ETF南方'),
    ('159612', 'sz', 'SP500', '标普500ETF国泰'),
    ('159655', 'sz', 'SP500', '标普500ETF华夏'),
    ('513520', 'sh', 'NIKKEI225', '日经225ETF华夏'),
    ('513000', 'sh', 'NIKKEI225', '日经225ETF易方达'),
    ('513880', 'sh', 'NIKKEI225', '日经225ETF华安'),
    ('159866', 'sz', 'NIKKEI225', '日经225ETF工银'),
]


def fetch_t1_nav(code: str) -> dict:
    """从东方财富 pingzhongdata 接口获取 T-1 净值"""
    if code in __etf_nav_cache:
        cached = __etf_nav_cache[code]
        if time.time() - cached['_ts'] < __ETF_NAV_TTL:
            return cached
    try:
        url = f"http://fund.eastmoney.com/pingzhongdata/{code}.js"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "http://fund.eastmoney.com/"
        })
        resp = urllib.request.urlopen(req, timeout=10)
        text = resp.read().decode('utf-8')
        matches = re.findall(
            r'\{"x":(\d+),"y":([\d.]+),"equityReturn":[\d.]+,"unitMoney":""\}',
            text
        )
        if not matches:
            return {"nav": None, "date": None, "source": "eastmoney", "error": "no data"}
        latest_ts_ms = int(matches[-1][0])
        latest_nav = float(matches[-1][1])
        date_str = datetime.datetime.fromtimestamp(latest_ts_ms / 1000).strftime("%Y-%m-%d")
        result = {
            "nav": latest_nav,
            "date": date_str,
            "source": "eastmoney",
            "_ts": time.time(),
        }
        __etf_nav_cache[code] = result
        return result
    except Exception as e:
        return {"nav": None, "date": None, "source": "eastmoney", "error": str(e)}


# ==================== API 路由 ====================
@app.route("/api/query")
def api_query():
    code = request.args.get("code", "").strip()
    if not code:
        return jsonify({"error": "缺少 code 参数"}), 400
    try:
        return jsonify(query_stock(code))
    except Exception as e:
        return jsonify({"error": f"服务器错误: {str(e)[:200]}"}), 500


@app.route("/api/etf-nav")
def api_etf_nav():
    """跨境 ETF T-1 净值（用于溢价监控）"""
    single_code = request.args.get("code", "").strip()
    try:
        if single_code:
            nav = fetch_t1_nav(single_code)
            return jsonify({"code": single_code, **nav})
        else:
            navs = {}
            for code, _, idx, name in ETF_LIST:
                n = fetch_t1_nav(code)
                navs[code] = {
                    "nav": n.get("nav"),
                    "date": n.get("date"),
                    "name": name,
                    "index": idx,
                }
            return jsonify({"navs": navs, "count": len(navs)})
    except Exception as e:
        return jsonify({"error": f"服务器错误: {str(e)[:200]}"}), 500


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "platform": "vercel"})


# ==================== 静态文件 (public/) ====================
STATIC_DIR = os.path.join(ROOT, "public")


def _serve_static(rel_path: str):
    """通用静态文件读取"""
    # 防目录穿越
    if ".." in rel_path or rel_path.startswith("/"):
        return "Forbidden", 403
    full = os.path.join(STATIC_DIR, rel_path)
    if not os.path.exists(full) or not os.path.isfile(full):
        return "Not Found", 404
    # 简单 Content-Type
    ct = "text/plain; charset=utf-8"
    if rel_path.endswith(".html"):
        ct = "text/html; charset=utf-8"
    elif rel_path.endswith(".css"):
        ct = "text/css; charset=utf-8"
    elif rel_path.endswith(".js"):
        ct = "application/javascript; charset=utf-8"
    elif rel_path.endswith(".json"):
        ct = "application/json; charset=utf-8"
    elif rel_path.endswith(".svg"):
        ct = "image/svg+xml"
    elif rel_path.endswith(".png"):
        ct = "image/png"
    elif rel_path.endswith(".ico"):
        ct = "image/x-icon"
    with open(full, "rb") as f:
        body = f.read()
    # 加缓存: HTML 不缓存, 静态资源长缓存
    headers = {"Content-Type": ct}
    if not rel_path.endswith(".html"):
        headers["Cache-Control"] = "public, max-age=3600"
    return Response(body, mimetype=ct)


@app.route("/")
def root():
    return _serve_static("query.html")


@app.route("/query.html")
def query_page():
    return _serve_static("query.html")


@app.route("/dashboard.html")
def dashboard_page():
    return _serve_static("dashboard.html")


@app.route("/data/<path:filename>")
def data_files(filename):
    return _serve_static(f"data/{filename}")


# 兜底: 所有其他路径也走静态
@app.route("/<path:path>")
def catch_all(path):
    return _serve_static(path)
"""
Vercel Serverless 入口
- /api/query    智能识别代码 + 在线抓取 + LLM 分析 + 指数计算
- /api/health   健康检查
- 其他路径       → 静态文件 (public/)
"""
import os
import sys

# 把项目根目录加入 path, 让 api_server 等模块可被导入
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from flask import Flask, request, jsonify, Response  # noqa: E402

# 复用本地 api_server 的 query_stock
from api_server import query_stock  # noqa: E402

app = Flask(__name__, static_folder=None)

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
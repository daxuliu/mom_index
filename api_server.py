"""
动态股票查询 API 服务
- 监听 8766 端口
- GET  /api/query?code=XXX   智能识别代码 → 抓股吧 → LLM分析 → 算指数
- GET  /                      返回 query.html
- GET  /static/<file>         静态文件
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# 让项目根目录可被 import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collectors.guba_collector import fetch_board, parse_posts
from analyzer.llm_analyzer import analyze_post
from analyzer.index_calculator import compute_sector_index


# ==================== 智能识别代码 ====================
def identify_code(code: str) -> dict:
    """
    智能识别股票代码 → 东财股吧代码
    支持: sh/sz/of/hk/us 前缀、纯数字、纯字母
    """
    code = code.strip().upper()
    if not code:
        return None

    # 已带前缀
    if code.startswith(("SH", "SZ", "OF", "HK", "US")):
        prefix = code[:2].lower()
        rest = code[2:]
        market_map = {
            "sh": "A 股沪市",
            "sz": "A 股深市",
            "of": "A 股 (中证指数)",
            "hk": "港股",
            "us": "美股",
        }
        # 港股 / 美股要保证数字/字母部分大写
        if prefix in ("hk", "us"):
            rest = rest.upper()
        return {
            "guba_code": f"{prefix}{rest}",
            "display": rest,
            "market": market_map[prefix],
        }

    # 6 位纯数字 → A 股 (默认 sh，可试 sz)
    if code.isdigit() and len(code) == 6:
        return {"guba_code": f"sh{code}", "display": code, "market": "A 股"}

    # 5 位纯数字 → 港股
    if code.isdigit() and len(code) == 5:
        return {"guba_code": f"hk{code}", "display": code, "market": "港股"}

    # 1-5 位纯字母 → 美股
    if code.isalpha() and 1 <= len(code) <= 5:
        return {"guba_code": f"us{code}", "display": code, "market": "美股"}

    # 含 sh/sz 前缀但大小写不一致
    if len(code) > 2 and code[:2].lower() in ("sh", "sz", "of", "hk", "us"):
        return identify_code(code[:2].lower() + code[2:])

    # 兜底
    return {"guba_code": code, "display": code, "market": "未知"}


# ==================== 查询主流程 ====================
def query_stock(raw_code: str) -> dict:
    info = identify_code(raw_code)
    if not info:
        return {"error": "请输入股票代码"}

    guba_code = info["guba_code"]
    result = {
        "input": raw_code,
        "code_info": info,
        "fetch_ok": False,
        "fetch_error": None,
        "post_count": 0,
        "index_data": None,
        "sample_posts": [],
        "duration_ms": 0,
    }

    import time
    t0 = time.time()

    # 1. 抓数据
    try:
        html = fetch_board(guba_code)
        posts = parse_posts(html)
        result["fetch_ok"] = True
        result["post_count"] = len(posts)
    except Exception as e:
        result["fetch_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        posts = []

    # 2. LLM 分析
    if posts:
        analysis_results = []
        for post in posts[:30]:  # 限制最多 30 条, 避免超时
            try:
                r = analyze_post(post, guba_code)
                analysis_results.append(r)
            except Exception as e:
                # 单条失败不影响整体
                continue
        # 3. 算指数
        if analysis_results:
            idx = compute_sector_index(analysis_results)
            result["index_data"] = idx
        # 4. 保存 sample posts 用于详情弹窗
        result["sample_posts"] = [
            {
                "post_id": r.post_id,
                "title": r.title,
                "platform": r.platform,
                "url": r.url,
                "content": r.content,
                "score": r.newbie_score,
                "level": r.level,
                "confidence": r.newbie_confidence,
                "reasoning": r.reasoning[:200],
                "sentiment": r.sentiment_score,
                "intent": r.intent,
                "intent_strength": r.intent_strength,
                "intent_label": {"buy": "🟢 买入", "sell": "🔴 卖出", "neutral": "⚪ 观望"}.get(r.intent, ""),
                "key_signals": r.key_signals[:3],
            }
            for r in sorted(analysis_results, key=lambda x: x.newbie_score, reverse=True)[:10]
        ]

    result["duration_ms"] = int((time.time() - t0) * 1000)
    return result


# ==================== HTTP 处理器 ====================
class QueryHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # 简化日志
        print(f"[{self.log_date_time_string()}] {args[0]}")

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type="text/html"):
        try:
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # 根路径 → query.html
        if path in ("/", "/index.html", "/query.html"):
            self._send_file(os.path.join(os.path.dirname(__file__), "frontend", "query.html"))
            return

        # API
        if path == "/api/query":
            code = params.get("code", [""])[0].strip()
            if not code:
                self._send_json({"error": "缺少 code 参数"}, 400)
                return
            try:
                data = query_stock(code)
                self._send_json(data)
            except Exception as e:
                self._send_json({"error": f"服务器错误: {e}"}, 500)
            return

        # 健康检查
        if path == "/api/health":
            self._send_json({"status": "ok", "version": "1.0"})
            return

        # 静态文件 (含 data/ 目录)
        if (path.startswith("/static/") or path.startswith("/data/")
            or path.endswith((".js", ".css", ".png", ".ico", ".json", ".html"))):
            rel = path.lstrip("/")
            # 防止目录穿越
            if ".." in rel:
                self.send_response(400)
                self.end_headers()
                return
            full = os.path.join(os.path.dirname(__file__), "frontend", rel)
            if os.path.exists(full):
                ct = "text/plain"
                if rel.endswith(".css"): ct = "text/css"
                elif rel.endswith(".js"): ct = "application/javascript"
                elif rel.endswith(".json"): ct = "application/json"
                elif rel.endswith(".html"): ct = "text/html"
                self._send_file(full, ct)
                return

        # 404
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"404 Not Found")


class ReuseHTTPServer(HTTPServer):
    allow_reuse_address = True

def main():
    port = int(os.environ.get("API_PORT", 8766))
    server = ReuseHTTPServer(("0.0.0.0", port), QueryHandler)
    print(f"🚀 动态查询 API 服务启动: http://localhost:{port}")
    print(f"   GET /api/query?code=XXX  →  智能识别+实时采集+LLM分析+指数计算")
    print(f"   GET /                    →  查询页面 query.html")
    print(f"   GET /dashboard.html      →  主看板 (代理)")
    print(f"\n   示例:")
    print(f"     curl http://localhost:{port}/api/query?code=513310")
    print(f"     curl http://localhost:{port}/api/query?code=usMU")
    print(f"     curl http://localhost:{port}/api/query?code=MU")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 停止服务")
        server.shutdown()


if __name__ == "__main__":
    main()
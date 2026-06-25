"""
动态股票查询 API 服务
- 监听 8766 端口
- GET  /api/query?code=XXX   智能识别代码 → 抓股吧 → LLM分析 → 算指数
- GET  /                      返回 query.html
- GET  /static/<file>         静态文件
"""
import json
import os
import re
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# 让项目根目录可被 import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collectors.guba_collector import fetch_board, parse_posts
from analyzer.llm_analyzer import analyze_post
from analyzer.index_calculator import compute_sector_index


# ==================== 常用股票名称→代码映射 ====================
STOCK_NAME_MAP = {
    # A 股 ETF / 指数
    "纳斯达克": "513100", "纳指": "513100", "纳基": "513100",
    "黄金": "518880", "黄金ETF": "518880",
    "CPO": "515880", "CPO通信": "515880",
    "半导体": "512480", "芯片": "512480",
    "中韩半导体": "513310", "中韩芯片": "513310",
    "恒生科技": "513130", "科网": "513130",
    "红利": "000922", "中证红利": "000922",
    "沪深300": "510300", "300ETF": "510300",
    "中证500": "510500", "500ETF": "510500",
    "科创50": "588000",
    "医疗": "512170", "医疗ETF": "512170",
    "白酒": "512690", "酒ETF": "512690",
    "光伏": "515790", "光伏ETF": "515790",
    "新能源": "516160", "新能源车": "515030",
    "医药": "512010", "医药ETF": "512010",
    "军工": "512660", "军工ETF": "512660",
    "银行": "512800", "银行ETF": "512800",
    "券商": "512000", "券商ETF": "512000",
    "地产": "512200", "房地产": "512200",
    "农业": "515790", "农业ETF": "515790",
    "钢铁": "515210", "钢铁ETF": "515210",
    "有色": "512400", "有色金属": "512400",
    "煤炭": "515220", "煤炭ETF": "515220",
    "消费": "512880", "消费ETF": "512880",
    "互联网": "515000", "中概互联": "513050",
    "原油": "162411", "石油": "162411",
    
    # A 股个股
    "贵州茅台": "600519", "茅台": "600519",
    "五粮液": "000858", "五粮": "000858",
    "招商银行": "600036", "招行": "600036",
    "中国平安": "601318", "平安": "601318",
    "比亚迪": "002594", "比嫂": "002594",
    "宁德时代": "300750", "宁王": "300750", "宁德": "300750",
    "中国中免": "601888", "中免": "601888",
    "伊利股份": "600887", "伊利": "600887",
    "格力电器": "000651", "格力": "000651",
    "美的集团": "000333", "美的": "000333",
    "海康威视": "002415", "海康": "002415",
    "恒瑞医药": "600276", "恒瑞": "600276",
    "药明康德": "603259", "药明": "603259",
    "立讯精密": "002475", "立讯": "002475",
    "三一重工": "600031", "三一": "600031",
    "中国石化": "600028", "中石化": "600028",
    "中国石油": "601857", "中石油": "601857",
    "工商银行": "601398", "工行": "601398",
    "建设银行": "601939", "建行": "601939",
    "中国银行": "601988", "中行": "601988",
    "农业银行": "601288", "农行": "601288",
    "中国银行": "601988",
    "中信证券": "600030", "中信": "600030",
    "东方财富": "300059", "东财": "300059",
    "同花顺": "300033",
    "分众传媒": "002027", "分众": "002027",
    "永辉超市": "601933", "永辉": "601933",
    "牧原股份": "002714", "牧原": "002714",
    "温氏股份": "300498", "温氏": "300498",
    "湖南白银": "000906", "湖南黄金": "000906", "白银": "000906",
    "白银有色": "601212", "白银有色股份": "601212",
    "豫光金铅": "600531", "豫光": "600531",
    "山东黄金": "600547", "山金": "600547",
    "中金黄金": "600489", "中金": "600489",
    "紫金矿业": "601899", "紫金": "601899",
    "洛阳钼业": "603993", "洛钼": "603993",
    "江西铜业": "600362", "江铜": "600362",
    "铜陵有色": "000630", "铜陵": "000630",
    "中国铝业": "601600", "中铝": "601600",
    "北方稀土": "600111", "北稀": "600111",
    "盛和资源": "600392", "盛和": "600392",
    "中科曙光": "603019", "曙光": "603019",
    "寒武纪": "688256",
    "中芯国际": "688981", "中芯": "688981",
    "韦尔股份": "603501", "韦尔": "603501",
    "北方华创": "002371", "北创": "002371",
    "中微公司": "688012", "中微": "688012",
    "金山办公": "688111", "金山": "688111",
    "用友网络": "600588", "用友": "600588",
    "广联达": "002410", "广联": "002410",
    "宝钢股份": "600019", "宝钢": "600019",
    "鞍钢股份": "000898", "鞍钢": "000898",
    "华菱钢铁": "000932", "华菱": "000932",
    "中信特钢": "000708", "中信特钢": "000708",
    "恒力石化": "600346", "恒力": "600346",
    "荣盛石化": "002493", "荣盛": "002493",
    "万华化学": "600309", "万华": "600309",
    "中国重汽": "000951", "重汽": "000951",
    "潍柴动力": "000338", "潍柴": "000338",
    "三一重工": "600031",
    "徐工机械": "000425", "徐工": "000425",
    "中联重科": "000157", "中联": "000157",
    "海尔智家": "600690", "海尔": "600690",
    "海信视像": "600060", "海信": "600060",
    "TCL科技": "000100", "TCL": "000100",
    "京东方": "000725", "BOE": "000725",
    "立讯精密": "002475",
    "歌尔股份": "002241", "歌尔": "002241",
    "蓝思科技": "300433", "蓝思": "300433",
    "领益智造": "002600", "领益": "002600",
    "工业富联": "601138", "富士康": "601138", "工富联": "601138",
    "立讯精密": "002475",
    
    # 港股
    "腾讯控股": "00700", "腾讯": "00700",
    "阿里巴巴": "09988", "阿里": "09988",
    "美团": "03690", "美团网": "03690",
    "小米集团": "01810", "小米": "01810",
    "京东集团": "09618", "京东": "09618",
    "百度集团": "09888", "百度": "09888",
    "网易": "09999",
    "字节跳动": "未上市",
    "拼多多": "PDD",
    "中国移动": "00941", "中移动": "00941",
    "中国联通": "00762", "联通": "00762",
    "中国电信": "00728", "中电信": "00728",
    "中国银行(港)": "03988",
    "汇丰控股": "00005", "汇丰": "00005",
    "香港交易所": "00388", "港交所": "00388",
    "友邦保险": "01299", "友邦": "01299",
    "中国人寿": "02628", "人寿": "02628",
    "中国平安(港)": "02318",
    "碧桂园": "02007",
    "融创中国": "01918",
    "中国恒大": "03333", "恒大": "03333",
    "比亚迪电子": "00285", "比电": "00285",
    "舜宇光学": "02382", "舜宇": "02382",
    "中芯国际(港)": "00981", "中芯港": "00981",
    "华虹半导体": "01347", "华虹": "01347",
    "药明康德(港)": "02359",
    "康龙化成": "03759", "康龙": "03759",
    "恒瑞医药(港)": "06002",
    "海底捞": "06862",
    "九毛九": "09992",
    "泡泡玛特": "09992", "泡泡": "09992",
    
    # 美股
    "苹果": "AAPL", "Apple": "AAPL",
    "微软": "MSFT", "Microsoft": "MSFT",
    "谷歌": "GOOGL", "Google": "GOOGL", "Alphabet": "GOOGL",
    "亚马逊": "AMZN", "Amazon": "AMZN",
    "Meta": "META", "脸书": "META", "Facebook": "META",
    "奈飞": "NFLX", "Netflix": "NFLX",
    "特斯拉": "TSLA", "Tesla": "TSLA",
    "英伟达": "NVDA", "Nvidia": "NVDA", "黄仁勋": "NVDA",
    "美光": "MU", "Micron": "MU",
    "英特尔": "INTC", "Intel": "INTC",
    "超威半导体": "AMD", "Advanced Micro Devices": "AMD",
    "博通": "AVGO", "Broadcom": "AVGO",
    "台积电(ADR)": "TSM", "TSMC": "TSM",
    "阿斯麦": "ASML",
    "应用材料": "AMAT",
    "高通": "QCOM", "Qualcomm": "QCOM",
    "博通(旧)": "BRCM",
    "台积电": "TSM",
    "阿里巴巴(美)": "BABA", "BABA": "BABA",
    "京东(美)": "JD",
    "拼多多(美)": "PDD",
    "网易(美)": "NTES",
    "哔哩哔哩": "BILI", "B站": "BILI", "Bilibili": "BILI",
    "携程": "TCOM", "Ctrip": "TCOM",
    "去哪儿": "QUNR",
    "唯品会": "VIPS",
    "搜狐": "SOHU",
    "畅游": "CYOU",
    "汽车之家": "ATHM",
    "易车": "BITA",
    "新东方": "EDU",
    "好未来": "TAL",
    "高途": "GOTU",
    "滴滴": "DIDI",
    "贝壳": "BEKE",
    "满帮": "YMM",
    "名创优品": "MNSO",
    "欢聚集团": "YY",
    "虎牙": "HUYA",
    "斗鱼": "DOYU",
    "百度(美)": "BIDU",
    "腾讯音乐": "TME",
    "唯品会(旧)": "VIPS",
    "兰亭集势": "LITB",
    "聚仙": "JCO",
    "华米": "HMI",
    "小米(美)": "XIACF",
    "京东健康": "JDHK",
    "阿里健康": "00241",
    "哔哩哔哩(美)": "BILI",
    "腾讯(美)": "TCEHY",
    "阿里巴巴(美)": "BABA",
    "中石油(美)": "PTR",
    "中石化(美)": "SNP",
    "中国电信(美)": "CHA",
    "中国移动(美)": "CHL",
    "中国联通(美)": "HK",
    "中国人寿(美)": "LFC",
    "中国平安(美)": "PNGAY",
    "新东方(美)": "EDU",
    "好未来(美)": "TAL",
    "携程(美)": "TCOM",
    "阿里巴巴": "BABA",
    "京东": "JD",
    "拼多多": "PDD",
    "网易": "NTES",
    "哔哩哔哩": "BILI",
    "百度": "BIDU",
    "腾讯音乐": "TME",
    "携程": "TCOM",
    "唯品会": "VIPS",
}

# 反向映射: 代码 → 名称（用于显示）
CODE_TO_NAME = {}
for name, code in STOCK_NAME_MAP.items():
    if code not in CODE_TO_NAME:
        CODE_TO_NAME[code] = name


# ==================== 智能识别代码 ====================
def identify_code(code: str) -> dict:
    """
    智能识别股票代码 → 东财股吧代码
    支持: 中文名称、sh/sz/of/hk/us 前缀、纯数字、纯字母
    """
    original = code.strip()
    if not original:
        return None
    
    # 1. 先查中文名称映射
    if original in STOCK_NAME_MAP:
        mapped_code = STOCK_NAME_MAP[original]
        return identify_code(mapped_code)
    
    # 模糊匹配: 包含关键词
    for name, mapped_code in STOCK_NAME_MAP.items():
        if original in name or name in original:
            return identify_code(mapped_code)
    
    code = original.upper()

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

    # 6 位纯数字 → A 股 (按号段判断 sh/sz)
    if code.isdigit() and len(code) == 6:
        if code.startswith(("000", "001", "002", "003", "2", "300", "301", "302")):
            market = "A 股深市"
            prefix = "sz"
        elif code.startswith(("5", "6", "689", "688")):
            market = "A 股沪市"
            prefix = "sh"
        else:
            market = "A 股"
            prefix = "sh"
        return {"guba_code": f"{prefix}{code}", "display": code, "market": market}

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
# ==================== ETF T-1 净值缓存 ====================
# 净值每个交易日 21:00 左右更新一次，缓存 4 小时足够
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
    ('513500', 'sh', 'SP500',     '标普500ETF博时'),
    ('513650', 'sh', 'SP500',     '标普500ETF南方'),
    ('159612', 'sz', 'SP500',     '标普500ETF国泰'),
    ('159655', 'sz', 'SP500',     '标普500ETF华夏'),
    ('513520', 'sh', 'NIKKEI225', '日经225ETF华夏'),
    ('513000', 'sh', 'NIKKEI225', '日经225ETF易方达'),
    ('513880', 'sh', 'NIKKEI225', '日经225ETF华安'),
    ('159866', 'sz', 'NIKKEI225', '日经225ETF工银'),
]


def fetch_t1_nav(code: str) -> dict:
    """
    从东方财富 pingzhongdata 接口获取 T-1 净值
    返回: {"nav": float, "date": "YYYY-MM-DD", "source": "eastmoney"}
    """
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
        # 提取 NetWorthTrend 数组的最后一项的 y 值（T-1 净值）
        # 格式: {"x":1750780800000,"y":1.988,"equityReturn":0.52,"unitMoney":""}
        matches = re.findall(
            r'\{"x":(\d+),"y":([\d.]+),"equityReturn":[\d.]+,"unitMoney":""\}',
            text
        )
        if not matches:
            return {"nav": None, "date": None, "source": "eastmoney", "error": "no data"}
        # 最后一项 = 最新（通常是 T-1 净值）
        latest_ts_ms = int(matches[-1][0])
        latest_nav = float(matches[-1][1])
        # 时间戳转日期
        import datetime
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

        # 根路径 → dashboard.html（默认入口）
        if path in ("/", "/index.html"):
            self._send_file(os.path.join(os.path.dirname(__file__), "frontend", "dashboard.html"))
            return
        # 兼容：直接访问 /query.html
        if path == "/query.html":
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

        # 板块历史（用于看板走势图）
        if path == "/api/history" and "sector" in params:
            sector = params.get("sector", [""])[0]
            days_str = params.get("days", ["30"])[0]
            from_date = params.get("from", [None])[0]
            to_date = params.get("to", [None])[0]
            try:
                days = int(days_str)
            except ValueError:
                days = 30
            try:
                from history_store import get_sector_history
                history = get_sector_history(sector, days=days,
                                            from_date=from_date, to_date=to_date)
                self._send_json({"sector": sector, "data": history})
            except Exception as e:
                self._send_json({"error": f"获取历史失败: {e}"}, 500)
            return

        # 全板块历史（一次返回，供看板使用）
        if path == "/api/history/all":
            days_str = params.get("days", ["30"])[0]
            try:
                days = int(days_str)
            except ValueError:
                days = 30
            try:
                from history_store import get_all_sectors_history
                all_history = get_all_sectors_history(days=days)
                self._send_json({"days": days, "sectors": all_history})
            except Exception as e:
                self._send_json({"error": f"获取历史失败: {e}"}, 500)
            return

        # 列表所有有历史的板块
        if path == "/api/sectors":
            try:
                from history_store import list_sectors
                sectors = list_sectors()
                self._send_json({"sectors": sectors})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return

        # ETF T-1 净值（用于跨境 ETF 溢价监控）
        # GET /api/etf-nav             → 返回所有 21 只 ETF 的 T-1 NAV
        # GET /api/etf-nav?code=513100 → 返回单只
        if path == "/api/etf-nav":
            single_code = params.get("code", [None])[0]
            try:
                if single_code:
                    nav = fetch_t1_nav(single_code)
                    self._send_json({"code": single_code, **nav})
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
                    self._send_json({
                        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "count": len(navs),
                        "navs": navs,
                    })
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return

        # 健康检查
        if path == "/api/health":
            self._send_json({"status": "ok", "version": "1.0"})
            return

        # 静态文件 (含 data/ 目录)
        if path.startswith("/static/") or path.startswith("/data/") \
            or path.startswith("/assets/") \
            or path.endswith((".js", ".css", ".png", ".svg", ".ico", ".json", ".html", ".txt", ".xml")):
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
                elif rel.endswith(".svg"): ct = "image/svg+xml"
                elif rel.endswith(".png"): ct = "image/png"
                elif rel.endswith(".xml"): ct = "application/xml"
                elif rel.endswith(".txt"): ct = "text/plain; charset=utf-8"
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
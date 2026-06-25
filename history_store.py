"""
历史数据存储 (SQLite)
- 个股查询历史 + 板块每日指数历史
- 供前端走势图 / 分享使用
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Dict


DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "history.db")


def _get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guba_code TEXT NOT NULL,
            display TEXT,
            market TEXT,
            query_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            post_count INTEGER DEFAULT 0,
            fetch_ok INTEGER DEFAULT 0,
            fetch_error TEXT,
            index_value REAL,
            interpretation TEXT,
            details_json TEXT,
            sample_posts_json TEXT,
            raw_json TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_stock_date
        ON stock_queries(guba_code, query_date)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sector_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sector TEXT NOT NULL,
            record_date TEXT NOT NULL,
            index_value REAL,
            interpretation TEXT,
            details_json TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sector_date
        ON sector_daily(sector, record_date)
    """)
    conn.commit()
    conn.close()


_init_db()


# ===== 个股历史 =====

def save_query(guba_code: str, result: dict) -> int:
    conn = _get_conn()
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().isoformat()
    idx = result.get("index_data") or {}
    details = (idx.get("details") or {}) if isinstance(idx, dict) else {}

    conn.execute("""
        INSERT OR REPLACE INTO stock_queries
        (guba_code, display, market, query_date, created_at, post_count,
         fetch_ok, fetch_error, index_value, interpretation,
         details_json, sample_posts_json, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        guba_code,
        (result.get("code_info") or {}).get("display"),
        (result.get("code_info") or {}).get("market"),
        today, now,
        result.get("post_count", 0),
        1 if result.get("fetch_ok") else 0,
        result.get("fetch_error"),
        idx.get("index") if isinstance(idx, dict) else None,
        idx.get("interpretation") if isinstance(idx, dict) else None,
        json.dumps(details, ensure_ascii=False),
        json.dumps(result.get("sample_posts", [])[:10], ensure_ascii=False),
        json.dumps(result, ensure_ascii=False, default=str)[:10000],
    ))
    conn.commit()
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return row_id


def get_query(guba_code: str, date: Optional[str] = None) -> Optional[dict]:
    conn = _get_conn()
    if date:
        row = conn.execute("""
            SELECT * FROM stock_queries
            WHERE guba_code = ? AND query_date = ?
            ORDER BY id DESC LIMIT 1
        """, (guba_code, date)).fetchone()
    else:
        row = conn.execute("""
            SELECT * FROM stock_queries
            WHERE guba_code = ?
            ORDER BY query_date DESC, id DESC LIMIT 1
        """, (guba_code,)).fetchone()
    conn.close()
    if not row:
        return None
    return _stock_row_to_dict(row)


def get_stock_history(guba_code: str, days: int = 30) -> List[dict]:
    conn = _get_conn()
    rows = conn.execute("""
        SELECT * FROM stock_queries
        WHERE guba_code = ?
        ORDER BY query_date DESC, id DESC
    """, (guba_code,)).fetchall()
    conn.close()
    seen = set()
    result = []
    for r in rows:
        if r["query_date"] in seen:
            continue
        seen.add(r["query_date"])
        result.append(_stock_row_to_dict(r))
    result = result[:days]
    result.sort(key=lambda x: x["query_date"])
    return result


def list_stocks(limit: int = 100) -> List[dict]:
    conn = _get_conn()
    rows = conn.execute("""
        SELECT s.* FROM stock_queries s
        INNER JOIN (
            SELECT guba_code, MAX(id) as max_id
            FROM stock_queries
            GROUP BY guba_code
        ) latest ON s.id = latest.max_id
        ORDER BY s.query_date DESC, s.id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [_stock_row_to_dict(r) for r in rows]


# ===== 板块历史 =====

def save_sector_daily(sector: str, record_date: str,
                      index_value: float, interpretation: str = "",
                      details: Optional[dict] = None):
    conn = _get_conn()
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO sector_daily
        (sector, record_date, index_value, interpretation, details_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        sector, record_date, index_value, interpretation,
        json.dumps(details or {}, ensure_ascii=False),
        now,
    ))
    conn.commit()
    conn.close()


def save_all_sectors(sector_indices: Dict[str, dict], record_date: Optional[str] = None):
    """一次保存多个板块当日指数"""
    if not record_date:
        record_date = datetime.now().strftime("%Y-%m-%d")
    for sector, data in sector_indices.items():
        save_sector_daily(
            sector=sector,
            record_date=record_date,
            index_value=data.get("index", 0) if isinstance(data, dict) else 0,
            interpretation=data.get("interpretation", "") if isinstance(data, dict) else "",
            details=data.get("details", {}) if isinstance(data, dict) else {},
        )


def get_sector_history(sector: str, days: int = 30,
                       from_date: Optional[str] = None,
                       to_date: Optional[str] = None) -> List[dict]:
    conn = _get_conn()
    sql = "SELECT * FROM sector_daily WHERE sector = ?"
    params = [sector]
    if from_date:
        sql += " AND record_date >= ?"
        params.append(from_date)
    if to_date:
        sql += " AND record_date <= ?"
        params.append(to_date)
    sql += " ORDER BY record_date DESC, id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    seen = set()
    result = []
    for r in rows:
        if r["record_date"] in seen:
            continue
        seen.add(r["record_date"])
        result.append(_sector_row_to_dict(r))

    if not from_date and not to_date:
        result = result[:days]
    result.sort(key=lambda x: x["record_date"])
    return result


def get_all_sectors_history(days: int = 30) -> Dict[str, List[dict]]:
    """获取所有板块近 N 天历史，用于看板一次性渲染"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT * FROM sector_daily
        ORDER BY record_date DESC, id DESC
    """).fetchall()
    conn.close()

    grouped: Dict[str, List[dict]] = {}
    seen: Dict[str, set] = {}
    for r in rows:
        s = r["sector"]
        if s not in grouped:
            grouped[s] = []
            seen[s] = set()
        if r["record_date"] in seen[s]:
            continue
        seen[s].add(r["record_date"])
        grouped[s].append(_sector_row_to_dict(r))

    for s in grouped:
        grouped[s] = grouped[s][:days]
        grouped[s].sort(key=lambda x: x["record_date"])
    return grouped


def list_sectors(limit_days: int = 30) -> List[str]:
    conn = _get_conn()
    rows = conn.execute("""
        SELECT sector, MAX(record_date) as latest
        FROM sector_daily
        GROUP BY sector
        HAVING COUNT(*) >= 1
        ORDER BY latest DESC
    """).fetchall()
    conn.close()
    return [r["sector"] for r in rows]


# ===== 内部工具 =====

def _stock_row_to_dict(row) -> dict:
    try:
        details = json.loads(row["details_json"] or "{}")
    except Exception:
        details = {}
    try:
        sample_posts = json.loads(row["sample_posts_json"] or "[]")
    except Exception:
        sample_posts = []
    return {
        "id": row["id"],
        "guba_code": row["guba_code"],
        "display": row["display"],
        "market": row["market"],
        "query_date": row["query_date"],
        "created_at": row["created_at"],
        "post_count": row["post_count"],
        "fetch_ok": bool(row["fetch_ok"]),
        "fetch_error": row["fetch_error"],
        "index_value": row["index_value"],
        "interpretation": row["interpretation"],
        "details": details,
        "sample_posts": sample_posts,
    }


def _sector_row_to_dict(row) -> dict:
    try:
        details = json.loads(row["details_json"] or "{}")
    except Exception:
        details = {}
    return {
        "id": row["id"],
        "sector": row["sector"],
        "record_date": row["record_date"],
        "index": row["index_value"],
        "interpretation": row["interpretation"],
        "details": details,
    }


def ensure_fallback_sectors(sector_keys: List[str], days: int = 30):
    """如果 DB 没有足够历史，从 JSON 历史文件补齐（首次部署友好）"""
    json_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "history.json")
    if not os.path.exists(json_file):
        return 0
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return 0

    records = data.get("records", [])
    if not records:
        return 0

    count = 0
    existing_pairs = set()  # (sector, date) 去重

    # 收集 DB 已有
    conn = _get_conn()
    rows = conn.execute("SELECT sector, record_date FROM sector_daily").fetchall()
    conn.close()
    for r in rows:
        existing_pairs.add((r["sector"], r["record_date"]))

    # 倒序：让后面日期优先（保证最近的数据优先进入）
    for rec in reversed(records):
        date = rec.get("date")
        if not date:
            continue
        for sector in sector_keys:
            if (sector, date) in existing_pairs:
                continue
            sdata = (rec.get("sectors") or {}).get(sector)
            if not sdata:
                continue
            save_sector_daily(
                sector=sector,
                record_date=date,
                index_value=sdata.get("index", 0) if isinstance(sdata, dict) else 0,
                interpretation=sdata.get("interpretation", "") if isinstance(sdata, dict) else "",
                details=sdata.get("details", {}) if isinstance(sdata, dict) else {},
            )
            count += 1
            existing_pairs.add((sector, date))
    return count

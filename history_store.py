"""
历史数据存储 (SQLite)
- 存储每次查询的分析结果，用于历史走势和分享
- 按 (guba_code + date) 去重，同一股票同一天只保留最新一次
"""
import json
import os
import sqlite3
from datetime import datetime
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
    conn.commit()
    conn.close()


_init_db()


def save_query(guba_code: str, result: dict) -> int:
    """保存一次查询结果，同一 guba_code + date 会覆盖"""
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
        today,
        now,
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
    """查询某股票某天的分析结果（不传 date 默认今天，找不到则回退到最近一次）"""
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
    return _row_to_dict(row)


def get_history(guba_code: str, days: int = 30) -> List[dict]:
    """获取某股票近 N 天的历史指数"""
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
        result.append(_row_to_dict(r))
    result = result[:days]
    result.sort(key=lambda x: x["query_date"])
    return result


def list_stocks(limit: int = 50) -> List[dict]:
    """列出所有查询过的股票（最近一次结果）"""
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
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "guba_code": row["guba_code"],
        "display": row["display"],
        "market":
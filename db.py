import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "intel.db"
DB_PATH.parent.mkdir(exist_ok=True)


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn, col, decl):
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(reports)").fetchall()]
    if col not in cols:
        conn.execute(f"ALTER TABLE reports ADD COLUMN {col} {decl}")


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date    TEXT NOT NULL,
                pub_date    TEXT,
                source      TEXT NOT NULL,
                title       TEXT,
                title_zh    TEXT,
                source_url  TEXT,
                summary_md  TEXT,
                skill_md    TEXT,
                created_at  TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(source, title)
            )
        """)
        _ensure_column(conn, "pub_date", "TEXT")
        _ensure_column(conn, "title_zh", "TEXT")


def already_have(source, title):
    """這篇（同來源同標題）是否已處理過，用於增量抓取跳過。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM reports WHERE source=? AND title=? LIMIT 1",
            (source, title)).fetchone()
        return row is not None


def save_report(run_date, pub_date, source, title, title_zh,
                source_url, summary_md, skill_md):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO reports
            (run_date, pub_date, source, title, title_zh,
             source_url, summary_md, skill_md)
            VALUES (?,?,?,?,?,?,?,?)
        """, (run_date, pub_date, source, title, title_zh,
              source_url, summary_md, skill_md))


def list_run_dates():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT run_date FROM reports ORDER BY run_date DESC"
        ).fetchall()
        return [r["run_date"] for r in rows]


def list_pub_dates():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT pub_date FROM reports "
            "WHERE pub_date IS NOT NULL ORDER BY pub_date DESC"
        ).fetchall()
        return [r["pub_date"] for r in rows]


def list_sources():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT source FROM reports ORDER BY source").fetchall()
        return [r["source"] for r in rows]


_ORDER = " ORDER BY COALESCE(pub_date, run_date) DESC, created_at DESC"


def query_reports(pub_date=None, source=None):
    sql = "SELECT * FROM reports WHERE 1=1"
    params = []
    if pub_date and pub_date != "全部":
        sql += " AND pub_date=?"; params.append(pub_date)
    if source and source != "全部":
        sql += " AND source=?"; params.append(source)
    sql += _ORDER
    with get_conn() as conn:
        return conn.execute(sql, params).fetchall()


def search_reports(keyword):
    kw = f"%{keyword}%"
    sql = ("SELECT * FROM reports WHERE "
           "(title LIKE ? OR title_zh LIKE ? OR summary_md LIKE ? OR skill_md LIKE ?)"
           + _ORDER)
    with get_conn() as conn:
        return conn.execute(sql, [kw, kw, kw, kw]).fetchall()


def query_all_skills(pub_date=None, source=None):
    sql = ("SELECT * FROM reports WHERE skill_md IS NOT NULL AND skill_md != '' "
           "AND skill_md NOT LIKE '%一般性資訊%'")
    params = []
    if pub_date and pub_date != "全部":
        sql += " AND pub_date=?"; params.append(pub_date)
    if source and source != "全部":
        sql += " AND source=?"; params.append(source)
    sql += _ORDER
    with get_conn() as conn:
        return conn.execute(sql, params).fetchall()

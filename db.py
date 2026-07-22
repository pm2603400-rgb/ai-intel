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
                category    TEXT,
                title       TEXT,
                title_zh    TEXT,
                source_url  TEXT,
                summary_md  TEXT,
                skill_md    TEXT,
                use_cases            TEXT,
                application_patterns TEXT,
                created_at  TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(source, title)
            )
        """)
        _ensure_column(conn, "pub_date", "TEXT")
        _ensure_column(conn, "title_zh", "TEXT")
        _ensure_column(conn, "category", "TEXT")
        _ensure_column(conn, "use_cases", "TEXT")              # JSON 陣列字串：適用情境標籤
        _ensure_column(conn, "application_patterns", "TEXT")   # 多行文字：典型應用方式


def already_have(source, title):
    """這篇（同來源同標題）是否已處理過，用於增量抓取跳過。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM reports WHERE source=? AND title=? LIMIT 1",
            (source, title)).fetchone()
        return row is not None


def save_report(run_date, pub_date, source, title, title_zh,
                source_url, summary_md, skill_md, category="一般資訊",
                use_cases=None, application_patterns=""):
    import json
    uc = json.dumps(use_cases or [], ensure_ascii=False)
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO reports
            (run_date, pub_date, source, category, title, title_zh,
             source_url, summary_md, skill_md, use_cases, application_patterns)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (run_date, pub_date, source, category, title, title_zh,
              source_url, summary_md, skill_md, uc, application_patterns))


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


def list_categories():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT category FROM reports "
            "WHERE category IS NOT NULL AND category != '' ORDER BY category"
        ).fetchall()
        return [r["category"] for r in rows]


_ORDER = " ORDER BY COALESCE(pub_date, run_date) DESC, created_at DESC"


def query_reports(pub_date=None, source=None, category=None):
    sql = "SELECT * FROM reports WHERE 1=1"
    params = []
    if pub_date and pub_date != "全部":
        sql += " AND pub_date=?"; params.append(pub_date)
    if source and source != "全部":
        sql += " AND source=?"; params.append(source)
    if category and category != "全部":
        sql += " AND category=?"; params.append(category)
    sql += _ORDER
    with get_conn() as conn:
        return conn.execute(sql, params).fetchall()


def search_reports(keyword):
    kw = f"%{keyword}%"
    sql = ("SELECT * FROM reports WHERE "
           "(title LIKE ? OR title_zh LIKE ? OR summary_md LIKE ? "
           "OR skill_md LIKE ? OR category LIKE ? "
           "OR use_cases LIKE ? OR application_patterns LIKE ?)"
           + _ORDER)
    with get_conn() as conn:
        return conn.execute(sql, [kw, kw, kw, kw, kw, kw, kw]).fetchall()


def query_all_skills(pub_date=None, source=None, category=None):
    sql = ("SELECT * FROM reports WHERE skill_md IS NOT NULL AND skill_md != '' "
           "AND skill_md NOT LIKE '%一般性資訊%'")
    params = []
    if pub_date and pub_date != "全部":
        sql += " AND pub_date=?"; params.append(pub_date)
    if source and source != "全部":
        sql += " AND source=?"; params.append(source)
    if category and category != "全部":
        sql += " AND category=?"; params.append(category)
    sql += _ORDER
    with get_conn() as conn:
        return conn.execute(sql, params).fetchall()


def get_report(report_id):
    """取單筆（Admin 編輯、重新生成用）。"""
    with get_conn() as conn:
        return conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()


def all_skills_for_match():
    """取所有「含可操作 Skill」的精簡欄位，供情境查詢做語意比對。
    只取比對需要的欄位，減少送進 LLM 的量。"""
    sql = ("SELECT id, title, title_zh, category, use_cases, "
           "application_patterns, skill_md, source_url FROM reports "
           "WHERE skill_md IS NOT NULL AND skill_md != '' "
           "AND skill_md NOT LIKE '%一般性資訊%'" + _ORDER)
    with get_conn() as conn:
        return conn.execute(sql).fetchall()


def update_skill_fields(report_id, category=None, use_cases=None,
                        application_patterns=None, title_zh=None,
                        summary_md=None, skill_md=None):
    """更新單筆指定欄位（Admin 編輯 / 重新生成用）。只更新有傳入的欄位。"""
    import json
    sets, params = [], []
    if category is not None:
        sets.append("category=?"); params.append(category)
    if use_cases is not None:
        sets.append("use_cases=?"); params.append(json.dumps(use_cases, ensure_ascii=False))
    if application_patterns is not None:
        sets.append("application_patterns=?"); params.append(application_patterns)
    if title_zh is not None:
        sets.append("title_zh=?"); params.append(title_zh)
    if summary_md is not None:
        sets.append("summary_md=?"); params.append(summary_md)
    if skill_md is not None:
        sets.append("skill_md=?"); params.append(skill_md)
    if not sets:
        return
    params.append(report_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE reports SET {', '.join(sets)} WHERE id=?", params)


def insert_manual_skill(title, title_zh, category, skill_md, summary_md="",
                        use_cases=None, application_patterns="", source_url=""):
    """Admin 手動新增一則 skill。"""
    import json, datetime as _dt
    today = _dt.date.today().isoformat()
    uc = json.dumps(use_cases or [], ensure_ascii=False)
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO reports
            (run_date, pub_date, source, category, title, title_zh,
             source_url, summary_md, skill_md, use_cases, application_patterns)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (today, today, "✍️ 手動新增", category, title, title_zh,
              source_url, summary_md, skill_md, uc, application_patterns))


def delete_report(report_id):
    """Admin 刪除一則。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM reports WHERE id=?", (report_id,))


def query_by_date_range(start_date, end_date):
    """取 pub_date（或 run_date）落在 [start_date, end_date] 內的文章，供週報用。"""
    sql = ("SELECT * FROM reports WHERE "
           "COALESCE(pub_date, run_date) >= ? AND COALESCE(pub_date, run_date) <= ?"
           + _ORDER)
    with get_conn() as conn:
        return conn.execute(sql, [start_date, end_date]).fetchall()

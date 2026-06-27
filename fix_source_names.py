"""
一次性腳本：把資料庫裡的「舊來源名稱」批次更新成「新來源名稱（含國旗）」。
用法：放到 repo 根目錄，執行一次 `python fix_source_names.py`，完成後可刪除此檔。
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "intel.db"

# 舊名稱 → 新名稱 對照表
RENAME_MAP = {
    "OpenAI Blog": "OpenAI Blog 🇺🇸",
    "Google DeepMind": "Google DeepMind 🇺🇸",
    "Google AI Research": "Google AI Research 🇺🇸",
    "MIT News (AI)": "MIT News (AI) 🇺🇸",
    "BAIR (Berkeley AI)": "BAIR (Berkeley) 🇺🇸",
    "Hugging Face Daily Papers": "Hugging Face Papers 🌐",
    "Anthropic News": "Anthropic News 🌐",
}

if not DB_PATH.exists():
    print(f"找不到資料庫：{DB_PATH}")
    raise SystemExit(1)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

print("=== 修正前，各來源筆數 ===")
for r in conn.execute("SELECT source, COUNT(*) n FROM reports GROUP BY source ORDER BY source"):
    print(f"  {r['source']}: {r['n']} 筆")

total_changed = 0
for old, new in RENAME_MAP.items():
    # 先看有幾筆是舊名稱
    cur = conn.execute("SELECT COUNT(*) n FROM reports WHERE source=?", (old,))
    cnt = cur.fetchone()["n"]
    if cnt == 0:
        continue
    # 改名前先處理可能的「新舊重複」：若同標題在新名稱下已存在，刪掉舊的避免衝突
    conn.execute("""
        DELETE FROM reports
        WHERE source=? AND title IN (
            SELECT title FROM reports WHERE source=?
        )
    """, (old, new))
    # 其餘的舊名稱更新成新名稱
    conn.execute("UPDATE reports SET source=? WHERE source=?", (new, old))
    after = conn.execute("SELECT changes()").fetchone()[0]
    print(f"\n✔ 「{old}」→「{new}」：處理 {cnt} 筆")
    total_changed += cnt

conn.commit()

print(f"\n=== 修正後，各來源筆數 ===")
for r in conn.execute("SELECT source, COUNT(*) n FROM reports GROUP BY source ORDER BY source"):
    print(f"  {r['source']}: {r['n']} 筆")

conn.close()
print(f"\n完成！共處理 {total_changed} 筆。可以刪除此 fix_source_names.py 了。")

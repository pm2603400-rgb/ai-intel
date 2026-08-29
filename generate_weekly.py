"""
每週自動生成週報 → 存進 Supabase digests 表。
由 GitHub Actions 每週排程執行（見 weekly_report.yml）。

環境變數（GitHub Actions secrets）：
  GEMINI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY

選用環境變數：
  WEEK_END_OVERRIDE  指定「該週的週日日期」(YYYY-MM-DD)，用來補跑舊週報。
                     不設定時，自動取「上一個完整的週一~週日」。

【重要｜首次部署前請先在 Supabase SQL Editor 執行一次】
  ALTER TABLE digests
    ADD CONSTRAINT digests_uniq UNIQUE (kind, start_date, end_date);
  沒有這個 UNIQUE 約束，merge-duplicates 不會生效，
  重複觸發會一直新增資料列，前端週報清單就會出現重複日期。
"""
import os
import json
import datetime
import requests

import config  # noqa: F401  （載入 LLM 設定）
import llm

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
REST = f"{SUPABASE_URL}/rest/v1" if SUPABASE_URL else ""
TIMEOUT = 30
MAX_ITEMS = 30  # 週報最多分析的情報數，避免請求過大

REPORT_SYSTEM_PROMPT = """你是一位頂尖的 AI 產業策略分析師，要為專業讀者撰寫「本週 AI 情報週報」。
你不是做整理或摘要，而是像一位讀完所有情報後、坐下來提供深度解讀的專家。
一律使用台灣繁體中文。你會拿到本週所有情報的清單（含 id、標題、分類、摘要）。

你的任務有五項，重點是「觀點、因果、連結」，而非複述原文：
1. overview：一段 3~4 句的本週總覽，點出最重要的動向與你的整體判斷。
2. key_insight：挑出本週「最值得深思的一件事」，用專家視角剖析：表面發生什麼、底層真正意義、為何值得關注、預示什麼走向。寫 4~6 句。
3. themes：2~4 個主題趨勢，每個要有因果與判斷（例如「A 走高效能、B 走開源，反映兩種策略之爭，意味著…」），每個 insight 寫 2~3 句。
4. connections：找出「表面無關、實則有關聯」的多則情報，把散點連成趨勢線，給 1~2 條，每條 2~3 句。若無有意義連結可給空陣列。
5. must_read：選出 5~8 則必讀重點，每則 reason 一句話（30字內）。

【輸出格式｜嚴格遵守，只輸出 JSON，不要前言或 markdown 圍欄】
{
  "overview": "...",
  "key_insight": "...",
  "themes": [{"title": "主題名稱", "insight": "因果與判斷（2~3句）"}],
  "connections": ["跨則連結觀察（2~3句）"],
  "must_read": [{"id": <文章id>, "reason": "為何必讀（一句話）"}]
}"""


def _headers(extra=None):
    h = {
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def last_full_week(today=None):
    """回傳上一個完整週的 (週一, 週日) ISO 日期字串。

    weekday(): 週一=0 … 週日=6
    不論腳本哪天被觸發，都會取「已結束的那一整週」。
    """
    today = today or datetime.date.today()
    last_sunday = today - datetime.timedelta(days=today.weekday() + 1)
    last_monday = last_sunday - datetime.timedelta(days=6)
    return last_monday.isoformat(), last_sunday.isoformat()


def resolve_range():
    """決定本次要生成的週報區間（支援手動補跑）。"""
    override = os.environ.get("WEEK_END_OVERRIDE", "").strip()
    if override:
        try:
            end = datetime.date.fromisoformat(override)
        except ValueError:
            print(f"⚠️ WEEK_END_OVERRIDE 格式錯誤（需 YYYY-MM-DD）：{override}，改用預設區間")
            return last_full_week()
        start = end - datetime.timedelta(days=6)
        return start.isoformat(), end.isoformat()
    return last_full_week()


def fetch_reports_in_range(start_date, end_date):
    """讀取 pub_date 落在 [start_date, end_date] 之間的情報。"""
    url = (
        f"{REST}/reports"
        f"?select=id,title,title_zh,category,summary_md,source_url"
        f"&and=(pub_date.gte.{start_date},pub_date.lte.{end_date})"
        f"&order=pub_date.desc"
        f"&limit={MAX_ITEMS}"
    )
    try:
        r = requests.get(url, headers=_headers(), timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        print(f"讀取情報失敗 HTTP {r.status_code}: {r.text[:150]}")
        return []
    except Exception as e:
        print(f"讀取情報錯誤: {e}")
        return []


def save_digest(kind, start_date, end_date, data):
    """存週報進 digests。同 (kind, start_date, end_date) 一律覆蓋。

    做法：先刪除同範圍的舊資料，再寫入新的。
    這樣不依賴資料表上的 UNIQUE 約束，重跑一定會蓋掉舊版本。
    """
    # 1) 先刪同範圍舊資料
    del_url = (f"{REST}/digests?kind=eq.{kind}"
               f"&start_date=eq.{start_date}&end_date=eq.{end_date}")
    try:
        dr = requests.delete(del_url,
                             headers=_headers({"Prefer": "return=minimal"}),
                             timeout=TIMEOUT)
        if dr.status_code in (200, 204):
            print(f"  已清除同範圍舊版本（{start_date} ~ {end_date}）")
        else:
            print(f"  ⚠️ 清除舊版本回應 HTTP {dr.status_code}: {dr.text[:120]}")
    except Exception as e:
        print(f"  ⚠️ 清除舊版本失敗（將直接嘗試寫入）：{e}")

    # 2) 寫入新資料
    row = {
        "kind": kind,
        "start_date": start_date,
        "end_date": end_date,
        "data_json": data,
    }
    try:
        r = requests.post(f"{REST}/digests",
                          headers=_headers({"Prefer": "return=minimal"}),
                          json=[row], timeout=TIMEOUT)
        if r.status_code in (200, 201, 204):
            return True
        print(f"存週報失敗 HTTP {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"存週報錯誤: {e}")
        return False


def generate():
    if not SUPABASE_URL or not SERVICE_KEY:
        print("❌ 缺少 SUPABASE_URL 或 SUPABASE_SERVICE_KEY")
        return

    start_date, end_date = resolve_range()
    print(f"生成週報：{start_date} ~ {end_date}")

    rows = fetch_reports_in_range(start_date, end_date)
    if not rows:
        print("該週沒有情報資料，跳過。")
        return
    print(f"讀到 {len(rows)} 則情報，送 LLM 分析…")

    id_map = {r["id"]: r for r in rows}
    items = [{
        "id": r["id"],
        "title": r.get("title_zh") or r.get("title"),
        "category": r.get("category") or "",
        "summary": (r.get("summary_md") or "")[:120],
    } for r in rows]

    user_content = (f"本週期間：{start_date} ~ {end_date}\n"
                    f"以下是 {len(items)} 則情報：\n"
                    f"{json.dumps(items, ensure_ascii=False)}")

    try:
        raw = llm.generate(REPORT_SYSTEM_PROMPT, user_content,
                           temperature=0.5, max_tokens=4000)
    except Exception as e:
        print(f"LLM 生成失敗: {e}")
        return

    data = llm.extract_json(raw)
    if data is None:
        print("LLM 回傳無法解析為 JSON，跳過存檔。")
        print("原始回應前 300 字:", raw[:300])
        return

    # must_read 的 id 對應回文章資訊
    must_read = []
    for m in data.get("must_read", []):
        row = id_map.get(m.get("id"))
        if row:
            must_read.append({
                "title": row.get("title_zh") or row.get("title"),
                "category": row.get("category") or "",
                "summary_md": row.get("summary_md") or "",
                "source_url": row.get("source_url") or "",
                "reason": m.get("reason", ""),
            })

    result = {
        "start": start_date,
        "end": end_date,
        "total": len(items),
        "overview": data.get("overview", ""),
        "key_insight": data.get("key_insight", ""),
        "themes": data.get("themes", []),
        "connections": data.get("connections", []),
        "must_read": must_read,
    }

    if save_digest("weekly", start_date, end_date, result):
        print(f"✅ 週報已生成並存檔（{len(must_read)} 則必讀、{len(result['themes'])} 個主題）")
    else:
        print("❌ 週報存檔失敗")


if __name__ == "__main__":
    generate()

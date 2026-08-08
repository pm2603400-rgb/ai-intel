"""
每週自動生成週報 → 存進 Supabase digests 表。
由 GitHub Actions 每週排程執行（見 weekly_report.yml）。

環境變數（GitHub Actions secrets）：
  GEMINI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY
"""
import os
import json
import datetime
import requests

import config
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


def fetch_reports_in_range(start_date, end_date):
    """讀取日期範圍內的情報。"""
    params = {
        "select": "id,title,title_zh,category,summary_md,source_url",
        "or": f"(pub_date.gte.{start_date},pub_date.lte.{end_date})",
        "pub_date": f"gte.{start_date}",
        "order": "pub_date.desc",
        "limit": str(MAX_ITEMS),
    }
    # 用 pub_date 範圍過濾（gte + lte）
    params = {
        "select": "id,title,title_zh,category,summary_md,source_url",
        "pub_date": f"gte.{start_date}",
        "order": "pub_date.desc",
        "limit": str(MAX_ITEMS),
    }
    try:
        # PostgREST 多條件：pub_date>=start 且 <=end
        url = (f"{REST}/reports?select=id,title,title_zh,category,summary_md,source_url"
               f"&pub_date=gte.{start_date}&pub_date=lte.{end_date}"
               f"&order=pub_date.desc&limit={MAX_ITEMS}")
        r = requests.get(url, headers=_headers(), timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        print(f"讀取情報失敗 HTTP {r.status_code}: {r.text[:150]}")
        return []
    except Exception as e:
        print(f"讀取情報錯誤: {e}")
        return []


def save_digest(kind, start_date, end_date, data):
    """存週報進 digests（同範圍覆蓋）。"""
    row = {
        "kind": kind,
        "start_date": start_date,
        "end_date": end_date,
        "data_json": data,
    }
    headers = _headers({"Prefer": "resolution=merge-duplicates,return=minimal"})
    try:
        r = requests.post(f"{REST}/digests", headers=headers,
                          json=[row], timeout=TIMEOUT)
        if r.status_code in (200, 201, 204):
            return True
        print(f"存週報失敗 HTTP {r.status_code}: {r.text[:150]}")
        return False
    except Exception as e:
        print(f"存週報錯誤: {e}")
        return False


def generate():
    if not SUPABASE_URL or not SERVICE_KEY:
        print("❌ 缺少 SUPABASE_URL 或 SUPABASE_SERVICE_KEY")
        return

    today = datetime.date.today()
    end_date = today.isoformat()
    start_date = (today - datetime.timedelta(days=6)).isoformat()
    print(f"生成週報：{start_date} ~ {end_date}")

    rows = fetch_reports_in_range(start_date, end_date)
    if not rows:
        print("本週沒有情報資料，跳過。")
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

"""
週報：把某段期間的情報交給 LLM，選出必讀重點並歸納主題趨勢。
"""
import json
import datetime
import llm
import db

REPORT_SYSTEM_PROMPT = """你是一位資深 AI 產業分析師，要為忙碌的專業讀者製作「本週 AI 情報週報」。
一律使用台灣繁體中文。你會拿到本週所有情報的清單（含 id、標題、分類、摘要）。

你的任務：
1. 從中選出「本週最值得讀的 5~8 則必讀重點」，選擇標準是重要性、影響力、實用性（不是只看新）。
2. 把本週情報歸納成 2~4 個「主題趨勢」，每個主題用一兩句話點出脈絡（例如「本週多家業者發表多模態模型，顯示…」）。

【輸出格式｜嚴格遵守，只輸出 JSON，不要前言或 markdown 圍欄】
{
  "overview": "一段 2~3 句的本週總覽，點出最重要的動向。",
  "themes": [
    {"title": "主題名稱", "insight": "這個主題的脈絡與觀察（1~2句）"}
  ],
  "must_read": [
    {"id": <文章id>, "reason": "為什麼這則必讀（一句話）"}
  ]
}"""


def generate_weekly(start_date=None, end_date=None):
    """產生週報 dict。預設為最近 7 天。"""
    today = datetime.date.today()
    if end_date is None:
        end_date = today.isoformat()
    if start_date is None:
        start_date = (today - datetime.timedelta(days=6)).isoformat()

    rows = db.query_by_date_range(start_date, end_date)
    if not rows:
        return {"empty": True, "start": start_date, "end": end_date}

    # 限制資料量：最多取範圍內最新 MAX_ITEMS 則，避免免費方案資源爆量導致 app 重啟
    MAX_ITEMS = 30
    truncated = len(rows) > MAX_ITEMS
    rows = rows[:MAX_ITEMS]

    # 組裝精簡清單給 LLM（摘要只取前 120 字，大幅縮小請求）
    items = []
    id_map = {}
    for r in rows:
        id_map[r["id"]] = r
        summary = (r["summary_md"] or "")[:120]
        items.append({
            "id": r["id"],
            "title": r["title_zh"] or r["title"],
            "category": r["category"] or "",
            "summary": summary,
        })

    user_content = (f"本週期間：{start_date} ~ {end_date}\n"
                    f"以下是 {len(items)} 則情報"
                    f"{'（已取最新 30 則）' if truncated else ''}：\n"
                    f"{json.dumps(items, ensure_ascii=False)}")

    raw = llm.generate(REPORT_SYSTEM_PROMPT, user_content,
                       temperature=0.3, max_tokens=2000)
    data = llm.extract_json(raw)
    if data is None:
        return {"error": True, "raw": raw, "start": start_date, "end": end_date}

    # 把 must_read 的 id 對應回完整文章
    must_read = []
    for m in data.get("must_read", []):
        row = id_map.get(m.get("id"))
        if row is not None:
            must_read.append((row, m.get("reason", "")))

    return {
        "start": start_date,
        "end": end_date,
        "total": len(items),
        "overview": data.get("overview", ""),
        "themes": data.get("themes", []),
        "must_read": must_read,
    }

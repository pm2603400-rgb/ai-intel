"""
週報：把某段期間的情報交給 LLM，選出必讀重點並歸納主題趨勢。
"""
import json
import datetime
import llm
import db

REPORT_SYSTEM_PROMPT = """你是一位頂尖的 AI 產業策略分析師，要為專業讀者撰寫「本週 AI 情報週報」。
你不是做整理或摘要，而是像一位讀完所有情報後、坐下來提供深度解讀的專家。
一律使用台灣繁體中文。你會拿到本週所有情報的清單（含 id、標題、分類、摘要）。

你的任務有五項，重點是「觀點、因果、連結」，而非複述原文：

1. overview：一段 3~4 句的本週總覽，點出最重要的動向與你的整體判斷。

2. key_insight：這是最重要的一段。挑出本週「最值得深思的一件事」，用專家視角剖析：
   表面上發生了什麼、它底層真正的意義是什麼、為什麼值得關注、預示什麼走向。
   要有洞見與判斷，寫 4~6 句，讓讀者有「原來如此」的收穫。

3. themes：2~4 個主題趨勢，但每個主題不要只點題，要有「因果與判斷」。
   例如不要只說「多家發表新模型」，而要說「A 走高效能路線、B 走開源路線，反映兩種策略之爭，這意味著…」。
   每個 insight 寫 2~3 句，要有觀點。

4. connections：找出「表面無關、實則有關聯」的多則情報，把散點連成趨勢線。
   例如「X 發模型 + Y 出資安事件 + Z 談監管，合起來看說明 AI 進入能力與治理落差擴大的階段」。
   給 1~2 條這種跨則連結觀察，每條 2~3 句。若真的找不到有意義的連結，可給空陣列。

5. must_read：選出 5~8 則必讀重點，每則 reason 一句話（30字內）說明為何必讀。

【輸出格式｜嚴格遵守，只輸出 JSON，不要前言或 markdown 圍欄】
{
  "overview": "...",
  "key_insight": "...",
  "themes": [{"title": "主題名稱", "insight": "因果與判斷（2~3句）"}],
  "connections": ["跨則連結觀察（2~3句）"],
  "must_read": [{"id": <文章id>, "reason": "為何必讀（一句話）"}]
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
                       temperature=0.5, max_tokens=4000)
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
        "key_insight": data.get("key_insight", ""),
        "themes": data.get("themes", []),
        "connections": data.get("connections", []),
        "must_read": must_read,
    }

"""
情境查詢：把使用者的情境/痛點描述，與現有 skill 的 use_cases / application_patterns
一起交給 LLM 重排，回傳「哪幾個 skill 可以這樣套用」+ 具體怎麼套。
"""
import json
import llm
import db

MATCH_SYSTEM_PROMPT = """你是一位 AI 工具應用顧問。使用者會描述他「正在做的任務」或「遇到的痛點」，
你的任務是從提供的 skill 清單中，挑出最能幫上忙的 3~5 個，並具體說明「怎麼套用到他的情境」。

【最重要原則】
不要只是列出 skill 名稱。要明確說「針對他的情境，這個 skill 可以這樣用：…」，
幫使用者想到他原本沒想到的應用方式。這是這個功能的核心價值。

【輸出格式｜嚴格遵守，只輸出 JSON，不要任何前言或 markdown 標記】
{
  "matches": [
    {
      "id": <skill的id數字>,
      "why": "為什麼這個 skill 適合他的情境（一句話）",
      "how": "具體怎麼套用到他的情境（2~3 句，要具體可操作）"
    }
  ]
}
若清單中沒有任何適合的，回傳 {"matches": []}。
依適合程度由高到低排序，最多 5 個。"""


def find_matching_skills(user_situation, max_candidates=60):
    """回傳 [(report_row, why, how), ...]，依適合度排序。"""
    skills = db.all_skills_for_match()
    if not skills:
        return []

    # 組裝候選清單（精簡，控制送進 LLM 的量）
    candidates = []
    id_map = {}
    for r in skills[:max_candidates]:
        rid = r["id"]
        id_map[rid] = r
        uc = ""
        try:
            uc = "、".join(json.loads(r["use_cases"] or "[]"))
        except Exception:
            uc = ""
        candidates.append({
            "id": rid,
            "title": r["title_zh"] or r["title"],
            "category": r["category"] or "",
            "use_cases": uc,
            "application_patterns": (r["application_patterns"] or "")[:300],
        })

    user_content = (
        f"使用者的情境/痛點：\n{user_situation}\n\n"
        f"可用的 skill 清單（JSON）：\n{json.dumps(candidates, ensure_ascii=False)}"
    )

    raw = llm.generate(MATCH_SYSTEM_PROMPT, user_content,
                       temperature=0.3, max_tokens=2000)

    # 解析 JSON（容錯：去掉可能的 markdown 圍欄）
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
        matches = data.get("matches", [])
    except Exception:
        return [("PARSE_ERROR", raw, "")]

    results = []
    for m in matches:
        rid = m.get("id")
        row = id_map.get(rid)
        if row is not None:
            results.append((row, m.get("why", ""), m.get("how", "")))
    return results

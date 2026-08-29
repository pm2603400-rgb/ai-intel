"""
每月自動生成月報 → 存進 digests 表（kind='monthly'）。

做法：讀取當月已存的「週報」digests，讓 LLM 綜合成月度回顧。
不重新分析當月所有文章，因此省算力，而且是站在週報的判斷上再抽象一層。

環境變數：
  GEMINI_API_KEY / OPENAI_* （依 LLM_PROVIDER）
  SUPABASE_URL, SUPABASE_SERVICE_KEY

選用：
  MONTH_OVERRIDE  指定月份 YYYY-MM，用來補跑舊月報。不設＝上一個完整月份。

【首次執行前請確認 digests 表有 UNIQUE 約束】
  ALTER TABLE digests
    ADD CONSTRAINT digests_uniq UNIQUE (kind, start_date, end_date);
"""
import os
import json
import calendar
import datetime
import requests

import config  # noqa: F401
import llm

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
REST = f"{SUPABASE_URL}/rest/v1" if SUPABASE_URL else ""
TIMEOUT = 30

MONTHLY_SYSTEM_PROMPT = """你是一位頂尖的 AI 產業策略分析師，要撰寫「本月 AI 情報月報」。
你會拿到這個月已經生成好的數份「週報」內容（每份含總覽、關鍵洞察、主題趨勢、跨則連結）。
你的任務不是把週報接起來，而是**站在更高的位置重新判斷**：哪些是真正的月度主線、
哪些只是單週的雜訊、月初到月底之間有沒有出現態勢轉變。

一律使用台灣繁體中文。技術名詞、公司名、模型名保留英文原文。

你的任務有五項：
1. overview：4~6 句的本月總覽。點出這個月最重要的兩三條主線，以及你的整體判斷。
   要有「這個月與上個月相比如何」的視角，而不只是描述發生了什麼。
2. key_insight：本月「最值得深思的一件事」。用專家視角剖析：表面發生什麼、
   底層真正意義、為何值得關注、預示什麼走向。寫 6~8 句。
3. themes：3~5 個月度主題。每個要說明它如何在整個月中演變
   （例如「月初只是傳聞，到月底已有三家跟進，代表…」），每個 insight 寫 3~4 句。
4. shifts：找出「月初與月底態勢不同」的地方，1~3 條，每條 2~3 句。
   這是月報獨有的價值 —— 週報看不到跨週的轉折。若確實沒有可給空陣列。
5. outlook：對下個月的觀察建議，2~4 句。指出值得盯的具體訊號，
   而不是空泛的「值得持續關注」。

【輸出格式｜嚴格遵守，只輸出 JSON，不要前言或 markdown 圍欄】
{
  "overview": "...",
  "key_insight": "...",
  "themes": [{"title": "主題名稱", "insight": "演變與判斷（3~4句）"}],
  "shifts": ["月初到月底的態勢轉變（2~3句）"],
  "outlook": "下個月值得盯的訊號（2~4句）"
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


def last_full_month(today=None):
    """回傳上一個完整月份的 (起日, 迄日) ISO 字串。"""
    today = today or datetime.date.today()
    first_this = today.replace(day=1)
    last_prev = first_this - datetime.timedelta(days=1)
    start = last_prev.replace(day=1)
    return start.isoformat(), last_prev.isoformat()


def resolve_range():
    """決定要生成哪個月（支援手動補跑）。"""
    ov = os.environ.get("MONTH_OVERRIDE", "").strip()
    if ov:
        try:
            y, m = ov.split("-")
            y, m = int(y), int(m)
            last = calendar.monthrange(y, m)[1]
            return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{last:02d}"
        except Exception:
            print(f"⚠️ MONTH_OVERRIDE 格式錯誤（需 YYYY-MM）：{ov}，改用預設")
    return last_full_month()


def fetch_weekly_digests(start_date, end_date):
    """取這個月範圍內的週報（用週報結束日判斷歸屬）。"""
    url = (f"{REST}/digests"
           f"?select=start_date,end_date,data_json"
           f"&kind=eq.weekly"
           f"&and=(end_date.gte.{start_date},end_date.lte.{end_date})"
           f"&order=end_date.asc")
    try:
        r = requests.get(url, headers=_headers(), timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        print(f"讀取週報失敗 HTTP {r.status_code}: {r.text[:200]}")
        return []
    except Exception as e:
        print(f"讀取週報錯誤: {e}")
        return []


def save_digest(kind, start_date, end_date, data):
    """存月報進 digests。同 (kind, start_date, end_date) 一律覆蓋。

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
        print(f"存月報失敗 HTTP {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"存月報錯誤: {e}")
        return False


def generate():
    if not SUPABASE_URL or not SERVICE_KEY:
        print("❌ 缺少 SUPABASE_URL 或 SUPABASE_SERVICE_KEY")
        return

    start_date, end_date = resolve_range()
    print(f"生成月報：{start_date} ~ {end_date}")

    weeks = fetch_weekly_digests(start_date, end_date)
    if not weeks:
        print("這個月沒有已生成的週報，無法疊成月報。")
        print("請先確認該月的週報都已生成（Weekly Report workflow）。")
        return
    print(f"讀到 {len(weeks)} 份週報，送 LLM 綜合…")

    # 組裝送進 LLM 的內容：只取週報的判斷性欄位，不含必讀清單（省 token）
    packed = []
    total_items = 0
    for w in weeks:
        d = w.get("data_json") or {}
        total_items += int(d.get("total") or 0)
        packed.append({
            "期間": f"{w.get('start_date')} ~ {w.get('end_date')}",
            "總覽": d.get("overview", ""),
            "關鍵洞察": d.get("key_insight", ""),
            "主題趨勢": d.get("themes", []),
            "跨則連結": d.get("connections", []),
        })

    user_content = (f"本月期間：{start_date} ~ {end_date}\n"
                    f"這個月共有 {len(weeks)} 份週報、涵蓋約 {total_items} 則情報。\n"
                    f"以下是各週週報內容（依時間順序）：\n"
                    f"{json.dumps(packed, ensure_ascii=False)}")

    try:
        raw = llm.generate(MONTHLY_SYSTEM_PROMPT, user_content,
                           temperature=0.5, max_tokens=8000)
    except Exception as e:
        print(f"LLM 生成失敗: {e}")
        return

    data = llm.extract_json(raw)
    if data is None:
        print("❌ LLM 回傳無法解析為 JSON，跳過存檔。")
        print(f"   回應總長度：{len(raw or '')} 字")
        print("   開頭 400 字:", (raw or "")[:400])
        print("   結尾 400 字:", (raw or "")[-400:])
        return

    result = {
        "start": start_date,
        "end": end_date,
        "weeks": len(weeks),
        "total": total_items,
        "week_ranges": [f"{w.get('start_date')} ~ {w.get('end_date')}" for w in weeks],
        "overview": data.get("overview", ""),
        "key_insight": data.get("key_insight", ""),
        "themes": data.get("themes", []),
        "shifts": data.get("shifts", []),
        "outlook": data.get("outlook", ""),
    }

    if save_digest("monthly", start_date, end_date, result):
        print(f"✅ 月報已生成並存檔（疊合 {len(weeks)} 份週報、"
              f"{len(result['themes'])} 個主題、{len(result['shifts'])} 條態勢轉變）")
    else:
        print("❌ 月報存檔失敗")


if __name__ == "__main__":
    generate()

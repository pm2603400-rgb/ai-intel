"""
Supabase 版資料存取層（供 GitHub Actions 抓取腳本使用）。
提供與原本 db.py 相同的函式介面（already_have / save_report / query_reports），
底層改為呼叫 Supabase REST API。

金鑰一律從環境變數讀取（GitHub Actions secrets / 本機環境），程式碼零機密：
  SUPABASE_URL          你的 Supabase 專案 URL
  SUPABASE_SERVICE_KEY  service_role key（可寫入；只放後端，絕不進前端）
"""
import os
import json
import time
import requests

# ── 設定（環境變數，零硬編碼）──
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
REQUEST_TIMEOUT = 30

_REST = f"{SUPABASE_URL}/rest/v1" if SUPABASE_URL else ""


def _headers(extra=None):
    h = {
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _check_config():
    if not SUPABASE_URL or not SERVICE_KEY:
        raise RuntimeError(
            "缺少 SUPABASE_URL 或 SUPABASE_SERVICE_KEY 環境變數，無法連線 Supabase。")


def init_db():
    """相容用：Supabase 的表已由 schema 建好，這裡只驗證設定。"""
    _check_config()


def already_have(source, title):
    """這篇（同來源同標題）是否已存在。用於增量抓取跳過。"""
    _check_config()
    try:
        # 用 source + title 精確查詢，只取 id，limit 1
        params = {
            "select": "id",
            "source": f"eq.{source}",
            "title": f"eq.{title}",
            "limit": "1",
        }
        r = requests.get(f"{_REST}/reports", headers=_headers(),
                         params=params, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            return len(r.json()) > 0
        # 查詢失敗時保守回 False（寧可重複處理，也不要漏抓）；但印出原因
        print(f"    ⚠️ already_have 查詢異常 HTTP {r.status_code}：{r.text[:120]}")
        return False
    except requests.exceptions.Timeout:
        print("    ⚠️ already_have 查詢逾時，視為未存在（可能重複處理一次）。")
        return False
    except Exception as e:
        print(f"    ⚠️ already_have 查詢錯誤：{e}")
        return False


def save_report(run_date, pub_date, source, title, title_zh,
                source_url, summary_md, skill_md, category="一般資訊",
                use_cases=None, application_patterns=""):
    """寫入一筆情報（同 source+title 已存在則合併，不重複）。"""
    _check_config()
    row = {
        "run_date": run_date,
        "pub_date": pub_date,
        "source": source,
        "category": category,
        "title": title,
        "title_zh": title_zh,
        "source_url": source_url,
        "summary_md": summary_md,
        "skill_md": skill_md,
        "use_cases": use_cases or [],          # jsonb，直接傳陣列
        "application_patterns": application_patterns or "",
    }
    headers = _headers({"Prefer": "resolution=merge-duplicates,return=minimal"})
    try:
        r = requests.post(f"{_REST}/reports", headers=headers,
                         json=[row], timeout=REQUEST_TIMEOUT)
        if r.status_code in (200, 201, 204):
            return True
        print(f"    ✘ save_report 失敗 HTTP {r.status_code}：{r.text[:150]}")
        return False
    except requests.exceptions.Timeout:
        print("    ✘ save_report 逾時。")
        return False
    except Exception as e:
        print(f"    ✘ save_report 錯誤：{e}")
        return False


def query_reports(pub_date=None, source=None, category=None):
    """查詢情報（供匯出 markdown 用）。回傳 list of dict。"""
    _check_config()
    params = {
        "select": "*",
        "order": "pub_date.desc.nullslast,created_at.desc",
    }
    if pub_date and pub_date != "全部":
        params["pub_date"] = f"eq.{pub_date}"
    if source and source != "全部":
        params["source"] = f"eq.{source}"
    if category and category != "全部":
        params["category"] = f"eq.{category}"
    try:
        r = requests.get(f"{_REST}/reports", headers=_headers(),
                         params=params, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            return r.json()
        print(f"    ⚠️ query_reports 異常 HTTP {r.status_code}：{r.text[:120]}")
        return []
    except Exception as e:
        print(f"    ⚠️ query_reports 錯誤：{e}")
        return []

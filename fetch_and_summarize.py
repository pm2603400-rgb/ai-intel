import os
import re
import datetime
import time
import requests
import feedparser
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import google.generativeai as genai

import db

load_dotenv()
genai.configure(api_key=os.environ["GEMINI_API_KEY"])

MODEL = "gemini-2.5-flash"  # 完整版 Flash（確定免費、比 flash-lite 聰明）。Pro 已需付費故不用。
HEADERS = {"User-Agent": "Mozilla/5.0 (AI-Intel-Bot)"}
FETCH_PER_SOURCE = 25       # 每來源從 RSS 抓幾篇候選（含舊的，靠增量過濾）
DAILY_LIMIT = 20            # 每天最多送進 Gemini 幾篇（免費額度上限，用滿）
TEXT_LIMIT = 6000
SLEEP_SECONDS = 5           # 每篇間隔（避開每分鐘限制）

# 文章分類（Gemini 會從中擇一標註；網站可依此篩選）
CATEGORIES = [
    "模型發布",   # 新模型、新版本、權重釋出
    "研究論文",   # 學術成果、arXiv、技術報告
    "工具與技巧", # 可操作的方法、實用技術、開發技巧
    "產業動態",   # 公司、政策、市場、募資、合作
    "應用案例",   # 實際落地、產品、整合應用
    "一般資訊",   # 其他不易歸類者
]

# RSS 來源（Meta 因 RSS 不穩定已移除；arXiv 用官方 rss.arxiv.org）
RSS_SOURCES = {
    "arXiv (cs.AI)": "https://rss.arxiv.org/rss/cs.AI",
    "arXiv (cs.LG)": "https://rss.arxiv.org/rss/cs.LG",
    "Hugging Face Daily Papers": "https://huggingface.co/papers/feed",
    "OpenAI Blog": "https://openai.com/news/rss.xml",
    "Google DeepMind": "https://deepmind.google/blog/rss.xml",
    "Google AI Research": "https://research.google/blog/rss/",
    "BAIR (Berkeley AI)": "https://bair.berkeley.edu/blog/feed.xml",
    "MIT News (AI)": "https://news.mit.edu/rss/topic/artificial-intelligence2",
}


def _extract_arxiv_id(link, title):
    """從連結或標題抓出 arXiv 編號，如 2606.19464。抓不到回 None。"""
    m = re.search(r"(\d{4}\.\d{4,5})", f"{link} {title}")
    return m.group(1) if m else None


def _arxiv_date_from_id(arxiv_id):
    """從編號前 4 碼推年月（YYMM），回該月 1 日。當 API 失敗時的備援。"""
    if not arxiv_id:
        return None
    yy, mm = arxiv_id[:2], arxiv_id[2:4]
    try:
        year, month = 2000 + int(yy), int(mm)
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}-01"
    except Exception:
        pass
    return None


_arxiv_date_cache = {}


def _arxiv_exact_date(arxiv_id):
    """用 arXiv 官方 API 查論文「首次提交日」（精確到日）。
    成功回 YYYY-MM-DD；失敗回 None（呼叫端會退回年月）。"""
    if not arxiv_id:
        return None
    if arxiv_id in _arxiv_date_cache:
        return _arxiv_date_cache[arxiv_id]
    result = None
    try:
        url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}"
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            # API 回傳 Atom XML，第一個 <published> 是首次提交日
            m = re.search(r"<published>(\d{4}-\d{2}-\d{2})", r.text)
            if m:
                result = m.group(1)
        time.sleep(1)  # 對 arXiv API 友善，避免太頻繁
    except Exception:
        result = None
    _arxiv_date_cache[arxiv_id] = result
    return result


def _parse_pub_date(entry, source_name=""):
    link = entry.get("link", "")
    title = entry.get("title", "")
    # arXiv：RSS 的 published 是「feed 產生日」不可靠。三層防護取真正提交日：
    #   1) 官方 API 查精確提交日（精確到日）
    #   2) 失敗則用編號推年月
    #   3) 再失敗回 None（最後由呼叫端退回執行日）
    if "arXiv" in source_name:
        aid = _extract_arxiv_id(link, title)
        if aid:
            exact = _arxiv_exact_date(aid)
            if exact:
                return exact
            approx = _arxiv_date_from_id(aid)
            if approx:
                return approx
    # 其他來源：RSS 的 published / updated 通常準確
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if t:
        try:
            return time.strftime("%Y-%m-%d", t)
        except Exception:
            return None
    return None


def fetch_rss(name, url):
    items = []
    feed = feedparser.parse(url)
    for entry in feed.entries[:FETCH_PER_SOURCE]:
        summary = entry.get("summary", "")
        text = BeautifulSoup(summary, "lxml").get_text(" ", strip=True)
        items.append({
            "title": entry.get("title", "(無標題)").strip(),
            "link": entry.get("link", ""),
            "text": text[:TEXT_LIMIT],
            "pub_date": _parse_pub_date(entry, name),
        })
    return items


def fetch_anthropic_news():
    items = []
    try:
        r = requests.get("https://www.anthropic.com/news",
                         headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "lxml")
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/news/" not in href:
                continue
            full = href if href.startswith("http") else "https://www.anthropic.com" + href
            title = a.get_text(" ", strip=True)
            if not title or full in seen:
                continue
            seen.add(full)
            items.append({"title": title, "link": full,
                          "text": title, "pub_date": None})
            if len(items) >= FETCH_PER_SOURCE:
                break
    except Exception as e:
        print(f"[Anthropic] 抓取失敗：{e}")
    return items


SYSTEM_PROMPT = """你是一位專精於 AI 領域的台灣資深技術編輯。你的任務是把英文 AI 科技資訊，轉化為高品質的台灣繁體中文情報。

【語言規範｜最高優先】
1. 一律使用台灣慣用的繁體中文書寫，禁止任何簡體字、禁止中國大陸用語。
2. 專有名詞請使用台灣慣用譯法，例如：
   - optimization → 最佳化（不可寫「優化」）
   - model training → 模型訓練
   - default → 預設（不可寫「默認」）
   - quality → 品質（不可寫「質量」）
   - information → 資訊（不可寫「信息」）
   - performance → 效能（不可寫「性能」）
   - throughput / network → 吞吐量 / 網路（不可寫「網絡」）
3. 國際通用的技術縮寫（如 LLM、API、Transformer、RAG）可保留原文。

【防幻覺與溯源規範｜嚴格執行】
1. 只能根據我提供的原始文本撰寫，嚴禁杜撰原文沒有的數據、結論或功能。
2. 摘要與 Skill 的「每一個重點句」結尾都必須加上來源標註：(來源: 出處名稱)。
3. 若原始文本附有 URL，標註格式為：(來源: 出處名稱, https://...)。
4. 若某項資訊無法從原文確認，明確寫出「（原文未提供，無法確認）」，不可自行補充。

【輸出格式｜嚴格遵守】
請只輸出 Markdown，依下列四個區塊輸出，不要有多餘前言：

## 🏷️ 分類
（從以下選項中，挑選最符合本則資訊的「一個」分類，只輸出分類名稱本身一行，不要加說明：
模型發布、研究論文、工具與技巧、產業動態、應用案例、一般資訊）

## 🌐 中文標題
（將英文標題翻成精準、通順的台灣繁體中文標題，只輸出標題本身一行，不要加引號或來源標註）

## 💡 核心摘要
（3~5 點條列，每點皆含來源標註）

## 🛠️ Skill 模組
（將這則資訊轉化為可實際操作的技能 / 方法論，2~4 點。
若包含程式概念，請用 ```語言 程式碼區塊 ``` 呈現，每點皆含來源標註。
若該資訊不適合轉成可操作技能，請寫「此則為一般性資訊，暫無可操作 Skill。」）
"""


def summarize_with_gemini(source_name, title, url, text):
    user_content = f"""請處理以下這則 AI 科技資訊：

來源名稱：{source_name}
文章標題：{title}
原始 URL：{url or "（原文未提供 URL）"}

原始文本：
\"\"\"
{text}
\"\"\""""
    model = genai.GenerativeModel(model_name=MODEL, system_instruction=SYSTEM_PROMPT)
    resp = model.generate_content(
        user_content,
        generation_config=genai.types.GenerationConfig(
            temperature=0.2, max_output_tokens=3000))
    return split_sections(resp.text)


def split_sections(md):
    category, title_zh, summary, skill = "一般資訊", "", md, ""
    m_cat = "## 🏷️ 分類"
    m_title, m_sum, m_skill = "## 🌐 中文標題", "## 💡 核心摘要", "## 🛠️ Skill 模組"

    # 分類
    if m_cat in md:
        after = md.split(m_cat, 1)[1]
        for nxt in (m_title, m_sum, m_skill):
            if nxt in after:
                after = after.split(nxt, 1)[0]
                break
        lines = [l.strip() for l in after.strip().splitlines() if l.strip()]
        if lines:
            raw = lines[0].lstrip("# ").strip()
            # 比對到合法分類；模型若多寫字，取包含關係
            category = next((c for c in CATEGORIES if c in raw), "一般資訊")

    # 中文標題
    if m_title in md:
        after = md.split(m_title, 1)[1]
        for nxt in (m_sum, m_skill):
            if nxt in after:
                after = after.split(nxt, 1)[0]
                break
        lines = [l.strip() for l in after.strip().splitlines() if l.strip()]
        title_zh = lines[0] if lines else ""

    # 摘要 / Skill
    body = md
    if m_sum in body:
        body = body.split(m_sum, 1)[1]
    if m_skill in body:
        head, tail = body.split(m_skill, 1)
        summary, skill = head.strip(), tail.strip()
    else:
        summary = body.strip()
    return category, title_zh, summary, skill


def run():
    db.init_db()
    today = datetime.date.today().isoformat()
    print(f"=== 開始抓取 {today} 的 AI 情報（增量模式）===")

    # 1. 收集所有候選
    all_items = []
    for name, url in RSS_SOURCES.items():
        print(f"[{name}] 抓取中…")
        try:
            got = fetch_rss(name, url)
            print(f"    取得 {len(got)} 篇候選")
            for it in got:
                all_items.append((name, it))
        except Exception as e:
            print(f"[{name}] 失敗：{e}")
        time.sleep(1)
    print("[Anthropic News] 抓取中…")
    anth = fetch_anthropic_news()
    print(f"    取得 {len(anth)} 篇候選")
    for it in anth:
        all_items.append(("Anthropic News", it))

    # 2. 增量過濾：只留沒處理過的
    new_items = [(s, it) for s, it in all_items
                 if not db.already_have(s, it["title"])]
    print(f"\n候選共 {len(all_items)} 篇，其中 {len(new_items)} 篇是新的（未處理過）。")
    if not new_items:
        print("沒有新文章，今天無需呼叫 Gemini。")
        export_markdown(today)
        print("=== 完成 ===")
        return

    # 3. 在每日上限內處理新文章（用滿額度）
    todo = new_items[:DAILY_LIMIT]
    print(f"今日處理上限 {DAILY_LIMIT} 篇，本次將處理 {len(todo)} 篇。")
    if len(new_items) > DAILY_LIMIT:
        print(f"（還有 {len(new_items) - DAILY_LIMIT} 篇新文章，明天會自動接續處理）\n")

    done = 0
    for source_name, it in todo:
        try:
            category, title_zh, summary_md, skill_md = summarize_with_gemini(
                source_name, it["title"], it["link"], it["text"])
            pub_date = it.get("pub_date") or today
            db.save_report(today, pub_date, source_name, it["title"],
                           title_zh, it["link"], summary_md, skill_md, category)
            done += 1
            print(f"  ✔ [{pub_date}|{category}] {source_name} - {it['title'][:32]}")
            time.sleep(SLEEP_SECONDS)
        except Exception as e:
            msg = str(e)
            if "429" in msg or "quota" in msg.lower():
                print(f"  ⏹ 已達今日 Gemini 額度，停止。剩餘明天接續。")
                break
            print(f"  ✘ 失敗 {it['title'][:36]}：{msg[:80]}")

    print(f"\n本次成功處理 {done} 篇。")
    export_markdown(today)
    print("=== 完成 ===")


def export_markdown(run_date):
    from pathlib import Path
    rows = db.query_reports()
    if not rows:
        return
    out = Path(__file__).parent / "data" / f"{run_date}_news.md"
    lines = [f"# AI 科技情報（累積）— 匯出於 {run_date}\n"]
    for r in rows:
        zh = r["title_zh"] or ""
        lines.append(f"\n## 📰 {r['source']}｜{r['title']}")
        if zh:
            lines.append(f"**{zh}**")
        if r["pub_date"]:
            lines.append(f"> 🗓️ 發布日：{r['pub_date']}")
        if r["source_url"]:
            lines.append(f"> 🔗 來源連結：{r['source_url']}\n")
        lines.append(r["summary_md"] or "")
        lines.append("\n" + (r["skill_md"] or ""))
        lines.append("\n---")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"已輸出累積 Markdown：{out}（目前共 {len(rows)} 則）")


if __name__ == "__main__":
    run()

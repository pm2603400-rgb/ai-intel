import os
import re
import datetime
import time
import requests
import feedparser
from bs4 import BeautifulSoup
from dotenv import load_dotenv

import config
import llm
import db

load_dotenv()

MODEL = config.GEMINI_MODEL  # 模型由 config 控制（日後換院內模型只改 config）
HEADERS = {"User-Agent": "Mozilla/5.0 (AI-Intel-Bot)"}
FETCH_PER_SOURCE = 25       # 每來源從 RSS 抓幾篇候選（含舊的，靠增量過濾）
DAILY_LIMIT = 20            # 每天最多送進 Gemini 幾篇（免費額度上限，用滿）
ARXIV_DAILY_CAP = 6         # arXiv 系列每天最多處理幾篇（避免論文海吃光額度，其餘讓給各國媒體）
TEXT_LIMIT = 6000
MIN_TEXT_LEN = 200          # RSS 內文若少於此字數，嘗試抓原文全文；仍不足則跳過該篇
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

# ── 優先來源：各國精選媒體 + AI 大廠官方（每天先處理這些）──
# 格式：來源名稱（含國別標記）: RSS 網址
PRIORITY_SOURCES = {
    # 美國／國際 AI 官方與重點媒體（English）
    "OpenAI Blog 🇺🇸": "https://openai.com/news/rss.xml",
    "Google DeepMind 🇺🇸": "https://deepmind.google/blog/rss.xml",
    "Google AI Research 🇺🇸": "https://research.google/blog/rss/",
    "MIT News (AI) 🇺🇸": "https://news.mit.edu/rss/topic/artificial-intelligence2",
    "BAIR (Berkeley) 🇺🇸": "https://bair.berkeley.edu/blog/feed.xml",
    # 英國（English）
    "AI News 🇬🇧": "https://www.artificialintelligence-news.com/feed/",
    "The Guardian Tech 🇬🇧": "https://www.theguardian.com/uk/technology/rss",
    "The Register 🇬🇧": "https://www.theregister.com/headlines.atom",
    # 加拿大（English）
    "BetaKit 🇨🇦": "https://betakit.com/feed/",
    "The Logic 🇨🇦": "https://thelogic.co/category/news/feed/",
    "CBC Technology 🇨🇦": "https://www.cbc.ca/webfeed/rss/rss-technology",
    # 法國（French）
    "ActuIA 🇫🇷": "https://www.actuia.com/feed/",
    "FrenchWeb 🇫🇷": "https://www.frenchweb.fr/feed",
    "L'Usine Digitale 🇫🇷": "https://www.usine-digitale.fr/rss",
    # 德國（German）
    "Heise 🇩🇪": "https://www.heise.de/rss/heise-atom.xml",
    "Golem 🇩🇪": "https://rss.golem.de/rss.php?feed=RSS2.0",
    # 義大利（Italian）
    "Wired Italia 🇮🇹": "https://www.wired.it/feed/rss",
    "Agenda Digitale 🇮🇹": "https://www.agendadigitale.eu/feed/",
    # 日本（Japanese）
    "ITmedia AI 🇯🇵": "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml",
    "ASCII 🇯🇵": "https://ascii.jp/rss.xml",
    # 俄羅斯（Russian）
    "Habr AI 🇷🇺": "https://habr.com/ru/rss/hub/artificial_intelligence/all/",
    "CNews 🇷🇺": "https://www.cnews.ru/inc/rss/news.xml",
    # 其他官方
    "Hugging Face Papers 🌐": "https://huggingface.co/papers/feed",
}

# ── 大量來源：論文海，放最後處理且設每日上限 ──
BULK_SOURCES = {
    "arXiv (cs.AI)": "https://rss.arxiv.org/rss/cs.AI",
    "arXiv (cs.LG)": "https://rss.arxiv.org/rss/cs.LG",
}

# 合併給其他函式參照（保留相容）
RSS_SOURCES = {**PRIORITY_SOURCES, **BULK_SOURCES}


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
        r = requests.get(url, headers=HEADERS, timeout=10)
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


def fetch_fulltext(url):
    """RSS 內文太短時，抓原文網頁正文。失敗回空字串。"""
    if not url:
        return ""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "lxml")
        # 移除明顯非正文元素
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        # 優先抓 <article>，否則抓 <main>，再不然全頁文字
        node = soup.find("article") or soup.find("main") or soup.body
        if node is None:
            return ""
        text = node.get_text(" ", strip=True)
        return text[:TEXT_LIMIT]
    except Exception:
        return ""


def fetch_rss(name, url):
    items = []
    # 用 requests 帶逾時抓取（避免某來源連線卡死拖垮整體），再交給 feedparser 解析
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            print(f"    （HTTP {r.status_code}，略過）")
            return items
        feed = feedparser.parse(r.content)
    except requests.exceptions.Timeout:
        print(f"    （逾時 15 秒，略過）")
        return items
    except Exception as e:
        print(f"    （連線錯誤：{str(e)[:50]}，略過）")
        return items

    for entry in feed.entries[:FETCH_PER_SOURCE]:
        summary = entry.get("summary", "")
        # 部分 RSS 有 content 欄位更完整，優先用
        if entry.get("content"):
            try:
                summary = entry["content"][0].get("value", summary)
            except Exception:
                pass
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
0. 原始文本可能是英文、法文、德文、義大利文、日文或俄文等任何語言；無論原文是哪種語言，輸出一律為台灣慣用的繁體中文。
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

## 🎯 適用情境
（列出這個 skill 適合用在哪些情境/任務，3~6 個簡短標籤，每個標籤一行、以「- 」開頭。
標籤要具體、貼近實際工作情境，例如「- 大量圖片去背」「- 自動產生會議記錄」「- 多語言客服回覆」。）

## 🔧 典型應用方式
（這是最重要的一欄：用 2~4 句具體描述「實務上可以怎麼用這個 skill 解決問題」。
重點是幫讀者想到「原來這個能力可以這樣用」，避免只是抽象描述。
請寫成可直接套用的具體做法，而非泛泛而談。若不適用請寫「暫無典型應用方式」。）
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
    raw = llm.generate(SYSTEM_PROMPT, user_content)
    return split_sections(raw)


def _section(md, start_marker, end_markers):
    """擷取某區塊（start_marker 之後、到下一個 end_marker 之前）的內文。"""
    if start_marker not in md:
        return ""
    after = md.split(start_marker, 1)[1]
    for nxt in end_markers:
        if nxt in after:
            after = after.split(nxt, 1)[0]
            break
    return after.strip()


def split_sections(md):
    category, title_zh, summary, skill = "一般資訊", "", md, ""
    use_cases, app_patterns = [], ""
    m_cat = "## 🏷️ 分類"
    m_title = "## 🌐 中文標題"
    m_sum = "## 💡 核心摘要"
    m_skill = "## 🛠️ Skill 模組"
    m_uc = "## 🎯 適用情境"
    m_ap = "## 🔧 典型應用方式"
    all_after = [m_title, m_sum, m_skill, m_uc, m_ap]

    # 分類
    cat_text = _section(md, m_cat, all_after)
    if cat_text:
        lines = [l.strip() for l in cat_text.splitlines() if l.strip()]
        if lines:
            raw = lines[0].lstrip("# ").strip()
            category = next((c for c in CATEGORIES if c in raw), "一般資訊")

    # 中文標題
    title_text = _section(md, m_title, [m_sum, m_skill, m_uc, m_ap])
    if title_text:
        lines = [l.strip() for l in title_text.splitlines() if l.strip()]
        title_zh = lines[0] if lines else ""

    # 摘要
    summary = _section(md, m_sum, [m_skill, m_uc, m_ap]) or summary

    # Skill 模組
    skill = _section(md, m_skill, [m_uc, m_ap])

    # 適用情境（解析成陣列）
    uc_text = _section(md, m_uc, [m_ap])
    if uc_text:
        for l in uc_text.splitlines():
            l = l.strip().lstrip("-*・•").strip()
            if l and "暫無" not in l:
                use_cases.append(l)

    # 典型應用方式
    ap_text = _section(md, m_ap, [])
    if ap_text and "暫無" not in ap_text:
        app_patterns = ap_text

    return category, title_zh, summary, skill, use_cases, app_patterns


def run():
    db.init_db()
    today = datetime.date.today().isoformat()
    print(f"=== 開始抓取 {today} 的 AI 情報（增量模式）===")

    # 1a. 收集「優先來源」候選（各國精選媒體 + 官方）
    priority_items = []
    for name, url in PRIORITY_SOURCES.items():
        print(f"[優先] [{name}] 抓取中…")
        try:
            got = fetch_rss(name, url)
            print(f"    取得 {len(got)} 篇候選")
            for it in got:
                priority_items.append((name, it))
        except Exception as e:
            print(f"[{name}] 失敗：{e}")
        time.sleep(1)
    # Anthropic（HTML 爬取）也算優先官方來源
    print("[優先] [Anthropic News] 抓取中…")
    try:
        anth = fetch_anthropic_news()
        print(f"    取得 {len(anth)} 篇候選")
        for it in anth:
            priority_items.append(("Anthropic News 🌐", it))
    except Exception as e:
        print(f"[Anthropic News] 失敗：{e}")

    # 1b. 收集「大量來源」候選（arXiv 論文海）
    bulk_items = []
    for name, url in BULK_SOURCES.items():
        print(f"[大量] [{name}] 抓取中…")
        try:
            got = fetch_rss(name, url)
            print(f"    取得 {len(got)} 篇候選")
            for it in got:
                bulk_items.append((name, it))
        except Exception as e:
            print(f"[{name}] 失敗：{e}")
        time.sleep(1)

    # 2. 增量過濾：只留沒處理過的
    new_priority = [(s, it) for s, it in priority_items
                    if not db.already_have(s, it["title"])]
    new_bulk = [(s, it) for s, it in bulk_items
                if not db.already_have(s, it["title"])]
    total_new = len(new_priority) + len(new_bulk)
    print(f"\n候選：優先 {len(priority_items)} / 大量 {len(bulk_items)} 篇。"
          f"其中新的：優先 {len(new_priority)} / 大量 {len(new_bulk)} 篇。")
    if total_new == 0:
        print("沒有新文章，今天無需呼叫 Gemini。")
        export_markdown(today)
        print("=== 完成 ===")
        return

    # 3. 組裝今日處理清單：優先來源全進（在前），arXiv 最多取 ARXIV_DAILY_CAP 篇（在後）
    #    這樣即使 arXiv 量大，也不會吃光額度、各國媒體一定排得進。
    bulk_take = new_bulk[:ARXIV_DAILY_CAP]
    ordered = new_priority + bulk_take
    todo = ordered[:DAILY_LIMIT]
    n_pri = sum(1 for s, _ in todo if not s.startswith("arXiv"))
    n_arx = len(todo) - n_pri
    print(f"今日上限 {DAILY_LIMIT} 篇 → 本次處理 {len(todo)} 篇"
          f"（優先 {n_pri}、arXiv {n_arx}；arXiv 每日上限 {ARXIV_DAILY_CAP}）。")
    remaining = total_new - len(todo)
    if remaining > 0:
        print(f"（還有 {remaining} 篇新文章，下次自動接續處理）\n")

    done = 0
    skipped = 0
    for source_name, it in todo:
        # 內文太短 → 嘗試抓原文全文；仍不足則跳過（避免空摘要、不浪費額度）
        if len(it["text"]) < MIN_TEXT_LEN:
            full = fetch_fulltext(it["link"])
            if len(full) >= MIN_TEXT_LEN:
                it["text"] = full
            else:
                skipped += 1
                print(f"  ⊘ 內文不足且抓不到全文，跳過：{it['title'][:32]}")
                continue
        try:
            (category, title_zh, summary_md, skill_md,
             use_cases, app_patterns) = summarize_with_gemini(
                source_name, it["title"], it["link"], it["text"])
            pub_date = it.get("pub_date") or today
            db.save_report(today, pub_date, source_name, it["title"],
                           title_zh, it["link"], summary_md, skill_md, category,
                           use_cases=use_cases, application_patterns=app_patterns)
            done += 1
            print(f"  ✔ [{pub_date}|{category}] {source_name} - {it['title'][:32]}")
            time.sleep(SLEEP_SECONDS)
        except Exception as e:
            msg = str(e)
            if "429" in msg or "quota" in msg.lower():
                print(f"  ⏹ 已達今日 Gemini 額度，停止。剩餘明天接續。")
                break
            print(f"  ✘ 失敗 {it['title'][:36]}：{msg[:80]}")

    print(f"\n本次成功處理 {done} 篇" + (f"，跳過 {skipped} 篇（內文不足）。" if skipped else "。"))
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

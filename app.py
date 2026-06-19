import datetime
import streamlit as st
import db

st.set_page_config(page_title="AI 科技情報與 Skill 知識庫",
                   page_icon="🧠", layout="wide")

st.markdown("""
<style>
.report-card { background:#fff; border:1px solid #e6e6e6; border-radius:14px;
    padding:22px 26px; margin-bottom:22px; box-shadow:0 2px 10px rgba(0,0,0,0.04); }
.badge-row { margin-bottom:8px; }
.source-badge,.skill-badge,.date-badge { display:inline-block; font-size:13px;
    font-weight:600; padding:4px 12px; border-radius:999px; margin-right:6px; }
.source-badge { background:#eef2ff; color:#4338ca; }
.date-badge { background:#f1f5f9; color:#475569; }
.skill-yes { background:#dcfce7; color:#15803d; }
.skill-no { background:#f3f4f6; color:#6b7280; }
.card-title-en { font-size:19px; font-weight:700; margin:4px 0 2px; }
.card-title-zh { font-size:16px; font-weight:600; color:#374151; margin:0 0 6px; }
.card-link { font-size:13px; color:#6b7280; word-break:break-all; }
</style>
""", unsafe_allow_html=True)

db.init_db()


def has_skill(r):
    s = r["skill_md"] or ""
    return bool(s.strip()) and "一般性資訊" not in s


def render_card(r):
    st.markdown('<div class="report-card">', unsafe_allow_html=True)
    skill_badge = ('<span class="skill-badge skill-yes">🛠️ 含可操作 Skill</span>'
                   if has_skill(r)
                   else '<span class="skill-badge skill-no">📄 一般資訊</span>')
    pub = r["pub_date"] or r["run_date"]
    st.markdown(f'<div class="badge-row">'
                f'<span class="source-badge">📰 {r["source"]}</span>'
                f'<span class="date-badge">🗓️ 發布 {pub}</span>'
                f'{skill_badge}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="card-title-en">{r["title"]}</div>', unsafe_allow_html=True)
    if r["title_zh"]:
        st.markdown(f'<div class="card-title-zh">{r["title_zh"]}</div>', unsafe_allow_html=True)
    if r["source_url"]:
        st.markdown(f'<div class="card-link">🔗 {r["source_url"]}</div>', unsafe_allow_html=True)
    st.markdown("#### 💡 核心摘要")
    st.markdown(r["summary_md"] or "_（無摘要）_")
    with st.expander("🛠️ 展開 Skill 模組"):
        st.markdown(r["skill_md"] or "_（無 Skill 內容）_")
    st.markdown('</div>', unsafe_allow_html=True)


def build_skill_export(rows, suffix):
    lines = [f"# 🛠️ AI Skill 模組彙整 — {suffix}\n",
             f"> 匯出時間：{datetime.datetime.now():%Y-%m-%d %H:%M}　|　共 {len(rows)} 則\n"]
    for r in rows:
        zh = f"（{r['title_zh']}）" if r["title_zh"] else ""
        lines.append(f"\n## {r['source']}｜{r['title']} {zh}")
        meta = f"> 🗓️ 發布 {r['pub_date'] or r['run_date']}"
        if r["source_url"]:
            meta += f"　|　🔗 {r['source_url']}"
        lines.append(meta)
        lines.append("\n" + (r["skill_md"] or ""))
        lines.append("\n---")
    return "\n".join(lines)


st.sidebar.title("🧠 AI 情報庫")
st.sidebar.caption("自動化 AI 科技情報與 Skill 知識庫")

pub_dates = db.list_pub_dates()
if not db.list_run_dates():
    st.title("🧠 AI 科技情報與 Skill 知識庫")
    st.info("目前資料庫為空，請先執行 `python fetch_and_summarize.py` 抓取資料。")
    st.stop()

keyword = st.sidebar.text_input("🔍 關鍵字搜尋", placeholder="中英標題 / 摘要 / Skill")
st.sidebar.markdown("---")
sel_pub = st.sidebar.selectbox("🗓️ 發布日期", ["全部"] + pub_dates)
sel_source = st.sidebar.selectbox("📡 情報來源", ["全部"] + db.list_sources())
only_skill = st.sidebar.checkbox("只看含 Skill 的情報", value=False)
st.sidebar.markdown("---")
st.sidebar.caption("增量累積模式：每天只抓新文章，資料永久保留。\n卡片依發布日由新到舊排序。")

if keyword.strip():
    st.title("🔍 搜尋結果")
    results = db.search_reports(keyword.strip())
    if only_skill:
        results = [r for r in results if has_skill(r)]
    st.caption(f"關鍵字「{keyword.strip()}」共 {len(results)} 則")
    sk = [r for r in results if has_skill(r)]
    if sk:
        st.download_button("📥 匯出搜尋結果的 Skill 全文 (.md)",
                           data=build_skill_export(sk, f"搜尋_{keyword.strip()}"),
                           file_name=f"skills_search.md", mime="text/markdown")
    st.markdown("---")
    for r in results:
        render_card(r)
    st.stop()

st.title("🧠 AI 科技情報與 Skill 知識庫")
st.markdown(f"### 🗓️ 發布日：{sel_pub}　|　來源：{sel_source}　（依發布日排序）")

reports = db.query_reports(
    pub_date=None if sel_pub == "全部" else sel_pub,
    source=sel_source)
if only_skill:
    reports = [r for r in reports if has_skill(r)]

c1, c2 = st.columns(2)
with c1:
    sk = db.query_all_skills(pub_date=None if sel_pub == "全部" else sel_pub, source=sel_source)
    if sk:
        st.download_button(f"📥 匯出本頁 Skill 全文 (.md)　共 {len(sk)} 則",
                           data=build_skill_export(sk, sel_pub),
                           file_name="skills_page.md", mime="text/markdown")
with c2:
    allsk = db.query_all_skills()
    if allsk:
        st.download_button(f"📦 匯出全部 Skill 知識庫 (.md)　共 {len(allsk)} 則",
                           data=build_skill_export(allsk, "全部"),
                           file_name="skills_all.md", mime="text/markdown")

st.caption(f"本頁共 {len(reports)} 則情報")
st.markdown("---")
for r in reports:
    render_card(r)

import json
import datetime
import streamlit as st
import db

st.set_page_config(page_title="AI 科技情報與 Skill 知識庫",
                   page_icon="🧠", layout="wide")

CAT_COLORS = {
    "模型發布":   ("#fef3c7", "#b45309"),
    "研究論文":   ("#dbeafe", "#1d4ed8"),
    "工具與技巧": ("#dcfce7", "#15803d"),
    "產業動態":   ("#fce7f3", "#be185d"),
    "應用案例":   ("#ede9fe", "#6d28d9"),
    "一般資訊":   ("#f3f4f6", "#6b7280"),
}

st.markdown("""
<style>
.report-card { background:#fff; border:1px solid #e6e6e6; border-radius:14px;
    padding:22px 26px; margin-bottom:22px; box-shadow:0 2px 10px rgba(0,0,0,0.04); }
.badge-row { margin-bottom:8px; }
.source-badge,.skill-badge,.date-badge,.cat-badge,.uc-badge { display:inline-block; font-size:13px;
    font-weight:600; padding:4px 12px; border-radius:999px; margin-right:6px; margin-bottom:4px; }
.source-badge { background:#eef2ff; color:#4338ca; }
.date-badge { background:#f1f5f9; color:#475569; }
.skill-yes { background:#dcfce7; color:#15803d; }
.skill-no { background:#f3f4f6; color:#6b7280; }
.uc-badge { background:#fef9c3; color:#854d0e; font-weight:500; }
.card-title-en { font-size:19px; font-weight:700; margin:4px 0 2px; }
.card-title-zh { font-size:16px; font-weight:600; color:#374151; margin:0 0 6px; }
.card-link { font-size:13px; color:#6b7280; word-break:break-all; }
.ap-box { background:#f8fafc; border-left:4px solid #6366f1; padding:12px 16px;
    border-radius:6px; margin:8px 0; font-size:14px; color:#334155; }
.match-how { background:#eff6ff; border-left:4px solid #3b82f6; padding:12px 16px;
    border-radius:6px; margin:6px 0; }
.skill-mini { background:#fff; border:1px solid #e6e6e6; border-radius:12px;
    padding:16px 20px; margin-bottom:16px; box-shadow:0 1px 6px rgba(0,0,0,0.04); }
.mini-title { font-size:16px; font-weight:700; color:#1e293b; margin-bottom:4px; }
.ap-hero { background:linear-gradient(135deg,#eef2ff,#fdf4ff); border:1px solid #ddd6fe;
    border-radius:10px; padding:14px 18px; margin:10px 0; font-size:15px;
    color:#4338ca; line-height:1.6; }
.ap-hero b { color:#6d28d9; }
.uc-hero { display:inline-block; background:#fef9c3; color:#854d0e; font-size:13px;
    font-weight:600; padding:3px 12px; border-radius:999px; margin:2px 4px 2px 0; }
.no-ap { color:#9ca3af; font-size:13px; font-style:italic; margin:8px 0; }
</style>
""", unsafe_allow_html=True)

db.init_db()


def has_skill(r):
    s = r["skill_md"] or ""
    return bool(s.strip()) and "一般性資訊" not in s


def get_use_cases(r):
    try:
        return json.loads(r["use_cases"] or "[]") if "use_cases" in r.keys() else []
    except Exception:
        return []


def cat_badge(category):
    cat = category or "一般資訊"
    bg, fg = CAT_COLORS.get(cat, CAT_COLORS["一般資訊"])
    return (f'<span class="cat-badge" style="background:{bg};color:{fg}">'
            f'🏷️ {cat}</span>')


def render_card(r, show_patterns=True):
    st.markdown('<div class="report-card">', unsafe_allow_html=True)
    skill_badge = ('<span class="skill-badge skill-yes">🛠️ 含可操作 Skill</span>'
                   if has_skill(r)
                   else '<span class="skill-badge skill-no">📄 一般資訊</span>')
    pub = r["pub_date"] or r["run_date"]
    cat = r["category"] if "category" in r.keys() else None
    st.markdown(f'<div class="badge-row">'
                f'{cat_badge(cat)}'
                f'<span class="source-badge">📰 {r["source"]}</span>'
                f'<span class="date-badge">🗓️ 發布 {pub}</span>'
                f'{skill_badge}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="card-title-en">{r["title"]}</div>', unsafe_allow_html=True)
    if r["title_zh"]:
        st.markdown(f'<div class="card-title-zh">{r["title_zh"]}</div>', unsafe_allow_html=True)
    if r["source_url"]:
        st.markdown(f'<div class="card-link">🔗 {r["source_url"]}</div>', unsafe_allow_html=True)

    ucs = get_use_cases(r)
    if ucs:
        badges = "".join(f'<span class="uc-badge">🎯 {u}</span>' for u in ucs)
        st.markdown(f'<div class="badge-row">{badges}</div>', unsafe_allow_html=True)

    if show_patterns and "application_patterns" in r.keys() and r["application_patterns"]:
        st.markdown(f'<div class="ap-box">🔧 <b>典型應用方式</b><br>{r["application_patterns"]}</div>',
                    unsafe_allow_html=True)

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
        cat = r["category"] if "category" in r.keys() and r["category"] else ""
        tag = f"[{cat}] " if cat else ""
        lines.append(f"\n## {tag}{r['source']}｜{r['title']} {zh}")
        meta = f"> 🗓️ 發布 {r['pub_date'] or r['run_date']}"
        if r["source_url"]:
            meta += f"　|　🔗 {r['source_url']}"
        lines.append(meta)
        lines.append("\n" + (r["skill_md"] or ""))
        lines.append("\n---")
    return "\n".join(lines)


# ════════════════════════ 側邊欄 ════════════════════════
st.sidebar.title("🧠 AI 情報庫")
st.sidebar.caption("自動化 AI 科技情報與 Skill 知識庫")

if not db.list_run_dates():
    st.title("🧠 AI 科技情報與 Skill 知識庫")
    st.info("目前資料庫為空，請先執行抓取。")
    st.stop()

page = st.sidebar.radio(
    "功能選單",
    ["📰 情報瀏覽", "🗂️ 分類瀏覽", "🎯 情境查詢"],
    label_visibility="collapsed")
st.sidebar.markdown("---")


# ════════════════════════ 頁面一：情報瀏覽（原本功能）════════════════════════
def page_browse():
    keyword = st.sidebar.text_input("🔍 關鍵字搜尋", placeholder="標題 / 摘要 / Skill / 分類")
    st.sidebar.markdown("---")
    sel_cat = st.sidebar.selectbox("🏷️ 文章類型", ["全部"] + db.list_categories())
    sel_pub = st.sidebar.selectbox("🗓️ 發布日期", ["全部"] + db.list_pub_dates())
    sel_source = st.sidebar.selectbox("📡 情報來源", ["全部"] + db.list_sources())
    only_sk = st.sidebar.checkbox("只看含 Skill 的情報", value=False)

    if keyword.strip():
        st.title("🔍 搜尋結果")
        results = db.search_reports(keyword.strip())
        if only_sk:
            results = [r for r in results if has_skill(r)]
        st.caption(f"關鍵字「{keyword.strip()}」共 {len(results)} 則")
        for r in results:
            render_card(r)
        return

    st.title("🧠 AI 科技情報與 Skill 知識庫")
    st.markdown(f"### 🏷️ {sel_cat}　|　🗓️ {sel_pub}　|　📡 {sel_source}")
    _pub = None if sel_pub == "全部" else sel_pub
    _cat = None if sel_cat == "全部" else sel_cat
    reports = db.query_reports(pub_date=_pub, source=sel_source, category=_cat)
    if only_sk:
        reports = [r for r in reports if has_skill(r)]

    allsk = db.query_all_skills()
    if allsk:
        st.download_button(f"📦 匯出全部 Skill 知識庫 (.md)　共 {len(allsk)} 則",
                           data=build_skill_export(allsk, "全部"),
                           file_name="skills_all.md", mime="text/markdown")
    st.caption(f"本頁共 {len(reports)} 則情報")
    st.markdown("---")
    for r in reports:
        render_card(r)


# ════════════════════════ 頁面二：分類瀏覽 ════════════════════════
def page_by_category():
    st.title("🗂️ 分類瀏覽")
    st.caption("依主分類陳列手邊有哪些可用能力 — 沒有特定問題、想逛逛時用。")

    only_sk = st.sidebar.checkbox("只看含 Skill 的", value=True)
    cats = db.list_categories()
    if not cats:
        st.info("目前還沒有分類資料。")
        return

    for cat in cats:
        rows = db.query_reports(category=cat)
        if only_sk:
            rows = [r for r in rows if has_skill(r)]
        if not rows:
            continue
        bg, fg = CAT_COLORS.get(cat, CAT_COLORS["一般資訊"])
        st.markdown(
            f'<h3><span style="background:{bg};color:{fg};padding:4px 14px;'
            f'border-radius:999px;">🏷️ {cat}</span> '
            f'<span style="font-size:15px;color:#888;">（{len(rows)} 則）</span></h3>',
            unsafe_allow_html=True)
        with st.expander(f"展開「{cat}」的 {len(rows)} 則", expanded=False):
            for r in rows:
                render_inspire_card(r)


def render_inspire_card(r):
    """分類瀏覽用的精簡卡片：標題+連結+可展開 Skill，但「應用情境」最顯眼。"""
    title = r["title_zh"] or r["title"]
    st.markdown('<div class="skill-mini">', unsafe_allow_html=True)
    st.markdown(f'<div class="mini-title">💡 {title}</div>', unsafe_allow_html=True)

    # 情境標籤（顯眼）
    ucs = get_use_cases(r)
    if ucs:
        badges = "".join(f'<span class="uc-hero">🎯 {u}</span>' for u in ucs)
        st.markdown(f'<div>{badges}</div>', unsafe_allow_html=True)

    # 典型應用方式（最顯眼，啟發核心）
    ap = r["application_patterns"] if "application_patterns" in r.keys() else ""
    if ap:
        st.markdown(
            f'<div class="ap-hero">🔧 <b>可以這樣用</b><br>{ap}</div>',
            unsafe_allow_html=True)
    elif not ucs:
        # 完全沒有情境資料（多為舊資料）→ 顯示提示
        st.markdown(
            '<div class="no-ap">⚠️ 此則尚未生成應用情境（可至 Admin 用 LLM 重新生成）</div>',
            unsafe_allow_html=True)

    # 原文連結 + 可展開 Skill（精簡放下方）
    if r["source_url"]:
        st.markdown(f'<div class="card-link">🔗 {r["source_url"]}</div>',
                    unsafe_allow_html=True)
    with st.expander("🛠️ 展開 Skill 模組"):
        st.markdown(r["skill_md"] or "_（無 Skill 內容）_")
    st.markdown('</div>', unsafe_allow_html=True)


# ════════════════════════ 頁面三：情境查詢 ════════════════════════
def page_situation():
    st.title("🎯 情境查詢")
    st.caption("描述你正在做的任務或遇到的痛點，系統會找出哪些 skill 能套用、並說明「怎麼套」。")

    situation = st.text_area(
        "我正在做 / 我的痛點是…",
        placeholder="例如：我要把大量會議錄音整理成逐字稿和重點摘要，但人工聽打太慢了。",
        height=120)

    if st.button("🔍 找出能用的 Skill", type="primary"):
        if not situation.strip():
            st.warning("請先描述你的情境或痛點。")
            return
        with st.spinner("分析中…（呼叫 LLM 比對，會消耗一次額度）"):
            try:
                import skill_match
                results = skill_match.find_matching_skills(situation.strip())
            except Exception as e:
                st.error(f"查詢失敗：{e}")
                return

        if not results:
            st.info("目前知識庫裡找不到明顯適合的 skill。可以換個說法，或之後資料更多再試。")
            return
        if results and results[0][0] == "PARSE_ERROR":
            st.warning("LLM 回傳格式無法解析，以下是原始回應：")
            st.text(results[0][1])
            return

        st.success(f"找到 {len(results)} 個可能適用的 Skill：")
        for row, why, how in results:
            title = row["title_zh"] or row["title"]
            cat = row["category"] or ""
            st.markdown(f"### {title}　{cat_badge(cat)}", unsafe_allow_html=True)
            if why:
                st.markdown(f"**為什麼適合：** {why}")
            if how:
                st.markdown(f'<div class="match-how">🔧 <b>怎麼套用到你的情境</b><br>{how}</div>',
                            unsafe_allow_html=True)
            with st.expander("看這個 Skill 的完整內容"):
                st.markdown(row["skill_md"] or "_（無內容）_")
                if row["source_url"]:
                    st.caption(f"🔗 {row['source_url']}")
            st.markdown("---")


# ════════════════════════ 路由 ════════════════════════
if page == "📰 情報瀏覽":
    page_browse()
elif page == "🗂️ 分類瀏覽":
    page_by_category()
elif page == "🎯 情境查詢":
    page_situation()

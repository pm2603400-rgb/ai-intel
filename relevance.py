"""
相關性守門：在呼叫 Gemini「之前」擋掉與 AI 無關的文章，零 API 成本。
另提供拒絕回應偵測，避免把 Gemini 的拒絕文字當成摘要存進資料庫。

設計重點（v2）：
1. 拉丁字母關鍵字一律用「詞界」比對，避免義大利文 ragazzo 命中 rag、
   法文人名 Claude 命中模型名 Claude 這類誤判。中日韓文用子字串比對。
2. 關鍵字分強弱兩級。強關鍵字（artificial intelligence、OpenAI…）才有決定權；
   弱關鍵字（model、algorithm、GPU…）只能加分，不能單獨讓文章通過。
3. 內文只掃前段，因為網頁側欄與頁尾的「相關文章」充滿 AI 字眼，
   會讓不相關的文章分數虛高。

用法（在 fetch_and_summarize.py）：
    import relevance
    ok, reason = relevance.is_relevant(title, text, url)
    if not ok:
        continue
"""
import re

# ── 門檻設定 ──
BODY_SCAN_LIMIT = 1200   # 內文只掃前這麼多字（避開側欄／頁尾的相關文章雜訊）
MIN_BODY_STRONG = 2      # 標題沒命中時，內文至少要有幾個「不同的」強關鍵字才通過
MAX_WEAK_BONUS = 1       # 弱關鍵字最多加幾分
MIN_SCORE = 2            # 總分門檻（強關鍵字已是主要把關，這裡只擋極弱的邊緣案例）

# ── 強關鍵字：出現就幾乎可確定與 AI 有關 ──
STRONG_ASCII = [
    # 通用術語（多字詞，誤判率低）
    "artificial intelligence", "machine learning", "deep learning",
    "neural network", "generative ai", "large language model",
    "language model", "foundation model", "multimodal model",
    "reinforcement learning", "computer vision", "speech recognition",
    "diffusion model", "transformer model", "training data",
    "fine-tuning", "fine tuning", "context window", "prompt engineering",
    "prompt caching", "function calling", "chain-of-thought",
    "retrieval augmented", "vector search", "ai agent", "ai agents",
    "agentic", "chatbot", "hallucination", "open weights",
    "open-source model", "inference cost", "tokenizer", "quantization",
    # 縮寫（詞界比對，安全）
    "ai", "llm", "llms", "agi", "nlp", "rag", "mcp", "gpt", "chatgpt",
    # 公司／產品／模型名
    "openai", "anthropic", "deepmind", "hugging face", "huggingface",
    "gemini", "claude", "llama", "mistral", "qwen", "deepseek",
    "stable diffusion", "midjourney", "pytorch", "tensorflow",
    "langchain", "copilot", "bedrock", "vertex ai", "whisper",
    "perplexity", "nvidia",
]

STRONG_CJK = [
    # 繁體／簡體中文
    "人工智慧", "人工智能", "機器學習", "机器学习", "深度學習", "深度学习",
    "大型語言模型", "大语言模型", "生成式ai", "生成式 ai", "神經網路",
    "神经网络", "模型訓練", "語言模型", "语言模型",
    # 日文
    "人工知能", "生成ai", "機械学習", "深層学習", "大規模言語モデル",
]

STRONG_OTHER = [
    # 法文
    "intelligence artificielle", "apprentissage automatique",
    "apprentissage profond", "modèle de langage", "réseau de neurones",
    # 德文
    "künstliche intelligenz", "maschinelles lernen", "sprachmodell",
    "neuronales netz", "neuronale netze",
    # 義大利文
    "intelligenza artificiale", "apprendimento automatico",
    "modello linguistico", "rete neurale", "reti neurali",
    # 西班牙／葡萄牙文（部分來源會混）
    "inteligencia artificial", "inteligência artificial",
    # 俄文
    "искусственный интеллект", "машинное обучение", "нейросет",
    "нейронная сеть", "языковая модель",
]

# ── 弱關鍵字：只能加分，不能單獨讓文章通過 ──
WEAK_ASCII = [
    "model", "models", "algorithm", "algorithms", "gpu", "gpus",
    "dataset", "datasets", "benchmark", "inference", "embedding",
    "embeddings", "token", "tokens", "compute", "supercomputer",
    "data center", "datacenter", "automation", "sdk", "api",
]

# ── 網址路徑黑名單：這些欄位天生就不是科技報導 ──
URL_BLOCKLIST = [
    "/commentisfree/", "/opinion/", "/lifeandstyle/", "/sport/",
    "/football/", "/culture/", "/travel/", "/food/", "/games/review",
    "/politics/", "/obituaries/", "/lifestyle/", "/moda/", "/cinema/",
    "/musica/", "/gaming/", "/recensioni/",
]

# ── 標題黑名單：出現就直接排除 ──
TITLE_BLOCKLIST = [
    "recipe", "horoscope", "crossword", "quiz of the",
    "match report", "transfer news", "weather forecast",
    "in edicola",          # 義：雜誌上架公告
    "intervista a",        # 義：人物專訪
    "i migliori",          # 義：「最佳XX推薦」導購文
    "la prova del",        # 義：產品開箱
    "abbonamento",         # 義：訂閱推銷
]

# ── 「延伸閱讀／相關文章」的起始標記 ──
# 這些區塊塞滿其他文章標題，充滿 AI 字眼，會讓不相關的文章分數虛高。
# 計分前先從這些標記處把後面整段剪掉。
RELATED_MARKERS = [
    "leggi anche", "articoli correlati", "potrebbe interessarti",
    "related articles", "related stories", "read more", "more from",
    "you may also like", "recommended for you",
    "lire aussi", "à lire également", "sur le même sujet",
    "mehr zum thema", "auch interessant", "das könnte sie",
    "延伸閱讀", "相關報導", "更多報導", "推薦閱讀",
    "関連記事", "あわせて読みたい",
    "читайте также", "по теме",
]


def _strip_related(text_lower):
    """剪掉「延伸閱讀／相關文章」之後的內容，避免雜訊灌分。"""
    cut = len(text_lower)
    for m in RELATED_MARKERS:
        i = text_lower.find(m)
        if i != -1:
            cut = min(cut, i)
    return text_lower[:cut]


# ── Gemini 拒絕處理時常見的措辭 ──
REFUSAL_MARKERS = [
    "而非 ai 科技", "並非 ai 科技", "不是 ai 科技",
    "與 ai 科技無關", "無法依據您提供", "無法依據你提供",
    "請提供與 ai", "請提供 ai 相關", "我無法處理",
    "無法完成此任務", "不符合您指定的角色",
    "i cannot process", "i'm sorry, but",
]


def _compile(words):
    """把拉丁字母關鍵字編成帶詞界的 regex，避免子字串誤判。"""
    pats = []
    for w in words:
        esc = re.escape(w)
        # 詞界：前後不可以是字母或數字（連字號、空白、標點都算界線）
        pats.append((w, re.compile(r"(?<![a-z0-9])" + esc + r"(?![a-z0-9])", re.I)))
    return pats


_STRONG_PATS = _compile(STRONG_ASCII + STRONG_OTHER)
_WEAK_PATS = _compile(WEAK_ASCII)


def _strong_hits(text_lower):
    """回傳命中的強關鍵字集合（同一詞只算一次）。"""
    hits = {w for w, p in _STRONG_PATS if p.search(text_lower)}
    hits |= {w for w in STRONG_CJK if w in text_lower}
    return hits


def _weak_hits(text_lower):
    return {w for w, p in _WEAK_PATS if p.search(text_lower)}


def is_relevant(title, text, url=""):
    """判斷這篇是否值得送進 Gemini。

    回傳 (是否相關: bool, 原因: str)

    通過條件（兩者滿足其一，且總分達門檻）：
      A. 標題命中至少 1 個強關鍵字
      B. 內文前段命中至少 MIN_BODY_STRONG 個「不同的」強關鍵字
    """
    t = (title or "").lower()
    b = _strip_related((text or "").lower())[:BODY_SCAN_LIMIT]
    u = (url or "").lower()

    # 1) 網址黑名單
    for bad in URL_BLOCKLIST:
        if bad in u:
            return False, "網址屬非科技欄位（" + bad + "）"

    # 2) 標題黑名單
    for bad in TITLE_BLOCKLIST:
        if bad in t:
            return False, "標題屬非科技類型（" + bad + "）"

    # 3) 強關鍵字判定
    th = _strong_hits(t)
    bh = _strong_hits(b)

    if not th and len(bh) < MIN_BODY_STRONG:
        return False, ("AI 關聯不足（標題強命中 " + str(len(th)) +
                       "、內文強命中 " + str(len(bh)) +
                       " < " + str(MIN_BODY_STRONG) + "）")

    # 4) 計分（強關鍵字為主，弱關鍵字只補一點分）
    weak_bonus = min(len(_weak_hits(t) | _weak_hits(b)), MAX_WEAK_BONUS)
    score = len(th) * 3 + len(bh) + weak_bonus

    if score < MIN_SCORE:
        return False, "AI 相關度不足（得分 " + str(score) + " < " + str(MIN_SCORE) + "）"

    where = "標題" if th else "內文"
    return True, "相關（" + where + "命中，得分 " + str(score) + "）"


def is_refusal(summary_md):
    """偵測 Gemini 是否根本沒照格式做、而是回了一段拒絕說明。"""
    s = (summary_md or "").lower()
    if not s.strip():
        return True
    return any(m in s for m in REFUSAL_MARKERS)

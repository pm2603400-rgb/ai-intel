"""
相關性守門：在呼叫 Gemini「之前」擋掉與 AI 無關的文章，零 API 成本。
另提供拒絕回應偵測，避免把 Gemini 的拒絕文字當成摘要存進資料庫。

用法（在 fetch_and_summarize.py）：
    import relevance
    ok, reason = relevance.is_relevant(title, text, url)
    if not ok:
        continue
"""

# ── 門檻設定 ──
TITLE_WEIGHT = 3      # 標題命中一個關鍵字算幾分
BODY_WEIGHT = 1       # 內文命中一個關鍵字算幾分（同一個詞只算一次）
MIN_SCORE = 3         # 總分低於此值視為不相關
BODY_SCAN_LIMIT = 2500  # 內文只掃前面這麼多字，避免長文靠雜訊灌分

# ── AI 相關關鍵字（多語言，因為來源含法／德／義／日／俄文媒體）──
KEYWORDS = [
    # 英文：通用
    "artificial intelligence", "machine learning", "deep learning",
    "neural network", "generative ai", "large language model",
    "foundation model", "multimodal", "inference", "fine-tuning",
    "training data", "benchmark", "open-source model", "open weights",
    "prompt", "embedding", "transformer", "diffusion model",
    "reinforcement learning", "computer vision", "speech recognition",
    "agentic", "ai agent", "chatbot", "copilot", "hallucination",
    "context window", "tokenizer", "quantization", "rag",
    # 英文：縮寫與型號（用空白包夾避免誤判，見 _norm）
    " ai ", " llm ", " llms ", " gpu ", " nlp ", " agi ", " mcp ",
    " gpt ", "gpt-", "llm-",
    # 公司／產品／模型名
    "openai", "anthropic", "deepmind", "hugging face", "nvidia",
    "gemini", "claude", "chatgpt", "llama", "mistral", "qwen",
    "stable diffusion", "midjourney", "pytorch", "tensorflow",
    "langchain", "copilot", "bedrock", "vertex ai", "sora", "whisper",
    "deepseek", "grok", "perplexity",
    # 繁體／簡體中文
    "人工智慧", "人工智能", "機器學習", "机器学习", "深度學習",
    "大型語言模型", "大语言模型", "生成式", "神經網路", "神经网络",
    "模型訓練", "演算法", "算力", "推論", "微調",
    # 日文
    "人工知能", "生成ai", "機械学習", "深層学習", "大規模言語モデル",
    # 法文
    "intelligence artificielle", "apprentissage automatique",
    "apprentissage profond", "modèle de langage", "réseau de neurones",
    # 德文
    "künstliche intelligenz", "maschinelles lernen", "sprachmodell",
    "neuronales netz", "tiefes lernen",
    # 義大利文
    "intelligenza artificiale", "apprendimento automatico",
    "modello linguistico", "rete neurale",
    # 俄文
    "искусственный интеллект", "машинное обучение", "нейросет",
    "нейронная сеть", "языковая модель",
]

# ── 網址路徑黑名單：這些欄位天生就是評論／生活類，不是科技報導 ──
URL_BLOCKLIST = [
    "/commentisfree/",     # Guardian 評論專欄
    "/opinion/",
    "/lifeandstyle/",
    "/sport/",
    "/football/",
    "/culture/",
    "/travel/",
    "/food/",
    "/games/review",
    "/politics/",
    "/obituaries/",
]

# ── 標題黑名單詞：出現就直接排除（明顯非科技內容）──
TITLE_BLOCKLIST = [
    "recipe", "horoscope", "crossword", "quiz of the",
    "match report", "transfer news", "weather forecast",
]

# ── Gemini 拒絕處理時常見的措辭 ──
REFUSAL_MARKERS = [
    "而非 ai 科技", "並非 ai 科技", "不是 ai 科技",
    "與 ai 科技無關", "無法依據您提供", "無法依據你提供",
    "請提供與 ai", "請提供 ai 相關", "我無法處理",
    "無法完成此任務", "不符合您指定的角色",
    "i cannot process", "i'm sorry, but",
]


def _norm(s):
    """轉小寫並在頭尾補空白，讓 ' ai ' 這類空白包夾的關鍵字也能命中邊界。"""
    return " " + (s or "").lower().replace("\n", " ") + " "


def _hits(text, keywords):
    """回傳命中的關鍵字集合（同一詞只算一次）。"""
    return {k for k in keywords if k in text}


def is_relevant(title, text, url=""):
    """判斷這篇是否值得送進 Gemini。

    回傳 (是否相關: bool, 原因: str)
    """
    t = _norm(title)
    b = _norm(text)[:BODY_SCAN_LIMIT]
    u = (url or "").lower()

    # 1) 網址黑名單
    for bad in URL_BLOCKLIST:
        if bad in u:
            return False, f"網址屬非科技欄位（{bad}）"

    # 2) 標題黑名單
    for bad in TITLE_BLOCKLIST:
        if bad in t:
            return False, f"標題含非科技關鍵字（{bad}）"

    # 3) 關鍵字計分
    title_hits = _hits(t, KEYWORDS)
    body_hits = _hits(b, KEYWORDS)
    score = len(title_hits) * TITLE_WEIGHT + len(body_hits) * BODY_WEIGHT

    if score < MIN_SCORE:
        return False, f"AI 相關度不足（得分 {score} < {MIN_SCORE}）"

    return True, f"相關（得分 {score}）"


def is_refusal(summary_md):
    """偵測 Gemini 是否根本沒照格式做、而是回了一段拒絕說明。"""
    s = (summary_md or "").lower()
    if not s.strip():
        return True
    return any(m in s for m in REFUSAL_MARKERS)

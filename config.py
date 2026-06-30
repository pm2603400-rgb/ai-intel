"""
集中設定檔：LLM 模型與 API 設定。
日後若搬到院內封閉環境或換內部模型，只需改這個檔。
"""
import os

# ── LLM 供應商設定 ──
# provider 目前支援 "gemini"；日後可擴充 "openai_compatible"（院內模型多走這種介面）
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini")

# Gemini 設定
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# OpenAI 相容介面設定（院內模型多採此規格，預留；目前未啟用）
# 例：vLLM / Ollama / 院內 LLM 閘道
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")      # 如 http://internal-llm.vghks.gov.tw/v1
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "")

# ── 生成參數 ──
TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
MAX_OUTPUT_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "3000"))

# ── Admin 密碼（從環境變數讀，不寫死在程式碼）──
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

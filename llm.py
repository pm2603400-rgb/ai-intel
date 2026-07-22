"""
統一的 LLM 呼叫入口。所有需要呼叫模型的地方都經過這裡，
日後換供應商（Gemini → 院內模型）只需改這個檔與 config.py。
"""
import config


def generate(system_prompt, user_content,
             temperature=None, max_tokens=None):
    """送出一次生成請求，回傳純文字。各介面共用。"""
    temp = config.TEMPERATURE if temperature is None else temperature
    maxtok = config.MAX_OUTPUT_TOKENS if max_tokens is None else max_tokens

    if config.LLM_PROVIDER == "gemini":
        return _gemini(system_prompt, user_content, temp, maxtok)
    elif config.LLM_PROVIDER == "openai_compatible":
        return _openai_compatible(system_prompt, user_content, temp, maxtok)
    else:
        raise ValueError(f"未知的 LLM_PROVIDER：{config.LLM_PROVIDER}")


def _gemini(system_prompt, user_content, temperature, max_tokens):
    import google.generativeai as genai
    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=config.GEMINI_MODEL,
        system_instruction=system_prompt)
    resp = model.generate_content(
        user_content,
        generation_config=genai.types.GenerationConfig(
            temperature=temperature, max_output_tokens=max_tokens))
    return resp.text


def _openai_compatible(system_prompt, user_content, temperature, max_tokens):
    """院內模型多採 OpenAI 相容介面，預留。需要時 pip install openai。"""
    from openai import OpenAI
    client = OpenAI(base_url=config.OPENAI_BASE_URL,
                    api_key=config.OPENAI_API_KEY or "not-needed")
    resp = client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=temperature, max_tokens=max_tokens)
    return resp.choices[0].message.content


def extract_json(raw):
    """從 LLM 回應中穩健地抽出 JSON 物件並解析。
    處理 ```json 圍欄、前後雜訊等情況。失敗回 None。"""
    import json
    if not raw:
        return None
    text = raw.strip()
    # 去掉 markdown 圍欄
    if "```" in text:
        # 取圍欄內的內容
        parts = text.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{") or p.startswith("["):
                text = p
                break
    # 抓第一個 { 到最後一個 }（最穩健）
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except Exception:
        return None

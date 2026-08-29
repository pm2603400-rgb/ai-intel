"""
統一的 LLM 呼叫入口。所有需要呼叫模型的地方都經過這裡，
日後換供應商（Gemini → 院內模型）只需改這個檔與 config.py。
"""
import json

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


def _strip_fence(text):
    """去掉 markdown 圍欄，取出第一段看起來像 JSON 的內容。"""
    t = (text or "").strip()
    if "```" in t:
        for p in t.split("```"):
            p = p.strip()
            if p.lower().startswith("json"):
                p = p[4:].strip()
            if p.startswith("{") or p.startswith("["):
                return p
    return t


def _repair_truncated(text):
    """回應被 max_tokens 截斷時的補救。

    掃描整段文字，找出最後一個「安全切點」（某個完整的值剛結束的位置），
    截斷到那裡，再補上所有未閉合的括號。
    這樣即使 JSON 在字串中途被切斷，也能救回前面已完成的欄位。
    """
    stack = []          # 待閉合的括號
    in_str = False
    esc = False
    last_safe = -1
    safe_stack = []
    LITERAL_END = set('0123456789eE.+-truefalsenul')

    for i, c in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
                # 只有「值」才算安全切點；若是「鍵」（後面接冒號）不算。
                # 若字串就在結尾，無法判斷是鍵還是值，保守不採用。
                j = i + 1
                while j < len(text) and text[j] in ' \n\r\t':
                    j += 1
                if j < len(text) and text[j] in ',}]':
                    last_safe, safe_stack = i, list(stack)
            continue

        if c == '"':
            in_str = True
        elif c in '{[':
            stack.append('}' if c == '{' else ']')
        elif c in '}]':
            if stack:
                stack.pop()
            last_safe, safe_stack = i, list(stack)
        elif c == ',':
            last_safe, safe_stack = i - 1, list(stack)
        elif c in LITERAL_END:
            nxt = text[i + 1] if i + 1 < len(text) else ''
            if nxt in ',}] \n\r\t':
                last_safe, safe_stack = i, list(stack)

    if last_safe < 0:
        return None
    cand = text[:last_safe + 1].rstrip().rstrip(',')
    cand += ''.join(reversed(safe_stack))
    try:
        return json.loads(cand)
    except Exception:
        return None


def extract_json(raw):
    """從 LLM 回應中穩健地抽出 JSON 物件並解析。

    依序嘗試：
      1. 去掉 markdown 圍欄後直接解析
      2. 取第一個 { 到最後一個 } 之間的內容解析
      3. 截斷補救（回應被 max_tokens 切斷時，救回已完成的欄位）
    全部失敗回 None。
    """
    if not raw:
        return None

    text = _strip_fence(raw)

    # 先試最單純的情況
    try:
        return json.loads(text)
    except Exception:
        pass

    # 取第一個 { 到最後一個 }
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e != -1 and e > s:
        try:
            return json.loads(text[s:e + 1])
        except Exception:
            pass

    # 從第一個 { 開始做截斷補救
    if s != -1:
        text = text[s:]
    return _repair_truncated(text)

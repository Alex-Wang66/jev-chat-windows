# -*- coding: utf-8 -*-
"""起草 3 条候选回复。生成式模型（默认 DeepSeek），走 OpenRouter chat completions。

跟 jev_client 一样：只用 stdlib urllib、复用 OPENROUTER_API_KEY、绝不把 key 打进日志。
盲起草——不喂 Jev 判断，让生成模型自己读对话；排序交给 Jev。一次请求，省时省钱。
"""
from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request

try:  # 当模块导入 / 当脚本直接跑 都能用
    from .jev_client import JevError, _api_key, redact_secrets  # 复用 key 读取与脱敏
except ImportError:
    from jev_client import JevError, _api_key, redact_secrets

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "deepseek/deepseek-chat-v3.1"
MAX_RETRIES = 3

SYSTEM = (
    "You draft candidate replies for a private chat-assist tool. "
    "The user is the speaker 'me'; 'her' is the other person (any gender). "
    "Read the whole thread, then write EXACTLY 3 candidate next messages that 'me' could send. "
    "Make the three genuinely different in approach (e.g. one warm/acknowledging, "
    "one that takes responsibility or explains, one that offers a concrete next step). "
    "Write in natural, casual Chinese as real people text on WeChat — short, human, no formal tone, "
    "no emoji spam, no quotation marks around the whole line. "
    "Never propose sending money, transfers, or red packets. "
    'Output ONLY a JSON array of exactly 3 strings, e.g. ["...","...","..."]. No other text.'
)


def _parse_three(content: str) -> list[str]:
    """从模型输出里抠出 3 条。先按 JSON 数组,失败再退化按行。"""
    content = content.strip()
    # 去掉可能的 ```json 围栏
    content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.MULTILINE).strip()
    try:
        arr = json.loads(content)
        if isinstance(arr, list) and len(arr) >= 3:
            return [str(x).strip() for x in arr[:3]]
    except Exception:
        pass
    # 退化：逐行,去掉行首编号/符号
    lines = [re.sub(r"^\s*(?:\d+[.)、]|[-*])\s*", "", ln).strip().strip('"')
             for ln in content.splitlines() if ln.strip()]
    lines = [ln for ln in lines if ln]
    if len(lines) >= 3:
        return lines[:3]
    raise JevError(f"起草结果解析不出 3 条: {content[:200]!r}")


def draft_candidates(messages: list, relationship: str,
                     model: str = DEFAULT_MODEL, timeout: float = 30) -> list[str]:
    """messages: [(from, text)] from ∈ {her, me}; 返回 3 条中文候选。"""
    transcript = "\n".join(f"{w}: {t}" for w, t in
                           ((m[0], m[1]) if not isinstance(m, dict) else (m["from"], m["text"])
                            for m in messages[-10:]))
    user = f"relationship: {relationship}\n\n对话（最后一条是最新）:\n{transcript}"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": user}],
        "temperature": 0.8,
    }, ensure_ascii=False).encode("utf-8")

    key = _api_key()
    for attempt in range(MAX_RETRIES + 1):
        req = urllib.request.Request(CHAT_URL, data=payload, method="POST", headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json; charset=utf-8",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return _parse_three(body["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 529) and attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
            detail = redact_secrets(exc.read().decode("utf-8", "replace"))[:400]
            raise JevError(f"起草 HTTP {exc.code}: {detail}", exc.code) from None
        except (TimeoutError, socket.timeout):
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
            raise JevError(f"起草请求超时 {timeout}s") from None
        except urllib.error.URLError as exc:
            raise JevError(f"起草请求失败: {redact_secrets(getattr(exc, 'reason', exc))}") from None
    raise JevError("起草：重试用尽")


if __name__ == "__main__":
    # ponytail: 只测解析器（不联网）。解析是这里唯一会坏的非平凡逻辑。
    assert _parse_three('["a","b","c"]') == ["a", "b", "c"]
    assert _parse_three('```json\n["x", "y", "z"]\n```') == ["x", "y", "z"]
    assert _parse_three("1. 你好\n2. 在吗\n3. 咋了") == ["你好", "在吗", "咋了"]
    assert _parse_three("- 甲\n- 乙\n- 丙\n- 丁")[:3] == ["甲", "乙", "丙"]
    try:
        _parse_three("只有一条")
        raise SystemExit("应当抛错")
    except JevError:
        pass
    print("draft._parse_three ok")

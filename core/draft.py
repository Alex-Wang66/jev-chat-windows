# -*- coding: utf-8 -*-
"""起草 3 条候选回复。可走 OpenRouter，也可直连 DeepSeek（更快）；两家都是 OpenAI chat 格式。

跟 jev_client 一样：只用 stdlib urllib、key 只从环境变量读、绝不把 key 打进日志。
盲起草——不喂 Jev 判断，让生成模型自己读对话；排序交给 Jev（永远走 OpenRouter）。
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
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"  # OpenRouter 上的 DeepSeek V4.1 Flash
MAX_RETRIES = 3

# provider -> (url, 默认模型, key 的环境变量名)
PROVIDERS = {
    "openrouter": (CHAT_URL, DEFAULT_MODEL, "OPENROUTER_API_KEY"),
    # 官方 id：deepseek-flash = DeepSeek-V4.1-Flash（非思考，起草够用）；deepseek-chat 2026-07-24 已下线，只是暂时还被路由
    "deepseek": ("https://api.deepseek.com/chat/completions", "deepseek-flash", "DEEPSEEK_API_KEY"),
}

SYSTEM = (
    "You draft candidate replies for a private chat-assist tool. "
    "The user is the speaker 'me'; 'her' is the other person (any gender). "
    "Read the whole thread, then write EXACTLY 3 candidate next messages that 'me' could send. "
    "Make the three genuinely different in approach (e.g. one warm/acknowledging, "
    "one that takes responsibility or explains, one that offers a concrete next step). "
    "Write in natural, casual Chinese as real people text on WeChat — short, human, no formal tone, "
    "no emoji spam, no quotation marks around the whole line. "
    "Never propose sending money, transfers, or red packets. "
    "In a group chat the transcript prefixes each line with the speaker's own name instead of 'her', "
    "and if one person must be replied to, the user prompt names them. "
    'Output ONLY a JSON array of exactly 3 strings, e.g. ["...","...","..."]. No other text.'
)


def _clean(x: str) -> str:
    """剥掉一条候选两端的括号/引号/编号/逗号——模型偶尔一行给一个 ["…"]，或者整条带引号。"""
    x = re.sub(r"^\s*(?:\d+[.)、]|[-*])\s*", "", x.strip())
    return x.strip(" \t[]\"'“”‘’,，")


def _parse_candidates(content: str) -> list[str]:
    """从模型输出里抠候选（最多 3 条，可能不足）。先整体按 JSON 数组；不行就逐行——每行再试 JSON
    （一行一个 ["…"] 的情况），最后兜底剥符号。一条都没有才抛。"""
    content = content.strip()
    # 去掉可能的 ```json 围栏
    content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.MULTILINE).strip()
    try:
        arr = json.loads(content)
        if isinstance(arr, list):
            got = [_clean(str(x)) for x in arr]
            got = [g for g in got if g]
            if got:
                return got[:3]
    except Exception:
        pass
    got = []
    for ln in content.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        bare = re.sub(r"^\s*(?:\d+[.)、]|[-*])\s*", "", ln)
        try:
            v = json.loads(bare)
            items = v if isinstance(v, list) else [v]
        except Exception:
            # 几个 ["…"] 挤在一行（逗号连着）：把每个方括号里的字符串抠出来
            items = re.findall(r'\[\s*"((?:[^"\\]|\\.)*)"\s*\]', bare) if bare.startswith("[") else [ln]
            items = items or [ln]
        got += [c for c in (_clean(str(x)) for x in items) if c]
    if got:
        return got[:3]
    raise JevError(f"起草结果解析不出候选: {content[:200]!r}")


def _parse_three(content: str) -> list[str]:
    """严格版：不足 3 条就抛（自测用）。"""
    got = _parse_candidates(content)
    if len(got) < 3:
        raise JevError(f"起草结果解析不出 3 条: {content[:200]!r}")
    return got


def _chat(url: str, key: str, body: dict, timeout: float) -> str:
    """一次 chat completions 调用，429/529/超时退避重试，返回 content。"""
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    for attempt in range(MAX_RETRIES + 1):
        req = urllib.request.Request(url, data=payload, method="POST", headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json; charset=utf-8",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
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


def _line(m) -> str:
    """一条台词：群里有发言人名就用名字打头，其余照旧 her/me。"""
    if isinstance(m, dict):
        who, text, name = m.get("from"), m.get("text"), m.get("name")
    else:
        who, text = m[0], m[1]
        name = m[2] if len(m) > 2 else None
    return f"{name if who == 'her' and name else who}: {text}"


def draft_candidates(messages: list, relationship: str, provider: str = "openrouter",
                     model: str | None = None, timeout: float = 30, keep: int = 10,
                     reply_to: str | None = None) -> list[str]:
    """messages: [(from, text)] 或 [(from, text, name)]，from ∈ {her, me}，name = 群里的发言人；
    只看最近 keep 条。返回最多 3 条中文候选（模型两次都给不够时可能少于 3，至少 1）。

    reply_to: 群聊里指定回复给谁；None = 正常回复。
    provider ∈ PROVIDERS；model=None 用该来源的默认模型。"""
    url, default_model, env = PROVIDERS[provider]
    transcript = "\n".join(_line(m) for m in messages[-keep:])
    user = f"relationship: {relationship}\n\n对话（最后一条是最新）:\n{transcript}"
    if reply_to:
        user += f"\n\n这是群聊。你要回复的是「{reply_to}」的话，三条候选都对 TA 说，不要@别人。"
    user += "\n\n输出恰好 3 条候选，JSON 数组，每条一句。"
    chat = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    body = {"model": model or default_model, "messages": chat, "temperature": 0.8,
            "stream": False}  # DeepSeek 要显式关；OpenRouter 无所谓
    key = _api_key(env)

    content = _chat(url, key, body, timeout)
    cands = _parse_candidates(content)
    if len(cands) < 3:
        # 模型偶尔只给 1~2 条（V4.1 Flash 实测会把三条揉成一条）。带着它的回答追问一次，要补齐的那几条。
        need = 3 - len(cands)
        body["messages"] = chat + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": f"只给了 {len(cands)} 条。再给 {need} 条跟上面不一样的候选，"
                                        f"只输出这 {need} 条的 JSON 数组。"},
        ]
        try:
            extra = _parse_candidates(_chat(url, key, body, timeout))
        except JevError:
            extra = []
        cands += [c for c in extra if c not in cands]
    return cands[:3]  # 可能仍不足 3 条，下游按实际条数处理


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
    assert _parse_candidates('["只有一条"]') == ["只有一条"]
    assert _parse_candidates('["好，明天下午"]\n["好嘞，明天聊"]\n["行，今晚弄"]') == ["好，明天下午", "好嘞，明天聊", "行，今晚弄"]
    assert _parse_candidates('1. ["甲"]\n2. "乙"\n3. 丙') == ["甲", "乙", "丙"]
    assert _parse_candidates('["a"], ["b"], ["c"]') == ["a", "b", "c"]
    assert _parse_candidates('他说"明天见"，我回：好') == ['他说"明天见"，我回：好']
    print("draft._parse_three ok")

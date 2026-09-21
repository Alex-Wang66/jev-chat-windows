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
# (url, 默认模型, key 的环境变量名, 请求体里额外要带的字段)
# V4.1 Flash 默认**开着思考模式**（effort=high，max_tokens 64K）——起草三句聊天回复不需要，慢还贵，两边都显式关掉。
PROVIDERS = {
    "openrouter": (CHAT_URL, DEFAULT_MODEL, "OPENROUTER_API_KEY", {"reasoning": {"enabled": False}}),
    # 官方 id：deepseek-flash = DeepSeek-V4.1-Flash；deepseek-chat 2026-07-24 已下线，只是暂时还被路由
    "deepseek": ("https://api.deepseek.com/chat/completions", "deepseek-flash", "DEEPSEEK_API_KEY",
                 {"thinking": {"type": "disabled"}}),
}

# 中文写，DeepSeek 跟得更紧。每一条都是冲着「人机感」去的，别随手删。
SYSTEM = (
    "你是「me」本人，正在微信里打字。不是助手，不是客服，不是在写作文。\n"
    "读完整段对话，写 3 条 me 接下来可能发出去的消息。\n"
    "硬规则：\n"
    "- 不总结、不复述对方的话，也不解释自己为什么这么回；\n"
    "- 不用「首先」「其次」「另外」「总之」；不用「亲」「您」「希望」「祝」「加油哦」这类客套；\n"
    "- 不排比、不对仗、不凑三段式；\n"
    "- 句尾别习惯性加句号，能不加标点就不加；感叹号和 emoji 只有 me 自己平时用才用；\n"
    "- 允许不完整的句子、口头语、长短错落；别每条都以「好」「嗯」开头；\n"
    "- 三条不是「温暖版／负责版／行动版」的模板，是同一个人在三个心情下随手打的，"
    "长短不一，其中一条可以很短（几个字）。\n"
    "风格：优先模仿 me 在对话里的用词、句长、标点和语气词习惯（下面会给样本）；"
    "对方是谁、什么关系看用户提示。群聊里每行用发言人自己的名字打头，指定了回复对象就只对 TA 说。\n"
    "安全：绝不提转账、红包、借钱。\n"
    "输出：只输出一个 JSON 数组，恰好 3 个字符串，别的什么都别写；字符串就是消息本身，不要带「me:」之类的前缀。"
)


def _clean(x: str) -> str:
    """剥掉一条候选两端的括号/引号/编号/逗号——模型偶尔一行给一个 ["…"]，或者整条带引号。
    末尾的句号也去掉（微信里很少有人用句号收尾）；？！～ 照留，那是语气。"""
    x = re.sub(r"^\s*(?:\d+[.)、]|[-*])\s*", "", x.strip())
    x = x.strip(" \t[]\"'“”‘’,，")
    x = re.sub(r"^(?:me|我)\s*[:：]\s*", "", x)  # 对话样本是「me: xxx」格式，模型会照抄前缀
    return x[:-1] if x.endswith("。") else x


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
                     reply_to: str | None = None, style: str = "") -> list[str]:
    """messages: [(from, text)] 或 [(from, text, name)]，from ∈ {her, me}，name = 群里的发言人；
    只看最近 keep 条。返回最多 3 条中文候选（模型两次都给不够时可能少于 3，至少 1）。

    reply_to: 群聊里指定回复给谁；None = 正常回复。
    style: 用户自己描述的口吻（设置里的「说话风格」），空就只靠样本模仿。
    provider ∈ PROVIDERS；model=None 用该来源的默认模型。"""
    url, default_model, env, extra = PROVIDERS[provider]
    transcript = "\n".join(_line(m) for m in messages[-keep:])
    user = f"relationship: {relationship}\n\n对话（最后一条是最新）:\n{transcript}"
    # 风格样本：me 自己说过的短句，整段对话里捞（不止最近 keep 条）。链接和长段不是风格，扔掉。
    said = [str((m.get("text") if isinstance(m, dict) else m[1]) or "").strip()
            for m in messages if (m.get("from") if isinstance(m, dict) else m[0]) == "me"]
    samples = [t for t in said if t and len(t) <= 60 and "http" not in t][-12:]
    if len(samples) >= 2:
        user += "\n\n我平时是这么说话的（模仿用词、长短、标点习惯）：\n" + "\n".join(samples)
    if style.strip():
        user += f"\n\n我对自己口吻的描述：{style.strip()}"
    if reply_to:
        user += f"\n\n这是群聊。你要回复的是「{reply_to}」的话，三条候选都对 TA 说，不要@别人。"
    user += "\n\n输出恰好 3 条候选，JSON 数组，每条一句。"
    chat = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    # 1.2：DeepSeek 自己推荐的闲聊档位，0.8 出来的话太板正
    body = {"model": model or default_model, "messages": chat, "temperature": 1.2,
            "max_tokens": 400,  # 三句话的量；不设的话思考模式下默认 64K
            "stream": False, **extra}  # stream: DeepSeek 要显式关；OpenRouter 无所谓
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
    # 结尾的句号扒掉，？！～ 留着
    assert _parse_three('["知道了。","真的吗？","好～"]') == ["知道了", "真的吗？", "好～"]
    assert _parse_three('["me: 别急 我看这速度今晚能聊到天亮","me：就这","笑死"]') == ["别急 我看这速度今晚能聊到天亮", "就这", "笑死"]
    print("draft._parse_three ok")

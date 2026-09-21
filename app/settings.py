# -*- coding: utf-8 -*-
"""设置持久化。key 硬约束（docs/KICKOFF.md #6）：只进环境变量，绝不落文件；relationship 不是密钥，落 config.json。

key 的持久化走 Windows 用户环境变量（注册表 HKCU\\Environment，跟 setx 写的是同一个地方）。
读的时候先看进程环境，没有就直接读注册表——IDE 启动时把环境快照拿走了，之后再 Run 继承的还是旧环境，
只靠 os.environ 会「保存了下次打开还是没有」。"""
from __future__ import annotations

import ctypes
import json
import os
import sys  # 只为下面这一处：打包后 __file__ 指向临时解包目录，config.json 得放在 exe 旁边才存得住

_ROOT = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
         else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CONFIG = os.path.join(_ROOT, "config.json")
_DEFAULT_RELATIONSHIP = "romantic partners"
_DEFAULT_CONTEXT = 10
_ENV = "OPENROUTER_API_KEY"


def relationship() -> str:
    """每次都重新读文件，改设置不用重启进程。"""
    try:
        with open(_CONFIG, encoding="utf-8") as f:
            return json.load(f).get("relationship") or _DEFAULT_RELATIONSHIP
    except (OSError, ValueError):
        return _DEFAULT_RELATIONSHIP


def context() -> int:
    """参考上下文条数：起草和判断各看最近多少条消息。3~30，缺失/脏数据一律退默认值。"""
    try:
        with open(_CONFIG, encoding="utf-8") as f:
            n = int(json.load(f).get("context", _DEFAULT_CONTEXT))
    except (OSError, ValueError, TypeError):
        return _DEFAULT_CONTEXT
    return max(3, min(30, n))


def _registry_key() -> str:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, _ENV)[0]).strip()
    except Exception:  # 非 Windows / 没这个值
        return ""


def key() -> str:
    """进程环境优先；没有就读注册表并带进进程环境，之后 core/ 里按 os.environ 读就有了。"""
    v = os.environ.get(_ENV, "").strip()
    if not v:
        v = _registry_key()
        if v:
            os.environ[_ENV] = v
    return v


def has_key() -> bool:
    return bool(key())


def save(key_text: str | None, relationship_text: str, context_n: int | None = None) -> None:
    """key 为空/None = 不改当前值。key 只写进程环境 + HKCU\\Environment，不写任何文件。
    context_n 为 None = 保留原来的参考上下文条数。"""
    if key_text:
        os.environ[_ENV] = key_text
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, _ENV, 0, winreg.REG_SZ, key_text)
            # 广播一下，之后新开的终端/进程就能看到；已经开着的 IDE 看不到也无所谓，启动时会读注册表
            ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, "Environment", 2, 5000, None)
        except Exception:
            pass  # 非 Windows（本机 Mac 开发）走不到，忽略
    n = context() if context_n is None else max(3, min(30, int(context_n)))
    with open(_CONFIG, "w", encoding="utf-8") as f:
        json.dump({"relationship": relationship_text, "context": n}, f, ensure_ascii=False)

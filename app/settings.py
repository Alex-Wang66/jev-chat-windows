# -*- coding: utf-8 -*-
"""设置持久化。key 硬约束（docs/KICKOFF.md #6）：只进环境变量，绝不落文件；relationship 不是密钥，落 config.json。"""
from __future__ import annotations

import json
import os
import subprocess

_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")  # 项目根
_DEFAULT_RELATIONSHIP = "romantic partners"


def relationship() -> str:
    """每次都重新读文件，改设置不用重启进程。"""
    try:
        with open(_CONFIG, encoding="utf-8") as f:
            return json.load(f).get("relationship") or _DEFAULT_RELATIONSHIP
    except (OSError, ValueError):
        return _DEFAULT_RELATIONSHIP


def has_key() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY", "").strip())


def save(key: str | None, relationship_text: str) -> None:
    """key 为空/None = 不改当前值。key 只写进程环境 + Windows 用户环境变量，不写任何文件。"""
    if key:
        os.environ["OPENROUTER_API_KEY"] = key
        try:  # setx 让重启后依然生效；非 Windows（比如本机 Mac）上直接失败，忽略即可
            subprocess.run(["setx", "OPENROUTER_API_KEY", key], creationflags=0x08000000)
        except Exception:
            pass
    with open(_CONFIG, "w", encoding="utf-8") as f:
        json.dump({"relationship": relationship_text}, f, ensure_ascii=False)

# -*- coding: utf-8 -*-
"""用合成数据预览 Qt 界面；不采集、不联网、不操作真实微信。

    python tools/preview_ui.py --state ready
    python tools/preview_ui.py --state ready --screenshot docs/ui_home.png

演示设置只保存在内存，不读取真实密钥，也不修改环境变量或 config.json。
"""
from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

from app import settings


_STATES = ("ready", "waiting", "loading", "error", "setup", "settings", "paused")

_MESSAGES = (
    ("her", "周六想吃火锅，你有空吗？", "18:42"),
    ("me", "有空呀，还是上次那家？", "18:43"),
    ("her", "好呀！六点见怎么样？我好久没吃了 😋", "18:43"),
)

_RESULT = {
    "candidates": [
        "周六六点没问题，上次那家见～",
        "可以呀，周六六点在上次那家见！我也有点馋了 😋",
        "好呀，就周六六点！需要我先订个位吗？",
    ],
    # 推荐故意放在第二项，方便检查视觉排序和按钮对应关系。
    "best_index": 1,
    "best_reply": "可以呀，周六六点在上次那家见！我也有点馋了 😋",
    "scores": [0.21, 0.66, 0.13],
    "answers": {
        "literal_question": {"type": "noul", "noul": 0.98},
        "true_intent": {"type": "choice", "choice": "casual_chat"},
        "danger_level": {"type": "score", "score": 0},
        "should_reply_now": {"type": "noul", "noul": 0.96},
        "best_action": {"type": "choice", "choice": "make_plan"},
        "she_needs": {"type": "choice", "choice": "action"},
        "tension_resolved": {"type": "noul", "noul": 0.99},
        "best_reply": {
            "type": "choice", "choice": "reply_b",
            "probabilities": {"reply_a": 0.21, "reply_b": 0.66, "reply_c": 0.13},
        },
    },
    "usage": {},
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="用合成聊天预览 Qt UI；绝不采集、联网或填入真实微信。"
    )
    parser.add_argument("--state", choices=_STATES, default="ready", help="预览界面状态")
    parser.add_argument("--screenshot", metavar="PATH", help="将演示界面保存为 PNG 后退出（合成数据，不含微信内容）")
    args = parser.parse_args()
    target = Path(args.screenshot).expanduser() if args.screenshot else None

    demo_settings = {"has_key": args.state != "setup", "relationship": "friends"}

    def save_demo_settings(key, relationship_text):
        if key:
            demo_settings["has_key"] = True
        demo_settings["relationship"] = relationship_text

    # 在创建 Overlay 前替换设置接口，整个事件循环期间都保持隔离。
    with patch.multiple(
        settings,
        has_key=lambda: demo_settings["has_key"],
        relationship=lambda: demo_settings["relationship"],
        save=save_demo_settings,
    ):
        from PySide6.QtCore import QTimer
        from app.overlay import Overlay

        def simulate_fill(text):
            # 等 Overlay 自身的点击反馈结束后，再显示明确的演示提示。
            QTimer.singleShot(0, lambda: ov.set_status(
                f"演示模式：已模拟填入「{text}」；未操作微信。", kind="success"
            ))

        ov = Overlay(on_fill=simulate_fill)
        ov.win.setWindowTitle("WeChatJev · 界面演示（合成数据）")

        if args.state == "setup":
            ov.set_status("演示模式：请填写示例密钥，设置仅保存在本次预览内。", kind="warning")
            ov.open_settings()
        elif args.state == "waiting":
            ov.set_status("演示模式：等待对方的新消息；当前未连接微信。")
        else:
            for who, text, timestamp in _MESSAGES:
                ov.log_message(who, text, timestamp=timestamp)
            ov.show(_RESULT)
            ov.set_status("演示模式：已生成 3 条建议，点击填入仅模拟操作。", kind="success")
            if args.state == "loading":
                ov.set_busy(True)
                ov.set_status("演示模式：正在为最新消息生成建议…", kind="busy")
            elif args.state == "error":
                ov.set_busy(True)
                ov.set_status("演示模式：分析失败，请检查网络和密钥，等待下一条消息后重试。", kind="error")
            elif args.state == "settings":
                ov.open_settings()
            elif args.state == "paused":
                ov.set_capture(False)

        exit_code = 0
        if target is not None:
            def save_screenshot():
                nonlocal exit_code
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not ov.win.grab().save(str(target), "PNG"):
                        raise OSError(f"无法保存截图：{target}")
                    print(f"已保存合成界面截图：{target}")
                except OSError as exc:
                    print(str(exc))
                    exit_code = 1
                finally:
                    ov.app.quit()

            QTimer.singleShot(500, save_screenshot)
        ov.run()
        return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

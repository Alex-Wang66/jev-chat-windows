# -*- coding: utf-8 -*-
"""父进程：只管界面。截图 + OCR 在 app/worker.py 的子进程里跑，队列里收新消息 →
冒出新的对方消息才调 engine → 悬浮窗给 3 条候选 → 人点「填入」。发送永远手动。静默期零调用。

    pip install rapidocr-onnxruntime numpy windows-capture PySide6-Fluent-Widgets
OpenRouter key 在独立设置页填写，不用改代码。IDE 里直接 Run。
"""
import ctypes
import multiprocessing
import queue
import threading
import traceback
from collections import deque

from app import settings, worker
from app.capture import find_wechat_hwnd
from app.fill import fill
from app.overlay import Overlay
from core.engine import analyze

history = deque(maxlen=60)  # [(who, text)]，engine 只认 her/me；只是缓冲区，实际喂模型几条由设置里的「参考上下文」决定
state = {"area": None, "busy": False, "rerun": None, "revision": 0, "hwnd": None}
results = queue.Queue()


def fill_reply(text):
    if state["area"] is None or state["hwnd"] is None:  # 子进程重开过，hwnd 可能换了，用最新的
        raise RuntimeError("微信输入区域尚不可用")
    fill(state["hwnd"], state["area"], text)


def spawn_worker():
    """开一个采集子进程，它跟着 capture_on 走：置位=采集，清掉=暂停。"""
    p = multiprocessing.Process(target=worker.run, args=(q, state["hwnd"], capture_on), daemon=True)
    p.start()
    return p


def on_toggle_capture(on):
    """标题栏开关。启动时没找到微信就没有子进程，这会儿再找一次，找到了才真开得起来。"""
    global child
    if not on:
        capture_on.clear()
        return
    if child is None:
        try:
            state["hwnd"] = find_wechat_hwnd()
        except RuntimeError:
            ov.set_capture(False, "未找到微信窗口，打开微信后再开启采集")
            return
        child = spawn_worker()
    capture_on.set()


def analyze_bg(msgs, revision):
    """后台线程只跑网络调用，结果丢队列；UI 只在主线程的 tick 里动（Qt 不能跨线程碰）。"""
    try:
        results.put(("ok", analyze(msgs, settings.relationship(), context=settings.context()), revision))
    except Exception as e:
        results.put(("err", f"分析失败: {e}", revision))


def start_analyze(msgs):
    if not settings.has_key():
        ov.set_status("请先在设置中配置回复服务", "warning")
        return
    state["busy"] = True
    ov.set_busy(True)
    threading.Thread(target=analyze_bg, args=(msgs, state["revision"]), daemon=True).start()


def drain():
    """把子进程队列里攒的东西全收掉。"""
    global child
    while True:
        try:
            msg = q.get_nowait()
        except queue.Empty:
            return
        kind = msg[0]
        if kind == "area":  # 只是窗口挪了位置，坐标跟着更新，别的什么都不用动
            state["area"] = msg[1]
            continue
        if kind == "status":  # 单帧识别失败/报错，提示一下就好，别把正在跑的分析和已知坐标清掉
            ov.set_status(msg[1], "warning")
            ov.log(msg[1])
            continue
        if kind == "paused":  # 子进程确认已暂停
            ov.set_capture(False)
            continue
        if kind == "resumed":  # 子进程重新开始采集
            ov.set_capture(True)
            continue
        if kind == "dead":  # 采集彻底停了（微信关了之类），这才是真的要清状态
            state["area"] = None
            state["revision"] += 1
            state["rerun"] = None
            ov.invalidate_replies()
            ov.set_busy(False)
            ov.set_capture(False, msg[1])
            ov.log(msg[1])
            if child is not None:  # 子进程已经不干活了，收掉引用，下次打开开关重开一个
                child.terminate()
                child.join()
                child = None
            continue
        _, new, area = msg
        state["area"] = area
        state["revision"] += 1
        ov.invalidate_replies()
        for who, name, text in new:
            history.append((who, text))
            ov.log_message(who, text, name)
        if new[-1][0] == "her":  # 只有对方最新说话才值得分析
            msgs = list(history)
            if state["busy"]:
                state["rerun"] = msgs
                ov.set_busy(True)
            else:
                start_analyze(msgs)
        else:
            state["rerun"] = None
            ov.set_busy(False)
            ov.set_status("你已回复，等待对方的新消息")


def tick():
    try:
        drain()
        while not results.empty():
            kind, r, revision = results.get()
            state["busy"] = False
            if state["rerun"]:  # 分析期间又来了新消息，接着跑最新的
                msgs, state["rerun"] = state["rerun"], None
                start_analyze(msgs)
                continue
            if revision != state["revision"]:
                ov.set_busy(False)
                continue
            if kind == "ok":
                ov.show(r)
            else:
                ov.set_busy(False)
                ov.set_status("生成失败，请检查网络和服务设置；新消息到来后会重试。", "error")
                ov.log(r)
    except Exception:
        traceback.print_exc()  # 一帧出错不退出
    ov.after(50, tick)


if __name__ == "__main__":  # Windows 的 spawn 会让子进程重新执行本文件，没这行就无限套娃开进程
    ctypes.windll.user32.SetProcessDPIAware()
    q = multiprocessing.Queue()
    capture_on = multiprocessing.Event()  # 父子进程共用的开关，置位=采集
    ov = Overlay(on_fill=fill_reply, on_toggle_capture=on_toggle_capture)
    child = None
    try:
        state["hwnd"] = find_wechat_hwnd()
    except RuntimeError:
        ov.set_capture(False, "未找到微信窗口，打开微信后再开启采集")
    else:
        capture_on.set()
        child = spawn_worker()
    if not settings.has_key():
        ov.set_status("请先在设置中配置回复服务", "warning")
        ov.after(0, ov.open_settings)
    ov.after(50, tick)
    try:
        ov.run()
    finally:
        if child is not None:
            child.terminate()

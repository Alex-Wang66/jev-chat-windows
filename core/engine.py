# -*- coding: utf-8 -*-
"""整条链的唯一入口：对话 → 起草 3 条 → Jev 一次判断+排序 → 结构化结果。

平台无关。SSE 消费者、悬浮窗、命令行 demo 都只调 analyze()。
"""
from __future__ import annotations

try:
    from .draft import DEFAULT_MODEL, draft_candidates
    from .jev_client import ask
    from .questions import JUDGE_QUESTIONS, build_rank_question, build_state
except ImportError:
    from draft import DEFAULT_MODEL, draft_candidates
    from jev_client import ask
    from questions import JUDGE_QUESTIONS, build_rank_question, build_state

_REPLY_IDX = {"reply_a": 0, "reply_b": 1, "reply_c": 2}


def analyze(messages: list, relationship: str,
            model: str = DEFAULT_MODEL, timeout: float = 30, context: int = 10) -> dict:
    """messages: [(from, text)] from ∈ {her, me}，最新一条在最后。
    context: 起草和判断各看最近多少条消息（用户设置里的「参考上下文」）。

    返回 {candidates, best_index, best_reply, scores, answers, usage}。
    scores 是每条候选的胜出概率（0~1），取自 best_reply.probabilities，取不到记 0.0。
    只有对方最新说话时才有意义调它——是不是该触发由调用方判断（看 latest_from）。
    """
    candidates = draft_candidates(messages, relationship, model=model, timeout=timeout, keep=context)

    questions = dict(JUDGE_QUESTIONS)
    questions.update(build_rank_question(candidates))
    result = ask(build_state(messages, relationship, keep=context), questions, timeout=timeout)

    answers = result.get("answers") or {}
    best_key = (answers.get("best_reply") or {}).get("choice")
    best_index = _REPLY_IDX.get(best_key, 0)  # 解析不出就退第一条

    probabilities = (answers.get("best_reply") or {}).get("probabilities") or {}
    scores = [0.0, 0.0, 0.0]
    for key, idx in _REPLY_IDX.items():
        try:
            scores[idx] = float(probabilities.get(key, 0.0))
        except (TypeError, ValueError):
            scores[idx] = 0.0  # 脏数据一律按 0 处理

    return {
        "candidates": candidates,
        "best_index": best_index,
        "best_reply": candidates[best_index],
        "scores": scores,
        "answers": answers,
        "usage": result.get("usage") or {},
    }

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

DEFAULT_STATUS_DIR = Path(os.getenv("AGENT_STATUS_DIR") or os.getenv("BK_LIGHT_STATUS_DIR") or "/tmp/hermes_agent_status")
_VALID_STATES = {"idle", "thinking", "tool_use", "permission"}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_LOCK = threading.RLock()


@dataclass
class SessionState:
    tool_depth: int = 0
    approval_pending: bool = False


_SESSION_STATE: dict[str, SessionState] = {}
_THREAD_TO_SESSION: dict[int, str] = {}
_TASK_TO_SESSION: dict[str, str] = {}

def _status_dir() -> Path:
    return Path(os.getenv("AGENT_STATUS_DIR") or os.getenv("BK_LIGHT_STATUS_DIR") or str(DEFAULT_STATUS_DIR))


def _safe_session_name(session_id: str | None) -> str | None:
    if not session_id:
        return None
    cleaned = _SAFE_NAME.sub("_", session_id).strip("._")
    return cleaned or None


def _state_file(session_id: str | None) -> Path | None:
    safe = _safe_session_name(session_id)
    if not safe:
        return None
    directory = _status_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory / safe


def _write_state(session_id: str | None, state: str) -> None:
    if state not in _VALID_STATES:
        return
    state_file = _state_file(session_id)
    if state_file is None:
        return
    state_file.write_text(state, encoding="utf-8")


def _remove_state(session_id: str | None) -> None:
    state_file = _state_file(session_id)
    if state_file is None:
        return
    state_file.unlink(missing_ok=True)


def _ensure_state(session_id: str | None) -> SessionState | None:
    safe = _safe_session_name(session_id)
    if safe is None:
        return None
    return _SESSION_STATE.setdefault(safe, SessionState())


def _drop_state(session_id: str | None) -> None:
    safe = _safe_session_name(session_id)
    if safe is None:
        return
    _SESSION_STATE.pop(safe, None)


def _note_thread_session(session_id: str | None) -> None:
    safe = _safe_session_name(session_id)
    if safe is None:
        return
    _THREAD_TO_SESSION[threading.get_ident()] = safe


def _clear_session_mappings(session_id: str | None) -> None:
    safe = _safe_session_name(session_id)
    if safe is None:
        return
    for thread_id, mapped in list(_THREAD_TO_SESSION.items()):
        if mapped == safe:
            _THREAD_TO_SESSION.pop(thread_id, None)
    for task_id, mapped in list(_TASK_TO_SESSION.items()):
        if mapped == safe:
            _TASK_TO_SESSION.pop(task_id, None)


def _resolve_tool_session_id(session_id: str | None, task_id: str | None) -> str | None:
    safe_session = _safe_session_name(session_id)
    if safe_session:
        _note_thread_session(safe_session)
        if task_id:
            _TASK_TO_SESSION[task_id] = safe_session
        return safe_session

    if task_id and task_id in _TASK_TO_SESSION:
        return _TASK_TO_SESSION[task_id]

    mapped = _THREAD_TO_SESSION.get(threading.get_ident())
    if mapped:
        if task_id:
            _TASK_TO_SESSION[task_id] = mapped
        return mapped

    if len(_SESSION_STATE) == 1:
        only_session = next(iter(_SESSION_STATE))
        if task_id:
            _TASK_TO_SESSION[task_id] = only_session
        return only_session

    return task_id or None


def _transition_after_tool(session_id: str | None) -> None:
    session = _ensure_state(session_id)
    if session is None:
        return
    if session.approval_pending:
        _write_state(session_id, "permission")
    elif session.tool_depth > 0:
        _write_state(session_id, "tool_use")
    else:
        _write_state(session_id, "thinking")


def _on_session_start(session_id: str, **kwargs) -> None:
    del kwargs
    with _LOCK:
        _ensure_state(session_id)
        _note_thread_session(session_id)
        _write_state(session_id, "idle")


def _on_pre_llm_call(session_id: str, **kwargs) -> None:
    del kwargs
    with _LOCK:
        session = _ensure_state(session_id)
        if session is None:
            return
        _note_thread_session(session_id)
        if session.approval_pending:
            _write_state(session_id, "permission")
        elif session.tool_depth > 0:
            _write_state(session_id, "tool_use")
        else:
            _write_state(session_id, "thinking")


def _on_pre_tool_call(task_id: str = "", session_id: str = "", **kwargs) -> None:
    del kwargs
    target = _resolve_tool_session_id(session_id, task_id)
    with _LOCK:
        session = _ensure_state(target)
        if session is None:
            return
        session.tool_depth += 1
        _write_state(target, "tool_use")


def _on_post_tool_call(task_id: str = "", session_id: str = "", **kwargs) -> None:
    del kwargs
    target = _resolve_tool_session_id(session_id, task_id)
    with _LOCK:
        session = _ensure_state(target)
        if session is None:
            return
        session.tool_depth = max(0, session.tool_depth - 1)
        _transition_after_tool(target)


def _on_pre_approval_request(session_key: str, **kwargs) -> None:
    with _LOCK:
        session = _ensure_state(session_key)
        if session is None:
            return
        _note_thread_session(session_key)
        session.approval_pending = True
        _write_state(session_key, "permission")


def _on_post_approval_response(session_key: str, **kwargs) -> None:
    with _LOCK:
        session = _ensure_state(session_key)
        if session is None:
            return
        _note_thread_session(session_key)
        session.approval_pending = False
        if session.tool_depth > 0:
            _write_state(session_key, "tool_use")
        else:
            _write_state(session_key, "thinking")


def _on_post_llm_call(session_id: str, **kwargs) -> None:
    del kwargs
    with _LOCK:
        session = _ensure_state(session_id)
        if session is None:
            return
        _note_thread_session(session_id)
        session.tool_depth = 0
        session.approval_pending = False
        _write_state(session_id, "idle")


def _on_session_end(session_id: str, **kwargs) -> None:
    del kwargs
    with _LOCK:
        session = _ensure_state(session_id)
        if session is None:
            return
        _note_thread_session(session_id)
        session.tool_depth = 0
        session.approval_pending = False
        _write_state(session_id, "idle")


def _on_session_finalize(session_id: str | None, **kwargs) -> None:
    del kwargs
    with _LOCK:
        _clear_session_mappings(session_id)
        _drop_state(session_id)
        _remove_state(session_id)


def _on_session_reset(session_id: str | None = None, **kwargs) -> None:
    del kwargs
    with _LOCK:
        _clear_session_mappings(session_id)
        _drop_state(session_id)
        _remove_state(session_id)


def register(ctx) -> None:
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("pre_approval_request", _on_pre_approval_request)
    ctx.register_hook("post_approval_response", _on_post_approval_response)
    ctx.register_hook("post_llm_call", _on_post_llm_call)
    ctx.register_hook("on_session_end", _on_session_end)
    ctx.register_hook("on_session_finalize", _on_session_finalize)
    ctx.register_hook("on_session_reset", _on_session_reset)


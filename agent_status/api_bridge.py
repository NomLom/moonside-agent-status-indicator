from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = os.getenv("HERMES_API_BASE_URL", "http://127.0.0.1:8642")
DEFAULT_API_KEY = os.getenv("HERMES_API_KEY", "")
DEFAULT_STATUS_DIR = Path(os.getenv("AGENT_STATUS_DIR") or os.getenv("BK_LIGHT_STATUS_DIR") or "/tmp/hermes_agent_status")
_VALID_STATES = {"idle", "thinking", "tool_use", "permission", "success", "failed", "cancelled"}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_session_name(session_id: str) -> str:
    cleaned = _SAFE_NAME.sub("_", session_id).strip("._")
    return cleaned or "session"


def _status_path(status_dir: Path, session_id: str) -> Path:
    status_dir.mkdir(parents=True, exist_ok=True)
    return status_dir / _safe_session_name(session_id)


def write_state(status_dir: Path, session_id: str, state: str) -> None:
    if state not in _VALID_STATES:
        return
    _status_path(status_dir, session_id).write_text(state, encoding="utf-8")


def build_headers(api_key: str | None, session_key: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "moonside-agent-status/1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if session_key:
        headers["X-Hermes-Session-Key"] = session_key
    return headers


def http_json(method: str, url: str, headers: dict[str, str], body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload) if payload else {}


def create_run(
    base_url: str,
    api_key: str | None,
    prompt: str,
    instructions: str | None = None,
    session_id: str | None = None,
    session_key: str | None = None,
    model: str | None = None,
) -> tuple[str, str]:
    headers = build_headers(api_key, session_key)
    body: dict[str, Any] = {"input": prompt}
    if instructions:
        body["instructions"] = instructions
    if session_id:
        body["session_id"] = session_id
    if model:
        body["model"] = model
    payload = http_json("POST", f"{base_url.rstrip('/')}/v1/runs", headers, body)
    run_id = str(payload["run_id"])
    return run_id, session_id or run_id


def _event_to_state(event: str, payload: dict[str, Any]) -> str | None:
    if event in {"message.delta", "reasoning.available"}:
        return "thinking"
    if event == "tool.started":
        return "tool_use"
    if event == "tool.completed":
        return "thinking"
    if event == "tool.failed":
        return "failed"
    if event == "approval.request":
        return "permission"
    if event == "run.completed":
        return "success"
    if event == "run.failed":
        return "failed"
    if event == "run.cancelled":
        return "cancelled"
    if event == "run.started":
        return "thinking"
    if event == "run.queued":
        return "thinking"
    if payload.get("status") == "waiting_for_approval":
        return "permission"
    return None


def stream_run_events(
    base_url: str,
    run_id: str,
    session_id: str,
    api_key: str | None,
    status_dir: Path,
    final_hold_seconds: float = 8.0,
) -> int:
    headers = build_headers(api_key)
    headers["Accept"] = "text/event-stream"
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/runs/{urllib.parse.quote(run_id)}/events",
        headers=headers,
        method="GET",
    )

    final_state = None
    write_state(status_dir, session_id, "thinking")

    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            event_name = ""
            data_lines: list[str] = []
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                if not line.strip():
                    if data_lines:
                        payload = json.loads("\n".join(data_lines))
                        data_lines = []
                        event = event_name or str(payload.get("event", ""))
                        event_name = ""
                        state = _event_to_state(event, payload)
                        if state:
                            write_state(status_dir, session_id, state)
                            final_state = state if state in {"success", "failed", "cancelled"} else final_state
                        if event in {"run.completed", "run.failed", "run.cancelled"}:
                            break
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        write_state(status_dir, session_id, "failed")
        print(error_body, file=sys.stderr)
        return exc.code or 1
    except Exception as exc:
        write_state(status_dir, session_id, "failed")
        print(f"event stream failed: {exc}", file=sys.stderr)
        return 1

    if final_state:
        time.sleep(max(0.0, final_hold_seconds))
    write_state(status_dir, session_id, "idle")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hermes API/SSE status bridge for the Moonside status lamp")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", default=DEFAULT_BASE_URL)
    common.add_argument("--api-key", default=DEFAULT_API_KEY)
    common.add_argument("--status-dir", default=str(DEFAULT_STATUS_DIR))
    common.add_argument("--session-id", default=None)
    common.add_argument("--session-key", default=None)
    common.add_argument("--final-hold-seconds", type=float, default=8.0)

    run_parser = sub.add_parser("run", parents=[common], help="create a run and mirror its state")
    run_parser.add_argument("prompt")
    run_parser.add_argument("--instructions", default=None)
    run_parser.add_argument("--model", default=None)

    watch_parser = sub.add_parser("watch", parents=[common], help="watch an existing run_id over SSE")
    watch_parser.add_argument("run_id")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    status_dir = Path(args.status_dir)

    if args.command == "run":
        run_id, session_id = create_run(
            base_url=args.base_url,
            api_key=args.api_key or None,
            prompt=args.prompt,
            instructions=args.instructions,
            session_id=args.session_id,
            session_key=args.session_key,
            model=args.model,
        )
        print(run_id)
        return stream_run_events(
            base_url=args.base_url,
            run_id=run_id,
            session_id=session_id,
            api_key=args.api_key or None,
            status_dir=status_dir,
            final_hold_seconds=args.final_hold_seconds,
        )

    session_id = args.session_id or args.run_id
    return stream_run_events(
        base_url=args.base_url,
        run_id=args.run_id,
        session_id=session_id,
        api_key=args.api_key or None,
        status_dir=status_dir,
        final_hold_seconds=args.final_hold_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())


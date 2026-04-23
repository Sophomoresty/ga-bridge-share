#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import json
import os
import queue
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fcntl


TOOL_NAME = "ga"
CONFIG_ENV_VAR = "GA_CONFIG_FILE"
DEFAULT_GA_ROOT_WIN = r"D:\GenericAgent"
DEFAULT_GA_ROOT_WSL = Path("/mnt/d/GenericAgent")
DEFAULT_WEBUI_BIN = Path.home() / ".local" / "bin" / "ga-webui"
DEFAULT_WINDOWS_TEMP_WIN = r"C:\Users\<YOUR_WINDOWS_USER>\AppData\Local\Temp"
DEFAULT_WINDOWS_TEMP_WSL = Path("/mnt/c/Users/<YOUR_WINDOWS_USER>/AppData/Local/Temp")
DEFAULT_SKILLS_ROOT = Path.home() / ".codex" / "skills"
DEFAULT_WSL_DISTRO = os.environ.get("WSL_DISTRO_NAME", "Ubuntu").strip() or "Ubuntu"
DEFAULT_WSL_EXE_WIN = r"C:\Windows\System32\wsl.exe"
SCRIPT_PATH = Path(__file__).resolve()
TERMINAL_STATES = {"completed", "failed", "stopped"}
SESSION_TERMINAL_STATES = {"expired", "failed", "stopped"}
REVISABLE_SESSION_STATES = {"waiting_reply"}
REVISABLE_JOB_STATES = {"waiting_review"}
DEFAULT_WATCH_IDLE_TIMEOUT_SEC = 180.0
WATCH_RETRY_GRACE_SEC = 15.0
MAX_TASK_FILE_BYTES = 1024 * 1024
MAX_TASK_STAGE_TOTAL_BYTES = 16 * 1024 * 1024
MAX_TASK_STAGE_FILE_COUNT = 500
ROUND_END_MARKER = "\n\n[ROUND END]\n"
TURN_RE = re.compile(r"LLM Running \(Turn (\d+)\)")
VERBOSE_TOOL_RE = re.compile(r"🛠️\s+Tool:\s+`([^`]+)`(?:\s+📥 args:\s*````text\n([\s\S]*?)\n````)?")
COMPACT_TOOL_RE = re.compile(r"🛠️\s+([^\s(`\n][^\(\n`]*)\((.*?)\)")
ACTION_RE = re.compile(r"\[Action\]\s*(.+)")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
RETRY_DELAY_RE = re.compile(r"retry in\s+([0-9]+(?:\.[0-9]+)?)s", re.IGNORECASE)
LOCAL_TASK_FILE_RE = re.compile(r"(?P<path>(?:/home|/mnt)/[^\s\"'`;,)>\]]+)")
FINAL_RESPONSE_MARKER_RE = re.compile(r"`{5}\s*\n\[Info\]\s+Final response to user\.\s*\n`{5}", re.MULTILINE)
TAG_BLOCK_RE = re.compile(r"<(thinking|summary|tool_use)>[\s\S]*?</\1>", re.IGNORECASE)
RUNNING_HEADER_RE = re.compile(r"\*\*LLM Running \(Turn \d+\) \.\.\.\*\*", re.MULTILINE)
FENCED_BLOCK_RE = re.compile(r"`{4,}[\s\S]*?`{4,}", re.MULTILINE)

SUPPORTED_PROFILES = ["frontend", "analysis", "review"]

PROFILE_PROMPT_TEXT = {
    "frontend": """### Execution profile
当前 profile = frontend.

- 这是实现型任务, 目标是产出可运行页面, 组件, 或界面文件.
- 优先输出可直接落地的文件结果, 不要停留在泛泛分析.
- 若任务涉及设计探索, 可以使用 subagent 做探索或独立验证, 但主 agent 负责最终收敛与写回.
""".strip(),
    "analysis": """### Execution profile
当前 profile = analysis.

- 这是分析型任务.
- 以读取, 观察, 比较, 提炼结论为主.
- 除非用户明确要求修改或写入产物, 否则不要进行持久写入, 不要触发会改变外部状态的操作.
""".strip(),
    "review": """### Execution profile
当前 profile = review.

- 这是只读审查任务.
- 只允许读取, 观察, 比较, 截图, OCR, 页面检查, 文件检查, 日志检查.
- 禁止创建, 修改, 删除文件.
- 禁止点击会提交表单, 发送消息, 安装依赖, 执行会产生持久副作用的操作.
- 如果验证某个结论必须写入或执行副作用操作, 先明确标记阻塞, 不要擅自执行.
- 最终输出只给审查结论, 风险点, 和必要证据.
""".strip(),
}


class HiddenAwareArgumentParser(argparse.ArgumentParser):
    def format_help(self) -> str:
        raw = super().format_help()
        lines = [line for line in raw.splitlines() if "==SUPPRESS==" not in line]
        return "\n".join(lines) + "\n"


SUBAGENT_POLICY_TEXT = """### Delegation policy
你必须先判断当前任务是否适合 subagent.

- 如果当前任务是在完善, 继续修改, review 后修复, 或延续同一产物:
  - 优先沿用当前会话里的 working memory 和既有计划.
  - 不要把同一任务拆成彼此无关的新上下文.

- 简单单步任务: 主 agent 自己做.
- 满足以下任一条件: 必须启动 subagent.
  - 需要读取大量文件或大量代码.
  - 需要并行探索多个方向.
  - 需要独立验证.
  - 需要长输出探测, 避免污染主上下文.
  - 当前是前端/设计类多轮迭代任务, 且需要探索和验证分离.
- 当任务符合委派条件时:
  - 主 agent 负责规划, 汇总结论, 最终整合与写入.
  - subagent 负责重读大量上下文, 并行探索, 或独立验证.
- 当任务不符合委派条件时:
  - 不要为了形式而启动 subagent.
"""

_CONFIG_CACHE: dict[str, Any] | None = None


def config_path() -> Path:
    explicit = os.environ.get(CONFIG_ENV_VAR, "").strip()
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "ga" / "config.json"


def load_config() -> dict[str, Any]:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    path = config_path()
    if not path.exists():
        _CONFIG_CACHE = {}
        return _CONFIG_CACHE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _CONFIG_CACHE = {}
        return _CONFIG_CACHE
    _CONFIG_CACHE = payload if isinstance(payload, dict) else {}
    return _CONFIG_CACHE


def config_value(key: str, default: Any) -> Any:
    payload = load_config()
    value = payload.get(key, default)
    return default if value in (None, "") else value


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def home_root() -> Path:
    return Path(os.environ.get("GA_HOME_ROOT", str(Path.home()))).expanduser().resolve()


def jobs_root() -> Path:
    return home_root() / ".local" / "share" / "ga" / "jobs"


def sessions_root() -> Path:
    return home_root() / ".local" / "share" / "ga" / "sessions"


def ga_root_win() -> str:
    return os.environ.get("GA_ROOT_WIN", str(config_value("ga_root_win", DEFAULT_GA_ROOT_WIN)))


def ga_root_wsl() -> Path:
    return Path(os.environ.get("GA_ROOT_WSL", str(config_value("ga_root_wsl", str(DEFAULT_GA_ROOT_WSL))))).expanduser()


def ga_webui_bin() -> Path:
    return Path(os.environ.get("GA_WEBUI_BIN", str(config_value("ga_webui_bin", str(DEFAULT_WEBUI_BIN))))).expanduser()


def skills_root() -> Path:
    return Path(os.environ.get("GA_SKILLS_ROOT", str(config_value("skills_root", str(DEFAULT_SKILLS_ROOT))))).expanduser()


def windows_temp_root_win() -> str:
    return os.environ.get(
        "GA_WINDOWS_TEMP_ROOT_WIN",
        str(config_value("windows_temp_win", DEFAULT_WINDOWS_TEMP_WIN)),
    )


def windows_temp_root_wsl() -> Path:
    return Path(
        os.environ.get(
            "GA_WINDOWS_TEMP_ROOT_WSL",
            str(config_value("windows_temp_wsl", str(DEFAULT_WINDOWS_TEMP_WSL))),
        )
    ).expanduser()


def default_wsl_distro() -> str:
    configured = str(config_value("wsl_distro", DEFAULT_WSL_DISTRO))
    return os.environ.get("GA_WSL_DISTRO", configured).strip() or configured


def default_wsl_cwd() -> str:
    configured = str(config_value("wsl_default_cwd", str(home_root())))
    return os.environ.get("GA_WSL_DEFAULT_CWD", configured).strip() or configured


def default_wsl_exe_win() -> str:
    configured = str(config_value("wsl_exe_win", DEFAULT_WSL_EXE_WIN))
    return os.environ.get("GA_WSL_EXE_WIN", configured).strip() or configured


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def emit_json(payload: dict[str, Any], *, stream: Any = None) -> None:
    target = stream or sys.stdout
    target.write(json.dumps(payload, ensure_ascii=False) + "\n")


def die(message: str, *, json_mode: bool = False, exit_code: int = 1) -> None:
    if json_mode:
        emit_json({"tool": TOOL_NAME, "error": message}, stream=sys.stderr)
    else:
        sys.stderr.write(f"{TOOL_NAME}: {message}\n")
    raise SystemExit(exit_code)


def job_dir_for(job_id: str) -> Path:
    return jobs_root() / job_id


def session_dir_for(session_id: str) -> Path:
    return sessions_root() / session_id


def session_task_dir_wsl(session_id: str) -> Path:
    return ga_root_wsl() / "temp" / session_id


def job_status_path(job_dir: Path) -> Path:
    return job_dir / "status.json"


def job_events_path(job_dir: Path) -> Path:
    return job_dir / "events.jsonl"


def job_lock_path(job_dir: Path) -> Path:
    return job_dir / ".state.lock"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex[:8]}")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def write_text_exclusive(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    with os.fdopen(os.open(path, flags, 0o644), "w", encoding="utf-8") as handle:
        handle.write(text)


@contextlib.contextmanager
def job_store_lock(job_dir: Path):
    lock_path = job_lock_path(job_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_job_record_unlocked(job_dir: Path) -> dict[str, Any]:
    path = job_status_path(job_dir)
    if not path.exists():
        raise FileNotFoundError(f"job status not found: {path}")
    return json.loads(read_text(path))


def _write_job_record_unlocked(job_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    payload = dict(record)
    payload["job_dir"] = str(job_dir)
    payload["updated_at"] = now_iso()
    write_text_atomic(job_status_path(job_dir), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def read_job_record(job_dir: Path) -> dict[str, Any]:
    with job_store_lock(job_dir):
        return _read_job_record_unlocked(job_dir)


def write_job_record(job_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    with job_store_lock(job_dir):
        return _write_job_record_unlocked(job_dir, record)


def update_job_record(job_dir: Path, **updates: Any) -> dict[str, Any]:
    with job_store_lock(job_dir):
        record = _read_job_record_unlocked(job_dir)
        record.update(updates)
        return _write_job_record_unlocked(job_dir, record)


def load_task_text(args: argparse.Namespace) -> str:
    if getattr(args, "task", None):
        return args.task
    file_path = getattr(args, "file", None)
    if file_path:
        path = Path(file_path).expanduser()
        if path.stat().st_size > MAX_TASK_FILE_BYTES:
            raise ValueError(f"task file too large: {path} exceeds {MAX_TASK_FILE_BYTES} bytes")
        return path.read_text(encoding="utf-8")
    raise ValueError("either --task or --file is required")


def make_job_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"ga-{stamp}-{uuid.uuid4().hex[:8]}"


def make_session_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"ga-session-{stamp}-{uuid.uuid4().hex[:8]}"


def validate_session_id(session_id: str) -> str:
    normalized = session_id.strip()
    if not normalized:
        raise ValueError("session id cannot be empty")
    if not SESSION_ID_RE.match(normalized):
        raise ValueError("session id must match [A-Za-z0-9][A-Za-z0-9._-]*")
    return normalized


def pid_is_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def windows_pid_is_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    result = subprocess.run(
        [
            "pwsh.exe",
            "-NoLogo",
            "-NoProfile",
            "-Command",
            f"$p = Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue; if ($p) {{ '1' }}",
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    return result.returncode == 0 and result.stdout.strip() == "1"


def trim_preview(text: str, limit: int = 200) -> str:
    clean = text.strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


def compact_inline(text: str, limit: int = 120) -> str:
    collapsed = " ".join(text.strip().split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def is_structural_preview(text: str) -> bool:
    preview = text.strip()
    if not preview:
        return True
    markers = ("LLM Running", "🛠️ Tool:", "[Action]", "[Info]", "`````", "````text")
    return any(marker in preview for marker in markers)


def append_job_event(job_dir: Path, event: dict[str, Any]) -> dict[str, Any]:
    payload = dict(event)
    payload.setdefault("ts", now_iso())
    with job_store_lock(job_dir):
        append_text(job_events_path(job_dir), json.dumps(payload, ensure_ascii=False) + "\n")
    return payload


def read_recent_job_events(job_dir: Path, limit: int = 20) -> list[dict[str, Any]]:
    path = job_events_path(job_dir)
    with job_store_lock(job_dir):
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        for raw_line in read_text(path).splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                events.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
    if limit <= 0:
        return events
    return events[-limit:]


def attach_recent_events(record: dict[str, Any], job_dir: Path, *, limit: int = 20) -> dict[str, Any]:
    payload = dict(record)
    payload["recent_events"] = read_recent_job_events(job_dir, limit=limit)
    return payload


def iter_turn_numbers(text: str) -> list[int]:
    return [int(match.group(1)) for match in TURN_RE.finditer(text)]


def iter_tool_observations(text: str) -> list[tuple[str, str]]:
    tools: list[tuple[str, str]] = []
    for match in VERBOSE_TOOL_RE.finditer(text):
        tools.append((match.group(1), trim_preview(match.group(2) or "", 240)))
    if tools:
        return tools
    for match in COMPACT_TOOL_RE.finditer(text):
        tools.append((match.group(1).strip(), trim_preview(match.group(2) or "", 240)))
    return tools


def extract_action_summaries(text: str) -> list[str]:
    return [compact_inline(match.group(1), 160) for match in ACTION_RE.finditer(text)]


def summarize_tool_call(tool_name: str, args_preview: str) -> str:
    summary = compact_inline(args_preview, 120)
    stripped = args_preview.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            for key in ("path", "file_path", "url", "command", "text", "task"):
                value = str(payload.get(key, "")).strip()
                if value:
                    return f"{tool_name}: {compact_inline(value, 120)}"
    if summary:
        return f"{tool_name}: {summary}"
    return tool_name


def build_continue_hint(session_id: str) -> str:
    normalized = str(session_id or "").strip()
    if not normalized:
        return ""
    return f"{TOOL_NAME} revise-job --job {normalized} --feedback ..."


def build_job_continue_hint(state: str, session_id: str) -> str:
    if str(state or "") not in REVISABLE_JOB_STATES:
        return ""
    return build_continue_hint(session_id)


def build_session_continue_hint(state: str, session_id: str) -> str:
    if str(state or "") not in REVISABLE_SESSION_STATES:
        return ""
    return build_continue_hint(session_id)


def parse_updated_at_sort_key(value: str) -> tuple[str, str]:
    normalized = str(value or "").strip()
    return (normalized, normalized)


def iter_session_records() -> list[dict[str, Any]]:
    root = sessions_root()
    if not root.exists():
        return []
    items: list[dict[str, Any]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            items.append(read_job_record(child))
        except FileNotFoundError:
            continue
    return items


def iter_session_dirs() -> list[Path]:
    root = sessions_root()
    if not root.exists():
        return []
    return [child for child in root.iterdir() if child.is_dir()]


def load_session_record_for_revision(session_dir: Path) -> dict[str, Any]:
    record = read_job_record(session_dir)
    if str(record.get("state", "") or "") in REVISABLE_SESSION_STATES:
        return record
    try:
        return reconcile_session_record(session_dir)
    except FileNotFoundError:
        return record


def find_latest_revisable_session_id() -> str:
    candidates: list[dict[str, Any]] = []
    for session_dir in iter_session_dirs():
        try:
            item = load_session_record_for_revision(session_dir)
        except FileNotFoundError:
            continue
        session_id = str(item.get("session_id", "") or session_dir.name)
        if not session_id:
            continue
        if str(item.get("state", "") or "") not in REVISABLE_SESSION_STATES:
            continue
        candidates.append(item)
    if not candidates:
        raise ValueError("no revisable session found")
    candidates.sort(key=lambda item: (str(item.get("updated_at", "") or ""), str(item.get("session_id", "") or "")))
    return str(candidates[-1]["session_id"])


def resolve_revisable_session_id(job_or_session: str | None) -> str:
    if not job_or_session:
        return find_latest_revisable_session_id()
    target = str(job_or_session).strip()
    if not target:
        return find_latest_revisable_session_id()
    session_dir = session_dir_for(target)
    if session_dir.exists():
        record = load_session_record_for_revision(session_dir)
        state = str(record.get("state", "") or "")
        if state not in REVISABLE_SESSION_STATES:
            raise ValueError(f"session {target} is not revisable; current state: {state}")
        return target
    job_dir = job_dir_for(target)
    if job_dir.exists():
        record = read_job_record(job_dir)
        session_id = str(record.get("session_id", "") or "")
        if session_id:
            session_dir = session_dir_for(session_id)
            if session_dir.exists():
                session_record = load_session_record_for_revision(session_dir)
                state = str(session_record.get("state", "") or "")
                if state in REVISABLE_SESSION_STATES:
                    return session_id
                raise ValueError(f"job {target} points to session {session_id}, but it is not revisable; current state: {state}")
        mode = str(record.get("mode", "") or "")
        if mode == "legacy_job":
            raise ValueError(f"job {target} is a one-shot legacy run and cannot be revised; use {TOOL_NAME} start for resumable sessions")
        raise ValueError(f"job {target} does not have a resumable session_id; use {TOOL_NAME} start for resumable sessions")
    raise FileNotFoundError(f"job or session not found: {target}")


def session_output_filename(round_no: int) -> str:
    return "output.txt" if round_no <= 0 else f"output{round_no}.txt"


def session_output_path(task_dir: Path, round_no: int) -> Path:
    return task_dir / session_output_filename(round_no)


def session_round_from_output_name(name: str) -> int | None:
    if name == "output.txt":
        return 0
    match = re.fullmatch(r"output(\d+)\.txt", name)
    if not match:
        return None
    return int(match.group(1))


def strip_round_end_marker(text: str) -> str:
    if text.endswith(ROUND_END_MARKER):
        return text[: -len(ROUND_END_MARKER)]
    return text


def extract_final_response_text(text: str) -> str:
    raw = strip_round_end_marker(text or "").strip()
    if not raw:
        return ""
    segments = re.split(RUNNING_HEADER_RE, raw)
    cleaned_candidates: list[str] = []
    for segment in segments:
        if not segment.strip():
            continue
        marker = FINAL_RESPONSE_MARKER_RE.search(segment)
        candidate = segment[: marker.start()] if marker else segment
        candidate = TAG_BLOCK_RE.sub("", candidate)
        candidate = FENCED_BLOCK_RE.sub("", candidate)
        lines: list[str] = []
        for raw_line in candidate.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            if stripped.startswith("🛠️ Tool:"):
                continue
            if stripped.startswith("[Action]") or stripped.startswith("[Info]") or stripped.startswith("[Status]") or stripped.startswith("[Stdout]"):
                continue
            if stripped.startswith("```") or stripped.startswith("````"):
                continue
            lines.append(stripped)
        if lines:
            cleaned_candidates.append("\n".join(lines).strip())
    if cleaned_candidates:
        return cleaned_candidates[-1]
    return raw


def list_session_outputs(task_dir: Path) -> list[tuple[int, Path]]:
    outputs: list[tuple[int, Path]] = []
    if not task_dir.exists():
        return outputs
    for child in task_dir.iterdir():
        round_no = session_round_from_output_name(child.name)
        if round_no is None or not child.is_file():
            continue
        outputs.append((round_no, child))
    outputs.sort(key=lambda item: item[0])
    return outputs


def observe_progress_chunk(job_dir: Path, chunk: str) -> dict[str, Any]:
    record = read_job_record(job_dir)
    delta_chars = len(chunk)
    delta_bytes = len(chunk.encode("utf-8"))
    total_chars = int(record.get("progress_chars_total", 0) or 0) + delta_chars
    total_bytes = int(record.get("progress_bytes_total", 0) or 0) + delta_bytes
    progress_event = append_job_event(
        job_dir,
        {
            "event": "progress",
            "chars_delta": delta_chars,
            "chars_total": total_chars,
            "bytes_delta": delta_bytes,
            "bytes_total": total_bytes,
            "preview": trim_preview(chunk, 140),
        },
    )
    updates: dict[str, Any] = {
        "latest_progress": chunk,
        "latest_preview": trim_preview(chunk),
        "progress_chars_total": progress_event["chars_total"],
        "progress_bytes_total": progress_event["bytes_total"],
        "progress_last_delta_chars": progress_event["chars_delta"],
        "progress_last_delta_bytes": progress_event["bytes_delta"],
        "progress_chunks": int(record.get("progress_chunks", 0) or 0) + 1,
        "progress_last_at": progress_event["ts"],
    }
    turn_numbers = iter_turn_numbers(chunk)
    if turn_numbers:
        for turn in turn_numbers:
            append_job_event(job_dir, {"event": "turn", "turn": turn})
        updates["last_turn"] = turn_numbers[-1]
    tool_observations = iter_tool_observations(chunk)
    if tool_observations:
        for tool_name, args_preview in tool_observations:
            append_job_event(
                job_dir,
                {
                    "event": "tool",
                    "tool_name": tool_name,
                    "args_preview": args_preview,
                    "summary": summarize_tool_call(tool_name, args_preview),
                    "turn": updates.get("last_turn", record.get("last_turn")),
                },
            )
        last_tool_name, last_tool_args_preview = tool_observations[-1]
        updates["last_tool_name"] = last_tool_name
        updates["last_tool_args_preview"] = last_tool_args_preview
    action_summaries = extract_action_summaries(chunk)
    if action_summaries:
        for summary in action_summaries:
            append_job_event(job_dir, {"event": "action", "summary": summary, "turn": updates.get("last_turn", record.get("last_turn"))})
        updates["last_action_summary"] = action_summaries[-1]
    return update_job_record(job_dir, **updates)


def windows_path(path: Path) -> str:
    resolved = path.resolve()
    text = str(resolved)
    if text.startswith("/mnt/") and len(text) > 6:
        drive = text[5].upper()
        rest = text[6:].replace("/", "\\")
        return f"{drive}:{rest}"
    return text.replace("/", "\\")


def resolve_skill_path(skill_spec: str) -> Path:
    spec = skill_spec.strip()
    if not spec:
        raise ValueError("skill spec cannot be empty")
    candidates: list[Path] = []
    raw = Path(spec).expanduser()
    if raw.name == "SKILL.md":
        candidates.append(raw)
    else:
        candidates.append(raw / "SKILL.md" if raw.suffix == "" else raw)
        candidates.append(skills_root() / spec / "SKILL.md")
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"skill not found: {skill_spec}")


def summarize_skill_content(content: str, *, max_lines: int = 8, max_chars: int = 1200) -> str:
    lines = content.splitlines()
    summary_lines: list[str] = []
    in_frontmatter = False
    frontmatter_done = False
    for index, raw_line in enumerate(lines):
        line = raw_line.rstrip()
        stripped = line.strip()
        if index == 0 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
                frontmatter_done = True
                continue
            if stripped.startswith("description:"):
                summary_lines.append(stripped)
            continue
        if not stripped:
            continue
        if not frontmatter_done and stripped.startswith("description:"):
            summary_lines.append(stripped)
            continue
        summary_lines.append(stripped)
        if len(summary_lines) >= max_lines:
            break
    return trim_preview("\n".join(summary_lines), max_chars)


def normalized_skill_stage_name(skill_spec: str, path: Path, index: int) -> str:
    base = Path(skill_spec).stem if skill_spec.strip() else path.parent.name
    if base.upper() == "SKILL":
        base = path.parent.name
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip(".-") or f"skill-{index}"
    return safe


def normalized_task_file_stage_name(path: Path, index: int) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip(".-") or f"file-{index}"
    suffix = path.suffix or ".txt"
    return f"{index:02d}-{stem}{suffix}"


def normalized_task_dir_stage_name(path: Path, index: int) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", path.name).strip(".-") or f"dir-{index}"
    return f"{index:02d}-{base}"


def stage_skill_bundle(skill_bundle: list[dict[str, str]], skill_stage_root: Path) -> list[dict[str, str]]:
    staged_bundle: list[dict[str, str]] = []
    ensure_dir(skill_stage_root)
    for index, item in enumerate(skill_bundle, start=1):
        stage_dir = skill_stage_root / normalized_skill_stage_name(item["spec"], Path(item["path"]), index)
        ensure_dir(stage_dir)
        staged_path = stage_dir / "SKILL.md"
        write_text(staged_path, item["content"])
        staged_bundle.append({**item, "staged_path": str(staged_path.resolve())})
    return staged_bundle


def load_skill_bundle(skill_specs: list[str], *, skill_stage_root: Path | None = None) -> list[dict[str, str]]:
    bundles: list[dict[str, str]] = []
    for skill_spec in skill_specs:
        path = resolve_skill_path(skill_spec)
        content = path.read_text(encoding="utf-8")
        bundles.append(
            {
                "spec": skill_spec,
                "path": str(path),
                "content": content,
                "summary": summarize_skill_content(content),
            }
        )
    if skill_stage_root is not None and bundles:
        return stage_skill_bundle(bundles, skill_stage_root)
    return bundles


def render_skill_injection(skills: list[dict[str, str]], *, skill_mode: str = "summary") -> str:
    if not skills:
        return ""
    normalized_mode = (skill_mode or "summary").strip().lower()
    if normalized_mode not in {"summary", "full"}:
        raise ValueError(f"invalid skill mode: {skill_mode}")
    parts = ["### Required skill documents"]
    if normalized_mode == "summary":
        parts.extend(
            [
                "CLI 已把本地 skill staged 到可读取路径. 你必须先 file_read 每个 staged SKILL.md, 提取与当前任务直接相关的硬约束和风格要求, 用 update_working_checkpoint 记录后再执行.",
                "不要把这些 skill 当作可忽略参考, 也不要只依赖下面的摘要.",
                "在读取完下面列出的所有 staged_path 之前, 不要开始浏览器操作, GUI 操作, 文件写入, 或其他执行步骤.",
            ]
        )
        for item in skills:
            parts.extend(
                [
                    "",
                    f"#### Skill source: {item['path']}",
                    f"- staged_path: {item.get('staged_path', item['path'])}",
                    "- required_action: file_read this staged_path before any execution",
                    "- concise_summary:",
                    "```text",
                    item["summary"].rstrip(),
                    "```",
                ]
    )
    return "\n".join(parts).rstrip() + "\n"


def extract_local_task_paths(task_text: str) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for match in LOCAL_TASK_FILE_RE.finditer(task_text or ""):
        candidate = match.group("path").rstrip(".,;:!?'\"")
        path = Path(candidate).expanduser()
        if not path.exists() or (not path.is_file() and not path.is_dir()):
            continue
        resolved = str(path.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        paths.append(Path(resolved))
    return paths


def task_needs_wsl_bridge(task_text: str, source_paths: list[Path]) -> bool:
    lowered = (task_text or "").lower()
    if "wsl" in lowered or "linux" in lowered:
        return True
    return any(str(path).startswith(("/home/", "/mnt/")) for path in source_paths)


def build_task_dir_manifest(entries: list[tuple[Path, int]], *, skipped: list[str]) -> str:
    lines = ["# Staged directory manifest", ""]
    if entries:
        lines.extend(["## Included files", ""])
        for rel_path, size in entries:
            lines.append(f"- {rel_path.as_posix()} ({size} bytes)")
    else:
        lines.extend(["## Included files", "", "- (none)"])
    lines.extend(["", "## Skipped", ""])
    if skipped:
        lines.extend(f"- {item}" for item in skipped)
    else:
        lines.append("- (none)")
    return "\n".join(lines).rstrip() + "\n"


def build_wsl_bridge_readme(*, windows_ps1_path: str, windows_cmd_path: str, windows_meta_path: str, distro: str, default_cwd: str) -> str:
    return (
        "# WSL bridge for GA\n\n"
        "Use this helper whenever you need direct WSL access from Windows.\n\n"
        "Rules:\n"
        f"- Use explicit wsl binary: `{default_wsl_exe_win()}`.\n"
        f"- Use distro: `{distro}`.\n"
        "- Do not use bare `wsl`.\n"
        "- Do not use `\\\\wsl.localhost\\...`.\n"
        "- Prefer this helper over ad-hoc PowerShell quoting.\n"
        "- Prefer `bridge_cmd` first because it pins `pwsh.exe` explicitly.\n"
        "- Do not invoke `wsl-bridge.ps1` via direct `& .\\wsl-bridge.ps1 ...` inside an arbitrary host PowerShell.\n"
        f"- Metadata file: `{windows_meta_path}`.\n"
        "- For reliable output, read `wsl-last-run.json` first; if stdout also contains JSON, treat them as the same summary.\n\n"
        "CMD example:\n"
        "```cmd\n"
        f"\"{windows_cmd_path}\" -LinuxCommand \"pwd\" -Cwd \"{default_cwd}\"\n"
        "```\n\n"
        "Explicit pwsh example:\n"
        "```powershell\n"
        f"pwsh.exe -NoLogo -NoProfile -File '{windows_ps1_path}' -LinuxCommand 'pwd' -Cwd '{default_cwd}'\n"
        "```\n"
    )


def build_wsl_bridge_ps1(*, distro: str, default_cwd: str) -> str:
    return "\n".join(
        [
            "param(",
            "  [Parameter(Mandatory=$true)][string]$LinuxCommand,",
            f"  [string]$Distro = '{distro}',",
            f"  [string]$Cwd = '{default_cwd}',",
            "  [string]$OutFile = $(Join-Path $PSScriptRoot 'wsl-stdout.txt'),",
            "  [string]$ErrFile = $(Join-Path $PSScriptRoot 'wsl-stderr.txt'),",
            "  [string]$MetaFile = $(Join-Path $PSScriptRoot 'wsl-last-run.json')",
            ")",
            "$ErrorActionPreference = 'Stop'",
            "$utf8 = [System.Text.UTF8Encoding]::new($false)",
            "[Console]::OutputEncoding = $utf8",
            "$psi = [System.Diagnostics.ProcessStartInfo]::new()",
            f"$psi.FileName = '{default_wsl_exe_win()}'",
            "$null = $psi.ArgumentList.Add('-d')",
            "$null = $psi.ArgumentList.Add($Distro)",
            "$null = $psi.ArgumentList.Add('--cd')",
            "$null = $psi.ArgumentList.Add($Cwd)",
            "$null = $psi.ArgumentList.Add('sh')",
            "$null = $psi.ArgumentList.Add('-lc')",
            "$null = $psi.ArgumentList.Add($LinuxCommand)",
            "$psi.UseShellExecute = $false",
            "$psi.RedirectStandardOutput = $true",
            "$psi.RedirectStandardError = $true",
            "$psi.StandardOutputEncoding = $utf8",
            "$psi.StandardErrorEncoding = $utf8",
            "$proc = [System.Diagnostics.Process]::new()",
            "$proc.StartInfo = $psi",
            "$null = $proc.Start()",
            "$stdout = $proc.StandardOutput.ReadToEnd()",
            "$stderr = $proc.StandardError.ReadToEnd()",
            "$proc.WaitForExit()",
            "[System.IO.File]::WriteAllText($OutFile, $stdout, $utf8)",
            "[System.IO.File]::WriteAllText($ErrFile, $stderr, $utf8)",
            "$summary = @{",
            "  exit_code = $proc.ExitCode",
            "  distro = $Distro",
            "  cwd = $Cwd",
            "  out_file = $OutFile",
            "  err_file = $ErrFile",
            "  meta_file = $MetaFile",
            "} | ConvertTo-Json -Compress",
            "[System.IO.File]::WriteAllText($MetaFile, $summary, $utf8)",
            "Write-Output $summary",
        ]
    ) + "\n"


def build_wsl_bridge_cmd() -> str:
    return "\n".join(
        [
            "@echo off",
            "setlocal",
            "pwsh.exe -NoLogo -NoProfile -File \"%~dp0wsl-bridge.ps1\" %*",
            "exit /b %ERRORLEVEL%",
        ]
    ) + "\n"


def stage_wsl_bridge_bundle(task_file_stage_root: Path, *, distro: str, default_cwd: str) -> dict[str, str]:
    stage_dir = task_file_stage_root / "00-wsl-bridge"
    ensure_dir(stage_dir)
    readme_path = stage_dir / "README.txt"
    ps1_path = stage_dir / "wsl-bridge.ps1"
    cmd_path = stage_dir / "wsl-bridge.cmd"
    meta_path = stage_dir / "wsl-last-run.json"
    write_text(ps1_path, build_wsl_bridge_ps1(distro=distro, default_cwd=default_cwd))
    write_text(cmd_path, build_wsl_bridge_cmd())
    write_text(
        readme_path,
        build_wsl_bridge_readme(
            windows_ps1_path=windows_path(ps1_path),
            windows_cmd_path=windows_path(cmd_path),
            windows_meta_path=windows_path(meta_path),
            distro=distro,
            default_cwd=default_cwd,
        ),
    )
    return {
        "kind": "wsl_bridge",
        "source_path": f"WSL bridge helper for distro {distro}",
        "staged_path": str(readme_path.resolve()),
        "windows_staged_path": windows_path(readme_path),
        "windows_ps1_path": windows_path(ps1_path),
        "windows_cmd_path": windows_path(cmd_path),
        "windows_meta_path": windows_path(meta_path),
        "distro": distro,
        "default_cwd": default_cwd,
    }


def stage_task_file_bundle(task_text: str, *, task_file_stage_root: Path | None = None) -> list[dict[str, str]]:
    if task_file_stage_root is None:
        return []
    source_paths = extract_local_task_paths(task_text)
    if not source_paths and "wsl" not in (task_text or "").lower() and "linux" not in (task_text or "").lower():
        return []
    ensure_dir(task_file_stage_root)
    staged: list[dict[str, str]] = []
    if task_needs_wsl_bridge(task_text, source_paths):
        staged.append(
            stage_wsl_bridge_bundle(
                task_file_stage_root,
                distro=default_wsl_distro(),
                default_cwd=default_wsl_cwd(),
            )
        )
    for index, source_path in enumerate(source_paths, start=1):
        if source_path.is_file():
            staged_path = task_file_stage_root / normalized_task_file_stage_name(source_path, index)
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, staged_path)
            staged.append(
                {
                    "kind": "file",
                    "source_path": str(source_path),
                    "staged_path": str(staged_path.resolve()),
                    "windows_staged_path": windows_path(staged_path),
                }
            )
            continue
        stage_dir = task_file_stage_root / normalized_task_dir_stage_name(source_path, index)
        ensure_dir(stage_dir)
        manifest_path = stage_dir / "_MANIFEST.txt"
        included: list[tuple[Path, int]] = []
        skipped: list[str] = []
        total_bytes = 0
        file_count = 0
        for child in sorted(path for path in source_path.rglob("*") if path.is_file()):
            rel_path = child.relative_to(source_path)
            size = child.stat().st_size
            if size > MAX_TASK_FILE_BYTES:
                skipped.append(f"{rel_path.as_posix()} exceeds {MAX_TASK_FILE_BYTES} bytes")
                continue
            if file_count >= MAX_TASK_STAGE_FILE_COUNT:
                skipped.append(f"remaining files skipped after {MAX_TASK_STAGE_FILE_COUNT} files")
                break
            if total_bytes + size > MAX_TASK_STAGE_TOTAL_BYTES:
                skipped.append(f"remaining files skipped after {MAX_TASK_STAGE_TOTAL_BYTES} staged bytes")
                break
            dest = stage_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(child, dest)
            included.append((rel_path, size))
            total_bytes += size
            file_count += 1
        write_text(manifest_path, build_task_dir_manifest(included, skipped=skipped))
        staged.append(
            {
                "kind": "directory",
                "source_path": str(source_path),
                "staged_path": str(manifest_path.resolve()),
                "windows_staged_path": windows_path(manifest_path),
                "staged_dir": str(stage_dir.resolve()),
                "windows_staged_dir": windows_path(stage_dir),
                "file_count": str(file_count),
                "total_bytes": str(total_bytes),
            }
        )
    return staged


def render_task_file_injection(task_files: list[dict[str, str]]) -> str:
    if not task_files:
        return ""
    parts = [
        "### Required local task files",
        "CLI 已自动把用户任务里直接提到的本地文件或目录复制到 Windows 可见 staged_path.",
        "当 source_path 是 Linux/WSL 路径时, staged_path 才是当前任务的权威可读副本.",
        "禁止自行猜测 Linux 路径到 Windows 路径的映射.",
        "禁止自行尝试 `\\\\wsl.localhost\\...` 或其他 UNC 路径绕过 staged_path.",
    ]
    for item in task_files:
        if item.get("kind") == "wsl_bridge":
            parts.extend(
                [
                    "",
                    "#### WSL bridge helper",
                    f"- bridge_readme: {item['windows_staged_path']}",
                    f"- bridge_ps1: {item['windows_ps1_path']}",
                    f"- bridge_cmd: {item['windows_cmd_path']}",
                    f"- bridge_meta: {item['windows_meta_path']}",
                    f"- distro: {item['distro']}",
                    f"- default_cwd: {item['default_cwd']}",
                    "- required_action: file_read bridge_readme before any direct WSL access",
                    "- required_rule: when you need direct WSL access, prefer bridge_cmd first; do not use bare `wsl` and do not use `\\\\wsl.localhost\\...`",
                    "- required_rule: do not invoke bridge_ps1 by direct `& <script>.ps1`; if you need the ps1 path, call it through explicit `pwsh.exe -File`",
                    "- required_rule: after running bridge_cmd or bridge_ps1, read bridge_meta first; then read `out_file` / `err_file` from that metadata",
                ]
            )
            continue
        if item.get("kind") == "directory":
            parts.extend(
                [
                    "",
                    f"#### Source directory: {item['source_path']}",
                    f"- manifest_path: {item['windows_staged_path']}",
                    f"- staged_dir: {item['windows_staged_dir']}",
                    f"- staged_file_count: {item['file_count']}",
                    f"- staged_total_bytes: {item['total_bytes']}",
                    "- required_action: file_read manifest_path first, then only read files under staged_dir",
                ]
            )
            continue
        parts.extend(
            [
                "",
                f"#### Source path: {item['source_path']}",
                f"- staged_path: {item['windows_staged_path']}",
                "- required_action: file_read this staged_path before continuing analysis or review",
            ]
        )
    return "\n".join(parts).rstrip() + "\n"


def build_effective_task_text(
    task_text: str,
    *,
    skill_specs: list[str] | None = None,
    subagent_policy: str = "auto",
    skill_mode: str = "summary",
    skill_stage_root: Path | None = None,
    task_file_stage_root: Path | None = None,
    profile: str | None = None,
) -> tuple[str, list[dict[str, str]], list[dict[str, str]]]:
    normalized_policy = (subagent_policy or "auto").strip().lower()
    if normalized_policy not in {"auto", "off", "force"}:
        raise ValueError(f"invalid subagent policy: {subagent_policy}")
    skill_specs = [item for item in (skill_specs or []) if str(item).strip()]
    skill_bundle = load_skill_bundle(skill_specs, skill_stage_root=skill_stage_root)
    task_file_bundle = stage_task_file_bundle(task_text, task_file_stage_root=task_file_stage_root)
    parts = []
    normalized_profile = (profile or "").strip().lower()
    if normalized_profile:
        if normalized_profile not in PROFILE_PROMPT_TEXT:
            raise ValueError(f"invalid profile: {profile}")
        parts.append(PROFILE_PROMPT_TEXT[normalized_profile])
    if normalized_policy == "auto":
        parts.append(SUBAGENT_POLICY_TEXT.strip())
    elif normalized_policy == "force":
        parts.append(
            (
                SUBAGENT_POLICY_TEXT
                + "\n### Force subagent requirement\n"
                + "这次任务必须显式使用 subagent workflow. 至少要有一个 subagent 负责探索, 或一个 subagent 负责独立验证. 主 agent 不可独占完成全部工作.\n"
            ).strip()
        )
    skill_text = render_skill_injection(skill_bundle, skill_mode=skill_mode)
    if skill_text:
        parts.append(skill_text.strip())
    task_file_text = render_task_file_injection(task_file_bundle)
    if task_file_text:
        parts.append(task_file_text.strip())
    parts.append("### User task")
    parts.append(task_text.strip())
    return "\n\n".join(part for part in parts if part).strip() + "\n", skill_bundle, task_file_bundle


def write_windows_runner_assets(job_id: str, task_text: str, llm_no: int) -> tuple[Path, Path, Path, Path]:
    temp_root = ensure_dir(windows_temp_root_wsl())
    prompt_wsl = temp_root / f"{job_id}.prompt.txt"
    events_wsl = temp_root / f"{job_id}.events.jsonl"
    runner_wsl = temp_root / f"{job_id}.runner.py"
    cmd_wsl = temp_root / f"{job_id}.runner.cmd"
    write_text(prompt_wsl, task_text)
    write_text(events_wsl, "")
    runner_code = "\n".join(
        [
            "from __future__ import annotations",
            "import json",
            "import os",
            "import sys",
            "import threading",
            f"repo = r'''{ga_root_win()}'''",
            "sys.path.insert(0, repo)",
            "os.chdir(repo)",
            "try:",
            "    from agentmain import GenericAgent as AgentClass",
            "except ImportError:",
            "    from agentmain import GeneraticAgent as AgentClass",
            "",
            "def emit(event_path: str, event: str, **payload):",
            "    with open(event_path, 'a', encoding='utf-8') as handle:",
            "        handle.write(json.dumps({'event': event, **payload}, ensure_ascii=False) + '\\n')",
            "",
            "def main() -> int:",
            "    prompt_path = sys.argv[1]",
            "    event_path = sys.argv[2]",
            "    task = open(prompt_path, 'r', encoding='utf-8').read()",
            "    agent = AgentClass()",
            "    if not agent.llmclients:",
            "        emit(event_path, 'error', message='No configured LLM clients.')",
            "        return 2",
            "    agent.verbose = True",
            "    agent.inc_out = True",
            f"    agent.next_llm({llm_no})",
            "    emit(event_path, 'started', windows_pid=os.getpid(), llm_client_count=len(agent.llmclients))",
            "    threading.Thread(target=agent.run, daemon=True).start()",
            "    dq = agent.put_task(task, source='user')",
            "    while True:",
            "        item = dq.get()",
            "        if 'next' in item:",
            "            emit(event_path, 'progress', text=item['next'])",
            "        if 'done' in item:",
            "            emit(event_path, 'done', text=item['done'])",
            "            return 0",
            "",
            "if __name__ == '__main__':",
            "    try:",
            "        raise SystemExit(main())",
            "    except SystemExit:",
            "        raise",
            "    except Exception as exc:",
            "        emit(sys.argv[2], 'error', message=str(exc))",
            "        raise",
        ]
    )
    write_text(runner_wsl, runner_code + "\n")
    cmd_lines = [
        "@echo off",
        "setlocal",
        "set PYTHONUTF8=1",
        f'cd /d "{ga_root_win()}"',
        f'".venv\\Scripts\\python.exe" "{windows_path(runner_wsl)}" "{windows_path(prompt_wsl)}" "{windows_path(events_wsl)}"',
    ]
    write_text(cmd_wsl, "\n".join(cmd_lines) + "\n")
    return prompt_wsl, events_wsl, runner_wsl, cmd_wsl


def cleanup_windows_runner_assets(*paths: Path) -> None:
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def stop_windows_process(pid: int) -> None:
    command = [
        "pwsh.exe",
        "-NoLogo",
        "-NoProfile",
        "-Command",
        f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue",
    ]
    subprocess.run(command, text=True, capture_output=True, check=False)


def choose_default_llm_no(options: list[dict[str, Any]]) -> int:
    if not options:
        return 0
    for option in options:
        name = str(option.get("name", "")).lower()
        if "glm-5.1" in name or "glm5.1" in name or "glm" in name:
            return int(option.get("llm_no", 0))
    return int(options[0].get("llm_no", 0))


def normalize_doctor_llm_options(
    options: list[dict[str, Any]],
    default_llm_no: int,
) -> tuple[list[dict[str, Any]], int | None, str]:
    normalized: list[dict[str, Any]] = []
    upstream_selected_llm_no: int | None = None
    default_llm_name = ""
    for option in options:
        llm_no = int(option.get("llm_no", 0))
        upstream_selected = bool(option.get("selected"))
        if upstream_selected and upstream_selected_llm_no is None:
            upstream_selected_llm_no = llm_no
        if llm_no == int(default_llm_no):
            default_llm_name = str(option.get("name", "") or "")
        normalized.append(
            {
                **option,
                "selected": llm_no == int(default_llm_no),
                "upstream_selected": upstream_selected,
            }
        )
    return normalized, upstream_selected_llm_no, default_llm_name


def probe_llm_inventory() -> tuple[list[dict[str, Any]], str]:
    if not ga_root_wsl().exists():
        return [], "GA root missing"
    temp_root = ensure_dir(windows_temp_root_wsl())
    last_error = "probe returned empty output"
    for _attempt in range(2):
        token = uuid.uuid4().hex[:8]
        probe_wsl = temp_root / f"ga_probe_llm_clients.{token}.py"
        result_wsl = temp_root / f"ga_probe_llm_clients.{token}.json"
        cmd_wsl = temp_root / f"ga_probe_llm_clients.{token}.cmd"
        probe_code = "\n".join(
            [
                "from __future__ import annotations",
                "import json",
                "import os",
                "import sys",
                f"repo = r'''{ga_root_win()}'''",
                "sys.path.insert(0, repo)",
                "os.chdir(repo)",
                "try:",
                "    from agentmain import GenericAgent as AgentClass",
                "except ImportError:",
                "    from agentmain import GeneraticAgent as AgentClass",
                "agent = AgentClass()",
                "result_path = sys.argv[1]",
                "with open(result_path, 'w', encoding='utf-8') as handle:",
                "    json.dump({'llm_client_count': len(agent.llmclients), 'llm_options': [{'llm_no': no, 'name': name, 'selected': selected} for no, name, selected in agent.list_llms()]}, handle, ensure_ascii=False)",
            ]
        )
        cmd_lines = [
            "@echo off",
            "setlocal",
            "set PYTHONUTF8=1",
            f'cd /d "{ga_root_win()}"',
            f'".venv\\Scripts\\python.exe" "{windows_path(probe_wsl)}" "{windows_path(result_wsl)}"',
            "exit /b %ERRORLEVEL%",
        ]
        write_text(probe_wsl, probe_code + "\n")
        write_text(result_wsl, "")
        write_text(cmd_wsl, "\n".join(cmd_lines) + "\n")
        try:
            result = subprocess.run(
                ["cmd.exe", "/d", "/s", "/c", windows_path(cmd_wsl)],
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            stderr_text = (result.stderr or "").strip()
            stdout_text = (result.stdout or "").strip()
            if result.returncode != 0:
                last_error = stderr_text or stdout_text or f"probe failed with {result.returncode}"
                continue
            deadline = time.time() + 2.0
            while time.time() <= deadline:
                if result_wsl.exists() and result_wsl.read_text(encoding="utf-8").strip():
                    break
                time.sleep(0.05)
            if not result_wsl.exists() or not result_wsl.read_text(encoding="utf-8").strip():
                last_error = stderr_text or stdout_text or "probe returned empty output"
                continue
            payload = json.loads(result_wsl.read_text(encoding="utf-8"))
            return list(payload.get("llm_options", [])), ""
        except Exception as exc:
            last_error = str(exc)
        finally:
            cleanup_windows_runner_assets(probe_wsl, result_wsl, cmd_wsl)
    return [], last_error


def probe_installation() -> dict[str, Any]:
    root_wsl = ga_root_wsl()
    webui_bin = ga_webui_bin()
    payload = {
        "tool": TOOL_NAME,
        "ga_root_win": ga_root_win(),
        "ga_root_wsl": str(root_wsl),
        "jobs_root": str(jobs_root()),
        "ga_webui_bin": str(webui_bin),
        "ga_root_exists": root_wsl.exists(),
        "venv_python_exists": (root_wsl / ".venv" / "Scripts" / "python.exe").exists(),
        "agentmain_exists": (root_wsl / "agentmain.py").exists(),
        "mykey_exists": (root_wsl / "mykey.py").exists(),
        "ga_webui_exists": webui_bin.exists(),
        "llm_client_count": 0,
        "llm_options": [],
        "default_llm_no": 0,
        "llm_probe_error": "",
    }
    if payload["ga_root_exists"] and payload["venv_python_exists"] and payload["agentmain_exists"]:
        llm_options, llm_error = probe_llm_inventory()
        payload["llm_options"] = llm_options
        payload["llm_client_count"] = len(llm_options)
        payload["default_llm_no"] = choose_default_llm_no(llm_options)
        payload["llm_probe_error"] = llm_error
    else:
        payload["llm_probe_error"] = "GA runtime files are incomplete"
    return payload


def ensure_ready_for_task() -> dict[str, Any]:
    payload = probe_installation()
    missing = []
    if not payload["ga_root_exists"]:
        missing.append("GA root missing")
    if not payload["venv_python_exists"]:
        missing.append("GA venv python missing")
    if not payload["agentmain_exists"]:
        missing.append("agentmain.py missing")
    if not payload["mykey_exists"]:
        missing.append("mykey.py missing")
    if payload["llm_client_count"] <= 0:
        detail = payload["llm_probe_error"] or "no configured llm clients"
        missing.append(detail)
    if missing:
        raise RuntimeError("; ".join(missing))
    return payload


def create_job(
    task_text: str,
    *,
    prompt_text: str | None = None,
    job_id: str | None = None,
    llm_no: int | None = None,
    subagent_policy: str = "auto",
    skill_mode: str = "summary",
    profile: str | None = None,
    skill_paths: list[str] | None = None,
    skill_stage_paths: list[str] | None = None,
    task_file_paths: list[str] | None = None,
    task_file_stage_paths: list[str] | None = None,
) -> tuple[Path, dict[str, Any]]:
    chosen_id = job_id or make_job_id()
    job_dir = job_dir_for(chosen_id)
    if job_dir.exists():
        raise FileExistsError(f"job already exists: {chosen_id}")
    ensure_dir(job_dir)
    write_text(job_dir / "prompt.txt", prompt_text if prompt_text is not None else task_text)
    record = {
        "tool": TOOL_NAME,
        "job_id": chosen_id,
        "state": "queued",
        "task_text": task_text,
        "task_preview": trim_preview(task_text, 120),
        "prompt_injected": (prompt_text if prompt_text is not None else task_text) != task_text,
        "subagent_policy": subagent_policy,
        "skill_mode": skill_mode,
        "profile": profile or "",
        "skill_paths": list(skill_paths or []),
        "skill_stage_paths": list(skill_stage_paths or []),
        "task_file_paths": list(task_file_paths or []),
        "task_file_stage_paths": list(task_file_stage_paths or []),
        "created_at": now_iso(),
        "runner_pid": None,
        "windows_pid": None,
        "llm_no": llm_no,
        "exit_code": None,
        "result_text": "",
        "result_preview": "",
        "last_error": "",
    }
    return job_dir, write_job_record(job_dir, record)


def create_session(
    task_text: str,
    *,
    prompt_text: str,
    session_id: str | None = None,
    llm_no: int | None = None,
    subagent_policy: str = "auto",
    skill_mode: str = "summary",
    profile: str | None = None,
    skill_paths: list[str] | None = None,
    skill_stage_paths: list[str] | None = None,
    task_file_paths: list[str] | None = None,
    task_file_stage_paths: list[str] | None = None,
) -> tuple[Path, dict[str, Any]]:
    chosen_id = validate_session_id(session_id or make_session_id())
    session_dir = session_dir_for(chosen_id)
    task_dir = session_task_dir_wsl(chosen_id)
    if session_dir.exists():
        raise FileExistsError(f"session already exists: {chosen_id}")
    if task_dir.exists():
        existing_entries = [child.name for child in task_dir.iterdir() if child.name not in {"skills", "task-files"}]
        if existing_entries:
            raise FileExistsError(f"upstream task dir already exists: {task_dir}")
    ensure_dir(session_dir)
    ensure_dir(task_dir)
    write_text(session_dir / "prompt.txt", prompt_text)
    write_text(task_dir / "input.txt", prompt_text)
    record = {
        "tool": TOOL_NAME,
        "mode": "session",
        "session_id": chosen_id,
        "state": "starting",
        "task_text": task_text,
        "task_preview": trim_preview(task_text, 120),
        "prompt_injected": prompt_text != task_text,
        "subagent_policy": subagent_policy,
        "skill_mode": skill_mode,
        "profile": profile or "",
        "skill_paths": list(skill_paths or []),
        "skill_stage_paths": list(skill_stage_paths or []),
        "task_file_paths": list(task_file_paths or []),
        "task_file_stage_paths": list(task_file_stage_paths or []),
        "created_at": now_iso(),
        "started_at": None,
        "runner_pid": None,
        "windows_pid": None,
        "llm_no": llm_no,
        "task_dir": str(task_dir),
        "current_round": 0,
        "completed_rounds": 0,
        "result_text": "",
        "result_preview": "",
        "last_error": "",
    }
    return session_dir, write_job_record(session_dir, record)


def launch_session_backend(session_id: str, llm_no: int) -> dict[str, Any]:
    task_dir = session_task_dir_wsl(session_id)
    session_dir = session_dir_for(session_id)
    temp_root = ensure_dir(windows_temp_root_wsl())
    cmd_wsl = temp_root / f"{session_id}.session-start.cmd"
    cmd_lines = [
        "@echo off",
        "setlocal",
        "set PYTHONUTF8=1",
        f'cd /d "{ga_root_win()}"',
        f'".venv\\Scripts\\python.exe" "agentmain.py" --task "{session_id}" --llm_no {int(llm_no)} --verbose',
    ]
    write_text(cmd_wsl, "\n".join(cmd_lines) + "\n")
    stdout_handle = (session_dir / "session.stdout.log").open("a", encoding="utf-8")
    stderr_handle = (session_dir / "session.stderr.log").open("a", encoding="utf-8")
    child_env = os.environ.copy()
    child_env["WIN_SHELL_UTF8_TIMEOUT_SEC"] = "0"
    process = subprocess.Popen(
        ["cmd.exe", "/c", windows_path(cmd_wsl)],
        text=True,
        encoding="utf-8",
        env=child_env,
        stdout=stdout_handle,
        stderr=stderr_handle,
        start_new_session=True,
    )
    stdout_handle.close()
    stderr_handle.close()
    return {
        "runner_pid": process.pid,
        "task_dir": str(task_dir),
    }


def reconcile_session_record(session_dir: Path, *, event_limit: int = 20) -> dict[str, Any]:
    record = read_job_record(session_dir)
    task_dir = Path(str(record.get("task_dir", ""))).expanduser()
    outputs = list_session_outputs(task_dir)
    latest_round = outputs[-1][0] if outputs else 0
    latest_output_path = outputs[-1][1] if outputs else session_output_path(task_dir, 0)
    latest_output_text = read_text(latest_output_path) if latest_output_path.exists() else ""
    latest_output_has_round_end = latest_output_text.endswith(ROUND_END_MARKER)
    completed_rounds = 0
    for _round_no, path in outputs:
        if read_text(path).endswith(ROUND_END_MARKER):
            completed_rounds += 1
    windows_pid = int(record.get("windows_pid") or 0) if record.get("windows_pid") is not None else None
    runner_pid = int(record.get("runner_pid") or 0) if record.get("runner_pid") is not None else None
    running = pid_is_running(runner_pid)
    reply_pending = False
    reply_path = task_dir / "reply.txt"
    if reply_path.exists():
        reply_pending = bool(read_text(reply_path).strip())
    result_text = extract_final_response_text(latest_output_text) if latest_output_has_round_end else ""
    if record.get("state") == "completed" or record.get("completed_at"):
        state = "completed"
    elif running:
        state = "waiting_reply" if latest_output_has_round_end and not reply_pending else "running_round"
    else:
        if record.get("state") == "stop_requested" or (session_dir / "stop.request").exists():
            state = "stopped"
        elif latest_output_has_round_end:
            state = "expired"
        else:
            state = "failed"
    payload = {
        **record,
        "state": state,
        "runner_pid": runner_pid,
        "current_round": latest_round,
        "completed_rounds": completed_rounds,
        "windows_pid": windows_pid,
        "runner_pid_running": running,
        "task_dir_exists": task_dir.exists(),
        "latest_output_path": str(latest_output_path),
        "latest_output_has_round_end": latest_output_has_round_end,
        "latest_output_text": latest_output_text,
        "reply_pending": reply_pending,
        "result_text": result_text,
        "result_preview": trim_preview(result_text or latest_output_text),
    }
    return attach_recent_events(payload, session_dir, limit=event_limit)


def spawn_worker(job_id: str, llm_no: int) -> int:
    job_dir = job_dir_for(job_id)
    stdout_handle = (job_dir / "worker.stdout.log").open("a", encoding="utf-8")
    stderr_handle = (job_dir / "worker.stderr.log").open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(SCRIPT_PATH), "__worker", "--job", job_id, "--llm-no", str(llm_no)],
        cwd=str(job_dir),
        stdout=stdout_handle,
        stderr=stderr_handle,
        text=True,
        start_new_session=True,
    )
    stdout_handle.close()
    stderr_handle.close()
    return process.pid


def refresh_job_record(job_dir: Path) -> dict[str, Any]:
    record = read_job_record(job_dir)
    session_id = str(record.get("session_id", "") or "")
    if session_id:
        session_payload = command_session_status(argparse.Namespace(session=session_id, limit=20))
        session_state = str(session_payload.get("state", "") or "")
        if record.get("state") == "completed" or record.get("completed_at"):
            mapped_state = "completed"
        elif session_state == "running_round":
            mapped_state = "running"
        elif session_state in {"waiting_reply", "expired"} and session_payload.get("result_text"):
            mapped_state = "waiting_review"
        elif session_state == "stop_requested":
            mapped_state = "stop_requested"
        elif session_state == "stopped":
            mapped_state = "stopped"
        elif session_state == "failed":
            mapped_state = "failed"
        else:
            mapped_state = session_state or str(record.get("state", "") or "running")
        payload = {
            **record,
            "mode": str(record.get("mode", "") or "session_job"),
            "session_id": session_id,
            "state": mapped_state,
            "profile": session_payload.get("profile", record.get("profile", "")),
            "task_text": session_payload.get("task_text", record.get("task_text", "")),
            "task_preview": session_payload.get("task_preview", record.get("task_preview", "")),
            "result_text": session_payload.get("result_text", ""),
            "result_preview": session_payload.get("result_preview", ""),
            "last_error": session_payload.get("last_error", record.get("last_error", "")),
            "current_round": session_payload.get("current_round"),
            "completed_rounds": session_payload.get("completed_rounds"),
            "reply_pending": session_payload.get("reply_pending"),
            "runner_pid": session_payload.get("runner_pid", record.get("runner_pid")),
            "llm_no": session_payload.get("llm_no", record.get("llm_no")),
            "continue_hint": build_job_continue_hint(mapped_state, session_id),
            "recent_events": session_payload.get("recent_events", []),
        }
        return payload
    result_path = job_dir / "result.txt"
    if result_path.exists():
        result_text = read_text(result_path)
        record["result_text"] = result_text
        record["result_preview"] = trim_preview(result_text)
        if record.get("state") not in {"failed", "stopped"}:
            record["state"] = "completed"
    state = record.get("state", "")
    runner_pid = record.get("runner_pid")
    if state not in TERMINAL_STATES and runner_pid and not pid_is_running(int(runner_pid)):
        if record.get("state") == "stop_requested" or (job_dir / "stop.request").exists():
            record["state"] = "stopped"
        elif result_path.exists():
            record["state"] = "completed"
        else:
            record["state"] = "failed"
            if not record.get("last_error"):
                record["last_error"] = "worker exited before producing a result"
    return attach_recent_events(record, job_dir)


def wait_for_job(job_dir: Path, *, timeout_sec: float, poll_interval: float = 0.5) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    while time.time() <= deadline:
        record = refresh_job_record(job_dir)
        if record.get("state") in TERMINAL_STATES:
            return record
        time.sleep(poll_interval)
    raise TimeoutError(f"timed out waiting for job {job_dir.name}")


def command_doctor() -> dict[str, Any]:
    payload = probe_installation()
    payload.setdefault("tool", TOOL_NAME)
    payload.setdefault("jobs_root", str(jobs_root()))
    payload.setdefault("sessions_root", str(sessions_root()))
    payload.setdefault("skills_root", str(skills_root()))
    payload.setdefault("default_subagent_policy", "auto")
    payload.setdefault("supported_subagent_policies", ["auto", "off", "force"])
    payload.setdefault("default_skill_mode", "summary")
    payload.setdefault("supported_skill_modes", ["summary", "full"])
    payload.setdefault("supported_profiles", list(SUPPORTED_PROFILES))
    payload["cwd"] = str(Path.cwd().resolve())
    if "default_llm_no" not in payload or payload.get("default_llm_no") is None:
        payload["default_llm_no"] = choose_default_llm_no(list(payload.get("llm_options", []) or []))
    llm_options, upstream_selected_llm_no, default_llm_name = normalize_doctor_llm_options(
        list(payload.get("llm_options", []) or []),
        int(payload.get("default_llm_no", 0) or 0),
    )
    payload["llm_options"] = llm_options
    payload["upstream_selected_llm_no"] = upstream_selected_llm_no
    payload["default_llm_name"] = default_llm_name
    payload["commands"] = [
        "doctor",
        "start",
        "revise-job",
        "complete",
        "summary",
        "logs",
    ]
    payload["advanced_commands"] = []
    payload["compat_commands"] = ["wait", "list", "stop", "webui"]
    return payload


def command_start_legacy(args: argparse.Namespace) -> dict[str, Any]:
    install_payload = ensure_ready_for_task()
    task_text = load_task_text(args)
    chosen_job_id = getattr(args, "job_id", None) or make_job_id()
    skill_stage_root = windows_temp_root_wsl() / f"{chosen_job_id}.skills"
    effective_task_text, skill_bundle, task_file_bundle = build_effective_task_text(
        task_text,
        skill_specs=list(getattr(args, "skill", []) or []),
        subagent_policy=getattr(args, "subagent_policy", "auto"),
        skill_mode=getattr(args, "skill_mode", "summary"),
        skill_stage_root=skill_stage_root if getattr(args, "skill", []) else None,
        task_file_stage_root=windows_temp_root_wsl() / f"{chosen_job_id}.task-files",
        profile=getattr(args, "profile", None),
    )
    llm_no = install_payload["default_llm_no"] if getattr(args, "llm_no", None) is None else int(args.llm_no)
    valid_llm_nos = {int(item.get("llm_no", -1)) for item in install_payload.get("llm_options", [])}
    if valid_llm_nos and llm_no not in valid_llm_nos:
        raise ValueError(f"invalid llm_no {llm_no}; available: {sorted(valid_llm_nos)}")
    job_dir, record = create_job(
        task_text,
        prompt_text=effective_task_text,
        job_id=chosen_job_id,
        llm_no=llm_no,
        subagent_policy=getattr(args, "subagent_policy", "auto"),
        skill_mode=getattr(args, "skill_mode", "summary"),
        profile=getattr(args, "profile", None),
        skill_paths=[item["path"] for item in skill_bundle],
        skill_stage_paths=[item.get("staged_path", item["path"]) for item in skill_bundle],
        task_file_paths=[item["source_path"] for item in task_file_bundle],
        task_file_stage_paths=[item["windows_staged_path"] for item in task_file_bundle],
    )
    runner_pid = spawn_worker(record["job_id"], llm_no)
    record = update_job_record(
        job_dir,
        mode="legacy_job",
        state="running",
        runner_pid=runner_pid,
        llm_no=llm_no,
        resumable=False,
        continue_hint="",
        subagent_policy=getattr(args, "subagent_policy", "auto"),
        skill_mode=getattr(args, "skill_mode", "summary"),
        profile=getattr(args, "profile", None),
        skill_paths=[item["path"] for item in skill_bundle],
        skill_stage_paths=[item.get("staged_path", item["path"]) for item in skill_bundle],
        task_file_paths=[item["source_path"] for item in task_file_bundle],
        task_file_stage_paths=[item["windows_staged_path"] for item in task_file_bundle],
    )
    return record


def command_start(args: argparse.Namespace) -> dict[str, Any]:
    chosen_job_id = getattr(args, "job_id", None) or make_job_id()
    session_payload = command_session_start(
        argparse.Namespace(
            task=getattr(args, "task", None),
            file=getattr(args, "file", None),
            llm_no=getattr(args, "llm_no", None),
            skill=list(getattr(args, "skill", []) or []),
            subagent_policy=getattr(args, "subagent_policy", "auto"),
            skill_mode=getattr(args, "skill_mode", "summary"),
            profile=getattr(args, "profile", None),
            session_id=chosen_job_id,
        )
    )
    session_dir = session_dir_for(chosen_job_id)
    prompt_text = read_text(session_dir / "prompt.txt")
    task_text = load_task_text(args)
    job_dir, _record = create_job(
        task_text,
        prompt_text=prompt_text,
        job_id=chosen_job_id,
        llm_no=session_payload.get("llm_no"),
        subagent_policy=session_payload.get("subagent_policy", "auto"),
        skill_mode=session_payload.get("skill_mode", "summary"),
        profile=session_payload.get("profile", getattr(args, "profile", None) or ""),
        skill_paths=list(session_payload.get("skill_paths", []) or []),
        skill_stage_paths=list(session_payload.get("skill_stage_paths", []) or []),
        task_file_paths=list(session_payload.get("task_file_paths", []) or []),
        task_file_stage_paths=list(session_payload.get("task_file_stage_paths", []) or []),
    )
    update_job_record(
        job_dir,
        mode="session_job",
        state="running",
        session_id=chosen_job_id,
        continue_hint="",
        runner_pid=session_payload.get("runner_pid"),
    )
    return refresh_job_record(job_dir)


def command_session_start(args: argparse.Namespace) -> dict[str, Any]:
    install_payload = ensure_ready_for_task()
    task_text = load_task_text(args)
    chosen_session_id = validate_session_id(getattr(args, "session_id", None) or make_session_id())
    effective_task_text, skill_bundle, task_file_bundle = build_effective_task_text(
        task_text,
        skill_specs=list(getattr(args, "skill", []) or []),
        subagent_policy=getattr(args, "subagent_policy", "auto"),
        skill_mode=getattr(args, "skill_mode", "summary"),
        skill_stage_root=session_task_dir_wsl(chosen_session_id) / "skills" if getattr(args, "skill", []) else None,
        task_file_stage_root=session_task_dir_wsl(chosen_session_id) / "task-files",
        profile=getattr(args, "profile", None),
    )
    llm_no = install_payload["default_llm_no"] if getattr(args, "llm_no", None) is None else int(args.llm_no)
    valid_llm_nos = {int(item.get("llm_no", -1)) for item in install_payload.get("llm_options", [])}
    if valid_llm_nos and llm_no not in valid_llm_nos:
        raise ValueError(f"invalid llm_no {llm_no}; available: {sorted(valid_llm_nos)}")
    session_dir, record = create_session(
        task_text,
        prompt_text=effective_task_text,
        session_id=chosen_session_id,
        llm_no=llm_no,
        subagent_policy=getattr(args, "subagent_policy", "auto"),
        skill_mode=getattr(args, "skill_mode", "summary"),
        profile=getattr(args, "profile", None),
        skill_paths=[item["path"] for item in skill_bundle],
        skill_stage_paths=[item.get("staged_path", item["path"]) for item in skill_bundle],
        task_file_paths=[item["source_path"] for item in task_file_bundle],
        task_file_stage_paths=[item["windows_staged_path"] for item in task_file_bundle],
    )
    backend = launch_session_backend(record["session_id"], llm_no)
    append_job_event(
        session_dir,
        {
            "event": "session_started",
            "runner_pid": backend["runner_pid"],
            "task_dir": backend["task_dir"],
        },
    )
    write_job_record(
        session_dir,
        {
            **record,
            "state": "running_round",
            "started_at": now_iso(),
            "runner_pid": int(backend["runner_pid"]),
            "task_dir": backend["task_dir"],
        },
    )
    payload = reconcile_session_record(session_dir)
    payload["continue_hint"] = build_session_continue_hint(payload["state"], payload["session_id"])
    return payload


def command_session_status(args: argparse.Namespace) -> dict[str, Any]:
    session_dir = session_dir_for(args.session)
    if not session_dir.exists():
        raise FileNotFoundError(f"session not found: {args.session}")
    payload = reconcile_session_record(session_dir, event_limit=getattr(args, "limit", 20))
    payload["continue_hint"] = build_session_continue_hint(payload["state"], payload["session_id"])
    return payload


def command_session_send(args: argparse.Namespace) -> dict[str, Any]:
    session_dir = session_dir_for(args.session)
    if not session_dir.exists():
        raise FileNotFoundError(f"session not found: {args.session}")
    task_text = load_task_text(args).strip()
    if not task_text:
        raise ValueError("reply task cannot be empty")
    with job_store_lock(session_dir):
        record = _read_job_record_unlocked(session_dir)
        if record.get("state") != "waiting_reply":
            raise RuntimeError(f"session {args.session} is not ready for a new turn; current state: {record.get('state')}")
        task_dir = Path(record["task_dir"])
        reply_path = task_dir / "reply.txt"
        if reply_path.exists():
            if read_text(reply_path).strip():
                raise RuntimeError(f"session {args.session} already has a pending reply.txt")
            reply_path.unlink()
        stop_token = task_dir / "_stop"
        if stop_token.exists():
            stop_token.unlink()
        try:
            write_text_exclusive(reply_path, task_text)
        except FileExistsError as exc:
            raise RuntimeError(f"session {args.session} already has a pending reply.txt") from exc
        record.update(
            {
                "state": "running_round",
                "last_reply_at": now_iso(),
                "last_reply_text": task_text,
            }
        )
        _write_job_record_unlocked(session_dir, record)
    append_job_event(
        session_dir,
        {
            "event": "reply",
            "round": int(record.get("current_round", 0)) + 1,
            "summary": trim_preview(task_text, 140),
        },
    )
    payload = reconcile_session_record(session_dir)
    payload["continue_hint"] = build_session_continue_hint(payload["state"], payload["session_id"])
    return payload


def command_session_list(args: argparse.Namespace) -> dict[str, Any]:
    root = sessions_root()
    ensure_dir(root)
    sessions = []
    for child in sorted(root.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        try:
            sessions.append(reconcile_session_record(child))
        except FileNotFoundError:
            continue
    if args.limit:
        sessions = sessions[: args.limit]
    return {"tool": TOOL_NAME, "sessions_root": str(root), "count": len(sessions), "sessions": sessions}


def command_session_stop(args: argparse.Namespace) -> dict[str, Any]:
    session_dir = session_dir_for(args.session)
    if not session_dir.exists():
        raise FileNotFoundError(f"session not found: {args.session}")
    record = reconcile_session_record(session_dir)
    task_dir = Path(record["task_dir"])
    write_text(task_dir / "_stop", now_iso() + "\n")
    write_text(session_dir / "stop.request", now_iso() + "\n")
    if record.get("runner_pid_running") and record.get("runner_pid"):
        try:
            os.kill(int(record["runner_pid"]), signal.SIGTERM)
        except OSError:
            pass
    append_job_event(
        session_dir,
        {
            "event": "stop_requested",
            "runner_pid": record.get("runner_pid"),
        },
    )
    payload = update_job_record(session_dir, state="stop_requested", stop_requested_at=now_iso())
    payload["continue_hint"] = ""
    return payload


def command_revise(args: argparse.Namespace, *, json_mode: bool | None = None) -> dict[str, Any]:
    send_payload = command_session_send(
        argparse.Namespace(
            session=args.session,
            task=args.feedback,
            file=None,
        )
    )
    return command_session_watch(
        argparse.Namespace(
            session=send_payload["session_id"],
            max_idle=getattr(args, "max_idle", None),
            timeout=None,
            interval=getattr(args, "interval", 0.5),
        ),
        json_mode=json_mode,
    )


def command_revise_job(args: argparse.Namespace, *, json_mode: bool | None = None) -> dict[str, Any]:
    session_id = resolve_revisable_session_id(getattr(args, "job", None))
    return command_revise(
        argparse.Namespace(
            session=session_id,
            feedback=args.feedback,
            max_idle=getattr(args, "max_idle", None),
            interval=getattr(args, "interval", 0.5),
        ),
        json_mode=json_mode,
    )


def command_complete(args: argparse.Namespace) -> dict[str, Any]:
    job_dir = job_dir_for(args.job)
    if not job_dir.exists():
        raise FileNotFoundError(f"job not found: {args.job}")
    current = refresh_job_record(job_dir)
    current_state = str(current.get("state", "") or "")
    if current_state == "completed":
        current["continue_hint"] = ""
        return current
    if current_state != "waiting_review":
        raise RuntimeError(f"job {args.job} is not ready to complete; current state: {current_state}")
    completed_at = now_iso()
    session_id = str(current.get("session_id", "") or "")
    if session_id:
        session_dir = session_dir_for(session_id)
        session_record = read_job_record(session_dir)
        runner_pid = int(session_record.get("runner_pid") or 0) if session_record.get("runner_pid") is not None else None
        if runner_pid and pid_is_running(runner_pid):
            try:
                os.kill(runner_pid, signal.SIGTERM)
            except OSError:
                pass
        write_text(Path(session_record["task_dir"]) / "_stop", completed_at + "\n")
        write_text(session_dir / "stop.request", completed_at + "\n")
        append_job_event(session_dir, {"event": "completed", "runner_pid": runner_pid})
        update_job_record(session_dir, state="completed", completed_at=completed_at)
    update_job_record(job_dir, state="completed", completed_at=completed_at, continue_hint="")
    refreshed = refresh_job_record(job_dir)
    refreshed["completed_at"] = completed_at
    refreshed["continue_hint"] = ""
    return refreshed


def command_status(args: argparse.Namespace) -> dict[str, Any]:
    job_dir = job_dir_for(args.job)
    if not job_dir.exists():
        raise FileNotFoundError(f"job not found: {args.job}")
    payload = refresh_job_record(job_dir)
    session_id = str(payload.get("session_id", "") or "")
    if session_id:
        payload["continue_hint"] = build_job_continue_hint(payload.get("state", ""), session_id)
    return payload


def command_summary(args: argparse.Namespace) -> dict[str, Any]:
    job_dir = job_dir_for(args.job)
    if not job_dir.exists():
        raise FileNotFoundError(f"job not found: {args.job}")
    record = refresh_job_record(job_dir)
    payload = {
        "tool": TOOL_NAME,
        "job_id": record["job_id"],
        "state": record["state"],
        "mode": record.get("mode", ""),
        "llm_no": record.get("llm_no"),
        "subagent_policy": record.get("subagent_policy"),
        "profile": record.get("profile", ""),
        "skill_mode": record.get("skill_mode"),
        "skill_paths": record.get("skill_paths", []),
        "skill_stage_paths": record.get("skill_stage_paths", []),
        "task_file_paths": record.get("task_file_paths", []),
        "task_file_stage_paths": record.get("task_file_stage_paths", []),
        "windows_pid": record.get("windows_pid"),
        "last_turn": record.get("last_turn"),
        "last_tool_name": record.get("last_tool_name", ""),
        "last_action_summary": record.get("last_action_summary", ""),
        "progress_bytes_total": record.get("progress_bytes_total", 0),
        "result_text": record.get("result_text", ""),
        "result_preview": record.get("result_preview", ""),
        "last_error": record.get("last_error", ""),
        "recent_events": record.get("recent_events", read_recent_job_events(job_dir, limit=getattr(args, "limit", 12))),
    }
    session_id = str(record.get("session_id", "") or "")
    if session_id:
        try:
            session_status = command_session_status(argparse.Namespace(session=session_id, limit=getattr(args, "limit", 12)))
            recent_events = list(session_status.get("recent_events", []))
        except FileNotFoundError:
            recent_events = list(record.get("recent_events", []))
        payload["session_id"] = session_id
        payload["continue_hint"] = build_job_continue_hint(payload["state"], session_id)
        limit = int(getattr(args, "limit", 12))
        payload["recent_events"] = recent_events[-limit:] if limit > 0 else recent_events
    return payload


def command_wait(args: argparse.Namespace) -> dict[str, Any]:
    job_dir = job_dir_for(args.job)
    if not job_dir.exists():
        raise FileNotFoundError(f"job not found: {args.job}")
    return wait_for_job(job_dir, timeout_sec=args.timeout)


def command_list(args: argparse.Namespace) -> dict[str, Any]:
    root = jobs_root()
    ensure_dir(root)
    jobs = []
    for child in sorted(root.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        try:
            jobs.append(refresh_job_record(child))
        except FileNotFoundError:
            continue
    if args.limit:
        jobs = jobs[: args.limit]
    return {"tool": TOOL_NAME, "jobs_root": str(root), "count": len(jobs), "jobs": jobs}


def command_stop(args: argparse.Namespace) -> dict[str, Any]:
    job_dir = job_dir_for(args.job)
    if not job_dir.exists():
        raise FileNotFoundError(f"job not found: {args.job}")
    record = read_job_record(job_dir)
    session_id = str(record.get("session_id", "") or "")
    if session_id:
        command_session_stop(argparse.Namespace(session=session_id))
        return refresh_job_record(job_dir)
    write_text(job_dir / "stop.request", now_iso() + "\n")
    runner_pid = int(record.get("runner_pid") or 0) if record.get("runner_pid") is not None else None
    if runner_pid and pid_is_running(runner_pid):
        try:
            os.kill(runner_pid, signal.SIGTERM)
        except OSError:
            pass
    payload = refresh_job_record(job_dir)
    if payload.get("state") not in TERMINAL_STATES:
        payload = update_job_record(job_dir, state="stop_requested")
    return payload


def command_logs(args: argparse.Namespace) -> dict[str, Any]:
    job_dir = job_dir_for(args.job)
    if not job_dir.exists():
        raise FileNotFoundError(f"job not found: {args.job}")
    record = refresh_job_record(job_dir)
    session_id = str(record.get("session_id", "") or "")
    if session_id:
        session_dir = session_dir_for(session_id)
        session_status = command_session_status(argparse.Namespace(session=session_id, limit=getattr(args, "limit", 20)))
        recent_events = list(session_status.get("recent_events", []))
        limit = int(getattr(args, "limit", 20))
        if limit > 0:
            recent_events = recent_events[-limit:]
        return {
            "tool": TOOL_NAME,
            "job_id": record["job_id"],
            "session_id": session_id,
            "state": record["state"],
            "stream_log": session_status.get("latest_output_text", ""),
            "stdout_log": read_text(session_dir / "session.stdout.log") if (session_dir / "session.stdout.log").exists() else "",
            "stderr_log": read_text(session_dir / "session.stderr.log") if (session_dir / "session.stderr.log").exists() else "",
            "result_text": session_status.get("result_text", ""),
            "recent_events": recent_events,
            "rendered_events": "\n".join(format_watch_event(event) for event in recent_events),
            "continue_hint": build_job_continue_hint(record.get("state", ""), session_id),
        }
    stream_text = read_text(job_dir / "stream.log") if (job_dir / "stream.log").exists() else ""
    stderr_text = read_text(job_dir / "stderr.log") if (job_dir / "stderr.log").exists() else ""
    stdout_text = read_text(job_dir / "stdout.log") if (job_dir / "stdout.log").exists() else ""
    payload = {
        "tool": TOOL_NAME,
        "job_id": record["job_id"],
        "state": record["state"],
        "stream_log": stream_text,
        "stdout_log": stdout_text,
        "stderr_log": stderr_text,
        "result_text": record.get("result_text", ""),
        "recent_events": read_recent_job_events(job_dir, limit=getattr(args, "limit", 20)),
        "rendered_events": render_event_log(job_dir),
    }
    session_id = str(record.get("session_id", "") or "")
    if session_id:
        payload["session_id"] = session_id
        payload["continue_hint"] = build_job_continue_hint(payload.get("state", ""), session_id)
    return payload


def command_webui(args: argparse.Namespace) -> dict[str, Any]:
    wrapper = ga_webui_bin()
    if not wrapper.exists():
        raise FileNotFoundError(f"ga-webui wrapper not found: {wrapper}")
    result = subprocess.run(
        [str(wrapper), args.action],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or f"ga-webui exited with {result.returncode}")
    line = next((item for item in reversed(result.stdout.splitlines()) if item.strip()), "")
    if not line:
        raise RuntimeError("ga-webui returned empty output")
    payload = json.loads(line)
    payload["tool"] = TOOL_NAME
    payload["mode"] = "webui"
    return payload


def stream_reader(handle: Any, target: queue.Queue[tuple[str, str]], kind: str) -> None:
    try:
        for line in iter(handle.readline, ""):
            if not line:
                break
            target.put((kind, line))
    finally:
        handle.close()


def worker_main(args: argparse.Namespace) -> int:
    job_dir = job_dir_for(args.job)
    record = read_job_record(job_dir)
    prompt_text = read_text(job_dir / "prompt.txt")
    prompt_wsl, events_wsl, runner_wsl, cmd_wsl = write_windows_runner_assets(record["job_id"], prompt_text, int(args.llm_no))
    stdout_path = job_dir / "stdout.log"
    stderr_path = job_dir / "stderr.log"
    stream_path = job_dir / "stream.log"
    result_path = job_dir / "result.txt"
    stop_path = job_dir / "stop.request"
    command = ["cmd.exe", "/c", windows_path(cmd_wsl)]
    child_env = os.environ.copy()
    # GA tasks often run longer than the Windows shell bridge default.
    child_env["WIN_SHELL_UTF8_TIMEOUT_SEC"] = "0"
    process = subprocess.Popen(
        command,
        text=True,
        encoding="utf-8",
        env=child_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        bufsize=1,
    )
    stderr_queue: queue.Queue[tuple[str, str]] = queue.Queue()
    assert process.stderr is not None
    threading.Thread(target=stream_reader, args=(process.stderr, stderr_queue, "stderr"), daemon=True).start()
    write_job_record(
        job_dir,
        {
            **record,
            "state": "running",
            "runner_pid": os.getpid(),
            "worker_started_at": now_iso(),
        },
    )
    latest_progress = ""
    windows_pid = None
    event_offset = 0
    saw_done = False
    process_exited_at: float | None = None
    try:
        while True:
            if stop_path.exists():
                if windows_pid:
                    stop_windows_process(int(windows_pid))
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                write_job_record(
                    job_dir,
                    {
                        **read_job_record(job_dir),
                        "state": "stopped",
                        "exit_code": process.returncode,
                        "result_text": read_text(result_path) if result_path.exists() else "",
                        "result_preview": trim_preview(read_text(result_path)) if result_path.exists() else "",
                    },
                )
                return 0
            try:
                kind, line = stderr_queue.get(timeout=0.1)
            except queue.Empty:
                kind, line = "", ""
            if line:
                append_text(stderr_path, line)
            saw_new_event = False
            if events_wsl.exists():
                event_text = read_text(events_wsl)
                if event_offset < len(event_text):
                    chunk = event_text[event_offset:]
                    event_offset = len(event_text)
                    for raw_line in chunk.splitlines():
                        saw_new_event = True
                        stripped = raw_line.strip()
                        if not stripped:
                            continue
                        append_text(stdout_path, stripped + "\n")
                        try:
                            event = json.loads(stripped)
                        except json.JSONDecodeError:
                            append_text(stream_path, stripped + "\n")
                            continue
                        event_type = event.get("event")
                        if event_type == "started":
                            windows_pid = event.get("windows_pid")
                            append_text(stream_path, f"[started] windows_pid={windows_pid}\n")
                            append_job_event(
                                job_dir,
                                {
                                    "event": "started",
                                    "windows_pid": windows_pid,
                                    "llm_client_count": event.get("llm_client_count"),
                                },
                            )
                            update_job_record(
                                job_dir,
                                windows_pid=windows_pid,
                                llm_client_count=event.get("llm_client_count"),
                            )
                        elif event_type == "progress":
                            latest_progress = event.get("text", "")
                            append_text(stream_path, latest_progress)
                            observe_progress_chunk(job_dir, latest_progress)
                        elif event_type == "done":
                            saw_done = True
                            result_text = extract_final_response_text(str(event.get("text", "") or ""))
                            write_text(result_path, result_text)
                            append_text(stream_path, "\n[done]\n")
                            append_job_event(
                                job_dir,
                                {
                                    "event": "done",
                                    "result_chars": len(result_text),
                                    "result_bytes": len(result_text.encode("utf-8")),
                                    "preview": trim_preview(result_text, 140),
                                },
                            )
                            update_job_record(
                                job_dir,
                                state="completed",
                                result_text=result_text,
                                result_preview=trim_preview(result_text),
                            )
                        elif event_type == "error":
                            append_text(stream_path, f"[error] {event.get('message', 'unknown error')}\n")
                            append_job_event(
                                job_dir,
                                {
                                    "event": "error",
                                    "message": event.get("message", "unknown error"),
                                },
                            )
                            update_job_record(job_dir, last_error=event.get("message", "unknown error"))
            if process.poll() is not None:
                if process_exited_at is None:
                    process_exited_at = time.time()
                if saw_done:
                    break
                if not saw_new_event and process_exited_at and (time.time() - process_exited_at) >= 12.0:
                    break
            else:
                process_exited_at = None
            if saw_done:
                break
        exit_code = process.wait(timeout=5)
        record = read_job_record(job_dir)
        if result_path.exists():
            result_text = extract_final_response_text(read_text(result_path))
            write_text(result_path, result_text)
            write_job_record(
                job_dir,
                {
                    **record,
                    "state": "completed",
                    "exit_code": exit_code,
                    "result_text": result_text,
                    "result_preview": trim_preview(result_text),
                },
            )
            return 0
        write_job_record(
            job_dir,
            {
                **record,
                "state": "failed" if record.get("state") != "stopped" else "stopped",
                "exit_code": exit_code,
                "last_error": record.get("last_error") or f"GA worker exited with code {exit_code}",
                "result_preview": trim_preview(latest_progress),
            },
        )
        return exit_code or 1
    finally:
        cleanup_windows_runner_assets(prompt_wsl, events_wsl, runner_wsl, cmd_wsl)


def build_parser() -> argparse.ArgumentParser:
    parser = HiddenAwareArgumentParser(prog=TOOL_NAME, description="GenericAgent bridge CLI.")
    parser.add_argument("--text", action="store_true", help="Emit plain text instead of JSON for non-log commands.")
    parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{doctor,start,revise-job,complete,summary,logs}",
    )

    sub.add_parser("doctor", help="Check GA runtime and local wrapper state.")

    task_parent = argparse.ArgumentParser(add_help=False)
    group = task_parent.add_mutually_exclusive_group(required=True)
    group.add_argument("--task", help="Inline task text.")
    group.add_argument("-f", "--file", help="Read task text from file.")
    task_parent.add_argument("--job-id", help="Optional explicit job id.")
    task_parent.add_argument("--llm-no", type=int, help="GA llm index. Defaults to glm-5.1 when available.")
    task_parent.add_argument("--profile", choices=SUPPORTED_PROFILES, help="Prompt-level execution profile. Use review for read-only audits.")
    task_parent.add_argument("--skill", action="append", default=[], help="Inject a skill by name or SKILL.md path. Repeatable.")
    task_parent.add_argument("--skill-mode", choices=["summary", "full"], default="summary", help="How skill documents are injected into the GA prompt.")
    task_parent.add_argument("--subagent-policy", choices=["auto", "off", "force"], default="auto", help="Subagent delegation policy injected into the GA task prompt.")

    start_parser = sub.add_parser("start", parents=[task_parent], help="Start a resumable GA job and immediately watch it.")
    start_parser.add_argument("--timeout", type=float, default=0.0, help=argparse.SUPPRESS)
    start_parser.add_argument("--max-idle", type=float, default=None, help=f"Stop watching after N idle seconds without killing the job. Defaults to {int(DEFAULT_WATCH_IDLE_TIMEOUT_SEC)}.")
    start_parser.add_argument("--interval", type=float, default=0.5, help="Poll interval in seconds.")

    revise_job_parser = sub.add_parser("revise-job", help="Continue the latest or selected resumable GA session.")
    revise_job_parser.add_argument("--job", help="Job id or session id. Omit to use the latest resumable session.")
    revise_job_parser.add_argument("--feedback", required=True, help="Follow-up instruction for the same GA conversation.")
    revise_job_parser.add_argument("--max-idle", type=float, default=None, help="Stop watching after N idle seconds without killing the session.")
    revise_job_parser.add_argument("--interval", type=float, default=0.5, help="Poll interval in seconds.")

    complete_parser = sub.add_parser("complete", help="Mark a GA job as reviewed and completed.")
    complete_parser.add_argument("--job", required=True, help="Job id.")

    summary_parser = sub.add_parser("summary", help="Show a compact summary for a job.")
    summary_parser.add_argument("--job", required=True, help="Job id.")
    summary_parser.add_argument("--limit", type=int, default=12, help="Maximum compact recent events.")

    wait_parser = sub.add_parser("wait", help=argparse.SUPPRESS)
    wait_parser.add_argument("--job", required=True, help="Job id.")
    wait_parser.add_argument("--timeout", type=float, default=600.0, help="Wait timeout in seconds.")

    logs_parser = sub.add_parser("logs", help="Read captured logs for a job.")
    logs_parser.add_argument("--job", required=True, help="Job id.")
    logs_parser.add_argument("--limit", type=int, default=20, help="Maximum structured events to include.")

    list_parser = sub.add_parser("list", help=argparse.SUPPRESS)
    list_parser.add_argument("--limit", type=int, default=20, help="Maximum number of jobs to show.")

    stop_parser = sub.add_parser("stop", help=argparse.SUPPRESS)
    stop_parser.add_argument("--job", required=True, help="Job id.")

    webui_parser = sub.add_parser("webui", help=argparse.SUPPRESS)
    webui_parser.add_argument("action", choices=["start", "stop", "status"], help="WebUI action.")

    worker_parser = sub.add_parser("__worker", help=argparse.SUPPRESS)
    worker_parser.add_argument("--job", required=True, help="Internal worker job id.")
    worker_parser.add_argument("--llm-no", type=int, required=True, help=argparse.SUPPRESS)
    return parser


def human_output(command: str, payload: dict[str, Any]) -> str:
    if command == "doctor":
        lines = [
            f"tool: {payload['tool']}",
            f"ga_root: {payload['ga_root_win']}",
            f"jobs_root: {payload['jobs_root']}",
            f"sessions_root: {payload['sessions_root']}",
            f"skills_root: {payload['skills_root']}",
            f"ready: {'yes' if payload['llm_client_count'] > 0 else 'no'}",
            f"llm_clients: {payload['llm_client_count']}",
            f"default_llm_no: {payload.get('default_llm_no', 0)}",
            f"default_subagent_policy: {payload.get('default_subagent_policy', 'auto')}",
            f"default_skill_mode: {payload.get('default_skill_mode', 'summary')}",
        ]
        for option in payload.get("llm_options", []):
            lines.append(f"llm[{option['llm_no']}]: {option['name']}")
        if payload.get("llm_probe_error"):
            lines.append(f"probe_error: {payload['llm_probe_error']}")
        return "\n".join(lines)
    if command in {"session-status", "session-send", "session-stop", "session-watch"}:
        lines = [
            f"session: {payload['session_id']}",
            f"state: {payload['state']}",
        ]
        if payload.get("subagent_policy"):
            lines.append(f"subagent_policy: {payload['subagent_policy']}")
        if payload.get("profile"):
            lines.append(f"profile: {payload['profile']}")
        if payload.get("skill_mode"):
            lines.append(f"skill_mode: {payload['skill_mode']}")
        if payload.get("skill_paths"):
            lines.append(f"skills: {', '.join(payload['skill_paths'])}")
        if payload.get("skill_stage_paths"):
            lines.append(f"skill_staged: {', '.join(payload['skill_stage_paths'])}")
        if payload.get("task_file_stage_paths"):
            lines.append(f"task_files_staged: {', '.join(payload['task_file_stage_paths'])}")
        if payload.get("llm_no") is not None:
            lines.append(f"llm_no: {payload['llm_no']}")
        if payload.get("runner_pid"):
            lines.append(f"runner_pid: {payload['runner_pid']}")
        if payload.get("current_round") is not None:
            lines.append(f"current_round: {payload['current_round']}")
        if payload.get("completed_rounds") is not None:
            lines.append(f"completed_rounds: {payload['completed_rounds']}")
        if payload.get("reply_pending") is not None:
            lines.append(f"reply_pending: {payload['reply_pending']}")
        if payload.get("result_preview"):
            lines.append(f"preview: {payload['result_preview']}")
        if payload.get("last_error"):
            lines.append(f"error: {payload['last_error']}")
        if payload.get("continue_hint"):
            lines.append(f"continue_hint: {payload['continue_hint']}")
        recent_events = payload.get("recent_events") or []
        if recent_events:
            lines.extend(format_watch_event(event) for event in recent_events[-6:])
        return "\n".join(lines)
    if command == "revise-job":
        lines = [
            f"session: {payload['session_id']}",
            f"state: {payload['state']}",
        ]
        if payload.get("current_round") is not None:
            lines.append(f"current_round: {payload['current_round']}")
        if payload.get("completed_rounds") is not None:
            lines.append(f"completed_rounds: {payload['completed_rounds']}")
        if payload.get("watch_state"):
            lines.append(f"watch_state: {payload['watch_state']}")
        if payload.get("result_preview"):
            lines.append(f"preview: {payload['result_preview']}")
        if payload.get("continue_hint"):
            lines.append(f"continue_hint: {payload['continue_hint']}")
        return "\n".join(lines)
    if command == "complete":
        lines = [
            f"job: {payload['job_id']}",
            f"state: {payload['state']}",
        ]
        if payload.get("completed_at"):
            lines.append(f"completed_at: {payload['completed_at']}")
        return "\n".join(lines)
    if command in {"start", "wait", "stop"}:
        lines = [
            f"job: {payload['job_id']}",
            f"state: {payload['state']}",
        ]
        if payload.get("subagent_policy"):
            lines.append(f"subagent_policy: {payload['subagent_policy']}")
        if payload.get("profile"):
            lines.append(f"profile: {payload['profile']}")
        if payload.get("skill_mode"):
            lines.append(f"skill_mode: {payload['skill_mode']}")
        if payload.get("skill_paths"):
            lines.append(f"skills: {', '.join(payload['skill_paths'])}")
        if payload.get("skill_stage_paths"):
            lines.append(f"skill_staged: {', '.join(payload['skill_stage_paths'])}")
        if payload.get("task_file_stage_paths"):
            lines.append(f"task_files_staged: {', '.join(payload['task_file_stage_paths'])}")
        if payload.get("runner_pid"):
            lines.append(f"runner_pid: {payload['runner_pid']}")
        if payload.get("windows_pid"):
            lines.append(f"windows_pid: {payload['windows_pid']}")
        if payload.get("last_turn"):
            lines.append(f"turn: {payload['last_turn']}")
        if payload.get("last_tool_name"):
            lines.append(f"tool: {payload['last_tool_name']}")
        if payload.get("last_action_summary"):
            lines.append(f"progress: {payload['last_action_summary']}")
        if payload.get("progress_bytes_total") is not None:
            lines.append(f"bytes_total: {payload.get('progress_bytes_total', 0)}")
        if payload.get("last_error"):
            lines.append(f"error: {payload['last_error']}")
        if payload.get("result_text"):
            lines.append(f"result_preview: {payload.get('result_preview') or trim_preview(payload['result_text'], 280)}")
        if payload.get("continue_hint"):
            lines.append(f"continue_hint: {payload['continue_hint']}")
        recent_events = payload.get("recent_events") or []
        if recent_events:
            lines.extend(format_watch_event(event) for event in recent_events[-6:])
        return "\n".join(lines)
    if command == "summary":
        lines = [
            f"job: {payload['job_id']}",
            f"state: {payload['state']}",
        ]
        if payload.get("subagent_policy"):
            lines.append(f"subagent_policy: {payload['subagent_policy']}")
        if payload.get("profile"):
            lines.append(f"profile: {payload['profile']}")
        if payload.get("skill_mode"):
            lines.append(f"skill_mode: {payload['skill_mode']}")
        if payload.get("skill_paths"):
            lines.append(f"skills: {', '.join(payload['skill_paths'])}")
        if payload.get("skill_stage_paths"):
            lines.append(f"skill_staged: {', '.join(payload['skill_stage_paths'])}")
        if payload.get("task_file_stage_paths"):
            lines.append(f"task_files_staged: {', '.join(payload['task_file_stage_paths'])}")
        if payload.get("last_turn"):
            lines.append(f"turn: {payload['last_turn']}")
        if payload.get("last_tool_name"):
            lines.append(f"tool: {payload['last_tool_name']}")
        if payload.get("last_action_summary"):
            lines.append(f"progress: {payload['last_action_summary']}")
        lines.append(f"bytes_total: {payload.get('progress_bytes_total', 0)}")
        if payload.get("last_error"):
            lines.append(f"error: {payload['last_error']}")
        if payload.get("result_text"):
            lines.append(f"result_preview: {payload.get('result_preview') or trim_preview(payload['result_text'], 280)}")
        if payload.get("continue_hint"):
            lines.append(f"continue_hint: {payload['continue_hint']}")
        recent_events = payload.get("recent_events") or []
        lines.extend(format_watch_event(event) for event in recent_events)
        return "\n".join(lines)
    if command == "list":
        if not payload["jobs"]:
            return "no jobs"
        return "\n".join(
            f"{job['job_id']}  {job['state']}  {job.get('result_preview', job.get('task_preview', ''))}"
            for job in payload["jobs"]
        )
    if command == "session-list":
        if not payload["sessions"]:
            return "no sessions"
        return "\n".join(
            f"{session['session_id']}  {session['state']}  {session.get('result_preview', session.get('task_preview', ''))}"
            for session in payload["sessions"]
        )
    if command == "logs":
        rendered = payload.get("rendered_events") or ""
        parts: list[str] = []
        if rendered:
            parts.append(rendered)
        events = payload.get("recent_events") or []
        if events and not rendered:
            parts.append("\n".join(format_watch_event(event) for event in events))
        if payload.get("result_text"):
            parts.append("result_text=\n" + str(payload["result_text"]))
        if not parts and payload.get("stream_log"):
            parts.append(str(payload["stream_log"]))
        if not parts and payload.get("stderr_log"):
            parts.append(str(payload["stderr_log"]))
        return "\n\n".join(part for part in parts if part)
    if command == "webui":
        parts = [f"url: {payload.get('url', '')}"]
        if "running" in payload:
            parts.insert(0, f"running: {payload['running']}")
        if "ready" in payload:
            parts.insert(0, f"ready: {payload['ready']}")
        if "pid" in payload:
            parts.append(f"pid: {payload['pid']}")
        return "\n".join(parts)
    return json.dumps(payload, ensure_ascii=False)


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "doctor":
        return command_doctor()
    if args.command == "start":
        return command_start(args)
    if args.command == "revise-job":
        return command_revise_job(args)
    if args.command == "complete":
        return command_complete(args)
    if args.command == "summary":
        return command_summary(args)
    if args.command == "wait":
        return command_wait(args)
    if args.command == "logs":
        return command_logs(args)
    if args.command == "list":
        return command_list(args)
    if args.command == "stop":
        return command_stop(args)
    if args.command == "webui":
        return command_webui(args)
    if args.command == "__worker":
        raise RuntimeError("internal worker must be handled separately")
    raise RuntimeError(f"unknown command: {args.command}")


def format_watch_event(event: dict[str, Any]) -> str:
    event_type = event.get("event", "")
    if event_type == "started":
        return f"[init] windows_pid={event.get('windows_pid')} llm_clients={event.get('llm_client_count')}"
    if event_type == "session_started":
        return f"[init] runner_pid={event.get('runner_pid')} task_dir={event.get('task_dir')}"
    if event_type == "reply":
        return f"[reply] round={event.get('round')} {event.get('summary', '')}".rstrip()
    if event_type == "stop_requested":
        return f"[stop] runner_pid={event.get('runner_pid')}"
    if event_type == "turn":
        return f"[status] turn={event.get('turn')}"
    if event_type == "tool":
        return f"[tool] {event.get('summary') or event.get('tool_name')}"
    if event_type == "action":
        return f"[progress] {event.get('summary', '')}".rstrip()
    if event_type == "progress":
        summary = str(event.get("preview", "")).strip()
        base = f"[delta] +{event.get('bytes_delta', 0)}B total={event.get('bytes_total', 0)}B"
        if not summary or is_structural_preview(summary):
            return base
        return f"{base} {summary}".rstrip()
    if event_type == "done":
        return f"[result] bytes={event.get('result_bytes', 0)}"
    if event_type == "error":
        return f"[error] {event.get('message', '')}".rstrip()
    return json.dumps(event, ensure_ascii=False)


def render_event_log(job_dir: Path) -> str:
    return "\n".join(format_watch_event(event) for event in read_recent_job_events(job_dir, limit=0))


def resolve_watch_idle_limit(max_idle: float | None, timeout: float | None) -> float:
    if max_idle is not None:
        return float(max_idle)
    if timeout is not None:
        return float(timeout)
    return DEFAULT_WATCH_IDLE_TIMEOUT_SEC


def detect_retry_delay_seconds(text: str) -> float | None:
    delays = []
    for match in RETRY_DELAY_RE.finditer(text or ""):
        try:
            delays.append(float(match.group(1)))
        except (TypeError, ValueError):
            continue
    if not delays:
        return None
    return max(delays)


def extend_idle_limit_for_retry(base_idle: float, *texts: str) -> float:
    retry_delay = None
    for text in texts:
        current = detect_retry_delay_seconds(text)
        if current is None:
            continue
        retry_delay = max(retry_delay or 0.0, current)
    if retry_delay is None:
        return base_idle
    return max(base_idle, retry_delay + WATCH_RETRY_GRACE_SEC)


def command_session_watch(args: argparse.Namespace, *, json_mode: bool | None = False) -> dict[str, Any]:
    session_dir = session_dir_for(args.session)
    if not session_dir.exists():
        raise FileNotFoundError(f"session not found: {args.session}")
    last_round: int | None = None
    offset = 0
    emitted_result_for_round: int | None = None
    last_activity = time.time()
    idle_limit = resolve_watch_idle_limit(getattr(args, "max_idle", None), getattr(args, "timeout", None))
    while True:
        record = reconcile_session_record(session_dir)
        effective_idle_limit = extend_idle_limit_for_retry(
            idle_limit,
            str(record.get("latest_output_text", "") or ""),
            "\n".join(json.dumps(event, ensure_ascii=False) for event in (record.get("recent_events") or [])),
        )
        current_round = int(record.get("current_round", 0) or 0)
        latest_output_path = Path(record.get("latest_output_path") or session_output_path(Path(record["task_dir"]), current_round))
        saw_activity = False
        if last_round != current_round:
            last_round = current_round
            offset = 0
            emitted_result_for_round = None
            saw_activity = True
        if latest_output_path.exists():
            latest_text = read_text(latest_output_path)
            if offset < len(latest_text):
                chunk = latest_text[offset:]
                offset = len(latest_text)
                saw_activity = True
                event = {
                    "event": "progress",
                    "bytes_delta": len(chunk.encode("utf-8")),
                    "bytes_total": len(latest_text.encode("utf-8")),
                    "preview": trim_preview(chunk, 140),
                    "round": current_round,
                }
                if json_mode is True:
                    emit_json({"tool": TOOL_NAME, "session_id": args.session, "watch_event": event})
                elif json_mode is False:
                    sys.stdout.write(format_watch_event(event) + "\n")
                    sys.stdout.flush()
        if record.get("latest_output_has_round_end") and emitted_result_for_round != current_round:
            event = {
                "event": "done",
                "result_bytes": len(record.get("result_text", "").encode("utf-8")),
                "result_chars": len(record.get("result_text", "")),
                "preview": trim_preview(record.get("result_text", ""), 140),
                "round": current_round,
            }
            emitted_result_for_round = current_round
            saw_activity = True
            if json_mode is True:
                emit_json({"tool": TOOL_NAME, "session_id": args.session, "watch_event": event})
            elif json_mode is False:
                sys.stdout.write(format_watch_event(event) + "\n")
                sys.stdout.flush()
        if saw_activity:
            last_activity = time.time()
        if record.get("state") in {"waiting_reply", *SESSION_TERMINAL_STATES}:
            record["continue_hint"] = build_continue_hint(record["session_id"])
            return record
        if (time.time() - last_activity) >= effective_idle_limit:
            record["watch_state"] = "idle_timeout"
            record["idle_timeout_sec"] = effective_idle_limit
            record["continue_hint"] = build_continue_hint(record["session_id"])
            return record
        time.sleep(args.interval)


def command_watch(args: argparse.Namespace, *, json_mode: bool | None = False) -> dict[str, Any]:
    job_dir = job_dir_for(args.job)
    if not job_dir.exists():
        raise FileNotFoundError(f"job not found: {args.job}")
    record = read_job_record(job_dir)
    session_id = str(record.get("session_id", "") or "")
    if session_id:
        payload = command_session_watch(
            argparse.Namespace(
                session=session_id,
                max_idle=getattr(args, "max_idle", None),
                timeout=getattr(args, "timeout", None),
                interval=getattr(args, "interval", 0.5),
            ),
            json_mode=json_mode,
        )
        job_payload = refresh_job_record(job_dir)
        if payload.get("watch_state"):
            job_payload["watch_state"] = payload["watch_state"]
        return job_payload
    events_path = job_events_path(job_dir)
    offset = 0
    last_activity = time.time()
    idle_limit = resolve_watch_idle_limit(getattr(args, "max_idle", None), getattr(args, "timeout", None))
    while True:
        record = refresh_job_record(job_dir)
        effective_idle_limit = extend_idle_limit_for_retry(
            idle_limit,
            "\n".join(json.dumps(event, ensure_ascii=False) for event in (record.get("recent_events") or [])),
            str(record.get("last_error", "") or ""),
            str(record.get("result_text", "") or ""),
        )
        saw_activity = False
        if events_path.exists():
            event_text = read_text(events_path)
            if offset < len(event_text):
                chunk = event_text[offset:]
                offset = len(event_text)
                saw_activity = True
                for raw_line in chunk.splitlines():
                    stripped = raw_line.strip()
                    if not stripped:
                        continue
                    try:
                        event = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    if json_mode is True:
                        emit_json({"tool": TOOL_NAME, "job_id": args.job, "watch_event": event})
                    elif json_mode is False:
                        sys.stdout.write(format_watch_event(event) + "\n")
                        sys.stdout.flush()
        if saw_activity:
            last_activity = time.time()
        if record.get("state") in TERMINAL_STATES:
            return record
        if (time.time() - last_activity) >= effective_idle_limit:
            record["watch_state"] = "idle_timeout"
            record["idle_timeout_sec"] = effective_idle_limit
            return record
        time.sleep(args.interval)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    explicit_json = bool(getattr(args, "json", False))
    explicit_text = bool(getattr(args, "text", False))
    json_mode = not explicit_text
    if args.command in {"start", "revise-job"} and not explicit_json and not explicit_text:
        json_mode = False
    if args.command == "logs" and not explicit_json and not getattr(args, "text", False):
        json_mode = False
    if args.command == "__worker":
        return worker_main(args)
    try:
        if args.command == "start":
            payload = command_start(args)
            payload = command_watch(
                argparse.Namespace(
                    job=payload["job_id"],
                    timeout=(args.timeout if float(getattr(args, "timeout", 0.0) or 0.0) > 0.0 else None),
                    max_idle=getattr(args, "max_idle", None),
                    interval=getattr(args, "interval", 0.5),
                ),
                json_mode=False if not json_mode else True,
            )
        elif args.command == "revise-job":
            payload = command_revise_job(args, json_mode=False if not json_mode else True)
        else:
            payload = dispatch(args)
        if json_mode:
            emit_json(payload)
        else:
            text = human_output(args.command, payload)
            if text:
                sys.stdout.write(text.rstrip() + "\n")
        return 0
    except Exception as exc:
        die(str(exc), json_mode=json_mode)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

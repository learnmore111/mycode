"""会话记忆 — AI 代理上下文的统一记忆系统。

单文件架构：每个会话一个 JSONL 文件，包含高级摘要和每轮交互记录。

文件布局（JSONL）：
    {"type":"summary", ...}          <- 滚动摘要（LLM 生成）
    {"type":"turn", "turn":1, ...}   <- 每轮记录
    {"type":"turn", "turn":2, ...}
    {"type":"turn", "turn":3, ...}
    {"type":"summary", ...}          <- 3 轮后更新的摘要
    ...

每 SUMMARY_INTERVAL 轮（默认 3），系统调用 LLM 以：
1. 更新全局摘要（what_was_done、technical_context 等）
2. 将最近轮次的助手摘要精炼为简洁结论
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from mycode.config import config as configmod
from mycode.session.memory.filelock import FileLock
from mycode.util import log as logmod
from mycode.util.paths import GlobalPaths

logger = logmod.create(service="session.memory")

MEMORY_DIR = GlobalPaths.data() / "memory"
SUMMARY_INTERVAL = 3


@dataclass
class InteractionEntry:
    """A single turn's interaction record."""
    turn: int
    timestamp: str
    user_query: str
    tool_calls: list[dict[str, Any]]
    assistant_summary: str


@dataclass
class SessionSummary:
    """Rolling session summary."""
    session_id: str
    project_path: str
    start_time: str
    end_time: str
    duration_minutes: int
    summary_text: str
    files_modified: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    tool_uses: dict[str, int] = field(default_factory=dict)
    key_topics: list[str] = field(default_factory=list)
    turn_count: int = 0


class SessionMemory:
    """统一会话记忆：单个 JSONL 文件中的摘要 + 每轮日志。

    用法：
        memory = SessionMemory(project_path)
        memory.record_tool_call(...)          # 流式传输期间
        await memory.record_turn(...)         # 每轮之后（可能触发 LLM）
        await memory.finalize(...)            # 会话结束时（强制 LLM）
    """

    def __init__(self, project_path: str, session_id: str | None = None):
        self.project_path = project_path
        self._session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.memory_dir = Path(project_path) / ".mycode" / "memory"
        self._config = self._load_config()
        self._turn_counter = 0
        self._current_tool_calls: list[dict[str, Any]] = []
        self._pending_turns: list[dict[str, Any]] = []
        self._summary: SessionSummary | None = None
        self._log_file_path: Path | None = None
        self._write_lock = asyncio.Lock()
        self._turn_lock = asyncio.Lock()
        self._summary_lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[InteractionEntry]] = set()

    @property
    def session_id(self) -> str:
        return self._session_id

    @session_id.setter
    def session_id(self, value: str) -> None:
        """Switch sessions without accidentally retaining the old log path."""
        if value == self._session_id:
            return
        self._session_id = value
        self._log_file_path = None
        self._turn_counter = 0
        self._pending_turns = []
        self._summary = None

    def _load_config(self) -> dict[str, Any]:
        cfg = configmod.get()
        sm = cfg.session_memory
        if not sm:
            return {"enabled": False, "note_language": "en", "min_duration_minutes": 1,
                    "min_user_prompts": 1, "max_notes_per_project": 50, "max_recent_for_context": 5}
        model_config = None
        if sm.model and (sm.model.provider or sm.model.name):
            model_config = {"provider": sm.model.provider, "name": sm.model.name,
                            "base_url": sm.model.base_url, "api_key": sm.model.api_key,
                            "api_key_env": sm.model.api_key_env}
        elif cfg.model:
            model_config = self._parse_main_model(cfg)
        return {"enabled": sm.enabled or False, "model": model_config,
                "note_language": sm.note_language or "en",
                "min_duration_minutes": sm.min_duration_minutes or 1,
                "min_user_prompts": sm.min_user_prompts or 1,
                "max_notes_per_project": sm.max_notes_per_project or 50,
                "max_recent_for_context": sm.max_recent_for_context or 5}

    def _parse_main_model(self, cfg: Any) -> dict[str, Any] | None:
        if not cfg.model:
            return None
        parts = cfg.model.split("/", 1)
        if len(parts) != 2:
            return None
        provider_id, model_id = parts
        base_url = api_key = api_key_env = None
        if cfg.provider and provider_id in cfg.provider:
            pcfg = cfg.provider[provider_id]
            if pcfg.api:
                base_url = pcfg.api
            if pcfg.options:
                api_key = pcfg.options.get("apiKey")
            if pcfg.env:
                api_key_env = pcfg.env[0] if isinstance(pcfg.env, list) and pcfg.env else pcfg.env
        if not api_key_env:
            env_map = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
                       "deepseek": "DEEPSEEK_API_KEY", "google": "GOOGLE_API_KEY", "groq": "GROQ_API_KEY"}
            api_key_env = env_map.get(provider_id)
        return {"provider": provider_id, "name": model_id, "base_url": base_url,
                "api_key": api_key, "api_key_env": api_key_env}

    @property
    def is_enabled(self) -> bool:
        return bool(self._config.get("enabled", False))

    # ------------------------------------------------------------------
    # 文件 I/O
    # ------------------------------------------------------------------

    def _ensure_dirs(self) -> None:
        date_str = datetime.now().strftime("%Y-%m-%d")
        (self.memory_dir / "sessions" / date_str).mkdir(parents=True, exist_ok=True)

    def _get_log_path(self) -> Path:
        if self._log_file_path:
            return self._log_file_path
        date_str = datetime.now().strftime("%Y-%m-%d")
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in self.session_id)
        self._log_file_path = self.memory_dir / "sessions" / date_str / f"{safe_id}.jsonl"
        return self._log_file_path

    async def _append_record(self, record: dict[str, Any]) -> None:
        """使用文件级锁定将记录追加到 JSONL 文件。

        确保线程安全、原子写入以防止 JSONL 损坏。
        在写入操作期间获取独占锁。
        """
        self._ensure_dirs()
        path = self._get_log_path()

        async with self._write_lock:
            lock = FileLock(path, timeout_seconds=10.0)
            async with lock:
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
        logger.debug("appended record", record_type=record.get("type"))

    def _load_all_records(self) -> list[dict[str, Any]]:
        path = self._get_log_path()
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        try:
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warn("skipping malformed JSONL record", path=str(path), line=line_number, error=str(exc))
                    continue
                if isinstance(record, dict):
                    records.append(record)
        except OSError:
            return []
        return records

    def _load_all_turns(self) -> list[dict[str, Any]]:
        return [r for r in self._load_all_records() if r.get("type") == "turn"]

    def _load_latest_summary(self) -> dict[str, Any] | None:
        records = self._load_all_records()
        for r in reversed(records):
            if r.get("type") == "summary":
                return r
        return None

    async def _rewrite_file(self, refined_turns: dict[int, str]) -> None:
        """使用仅追加合并策略重写 JSONL 文件。

        防止重写期间并发追加导致的数据丢失：
        1. 加载记录快照（操作开始时）
        2. 获取锁
        3. 再次加载记录（保留任何新记录）
        4. 合并：旧记录（带精炼）+ 新记录
        5. 原子写入合并结果
        6. 释放锁

        即使存在并发追加，这也确保零数据丢失。
        """
        # 第一个快照 - 用于稍后检测新记录
        records_snapshot = self._load_all_records()
        snapshot_ids = {json.dumps(r, sort_keys=True) for r in records_snapshot}

        async with self._write_lock:
            lock = FileLock(self._get_log_path(), timeout_seconds=10.0)
            async with lock:
                # 第二次读取 - 获取自第一次读取以来追加的任何记录
                records_now = self._load_all_records()

                # 识别新记录（在 records_now 中但不在快照中的记录）
                new_records = []
                for r in records_now:
                    r_json = json.dumps(r, sort_keys=True)
                    if r_json not in snapshot_ids:
                        new_records.append(r)

                # 处理带有精炼的旧记录
                processed_records = []
                for r in records_snapshot:
                    if r.get("type") == "turn" and r.get("turn") in refined_turns:
                        r = dict(r)  # Copy to avoid mutation
                        r["a"] = refined_turns[r["turn"]]
                    # Keep all records except old summaries
                    if r.get("type") != "summary":
                        processed_records.append(r)

                # 追加新记录（保留任何并发追加）
                processed_records.extend(new_records)

                # 追加最新摘要
                if self._summary:
                    processed_records.append({
                        "type": "summary",
                        "session_id": self._summary.session_id,
                        "project": self._summary.project_path,
                        "start": self._summary.start_time,
                        "end": self._summary.end_time,
                        "duration_min": self._summary.duration_minutes,
                        "text": self._summary.summary_text,
                        "files_modified": self._summary.files_modified,
                        "files_read": self._summary.files_read,
                        "tool_uses": self._summary.tool_uses,
                        "topics": self._summary.key_topics,
                        "turns": self._summary.turn_count,
                    })

                # 原子写入
                path = self._get_log_path()
                self._ensure_dirs()
                tmp = path.with_suffix(".tmp")
                with tmp.open("w", encoding="utf-8") as f:
                    for r in processed_records:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
                tmp.replace(path)

                logger.debug("rewrote file",
                            old_records=len(records_snapshot),
                            new_records_preserved=len(new_records),
                            refined_turns=len(refined_turns))

    # ------------------------------------------------------------------
    # 工具调用缓冲
    # ------------------------------------------------------------------

    def record_tool_call(self, tool_name: str, tool_input: dict[str, Any],
                         tool_output: str, status: str = "completed") -> None:
        input_summary = self._summarize_input(tool_name, tool_input)
        output_summary = tool_output[:200].strip() if tool_output else ""
        file_path = (tool_input.get("filePath") or tool_input.get("file_path")
                     or tool_input.get("path") or tool_input.get("target_file") or "")
        self._current_tool_calls.append({"tool": tool_name, "status": status,
                                          "input": input_summary, "output": output_summary, "file": file_path})

    # ------------------------------------------------------------------
    # 回合记录 + LLM 触发
    # ------------------------------------------------------------------

    async def record_turn(self, user_query: str, assistant_response: str,
                          messages: list[dict[str, Any]] | None = None,
                          start_time: datetime | None = None) -> InteractionEntry:
        async with self._turn_lock:
            self._turn_counter += 1
            entry = InteractionEntry(
                turn=self._turn_counter, timestamp=datetime.now().isoformat(),
                user_query=user_query[:500], tool_calls=list(self._current_tool_calls),
                assistant_summary=assistant_response[:200].strip())
            self._current_tool_calls = []
            turn_record = {"type": "turn", "turn": entry.turn, "ts": entry.timestamp,
                           "q": entry.user_query, "tools": entry.tool_calls, "a": entry.assistant_summary}
            await self._append_record(turn_record)
            self._pending_turns.append(turn_record)

            if self._turn_counter % SUMMARY_INTERVAL == 0 and self.is_enabled:
                await self._llm_update(messages=messages, start_time=start_time)

        logger.debug("recorded turn", turn=entry.turn, tools=len(entry.tool_calls))
        return entry

    def schedule_record_turn(
        self,
        user_query: str,
        assistant_response: str,
        messages: list[dict[str, Any]] | None = None,
        start_time: datetime | None = None,
    ) -> asyncio.Task[InteractionEntry]:
        """Schedule a turn record while retaining ownership of the task."""
        task = asyncio.create_task(self.record_turn(user_query, assistant_response, messages, start_time))
        self._background_tasks.add(task)

        def _done(completed: asyncio.Task[InteractionEntry]) -> None:
            self._background_tasks.discard(completed)
            if completed.cancelled():
                return
            try:
                completed.result()
            except Exception as exc:
                logger.warn("background memory write failed", error=str(exc))

        task.add_done_callback(_done)
        return task

    async def flush_pending_tasks(self, timeout: float = 5.0) -> None:
        """Wait for tracked background writes before shutdown/finalization."""
        tasks = list(self._background_tasks)
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in done:
            try:
                task.result()
            except Exception as exc:
                logger.warn("background memory write failed", error=str(exc))
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def finalize(self, messages: list[dict[str, Any]] | None = None,
                       start_time: datetime | None = None) -> Path | None:
        await self.flush_pending_tasks()
        if not self.is_enabled or self._turn_counter == 0:
            return None
        await self._llm_update(messages=messages, start_time=start_time, force=True)
        return self._get_log_path()

    # ------------------------------------------------------------------
    # LLM 更新逻辑
    # ------------------------------------------------------------------

    async def _llm_update(self, messages: list[dict[str, Any]] | None = None,
                          start_time: datetime | None = None, force: bool = False) -> None:
        async with self._summary_lock:
            try:
                all_turns = self._load_all_turns()
                recent_turns = list(self._pending_turns)
                result = await self._call_llm_combined(all_turns, recent_turns, messages, start_time)
                if result:
                    summary_text = result.get("summary", "")
                    refined_turns = result.get("refined_turns", {})
                    if summary_text:
                        now = datetime.now()
                        self._summary = SessionSummary(
                            session_id=self.session_id, project_path=self.project_path,
                            start_time=(start_time or now).isoformat(), end_time=now.isoformat(),
                            duration_minutes=max(int((now - (start_time or now)).total_seconds() / 60), 1),
                            summary_text=summary_text,
                            files_modified=self._extract_files(all_turns, "write"),
                            files_read=self._extract_files(all_turns, "read"),
                            tool_uses=self._count_tools(all_turns),
                            key_topics=self._infer_topics(set(self._extract_files(all_turns, "write"))),
                            turn_count=self._turn_counter)
                    await self._rewrite_file(refined_turns)
                self._pending_turns = []
            except Exception as e:
                logger.error("LLM update failed", error=str(e))
                self._pending_turns = []

    async def _call_llm_combined(self, all_turns: list[dict[str, Any]], recent_turns: list[dict[str, Any]],
                                  messages: list[dict[str, Any]] | None, start_time: datetime | None) -> dict[str, Any] | None:
        model_config = self._config.get("model")
        if not model_config:
            return self._fallback_combined(all_turns, recent_turns, start_time)
        api_key = model_config.get("api_key")
        if not api_key:
            env = model_config.get("api_key_env")
            if env:
                api_key = os.environ.get(env)
        if not api_key:
            return self._fallback_combined(all_turns, recent_turns, start_time)

        prompt_text = self._build_combined_prompt(all_turns, recent_turns, start_time)
        try:
            import litellm
            provider = model_config.get("provider", "openai")
            model_name = model_config.get("name", "gpt-4o-mini")
            base_url = model_config.get("base_url")
            if not base_url:
                cfg = configmod.get()
                if cfg.provider and provider in cfg.provider:
                    base_url = cfg.provider[provider].api
            if provider == "anthropic":
                model_str = f"anthropic/{model_name}"
            elif provider == "deepseek":
                model_str = f"openai/{model_name}"
                if not base_url:
                    base_url = "https://api.deepseek.com/v1"
            elif base_url or provider == "openai":
                model_str = f"openai/{model_name}"
            else:
                model_str = f"{provider}/{model_name}"

            kwargs: dict[str, Any] = {"model": model_str, "max_tokens": 2048, "temperature": 0.3,
                                       "api_key": api_key,
                                       "messages": [{"role": "user", "content": prompt_text}]}
            if base_url:
                kwargs["base_url"] = base_url
            logger.debug("calling LLM for memory update", provider=provider, model=model_name)

            # 对临时错误使用指数退避重试
            max_retries = 2
            for attempt in range(max_retries + 1):
                try:
                    resp = await litellm.acompletion(**kwargs)
                    raw = resp.choices[0].message.content or ""
                    return self._parse_llm_response(raw, recent_turns)
                except Exception as e:
                    if attempt < max_retries and _is_transient_error(e):
                        delay = 1.0 * (2 ** attempt)  # 1秒、2秒
                        logger.info("retrying LLM call for memory update",
                                    attempt=attempt + 1, delay=delay, error=str(e))
                        await asyncio.sleep(delay)
                        continue
                    logger.error("LLM call failed", error=str(e), attempts=attempt + 1)
                    return self._fallback_combined(all_turns, recent_turns, start_time)
        except Exception as e:
            logger.error("LLM call setup failed", error=str(e))
            return self._fallback_combined(all_turns, recent_turns, start_time)
        # Unreachable, but satisfies type checker
        return self._fallback_combined(all_turns, recent_turns, start_time)

    # ------------------------------------------------------------------
    # 提示词构建
    # ------------------------------------------------------------------

    def _build_combined_prompt(self, all_turns: list[dict[str, Any]], recent_turns: list[dict[str, Any]],
                                start_time: datetime | None) -> str:
        turns_text = ""
        for t in all_turns:
            n = t.get("turn", "?")
            q = t.get("q", "")
            tools = t.get("tools", [])
            a = t.get("a", "")
            tools_lines = ""
            for tc in tools:
                parts = [f"  - {tc.get('tool', '?')}"]
                if tc.get("file"):
                    parts.append(f"file={tc['file']}")
                if tc.get("input"):
                    parts.append(f"({tc['input']})")
                if tc.get("output"):
                    parts.append(f"→ {tc['output'][:80]}")
                tools_lines += " ".join(parts) + "\n"
            turns_text += f"[Turn {n}] user: {q}\n{tools_lines}  assistant: {a}\n\n"

        recent_text = ""
        for t in recent_turns:
            recent_text += f"  Turn {t.get('turn', '?')}: \"{t.get('a', '')}\"\n"

        existing = f"\n## Previous Summary\n{self._summary.summary_text}\n" if self._summary else ""
        now = datetime.now()
        dur = max(int((now - (start_time or now)).total_seconds() / 60), 1)

        return f"""You are an AI coding agent's memory system. You have TWO tasks:

## TASK 1: Update Session Summary
{existing}
## Session: {self.project_path} | {dur}min | {self._turn_counter} turns

## All Turns
{turns_text}
Output summary in EXACTLY this format:

## what_was_done
[1-3 bullet points: concrete technical actions]

## technical_context
[1-3 bullet points: key project/technical facts]

## problems_encountered
[0-2 bullet points or "none"]

## unfinished_work
[0-2 bullet points or "none"]

## key_files
[important files with one-line description each]

---

## TASK 2: Refine Turn Summaries

Rewrite each raw summary into a concise conclusion (max 150 chars).
Focus on WHAT WAS FOUND/DONE, not process.

Raw summaries:
{recent_text}
Output format (one per line):
TURN_<number>: <refined summary>

Rules: be concise, technical facts only, no filler, no code blocks."""

    def _parse_llm_response(self, raw: str, recent_turns: list[dict[str, Any]]) -> dict[str, Any]:
        lines = raw.split("\n")
        summary_lines = []
        refined: dict[int, str] = {}
        in_refinement = False
        for line in lines:
            s = line.strip()
            if s.startswith("TURN_"):
                in_refinement = True
                try:
                    after = s[5:]
                    ci = after.index(":")
                    num = int(after[:ci])
                    text = after[ci + 1:].strip()
                    if text:
                        refined[num] = text[:200]
                except (ValueError, IndexError):
                    pass
            elif not in_refinement:
                summary_lines.append(line)
        return {"summary": "\n".join(summary_lines).strip(), "refined_turns": refined}

    def _fallback_combined(self, all_turns: list[dict[str, Any]], recent_turns: list[dict[str, Any]],
                            start_time: datetime | None) -> dict[str, Any]:
        now = datetime.now()
        dur = max(int((now - (start_time or now)).total_seconds() / 60), 1)
        files_mod = self._extract_files(all_turns, "write")
        files_read = self._extract_files(all_turns, "read")
        tools = self._count_tools(all_turns)
        queries = [t.get("q", "") for t in all_turns if t.get("q")]

        lines = ["## what_was_done"]
        for q in queries[:5]:
            lines.append(f"- {q[:200]}")
        if not queries:
            lines.append("- none")
        lines.append("\n## technical_context")
        lines.append(f"- {dur}min, {len(all_turns)} turns")
        if tools:
            lines.append(f"- tools: {', '.join(f'{k}×{v}' for k, v in tools.items())}")
        lines.append("\n## problems_encountered\n- none")
        lines.append("\n## unfinished_work\n- none")
        lines.append("\n## key_files")
        for f in (files_mod or files_read)[:10]:
            lines.append(f"- {f}")
        if not files_mod and not files_read:
            lines.append("- none")
        return {"summary": "\n".join(lines), "refined_turns": {}}

    # ------------------------------------------------------------------
    # 上下文格式化（用于注入代理提示词）
    # ------------------------------------------------------------------

    def format_for_context(self, limit: int = 30) -> str:
        """将统一记忆文件格式化为代理可消费的上下文。"""
        records = self._load_all_records()
        if not records:
            return ""

        lines = ["<session_memory>"]

        # 1. 摘要部分（最后一条摘要记录）
        summary_rec = None
        for r in reversed(records):
            if r.get("type") == "summary":
                summary_rec = r
                break
        if summary_rec:
            lines.append("<summary>")
            lines.append(summary_rec.get("text", ""))
            lines.append("</summary>")

        # 2. 回合记录
        turns = [r for r in records if r.get("type") == "turn"][-limit:]
        if turns:
            lines.append("<turns>")
            for t in turns:
                n = t.get("turn", "?")
                q = t.get("q", "")
                tools = t.get("tools", [])
                a = t.get("a", "")
                lines.append(f"<turn n=\"{n}\">")
                lines.append(f"  user: {q[:300]}")
                for tc in tools:
                    parts = [f"    {tc.get('tool', '?')}"]
                    if tc.get("file"):
                        parts.append(f"file={tc['file']}")
                    if tc.get("input"):
                        parts.append(f"({tc['input']})")
                    if tc.get("output"):
                        parts.append(f"→ {tc['output'][:100]}")
                    lines.append(" ".join(parts))
                if a:
                    lines.append(f"  result: {a[:200]}")
                lines.append("</turn>")
            lines.append("</turns>")

        lines.append("</session_memory>")
        return "\n".join(lines)

    def load_recent_sessions(self, limit: int = 5) -> list[dict[str, Any]]:
        """加载最近会话文件的摘要记录以进行上下文注入。"""
        sessions_dir = self.memory_dir / "sessions"
        if not sessions_dir.exists():
            return []
        results = []
        # 按反向顺序遍历日期目录
        date_dirs = sorted(sessions_dir.iterdir(), reverse=True)
        for dd in date_dirs:
            if not dd.is_dir():
                continue
            for f in sorted(dd.iterdir(), reverse=True):
                if f.suffix != ".jsonl":
                    continue
                try:
                    for line in reversed(f.read_text(encoding="utf-8").strip().split("\n")):
                        if not line:
                            continue
                        rec = json.loads(line)
                        if rec.get("type") == "summary" and rec.get("project") == self.project_path:
                            results.append({"path": str(f), "date": dd.name, **rec})
                            break
                except (OSError, json.JSONDecodeError):
                    continue
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break
        return results

    def format_history_context(self, recent_sessions: list[dict[str, Any]] | None = None) -> str:
        """将先前会话的摘要 + 当前会话日志格式化为代理上下文。"""
        parts = []
        sessions = recent_sessions or self.load_recent_sessions()
        if sessions:
            lines = ["<session_history>"]
            for s in sessions:
                date = s.get("date", "?")
                dur = s.get("duration_min", 0)
                topics = ", ".join(s.get("topics", [])) or "general"
                lines.append(f"<session date=\"{date}\" duration=\"{dur}min\" topics=\"{topics}\">")
                lines.append(s.get("text", "(no summary)"))
                lines.append("</session>")
            lines.append("</session_history>")
            parts.append("\n".join(lines))

        current = self.format_for_context()
        if current:
            parts.append(current)
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # 辅助函数
    # ------------------------------------------------------------------

    @staticmethod
    def _summarize_input(tool_name: str, tool_input: dict[str, Any]) -> str:
        if not tool_input:
            return ""
        for key in ("query", "pattern", "queryString", "search", "regex"):
            if key in tool_input:
                return f"{key}={tool_input[key]!r}"
        for key in ("filePath", "file_path", "path", "target_file"):
            if key in tool_input:
                extras = []
                if "offset" in tool_input:
                    extras.append(f"offset={tool_input['offset']}")
                if "limit" in tool_input:
                    extras.append(f"limit={tool_input['limit']}")
                return ", ".join(extras) if extras else ""
        if "old_str" in tool_input:
            return f"replacing: {tool_input['old_str'][:80]!r}"
        if "command" in tool_input:
            return f"cmd={tool_input['command'][:100]!r}"
        for k, v in tool_input.items():
            if not k.startswith("_"):
                return f"{k}={str(v)[:80]!r}"
        return ""

    @staticmethod
    def _extract_files(turns: list[dict[str, Any]], mode: str) -> list[str]:
        write_tools = {"edit", "write", "write_file", "replace_in_file", "write_to_file"}
        read_tools = {"read", "read_file", "glob", "grep", "search_content", "codebase_search"}
        target = write_tools if mode == "write" else read_tools
        files: set[str] = set()
        for t in turns:
            for tc in t.get("tools", []):
                if tc.get("tool") in target and tc.get("file"):
                    files.add(tc["file"])
        return sorted(files)

    @staticmethod
    def _count_tools(turns: list[dict[str, Any]]) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for t in turns:
            for tc in t.get("tools", []):
                counter[tc.get("tool", "unknown")] += 1
        return dict(counter.most_common())

    @staticmethod
    def _infer_topics(files: set[str]) -> list[str]:
        ext_map = {".py": "Python", ".ts": "TypeScript", ".tsx": "React/TypeScript",
                   ".js": "JavaScript", ".jsx": "React/JavaScript", ".rs": "Rust",
                   ".go": "Go", ".java": "Java", ".css": "Styling", ".html": "HTML",
                   ".md": "Documentation", ".json": "Configuration", ".yaml": "Configuration",
                   ".yml": "Configuration", ".sql": "Database", ".sh": "Shell"}
        topics: set[str] = set()
        for f in files:
            ext = Path(f).suffix
            if ext in ext_map:
                topics.add(ext_map[ext])
        return list(topics)


def _is_transient_error(e: Exception) -> bool:
    """检查错误是否是临时的并值得重试。

    匹配来自 LLM API 提供商的常见临时错误模式：
    速率限制（429）、服务器错误（503）、超时和连接问题。
    """
    error_str = str(e).lower()
    transient_indicators = ["rate limit", "timeout", "429", "503", "connection", "temporary", "overloaded"]
    return any(ind in error_str for ind in transient_indicators)


# ---------------------------------------------------------------------------
# 便捷函数（向后兼容）
# ---------------------------------------------------------------------------


def create_session_memory(project_path: str, session_id: str) -> SessionMemory:
    """Create a SessionMemory instance."""
    return SessionMemory(project_path, session_id)


def load_recent_notes(project_path: str, limit: int = 5) -> list[dict[str, Any]]:
    """Load recent session summaries for a project."""
    memory = SessionMemory(project_path)
    return memory.load_recent_sessions(limit)


# ---------------------------------------------------------------------------
# 记忆新鲜度管理
# ---------------------------------------------------------------------------


def memory_age_days(mtime_ms: float) -> int:
    """计算记忆已有多少天。0=今天，1=昨天，等等。"""
    import datetime
    now = datetime.datetime.now()
    mem_time = datetime.datetime.fromtimestamp(mtime_ms / 1000)
    delta = now.date() - mem_time.date()
    return max(0, delta.days)


def memory_age_text(mtime_ms: float) -> str:
    """记忆的人类可读年龄字符串。"""
    days = memory_age_days(mtime_ms)
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def memory_freshness_note(mtime_ms: float) -> str | None:
    """为超过 1 天的记忆生成新鲜度警告。

    对于新鲜记忆返回 None，对于陈旧记忆返回警告字符串。
    较旧的记忆可能引用过时的代码状态。
    """
    days = memory_age_days(mtime_ms)
    if days <= 1:
        return None
    age = memory_age_text(mtime_ms)
    return (
        f"<system-reminder>This memory is {age}. "
        f"Memories are point-in-time observations, not live state — "
        f"claims about code behavior or file:line citations may be outdated. "
        f"Verify against current code before asserting as fact.</system-reminder>"
    )

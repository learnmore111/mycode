"""Permission manager — ask/reply flow for tool execution.

Handles the interactive permission flow:
1. Tool wants to execute → ask()
2. Evaluate rules → allow/deny/ask
3. If "ask" → block, publish event, wait for user reply
4. User replies → reply() → unblock tool

"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from mycode.bus.events import PERMISSION_ASKED, PERMISSION_REPLIED
from mycode.permission.evaluate import evaluate as eval_rule
from mycode.permission.schema import (
    CorrectedError,
    DeniedError,
    PermissionRequest,
    RejectedError,
    Reply,
    Rule,
    Ruleset,
)
from mycode.util import ids
from mycode.util import log as logmod
from mycode.util.wildcard import match

if TYPE_CHECKING:
    from mycode.bus.bus import Bus

logger = logmod.create(service="permission")

# Safety net: if a reply never arrives, fail closed after this long so tool
# execution cannot hang the session forever. Set to None to disable (used
# by some CLI clients where the user may legitimately take >1h).
_ASK_TIMEOUT_SECONDS = 3600.0


class PermissionManager:
    """Manages permission requests and approvals for an instance."""

    def __init__(self, bus: Bus, project_id: str) -> None:
        self._bus = bus
        self._project_id = project_id
        self._pending: dict[str, tuple[PermissionRequest, asyncio.Future[None]]] = {}
        self._approved: Ruleset = []
        self._lock = asyncio.Lock()  # Protect _pending dict from concurrent access

    async def ask(
        self,
        *,
        session_id: str,
        permission: str,
        patterns: list[str],
        ruleset: Ruleset,
        metadata: dict[str, Any] | None = None,
        always: list[str] | None = None,
        tool: dict[str, str] | None = None,
        request_id: str | None = None,
    ) -> None:
        """Check permissions. Blocks if user interaction is needed.

        Raises:
            DeniedError: If rules explicitly deny the action.
            RejectedError: If the user rejects the request.
            CorrectedError: If the user rejects with feedback.
            TimeoutError: If the user does not reply within the timeout.
        """
        needs_ask = False

        for pattern in patterns:
            rule = eval_rule(permission, pattern, ruleset, self._approved)
            logger.info("evaluated", permission=permission, pattern=pattern, action=rule.action)

            if rule.action == "deny":
                relevant = [r for r in ruleset if match(permission, r.permission)]
                raise DeniedError(relevant)
            if rule.action == "allow":
                continue
            needs_ask = True

        if not needs_ask:
            return

        rid = request_id or ids.permission_id()
        request = PermissionRequest(
            id=rid,
            session_id=session_id,
            permission=permission,
            patterns=patterns,
            metadata=metadata or {},
            always=always or patterns,
            tool=tool,
        )

        logger.info("asking", id=rid, permission=permission, patterns=patterns)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()

        # Register the pending request under the lock so reply() cannot
        # observe a partially-initialised entry.
        async with self._lock:
            self._pending[rid] = (request, future)

        await self._bus.publish(PERMISSION_ASKED, {
            "id": rid,
            "session_id": session_id,
            "permission": permission,
            "patterns": patterns,
            "metadata": metadata or {},
            "always": always or patterns,
            "tool": tool,
        })

        try:
            if _ASK_TIMEOUT_SECONDS is None:
                await future
            else:
                await asyncio.wait_for(future, timeout=_ASK_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            # No human reply in time — fail closed. Surface as a rejection
            # so the processor treats it like a regular deny.
            logger.warn("permission ask timed out", id=rid, permission=permission)
            raise RejectedError() from exc
        except asyncio.CancelledError:
            # The awaiting task was cancelled — surface cleanly so the
            # future is not left orphaned.
            if not future.done():
                future.cancel()
            raise
        finally:
            async with self._lock:
                self._pending.pop(rid, None)

    async def reply(self, *, request_id: str, reply: Reply, message: str | None = None) -> None:
        """Reply to a pending permission request."""
        # Compute work under the lock but perform bus publishes AFTER so we
        # do not hold the lock across `await`.
        cascade_rejects: list[tuple[str, str]] = []  # (session_id, request_id)
        cascade_allows: list[tuple[str, str]] = []   # (session_id, request_id)
        primary_session_id: str | None = None

        async with self._lock:
            entry = self._pending.pop(request_id, None)
            if not entry:
                return

            request, future = entry
            primary_session_id = request.session_id

            if reply == "reject":
                if not future.done():
                    if message:
                        future.set_exception(CorrectedError(message))
                    else:
                        future.set_exception(RejectedError())

                # Also reject all other pending requests for the same session.
                # Use a snapshot list so the dict is not mutated during iteration.
                siblings = [
                    (rid, (req, fut))
                    for rid, (req, fut) in list(self._pending.items())
                    if req.session_id == request.session_id
                ]
                for rid, (req, fut) in siblings:
                    self._pending.pop(rid, None)
                    if not fut.done():
                        # Another path may have completed it between `done()` and
                        # `set_*`; ignore the resulting InvalidStateError.
                        with contextlib.suppress(asyncio.InvalidStateError):
                            fut.set_exception(RejectedError())
                    cascade_rejects.append((req.session_id, rid))
            elif reply == "always":
                if not future.done():
                    with contextlib.suppress(asyncio.InvalidStateError):
                        future.set_result(None)

                for pattern in request.always:
                    self._approved.append(Rule(
                        permission=request.permission,
                        pattern=pattern,
                        action="allow",
                    ))

                # Auto-resolve other pending that now match.
                resolved_ids: list[str] = []
                for rid, (req, _fut) in list(self._pending.items()):
                    if req.session_id != request.session_id:
                        continue
                    all_ok = all(
                        eval_rule(req.permission, p, self._approved).action == "allow"
                        for p in req.patterns
                    )
                    if all_ok:
                        resolved_ids.append(rid)

                for rid in resolved_ids:
                    sibling = self._pending.pop(rid, None)
                    if sibling is None:
                        continue
                    sib_req, sib_fut = sibling
                    if not sib_fut.done():
                        with contextlib.suppress(asyncio.InvalidStateError):
                            sib_fut.set_result(None)
                    cascade_allows.append((sib_req.session_id, rid))
            else:  # "allow" (one-shot)
                if not future.done():
                    with contextlib.suppress(asyncio.InvalidStateError):
                        future.set_result(None)

        # Publish bus events AFTER releasing the lock to avoid deadlock or
        # long lock-hold while listeners run.
        await self._bus.publish(PERMISSION_REPLIED, {
            "session_id": primary_session_id,
            "request_id": request_id,
            "reply": reply,
        })
        for sid, rid in cascade_rejects:
            await self._bus.publish(PERMISSION_REPLIED, {
                "session_id": sid,
                "request_id": rid,
                "reply": "reject",
            })
        for sid, rid in cascade_allows:
            await self._bus.publish(PERMISSION_REPLIED, {
                "session_id": sid,
                "request_id": rid,
                "reply": "always",
            })

    def list_pending(self) -> list[PermissionRequest]:
        """List all pending permission requests."""
        return [req for req, _ in self._pending.values()]


def from_config(permission_config: dict[str, Any]) -> Ruleset:
    """Convert a config permission dict to a Ruleset.

    Handles both simple ("allow") and pattern-based ({"*.ts": "allow"}) formats.
    """
    ruleset: Ruleset = []
    for key, value in permission_config.items():
        if isinstance(value, str):
            ruleset.append(Rule(permission=key, pattern="*", action=value))  # type: ignore[arg-type]
        elif isinstance(value, dict):
            for pattern, action in value.items():
                ruleset.append(Rule(permission=key, pattern=pattern, action=action))  # type: ignore[arg-type]
    return ruleset


def merge(*rulesets: Ruleset) -> Ruleset:
    """Merge multiple rulesets. Later rules have higher priority."""
    result: Ruleset = []
    for rs in rulesets:
        result.extend(rs)
    return result

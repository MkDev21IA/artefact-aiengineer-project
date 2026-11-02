"""Ephemeral, TTL-bounded per-customer conversation session and history store.

Preserves atomic message turn groups so sliding-window trimming never separates
an OpenAI `assistant` `tool_calls` message from its corresponding `tool` outputs.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from emporio_agent.domain.models import CustomerProfile, EscalationTicketPayload
from emporio_agent.privacy.pii_vault import SessionPIIVault


@dataclass
class TurnMessageGroup:
    """Atomic sequence of OpenAI API messages generated during a single user turn."""

    messages: list[dict[str, Any]] = field(default_factory=list)


class CustomerSession:
    """Isolated runtime state for a single authenticated customer conversation."""

    def __init__(
        self,
        session_id: str,
        authenticated_customer_id: int,
        customer_profile: CustomerProfile | None = None,
        ttl_seconds: int = 1800,
        max_history_messages: int = 20,
        clock_fn=time.monotonic,
    ) -> None:
        self.session_id = session_id
        self.authenticated_customer_id = authenticated_customer_id
        self.customer_profile = customer_profile
        self._ttl_seconds = ttl_seconds
        self._max_history_messages = max_history_messages
        self._clock_fn = clock_fn
        self._last_touched: float = self._clock_fn()

        self.pii_vault = SessionPIIVault(ttl_seconds=ttl_seconds, clock_fn=clock_fn)
        if customer_profile is not None:
            self.pii_vault.bind_customer_profile(customer_profile)

        self._turn_groups: list[TurnMessageGroup] = []
        self._raw_user_utterances_norm: list[str] = []
        self.active_escalation: EscalationTicketPayload | None = None

    def is_expired(self) -> bool:
        """Return True if this session has been idle longer than `ttl_seconds`."""
        return (self._clock_fn() - self._last_touched) > self._ttl_seconds

    def touch(self) -> None:
        """Refresh the session inactivity timer."""
        self._last_touched = self._clock_fn()

    def clear(self) -> None:
        """Purge all conversation history and PII vault mappings from memory."""
        self._turn_groups.clear()
        self._raw_user_utterances_norm.clear()
        self.active_escalation = None
        self.pii_vault.clear()

    @property
    def prior_user_messages(self) -> tuple[str, ...]:
        """Return bounded history of prior masked user messages for repetition checks."""
        return tuple(self._raw_user_utterances_norm[-10:])

    def record_user_utterance_for_repetition(self, masked_text: str) -> None:
        """Append masked user text to the repetition sliding buffer."""
        self._raw_user_utterances_norm.append(masked_text)
        if len(self._raw_user_utterances_norm) > 10:
            self._raw_user_utterances_norm = self._raw_user_utterances_norm[-10:]

    def append_turn_group(self, messages: list[dict[str, Any]]) -> None:
        """Append a completed atomic turn group and enforce bounded message window."""
        self.touch()
        self._turn_groups.append(TurnMessageGroup(messages=list(messages)))
        self._trim_window()

    def _trim_window(self) -> None:
        """Drop oldest complete turn groups while preserving at least the newest turn."""
        while len(self._turn_groups) > 1 and self.total_message_count > self._max_history_messages:
            self._turn_groups.pop(0)

    @property
    def total_message_count(self) -> int:
        """Return total count of role messages currently retained in memory."""
        return sum(len(group.messages) for group in self._turn_groups)

    def get_openai_history(self) -> list[dict[str, Any]]:
        """Flatten retained turn groups into a valid OpenAI `messages` sequence."""
        self.touch()
        flattened: list[dict[str, Any]] = []
        for group in self._turn_groups:
            flattened.extend(group.messages)
        return flattened


class EphemeralSessionStore:
    """In-memory TTL session manager isolating state across customers."""

    def __init__(
        self,
        ttl_seconds: int = 1800,
        max_history_messages: int = 20,
        clock_fn=time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_history_messages = max_history_messages
        self._clock_fn = clock_fn
        self._sessions: dict[str, CustomerSession] = {}

    def purge_expired(self) -> int:
        """Evict all expired sessions and wipe their PII vaults; return evicted count."""
        expired_ids = [sid for sid, sess in self._sessions.items() if sess.is_expired()]
        for sid in expired_ids:
            self._sessions[sid].clear()
            del self._sessions[sid]
        return len(expired_ids)

    def get_or_create_session(
        self,
        authenticated_customer_id: int,
        session_id: str | None = None,
        customer_profile: CustomerProfile | None = None,
    ) -> CustomerSession:
        """Retrieve an active session matching `authenticated_customer_id` or create one.

        Enforces Zero-Trust binding: a `session_id` bound to Customer A can never be
        reused by Customer B.
        """
        self.purge_expired()
        resolved_sid = session_id or f"sess_{authenticated_customer_id}_{uuid.uuid4().hex[:8]}"
        existing = self._sessions.get(resolved_sid)

        if existing is not None:
            if existing.authenticated_customer_id != authenticated_customer_id:
                raise PermissionError(
                    f"Session '{resolved_sid}' belongs to a different authenticated customer."
                )
            existing.touch()
            return existing

        created = CustomerSession(
            session_id=resolved_sid,
            authenticated_customer_id=authenticated_customer_id,
            customer_profile=customer_profile,
            ttl_seconds=self._ttl_seconds,
            max_history_messages=self._max_history_messages,
            clock_fn=self._clock_fn,
        )
        self._sessions[resolved_sid] = created
        return created

    def reset_session(self, session_id: str) -> None:
        """Explicitly clear and remove a session by ID."""
        sess = self._sessions.pop(session_id, None)
        if sess is not None:
            sess.clear()

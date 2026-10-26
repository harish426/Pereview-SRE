"""Episodic memory: what was asked, which queries ran, and what was concluded in this chat.

Scope is a single chat session (CLI chat run or one browser tab). It lives in process memory
only: nothing is written to disk, and it is discarded when the chat ends, the tab is closed,
the session idles past the TTL, or the process restarts. Everything stored is redacted and
size-capped so memory can never grow without bound or hold secrets.
"""

import re
import threading
import time
from dataclasses import dataclass, field

from pereviewsre.logging_setup import get_logger
from pereviewsre.security.redaction import redact_text

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_MAX_QUESTION = 500
_MAX_ANSWER = 1500
_MAX_QUERY = 300
_MAX_QUERIES_PER_EPISODE = 6


def valid_session_id(session_id: str) -> bool:
  return bool(_SESSION_ID_RE.match(session_id or ""))


@dataclass
class Episode:
  question: str
  queries: list[str]  # tool name + (redacted) arguments that were executed
  conclusion: str
  at: float = field(default_factory=time.time)


class SessionMemory:
  """Episodes of one chat. Newest last; oldest are evicted past `max_episodes`."""

  def __init__(self, max_episodes: int = 10) -> None:
    self._max = max_episodes
    self._episodes: list[Episode] = []
    self._lock = threading.Lock()
    self.last_used = time.monotonic()

  def add(self, question: str, queries: list[str], conclusion: str) -> None:
    ep = Episode(
      question=redact_text(question)[:_MAX_QUESTION],
      queries=[redact_text(q)[:_MAX_QUERY] for q in queries[:_MAX_QUERIES_PER_EPISODE]],
      conclusion=redact_text(conclusion)[:_MAX_ANSWER],
    )
    with self._lock:
      self._episodes.append(ep)
      del self._episodes[: -self._max]
      self.last_used = time.monotonic()

  def __len__(self) -> int:
    return len(self._episodes)

  def render(self) -> str:
    """Text block for the LLM, or '' when there is nothing to recall."""
    with self._lock:
      episodes = list(self._episodes)
      self.last_used = time.monotonic()
    if not episodes:
      return ""
    header = (
      "SESSION MEMORY - earlier investigations in this same chat (oldest first). Use it for "
      "context and to avoid repeating work, e.g. reuse operation_Ids, time windows and "
      "suspects already found. It may be stale: re-query App Insights to confirm anything "
      "you rely on before concluding."
    )
    lines = [header]
    for i, ep in enumerate(episodes, 1):
      lines.append(f"\n[{i}] Question: {ep.question}")
      for q in ep.queries:
        lines.append(f"    ran: {q}")
      lines.append(f"    Concluded: {ep.conclusion}")
    return "\n".join(lines)


class MemoryStore:
  """Thread-safe map of session id -> SessionMemory with idle expiry and a global cap."""

  def __init__(self, ttl_s: int = 1800, max_sessions: int = 500, max_episodes: int = 10) -> None:
    self._log = get_logger(__name__)
    self._ttl = ttl_s
    self._max_sessions = max_sessions
    self._max_episodes = max_episodes
    self._sessions: dict[str, SessionMemory] = {}
    self._lock = threading.Lock()

  def _evict(self) -> None:
    now = time.monotonic()
    for sid in [s for s, m in self._sessions.items() if now - m.last_used > self._ttl]:
      del self._sessions[sid]
      self._log.info("Session memory expired")
    # Still over the cap: drop least recently used.
    while len(self._sessions) >= self._max_sessions:
      oldest = min(self._sessions, key=lambda s: self._sessions[s].last_used)
      del self._sessions[oldest]
      self._log.warning("Session memory cap reached; evicted least recently used")

  def get(self, session_id: str) -> SessionMemory:
    with self._lock:
      mem = self._sessions.get(session_id)
      if mem is None:
        self._evict()
        mem = SessionMemory(self._max_episodes)
        self._sessions[session_id] = mem
      return mem

  def clear(self, session_id: str) -> None:
    with self._lock:
      if self._sessions.pop(session_id, None) is not None:
        self._log.info("Session memory cleared")

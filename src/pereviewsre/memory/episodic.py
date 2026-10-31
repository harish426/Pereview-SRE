"""Episodic memory: what was asked, which queries ran, and what was concluded in this chat.

Scope is a single chat session (CLI chat run or one browser tab). It lives in process memory
only: nothing is written to disk, and it is discarded when the chat ends, the tab is closed,
the session idles past the TTL, or the process restarts. Everything stored is redacted and
size-capped so memory can never grow without bound or hold secrets.

Efficiency strategy (what the LLM is shown, newest information first-class):
  1. Pinned FACTS - a small structured block (time window, services, operation_Ids, root
     causes, open items) merged after every answer. Size is bounded however long the chat runs.
  2. Tiered episodes - the newest `_FULL_RECENT` episodes in full, older ones as a short digest.
  5. Noise dropped - duplicate tool calls collapsed, answers trimmed to Root cause +
     Recommended actions, raw tool results never stored (the agent also skips failed calls).
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
_FULL_RECENT = 2  # newest episodes rendered in full; older ones as a compact digest
_DIGEST_QUESTION = 120
_DIGEST_CONCLUSION = 240
_MAX_DIGEST_IDS = 5

# Pinned-facts caps (bounded memory regardless of chat length)
_MAX_FACT_IDS = 8
_MAX_FACT_SERVICES = 6
_MAX_FACT_CAUSES = 3
_MAX_FACT_OPEN = 3
_MAX_FACT_TEXT = 200

_OP_ID_RE = re.compile(
  r"\b[0-9a-f]{32}\b|\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b", re.IGNORECASE
)
_SERVICE_RE = re.compile(r"cloud_RoleName\s*(?:==|=~|has|contains)\s*\\?['\"]([^'\"\\]+)")
_LOOKBACK_RE = re.compile(r"\"lookback_hours\"\s*:\s*(\d+)")
_AGO_RE = re.compile(r"\bago\(\s*(\d+\s*[smhd])\s*\)", re.IGNORECASE)
_CONFIDENCE_RE = re.compile(r"confidence\W+(high|medium|low)\b", re.IGNORECASE)


def valid_session_id(session_id: str) -> bool:
  return bool(_SESSION_ID_RE.match(session_id or ""))


def _section(name: str, text: str) -> tuple[str, str] | None:
  """(heading line, body) of the '## <name>' markdown section, or None."""
  m = re.search(
    rf"^(##\s*{name}[^\n]*)\n+(.+?)(?=\n##|\Z)", text, re.IGNORECASE | re.DOTALL | re.MULTILINE
  )
  return (m.group(1).strip(), m.group(2).strip()) if m else None


def _oneline(text: str, limit: int) -> str:
  return " ".join(text.split())[:limit]


def _push(target: list[str], items: list[str], cap: int) -> None:
  """Append unique items, newest last; re-seen items move to the end; oldest dropped past cap."""
  for item in items:
    if item in target:
      target.remove(item)
    target.append(item)
  del target[:-cap]


def _compact_answer(answer: str) -> str:
  """Keep only what future turns need (Root cause + Recommended actions); fall back to full."""
  parts = [
    f"{h}\n{b}"
    for s in ("Root cause", "Recommended actions")
    if (r := _section(s, answer))
    for h, b in [r]
  ]
  return "\n".join(parts) if parts else answer


@dataclass
class Facts:
  """Small structured state of the chat, merged after each answer. Always sent to the LLM."""

  window: str = ""
  services: list[str] = field(default_factory=list)
  op_ids: list[str] = field(default_factory=list)
  causes: list[str] = field(default_factory=list)
  open_items: list[str] = field(default_factory=list)

  def update(self, queries: list[str], conclusion: str) -> None:
    blob = "\n".join(queries)
    if m := (_LOOKBACK_RE.findall(blob) or None):
      self.window = f"last {m[-1]}h"
    elif m := (_AGO_RE.findall(blob) or None):
      self.window = f"last {m[-1].replace(' ', '')}"
    _push(self.services, _SERVICE_RE.findall(blob), _MAX_FACT_SERVICES)
    _push(self.op_ids, _OP_ID_RE.findall(blob + "\n" + conclusion), _MAX_FACT_IDS)
    if cause := _section("Root cause", conclusion):
      heading, body = cause
      conf = _CONFIDENCE_RE.search(heading + " " + body)
      text = _oneline(body, _MAX_FACT_TEXT)
      if text:
        _push(
          self.causes, [f"{text} ({conf.group(1).lower()})" if conf else text], _MAX_FACT_CAUSES
        )
    if actions := _section("Recommended actions", conclusion):
      lines = [ln.strip("-*0123456789.) \t") for ln in actions[1].splitlines() if ln.strip()]
      self.open_items = [_oneline(ln, _MAX_FACT_TEXT) for ln in lines[:_MAX_FACT_OPEN] if ln]

  def render(self) -> str:
    rows = []
    if self.window:
      rows.append(f"time window: {self.window}")
    if self.services:
      rows.append("services: " + ", ".join(self.services))
    if self.op_ids:
      rows.append("operation_Ids: " + ", ".join(self.op_ids))
    for c in self.causes:
      rows.append(f"root cause: {c}")
    for o in self.open_items:
      rows.append(f"next/open: {o}")
    return "\n".join(f"  - {r}" for r in rows)


@dataclass
class Episode:
  question: str
  queries: list[str]  # tool name + (redacted) arguments that were executed
  conclusion: str
  at: float = field(default_factory=time.time)

  def digest(self) -> str:
    """Few-line summary: question, root cause (or start of answer) and operation_Ids found."""
    m = _section("Root cause", self.conclusion)
    cause = _oneline(m[1] if m else self.conclusion, _DIGEST_CONCLUSION)
    ids = list(dict.fromkeys(_OP_ID_RE.findall(self.conclusion)))[:_MAX_DIGEST_IDS]
    out = f"Question: {self.question[:_DIGEST_QUESTION]}\n    Concluded: {cause}"
    if ids:
      out += "\n    operation_Ids: " + ", ".join(ids)
    return out


class SessionMemory:
  """Pinned facts + episodes of one chat. Newest last; oldest episodes evicted past `max_episodes`."""

  def __init__(self, max_episodes: int = 10) -> None:
    self._max = max_episodes
    self._episodes: list[Episode] = []
    self._facts = Facts()
    self._lock = threading.Lock()
    self.last_used = time.monotonic()

  def add(self, question: str, queries: list[str], conclusion: str) -> None:
    unique = list(dict.fromkeys(queries))[:_MAX_QUERIES_PER_EPISODE]  # collapse duplicate calls
    clean_queries = [redact_text(q)[:_MAX_QUERY] for q in unique]
    clean_answer = redact_text(_compact_answer(conclusion))[:_MAX_ANSWER]
    ep = Episode(
      question=redact_text(question)[:_MAX_QUESTION],
      queries=clean_queries,
      conclusion=clean_answer,
    )
    with self._lock:
      self._episodes.append(ep)
      del self._episodes[: -self._max]
      self._facts.update(clean_queries, clean_answer)
      self.last_used = time.monotonic()

  def __len__(self) -> int:
    return len(self._episodes)

  def render(self) -> str:
    """Text block for the LLM, or '' when there is nothing to recall."""
    with self._lock:
      episodes = list(self._episodes)
      facts = self._facts.render()
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
    if facts:
      lines.append("\nKNOWN FACTS (this chat):\n" + facts)
    older = len(episodes) - _FULL_RECENT
    for i, ep in enumerate(episodes, 1):
      if i <= older:
        lines.append(f"\n[{i}] {ep.digest()}")
        continue
      lines.append(f"\n[{i}] Question: {ep.question}")
      for q in ep.queries:
        lines.append(f"    ran: {q}")
      lines.append(f"    Concluded: {ep.conclusion}")
    return "\n".join(lines)


class MemoryStore:
  """Thread-safe map of session id -> SessionMemory with idle expiry and a global cap."""

  def __init__(self, ttl_s: int = 1800, max_sessions: int = 50, max_episodes: int = 10) -> None:
    self._log = get_logger(__name__)
    self._ttl = ttl_s
    self._max_sessions = max_sessions
    self._max_episodes = max_episodes
    self._sessions: dict[str, SessionMemory] = {}
    self._lock = threading.Lock()

  def _evict(self) -> None:
    now = time.monotonic()
    for sid in [s for s, m in self._sessions.items() if now - m.last_used >= self._ttl]:
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

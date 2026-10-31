"""End-to-end check of per-chat memory through the HTTP API with a fake LLM (no network)."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from pereviewsre.agent.sre_agent import SreAgent
from pereviewsre.web import server

SID_A = "chat-aaaa-1111"
SID_B = "chat-bbbb-2222"


class FakeLLM:
  """Records the messages of each call and replies with a canned final answer."""

  def __init__(self) -> None:
    self.calls: list[list[dict]] = []
    self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

  def _create(self, model, messages, **_):
    self.calls.append(messages)
    msg = SimpleNamespace(content=f"answer #{len(self.calls)}", tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)


def _client():
  llm = FakeLLM()
  agent = SreAgent.__new__(SreAgent)
  agent._llm = llm
  agent._tools = SimpleNamespace(specs=list)
  agent._s = SimpleNamespace(max_agent_steps=3, aoai_deployment="x")
  agent._log = server.service._log
  server.service._agent = agent
  server.service._memory._sessions.clear()
  return TestClient(server.app), llm


def _memory_text(call: list[dict]) -> str:
  return "\n".join(
    m["content"] for m in call if m["role"] == "system" and "SESSION MEMORY" in m["content"]
  )


def test_same_chat_recalls_earlier_question():
  client, llm = _client()
  assert (
    client.post("/api/ask", json={"question": "why 500s?", "session_id": SID_A}).status_code == 200
  )
  client.post("/api/ask", json={"question": "which operation?", "session_id": SID_A})
  assert "why 500s?" in _memory_text(llm.calls[1])


def test_other_chat_does_not_see_memory():
  client, llm = _client()
  client.post("/api/ask", json={"question": "secret topic alpha", "session_id": SID_A})
  client.post("/api/ask", json={"question": "something else", "session_id": SID_B})
  assert _memory_text(llm.calls[1]) == ""


def test_reopened_chat_keeps_memory_after_other_chat_used():
  client, llm = _client()
  client.post("/api/ask", json={"question": "first in A", "session_id": SID_A})
  client.post("/api/ask", json={"question": "first in B", "session_id": SID_B})
  client.post("/api/ask", json={"question": "back to A", "session_id": SID_A})
  mem = _memory_text(llm.calls[2])
  assert "first in A" in mem and "first in B" not in mem


def test_delete_session_clears_only_that_chat():
  client, llm = _client()
  client.post("/api/ask", json={"question": "keep me", "session_id": SID_A})
  client.post("/api/ask", json={"question": "drop me", "session_id": SID_B})
  assert client.delete(f"/api/session/{SID_B}").status_code == 204
  client.post("/api/ask", json={"question": "again B", "session_id": SID_B})
  client.post("/api/ask", json={"question": "again A", "session_id": SID_A})
  assert _memory_text(llm.calls[2]) == ""
  assert "keep me" in _memory_text(llm.calls[3])


def test_invalid_session_id_rejected_and_stateless_has_no_memory():
  client, llm = _client()
  assert (
    client.post("/api/ask", json={"question": "hello?", "session_id": "../x"}).status_code == 422
  )
  client.post("/api/ask", json={"question": "no session one"})
  client.post("/api/ask", json={"question": "no session two"})
  assert _memory_text(llm.calls[1]) == ""

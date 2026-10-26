import time

from pereviewsre.memory.episodic import MemoryStore, SessionMemory, valid_session_id


def test_empty_memory_renders_nothing():
  assert SessionMemory().render() == ""


def test_memory_recalls_episodes_in_order():
  m = SessionMemory()
  m.add("why 500s?", ["run_kql {...}"], "Caused by SQL timeouts")
  m.add("which op?", ["get_trace {...}"], "operation abc")
  text = m.render()
  assert text.index("why 500s?") < text.index("which op?")
  assert "SQL timeouts" in text and "ran: run_kql" in text


def test_memory_redacts_and_caps():
  m = SessionMemory(max_episodes=2)
  m.add("q1", [], "a1")
  m.add("q2", [], "a2")
  m.add("q3", [], "x" * 10000)
  text = m.render()
  assert "q1" not in text and "q3" in text  # oldest evicted
  assert len(text) < 3000  # answer truncated


def test_store_isolates_sessions_and_clears():
  s = MemoryStore()
  s.get("aaaaaaaa-1").add("one", [], "ans1")
  assert s.get("bbbbbbbb-2").render() == ""
  s.clear("aaaaaaaa-1")
  assert s.get("aaaaaaaa-1").render() == ""


def test_store_expires_idle_sessions():
  s = MemoryStore(ttl_s=0)
  s.get("aaaaaaaa-1").add("one", [], "ans")
  time.sleep(0.01)
  s.get("bbbbbbbb-2")  # triggers eviction
  assert s.get("aaaaaaaa-1").render() == ""


def test_store_enforces_session_cap():
  s = MemoryStore(max_sessions=2)
  for i in range(5):
    s.get(f"session-{i:04d}")
  assert len(s._sessions) <= 2


def test_session_id_validation():
  assert valid_session_id("3f2b8c1e-aaaa-bbbb-cccc-123456789abc")
  assert not valid_session_id("../etc/passwd")
  assert not valid_session_id("short")
  assert not valid_session_id("x" * 100)

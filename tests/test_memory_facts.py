from pereviewsre.config import Settings
from pereviewsre.memory.episodic import SessionMemory

OP1 = "0123456789abcdef0123456789abcdef"
OP2 = "a1b2c3d4-e5f6-4789-abcd-ef0123456789"

ANSWER = f"""## Summary
Lots of boring summary detail.
## Evidence (operation_Ids, spans, timings)
span table {OP1} 3000ms
## Root cause (confidence: high)
SQL timeout on checkout dependency, op {OP1}
## Recommended actions
- Scale the database
- Check connection pool
"""

KQL = (
  r'run_kql {"query": "requests | where cloud_RoleName == \"checkout\" and timestamp > ago(1h)"}'
)


def test_facts_are_extracted_and_rendered():
  m = SessionMemory()
  m.add("why 500s?", [KQL, f'get_trace {{"operation_id": "{OP2}"}}'], ANSWER)
  text = m.render()
  assert "KNOWN FACTS" in text
  assert "time window: last 1h" in text
  assert "services: checkout" in text
  assert OP1 in text and OP2 in text
  assert "root cause: SQL timeout" in text and "(high)" in text
  assert "next/open: Scale the database" in text


def test_lookback_hours_sets_window_and_latest_wins():
  m = SessionMemory()
  m.add("q1", ['run_kql {"query": "x", "lookback_hours": 6}'], "a")
  m.add("q2", ['run_kql {"query": "x", "lookback_hours": 24}'], "b")
  assert "time window: last 24h" in m.render()


def test_facts_are_bounded_regardless_of_chat_length():
  m = SessionMemory(max_episodes=3)
  for i in range(40):
    op = f"{i:032x}"
    m.add(f"q{i}", [f'get_trace {{"operation_id": "{op}"}}'], f"## Root cause\ncause number {i}")
  facts = m._facts
  assert len(facts.op_ids) <= 8 and len(facts.causes) <= 3
  assert f"{39:032x}" in facts.op_ids and f"{0:032x}" not in facts.op_ids  # newest kept
  assert len(m.render()) < 4000


def test_noise_dropped_from_stored_answer_and_duplicate_calls():
  m = SessionMemory()
  m.add("q", [KQL, KQL, KQL], ANSWER)
  ep = m._episodes[0]
  assert len(ep.queries) == 1  # duplicates collapsed
  assert "boring summary" not in ep.conclusion  # Summary dropped
  assert "span table" not in ep.conclusion  # Evidence dropped
  assert "SQL timeout" in ep.conclusion and "Scale the database" in ep.conclusion


def test_unstructured_answer_is_kept_whole():
  m = SessionMemory()
  m.add("q", [], "plain answer without sections")
  assert m._episodes[0].conclusion == "plain answer without sections"


def test_older_episodes_are_compacted_and_keep_operation_ids():
  m = SessionMemory(max_episodes=10)
  m.add("old question", ["run_kql {long query text}"], ANSWER)
  m.add("mid", ["get_trace {...}"], "mid answer")
  m.add("newest", ["run_kql {x}"], "newest answer")
  text = m.render()
  assert "ran: run_kql {long query text}" not in text  # old tool calls dropped
  assert "SQL timeout" in text and OP1 in text  # digest + facts keep cause and id
  assert "ran: get_trace" in text and "ran: run_kql {x}" in text  # latest 2 are full


def test_secrets_are_redacted_in_memory():
  m = SessionMemory()
  m.add("login failed with password=hunter2secret", [], "ok")
  assert "hunter2secret" not in m.render()


def test_default_session_cap_is_50():
  assert Settings.model_fields["memory_max_sessions"].default == 50

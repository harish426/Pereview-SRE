import pytest

from azsre.security.kql_guard import KqlRejected, sanitize_kql, validate_operation_id
from azsre.security.redaction import redact_text


def test_allows_simple_query_and_adds_take():
    out = sanitize_kql("requests | where success == false | summarize count() by name", 50)
    assert out.endswith("| take 50")


def test_allows_let_and_union():
    q = "let o='abcdef12';\nunion requests, dependencies | where operation_Id == o"
    assert "take 10" in sanitize_kql(q, 10)


@pytest.mark.parametrize(
    "q",
    [
        ".drop table requests",
        "requests | evaluate bag_unpack(customDimensions)",
        "externaldata(x:string)['http://evil']",
        "workspace('other').AppRequests",
        "SecurityEvent | take 1",
        "requests; .set x <| print 1",
        "union requests, SecurityEvent",
        "",
    ],
)
def test_rejects_unsafe(q):
    with pytest.raises(KqlRejected):
        sanitize_kql(q, 10)


def test_operation_id_validation():
    assert validate_operation_id("abc123def456")
    with pytest.raises(KqlRejected):
        validate_operation_id("x'; drop")


def test_redaction():
    t = "Authorization: Bearer abc.def.ghi password=hunter2 mail bob@corp.com"
    r = redact_text(t)
    assert "hunter2" not in r and "bob@corp.com" not in r and "abc.def.ghi" not in r

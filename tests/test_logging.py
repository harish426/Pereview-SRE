import logging

from azsre.logging_setup import RedactingFormatter, get_logger
from azsre.security.redaction import redact_text


def test_logger_namespace():
    assert get_logger("tools.registry").name == "azsre.tools.registry"
    assert get_logger("azsre.agent").name == "azsre.agent"


def test_formatter_redacts_message_and_traceback():
    fmt = RedactingFormatter("%(message)s")
    try:
        raise RuntimeError("failed password=hunter2 Authorization: Bearer abc.def.ghi")
    except RuntimeError:
        import sys

        rec = logging.LogRecord("azsre.x", logging.ERROR, "f", 1, "key=%s", ("api_key=SECRET1",), sys.exc_info())
    out = fmt.format(rec)
    assert "SECRET1" not in out and "hunter2" not in out and "abc.def.ghi" not in out


def test_extra_redaction_patterns():
    t = (
        "https://user:pw123@host/x?sig=ABCDEF&a=1 "
        "AccountKey=abc123== from 10.1.2.3 InstrumentationKey=12345678-1234-1234-1234-123456789012"
    )
    r = redact_text(t)
    for leaked in ("pw123", "ABCDEF", "abc123==", "10.1.2.3", "12345678-1234"):
        assert leaked not in r

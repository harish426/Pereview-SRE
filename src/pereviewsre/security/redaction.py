"""Redact secrets/PII from telemetry before it is sent to the LLM or logged."""

import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9\-._~+/]+=*"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)\b(authorization:\s*basic)\s+[A-Za-z0-9+/]+=*"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)(://[^/\s:@]+:)[^@\s/]+@"), r"\1[REDACTED]@"),  # user:pass@host
    (re.compile(r"(?i)\b(AccountKey|SharedAccessKey|Password|Pwd|IngestionEndpoint)=[^;\s]+"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)([?&](?:sig|se|sv|code|token|key|access_token)=)[^&\s\"']+"), r"\1[REDACTED]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[REDACTED_IP]"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), "[REDACTED_JWT]"),
    (
        re.compile(
            r"(?i)\b(password|pwd|secret|api[_-]?key|access[_-]?token|sharedaccesskey|"
            r"accountkey|client[_-]?secret|sig)\b\s*[=:]\s*[^\s;,&\"']+"
        ),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"(?i)(InstrumentationKey)=[0-9a-f\-]{36}"), r"\1=[REDACTED]"),
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "[REDACTED_NUM]"),
]


def redact_text(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def redact_value(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    return value

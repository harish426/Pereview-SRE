"""Read-only KQL guard. All LLM-generated queries pass through here before execution.

Defense in depth: App Insights query API is read-only already, but we additionally block
management commands, data exfiltration plugins and cross-resource access.
"""

import re

MAX_QUERY_LEN = 4000

# Allowed table names (classic App Insights schema).
ALLOWED_TABLES = {
    "requests",
    "dependencies",
    "exceptions",
    "traces",
    "customEvents",
    "customMetrics",
    "pageViews",
    "availabilityResults",
    "performanceCounters",
    "browserTimings",
}

_FORBIDDEN_PATTERNS = [
    r"(^|\s)\.\w+",  # control/management commands (.set, .ingest, .drop ...)
    r"\bexternaldata\b",
    r"\bevaluate\b",
    r"\bhttp_request(_post)?\b",
    r"\bsql_request\b",
    r"\bcosmosdb_request\b",
    r"\bcluster\s*\(",
    r"\bdatabase\s*\(",
    r"\bworkspace\s*\(",
    r"\bapp\s*\(",
    r"\bresource\s*\(",
    r"\bingest\b",
    r"\bplugin\b",
    r"\bexecute_query\b",
    r"\bcurrent_principal",
    r"\bdatascope\b",
]

_FORBIDDEN_RE = re.compile("|".join(_FORBIDDEN_PATTERNS), re.IGNORECASE)
_COMMENT_RE = re.compile(r"//[^\n]*")
_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9\-_]{8,64}$")


class KqlRejected(ValueError):
    """Raised when a query violates the read-only policy."""


def validate_operation_id(value: str) -> str:
    if not _OPERATION_ID_RE.match(value or ""):
        raise KqlRejected("Invalid operation_id format")
    return value


def sanitize_kql(query: str, max_rows: int) -> str:
    """Validate and normalize a KQL query; returns a safe query with a row cap."""
    if not query or not query.strip():
        raise KqlRejected("Empty query")
    if len(query) > MAX_QUERY_LEN:
        raise KqlRejected(f"Query too long (>{MAX_QUERY_LEN} chars)")

    cleaned = _COMMENT_RE.sub("", query).strip().rstrip(";").strip()
    if ";" in re.sub(r"'[^']*'|\"[^\"]*\"", "", cleaned):
        # Only `let` statements may be chained with ';'
        statements = [s.strip() for s in cleaned.split(";") if s.strip()]
        if not all(s.lower().startswith("let ") for s in statements[:-1]):
            raise KqlRejected("Only 'let' statements may precede the main query")

    if _FORBIDDEN_RE.search(cleaned):
        raise KqlRejected("Query contains a forbidden construct")

    # The main (last) statement must start with an allowed table, or a `let`-defined name.
    main = cleaned.split(";")[-1].strip()
    m = _IDENT_RE.match(main)
    if not m:
        raise KqlRejected("Could not determine source table")
    first = m.group(1)
    let_names = set(re.findall(r"\blet\s+([A-Za-z_]\w*)\s*=", cleaned, re.IGNORECASE))
    if first not in ALLOWED_TABLES and first not in let_names and first.lower() != "union":
        raise KqlRejected(f"Table '{first}' is not allowed")

    if first.lower() == "union":
        # Every identifier right after union must be an allowed table / let var.
        head = re.split(r"\|", main, maxsplit=1)[0]
        for name in _IDENT_RE.findall(head)[1:]:
            if name.lower() in {"withsource", "kind", "inner", "outer", "isfuzzy", "true", "false"}:
                continue
            if name not in ALLOWED_TABLES and name not in let_names:
                raise KqlRejected(f"Table '{name}' is not allowed in union")

    return f"{cleaned}\n| take {int(max_rows)}"

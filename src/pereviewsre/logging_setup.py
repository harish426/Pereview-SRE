"""Central logging: rotating log file + console, with every line passed through redaction.

Usage in a class:
    self._log = get_logger(__name__)
    self._log.info("did something id=%s", x)
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pereviewsre.config import Settings
from pereviewsre.security.redaction import redact_text

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_configured = False


def running_in_azure() -> bool:
  """True inside Azure Functions / App Service (ephemeral disk; logs go to App Insights)."""
  return bool(os.environ.get("FUNCTIONS_WORKER_RUNTIME") and os.environ.get("WEBSITE_INSTANCE_ID"))


class RedactingFormatter(logging.Formatter):
  """Redacts the final rendered line, including exception tracebacks."""

  def format(self, record: logging.LogRecord) -> str:
    return redact_text(super().format(record))


def setup_logging(settings: Settings) -> Path:
  """Idempotent. Returns the log file path."""
  global _configured
  log_path = Path(settings.log_dir) / "pereviewsre.log"
  if _configured:
    return log_path

  in_azure = running_in_azure()
  if not in_azure:
    log_path.parent.mkdir(parents=True, exist_ok=True)
  formatter = RedactingFormatter(_FORMAT)

  console = logging.StreamHandler(sys.stderr)
  console.setFormatter(formatter)

  root = logging.getLogger("pereviewsre")
  root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
  if not in_azure:
    file_handler = RotatingFileHandler(
      log_path,
      maxBytes=settings.log_max_bytes,
      backupCount=settings.log_backup_count,
      encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
  root.addHandler(console)
  root.propagate = False

  # Third-party loggers can emit request headers/URLs at INFO/DEBUG: keep them quiet.
  for noisy in ("azure", "openai", "httpx", "httpcore", "urllib3", "msal"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

  _configured = True
  root.info("Logging initialised file=%s level=%s", log_path.resolve(), settings.log_level)
  return log_path


def get_logger(name: str) -> logging.Logger:
  """Return a logger under the 'pereviewsre' namespace so it inherits the handlers."""
  if not name.startswith("pereviewsre"):
    name = f"pereviewsre.{name}"
  return logging.getLogger(name)

"""Azure Functions entry point (Python v2 model).

Wraps the existing FastAPI app, so the chat UI (GET /) and API (POST /api/ask) work unchanged.
"""

import sys
from pathlib import Path

import azure.functions as func

# Make `src/azsre` importable when deployed as a plain Functions project.
sys.path.insert(0, str(Path(__file__).parent / "src"))

from azsre.web.server import app as fastapi_app  # noqa: E402

# ANONYMOUS at the Functions layer; access is enforced by AZSRE_WEB_TOKEN (required in Azure)
# or by App Service Authentication (Entra ID) in front of the app.
app = func.AsgiFunctionApp(app=fastapi_app, http_auth_level=func.AuthLevel.ANONYMOUS)

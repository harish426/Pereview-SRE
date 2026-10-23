"""Local web chat API. Binds to localhost by default; optional bearer token auth."""

import hmac
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from pereviewsre.agent.sre_agent import SreAgent
from pereviewsre.config import get_settings
from pereviewsre.logging_setup import get_logger, running_in_azure, setup_logging

STATIC = Path(__file__).parent / "static"


class ChatService:
    """Owns the agent (lazy) and handles auth + investigations, with logging."""

    def __init__(self) -> None:
        self._settings = get_settings()
        setup_logging(self._settings)  # idempotent
        self._log = get_logger(__name__)
        self._agent: SreAgent | None = None

    def _get_agent(self) -> SreAgent:
        if self._agent is None:
            self._agent = SreAgent(self._settings)
        return self._agent

    def check_auth(self, request: Request, authorization: str | None) -> None:
        token = self._settings.web_token.get_secret_value()
        if not token:
            if running_in_azure() or self._settings.is_production:
                # Production is reachable by others: never serve without authentication.
                self._log.error("PEREVIEWSRE_WEB_TOKEN not set; refusing request in Azure")
                raise HTTPException(status_code=503, detail="Service not configured")
            return
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied, token):
            client = request.client.host if request.client else "unknown"
            self._log.warning("Unauthorized API attempt client=%s", client)
            raise HTTPException(status_code=401, detail="Unauthorized")

    async def ask(self, question: str) -> dict:
        self._log.info("Web question received length=%d", len(question))
        steps: list[dict] = []
        try:
            answer = await run_in_threadpool(
                self._get_agent().investigate,
                question,
                lambda k, d: steps.append({"event": k, **d}),
            )
        except Exception:  # noqa: BLE001
            self._log.exception("Web investigation failed")
            # Never leak internal error details to the client.
            raise HTTPException(status_code=500, detail="Investigation failed. Check server logs.")
        return {"answer": answer, "steps": steps}


service = ChatService()
app = FastAPI(title="pereviewsre", docs_url=None, redoc_url=None, openapi_url=None)


def _auth(request: Request, authorization: str | None = Header(default=None)) -> None:
    service.check_auth(request, authorization)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.post("/api/ask", dependencies=[Depends(_auth)])
async def ask(req: AskRequest) -> dict:
    return await service.ask(req.question)

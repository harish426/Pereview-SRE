"""Command line interface: `azsre ask "..."`, `azsre chat`, `azsre web`."""

import argparse
import sys

from rich.console import Console
from rich.markdown import Markdown

from azsre.agent.sre_agent import SreAgent
from azsre.config import Settings, get_settings
from azsre.logging_setup import get_logger, setup_logging


class Cli:
    def __init__(self, settings: Settings) -> None:
        self._log = get_logger(__name__)
        self._settings = settings
        self._console = Console()

    def _on_event(self, kind: str, data: dict) -> None:
        if kind == "tool_call":  # args are already redacted by the agent
            self._console.print(f"[dim]→ {data['name']} {data['args'][:300]}[/dim]")

    def _ask(self, agent: SreAgent, question: str) -> None:
        try:
            with self._console.status("Investigating..."):
                answer = agent.investigate(question, self._on_event)
        except Exception:  # noqa: BLE001
            self._log.exception("Investigation failed")
            self._console.print("[red]Investigation failed. See the log file for details.[/red]")
            return
        self._console.print(Markdown(answer))

    def run(self, args: argparse.Namespace) -> None:
        self._log.info("CLI command=%s", args.cmd)
        if args.cmd == "web":
            import uvicorn

            self._log.info(
                "Starting web server host=%s port=%d", self._settings.web_host, self._settings.web_port
            )
            uvicorn.run(
                "azsre.web.server:app",
                host=self._settings.web_host,
                port=self._settings.web_port,
                log_config=None,  # our logging config handles everything
            )
            return

        agent = SreAgent(self._settings)
        if args.cmd == "ask":
            self._ask(agent, args.question)
            return
        self._console.print("[bold]azsre chat[/bold] (type 'exit' to quit)")
        while True:
            try:
                q = self._console.input("[cyan]you> [/cyan]").strip()
            except (EOFError, KeyboardInterrupt):
                sys.exit(0)
            if q.lower() in {"exit", "quit"}:
                return
            if q:
                self._ask(agent, q)


def main() -> None:
    parser = argparse.ArgumentParser(prog="azsre", description="Azure App Insights AI SRE")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_ask = sub.add_parser("ask", help="Investigate a single question")
    p_ask.add_argument("question")
    sub.add_parser("chat", help="Interactive investigation session")
    sub.add_parser("web", help="Start local web chat UI")
    args = parser.parse_args()

    settings = get_settings()
    log_path = setup_logging(settings)
    Console().print(f"[dim]Logs: {log_path.resolve()}[/dim]")
    Cli(settings).run(args)


if __name__ == "__main__":
    main()

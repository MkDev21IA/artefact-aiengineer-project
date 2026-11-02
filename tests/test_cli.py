"""Smoke and observability tests for the Rich CLI runner."""

from io import StringIO
from pathlib import Path

from rich.console import Console

from emporio_agent.agent.orchestrator import EmporioAgentOrchestrator
from emporio_agent.cli import run_cli
from emporio_agent.config import Settings
from emporio_agent.rag.policy_store import POLICY_PDF_FILENAME, PolicyVectorStore

DOCS_DIR = Path(__file__).resolve().parents[1] / "documents"


def test_cli_single_message_debug_mode_renders_guardrail_and_masks_pii() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    settings = Settings(openai_api_key="", documents_dir=DOCS_DIR)
    orch = EmporioAgentOrchestrator(
        settings=settings,
        policy_store=PolicyVectorStore(DOCS_DIR / POLICY_PDF_FILENAME, ephemeral=True),
    )

    exit_code = run_cli(
        argv=[
            "--customer-id",
            "1",
            "--simulate-datetime",
            "2026-11-03T14:30:00",
            "--debug",
            "--message",
            "Meu CPF é 529.982.247-25, qual é a receita de um bolo de cenoura?",
        ],
        orchestrator=orch,
        console=console,
    )
    rendered = buf.getvalue()
    assert exit_code == 0
    assert "BLOCK_OUT_OF_SCOPE" in rendered
    assert "<BR_CPF_1>" in rendered
    assert "529.982.247-25" not in rendered
    assert "Empório da Música" in rendered


def test_cli_invalid_simulated_datetime_returns_exit_code_2() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    exit_code = run_cli(
        argv=["--simulate-datetime", "not-a-valid-date", "--message", "Oi"],
        console=console,
    )
    assert exit_code == 2

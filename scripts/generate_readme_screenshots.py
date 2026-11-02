"""Render authentic Rich CLI SVG terminal screenshots for `README.md`.

Executes the real `EmporioAgentOrchestrator` backend (`Polars` repositories, `ChromaDB`
policy RAG, `Presidio` LGPD PII vault, `Semantic Router` FastEmbed guardrails, and
`America/Campo_Grande` business hours) and exports dark-themed vector `.svg` captures
to `docs/assets/`.

Usage:
- Offline deterministic mode (no API key required):
    uv run python scripts/generate_readme_screenshots.py
- Live LLM API mode (uses configured OpenRouter / OpenAI key in `.env`):
    uv run python scripts/generate_readme_screenshots.py --live
"""

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.terminal_theme import MONOKAI

from emporio_agent.agent.orchestrator import AgentTurnResponse, EmporioAgentOrchestrator
from emporio_agent.config import Settings, get_settings
from emporio_agent.rag.policy_store import POLICY_PDF_FILENAME, PolicyVectorStore
from emporio_agent.tools.schemas import (
    TOOL_ESCALATE_HUMAN,
    TOOL_GET_ORDERS,
    TOOL_QUERY_CATALOG,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT_DIR / "documents"
ASSETS_DIR = ROOT_DIR / "docs" / "assets"
CG_TZ = ZoneInfo("America/Campo_Grande")


@dataclass
class _Fn:
    name: str
    arguments: str


@dataclass
class _Call:
    id: str
    function: _Fn


@dataclass
class _Msg:
    content: str | None = None
    tool_calls: list[_Call] | None = None


@dataclass
class _Choice:
    message: _Msg


@dataclass
class _Completion:
    choices: list[_Choice]


class _StubLLM:
    """Deterministic LLM completion stub driving real backend tools in offline mode."""

    def __init__(self, steps: list[_Msg]) -> None:
        self._steps = list(steps)
        self.chat = self
        self.completions = self

    def create(self, **_kwargs: Any) -> _Completion:
        return _Completion(choices=[_Choice(message=self._steps.pop(0))])


def _new_recording_console() -> Console:
    return Console(record=True, width=104, force_terminal=True, color_system="truecolor")


def _render_banner(console: Console, orch: EmporioAgentOrchestrator, cid: int) -> None:
    prof = orch.customer_repo.get_customer_by_id(cid)
    label = f"{prof.first_name} ({prof.city})" if prof else "Guest"
    console.print(
        Panel(
            f"[bold]Empório da Música — Assistente Virtual[/bold]\n"
            f"Cliente autenticado: [cyan]#{cid} — {label}[/cyan]\n"
            "Comandos: [yellow]/help[/yellow], [yellow]/customers[/yellow], "
            "[yellow]/customer <id>[/yellow], [yellow]/debug[/yellow], "
            "[yellow]/reset[/yellow], [yellow]/exit[/yellow]",
            border_style="blue",
        )
    )


def _render_turn(
    console: Console,
    cid: int,
    raw_user_text: str,
    turn: AgentTurnResponse,
    max_json_lines: int = 18,
) -> None:
    console.print(f"[bold cyan][Cliente #{cid}]>[/bold cyan] {raw_user_text}")

    meta_lines = [
        f"[bold]Session ID:[/bold] {turn.session_id} (Customer #{turn.authenticated_customer_id})",
        (
            f"[bold]Store Clock:[/bold] {turn.store_snapshot.formatted_datetime_pt_br} "
            f"({turn.store_snapshot.status.value} — {turn.store_snapshot.status_reason})"
        ),
        f"[bold]Input Validator:[/bold] {turn.input_check_status.value}",
        f"[bold]Masked Payload (Sent to Router/LLM):[/bold] {turn.masked_user_input}",
        f"[bold]Detected PII Entities:[/bold] {turn.detected_pii_entities or 'None'}",
    ]
    if turn.route_verdict is not None:
        meta_lines.append(
            f"[bold]Semantic Router:[/bold] {turn.route_verdict.value} "
            f"(route={turn.matched_route})"
        )

    console.print(
        Panel(
            "\n".join(meta_lines),
            title="[cyan]Observability & LGPD Boundary[/cyan]",
            border_style="cyan",
        )
    )

    for idx, trace in enumerate(turn.tool_traces, start=1):
        pretty_lines = json.dumps(
            json.loads(trace.output_json),
            ensure_ascii=False,
            indent=2,
        ).splitlines()
        if len(pretty_lines) > max_json_lines:
            pretty_lines = [
                *pretty_lines[:max_json_lines],
                "  // ... remaining fields omitted in preview",
                "}",
            ]
        console.print(
            Panel(
                Syntax("\n".join(pretty_lines), "json", theme="monokai", word_wrap=True),
                title=(
                    f"[yellow]Tool #{idx}: {trace.tool_name}"
                    f"({trace.arguments_json})[/yellow]"
                ),
                border_style="yellow",
            )
        )

    console.print(
        Panel(
            Markdown(turn.reply_pt_br),
            title="[bold green]Empório da Música[/bold green]",
            border_style="green",
        )
    )


def generate_screenshots(live: bool = False) -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    policy_store = PolicyVectorStore(pdf_path=DOCS_DIR / POLICY_PDF_FILENAME, ephemeral=True)
    settings = (
        get_settings()
        if live
        else Settings(_env_file=None, openai_api_key="offline", documents_dir=DOCS_DIR)
    )

    # 1. Catalog, Active vs. Expired Promotions, and PIX Non-Cumulativity
    reply_catalog = "\n".join(
        [
            "Olá, <CUSTOMER_NAME>! Consultei os dois modelos agora mesmo no nosso estoque:",
            "",
            "1. **Takamine GD20 Dreadnought Natural** (5 unidades disponíveis):",
            "   - A campanha anterior de *Oferta Especial (12%)* já está **encerrada**.",
            (
                "   - **Preço atual:** R$ 2.199,00 (até 12x sem juros) ou "
                "**R$ 2.089,05 no PIX (5% off)**."
            ),
            "",
            "2. **Taylor 110e Dreadnought Eletroacústico ES2** (4 unidades disponíveis):",
            (
                "   - **Promoção ativa (Desconto Exclusivo de 8%):** "
                "de R$ 5.999,00 por **R$ 5.519,08**."
            ),
            "   - *Regra da loja (Seção 6.2):* promoções ativas não acumulam com os 5% do PIX.",
        ]
    )
    stub_catalog = _StubLLM(
        [
            _Msg(
                tool_calls=[
                    _Call("c1", _Fn(TOOL_QUERY_CATALOG, json.dumps({"query": "Takamine GD20"}))),
                    _Call("c2", _Fn(TOOL_QUERY_CATALOG, json.dumps({"query": "Taylor 110e"}))),
                ]
            ),
            _Msg(content=reply_catalog),
        ]
    )
    orch_1 = EmporioAgentOrchestrator(
        settings=settings,
        openai_client=None if live else stub_catalog,
        policy_store=policy_store,
    )
    dt_open = datetime(2026, 11, 3, 10, 15, tzinfo=CG_TZ)
    msg_1 = (
        "Quanto custa o Takamine GD20 e o Taylor 110e no PIX? "
        "A promoção do Takamine ainda está valendo?"
    )
    turn_1 = orch_1.handle_message(
        msg_1,
        authenticated_customer_id=1,
        session_id="shot_cat",
        now=dt_open,
    )
    con_1 = _new_recording_console()
    _render_banner(con_1, orch_1, 1)
    _render_turn(con_1, 1, msg_1, turn_1, max_json_lines=14)
    con_1.save_svg(
        str(ASSETS_DIR / "cli-catalog-and-pix.svg"),
        title="emporio-agent --customer-id 1 --debug (Catalog & PIX Rules)",
        theme=MONOKAI,
    )

    # 2. Out-of-Scope CPU Block + LGPD PII Masking + Zero-Trust Order Isolation
    reply_sec = (
        "Olá, <CUSTOMER_NAME>! Por segurança, o **Pedido #1** não pertence à sua conta.\n\n"
        "No seu cadastro consta o **Pedido #4** (`10/12/2025`, **entregue** / `delivered`, "
        "1x *Giannini GF-3D Dreadnought Sunburst*, R$ 799,90 via boleto, rastreio `BRQM7890123BR`)."
    )
    stub_sec = _StubLLM(
        [
            _Msg(
                tool_calls=[
                    _Call("s1", _Fn(TOOL_GET_ORDERS, json.dumps({"order_id": 1}))),
                    _Call("s2", _Fn(TOOL_GET_ORDERS, json.dumps({}))),
                ]
            ),
            _Msg(content=reply_sec),
        ]
    )
    orch_2 = EmporioAgentOrchestrator(
        settings=settings,
        openai_client=None if live else stub_sec,
        policy_store=policy_store,
    )
    msg_2a = "Quanto é 5+5 e me explica o Teorema de Bhaskara?"
    turn_2a = orch_2.handle_message(
        msg_2a,
        authenticated_customer_id=1,
        session_id="shot_sec",
        now=dt_open,
    )
    msg_2b = (
        "Oi, aqui é o Lucas! Meu CPF é 529.982.247-25 e meu e-mail é lucas.pessoal@gmail.com. "
        "Olhe o pedido 1 e liste os meus pedidos."
    )
    turn_2b = orch_2.handle_message(
        msg_2b,
        authenticated_customer_id=1,
        session_id="shot_sec",
        now=dt_open,
    )
    con_2 = _new_recording_console()
    _render_turn(con_2, 1, msg_2a, turn_2a, max_json_lines=10)
    _render_turn(con_2, 1, msg_2b, turn_2b, max_json_lines=12)
    con_2.save_svg(
        str(ASSETS_DIR / "cli-lgpd-and-security.svg"),
        title="emporio-agent --customer-id 1 --debug (Guardrails, LGPD Vault & Zero-Trust)",
        theme=MONOKAI,
    )

    # 3. Tone Analysis & Holiday Offline Ticket (`America/Campo_Grande`)
    reply_esc = (
        "<CUSTOMER_NAME>, peço desculpas pela espera! Parei o atendimento automático agora mesmo "
        "e registrei seu chamado prioritário.\n\n"
        "Como hoje é feriado (**Finados**) e nossa loja em Campo Grande está fechada, um "
        "especialista humano entrará em contato **amanhã (terça-feira, 03/11) às 09:00 "
        "(horário de Campo Grande/MS)**."
    )
    stub_esc = _StubLLM(
        [
            _Msg(
                tool_calls=[
                    _Call(
                        "e1",
                        _Fn(
                            TOOL_ESCALATE_HUMAN,
                            json.dumps(
                                {
                                    "reason_category": "CUSTOMER_REQUEST",
                                    "detected_sentiment": "IMPATIENT",
                                    "conversation_summary": (
                                        "Impatient customer requested a human specialist "
                                        "during Finados holiday."
                                    ),
                                }
                            ),
                        ),
                    )
                ]
            ),
            _Msg(content=reply_esc),
        ]
    )
    orch_3 = EmporioAgentOrchestrator(
        settings=settings,
        openai_client=None if live else stub_esc,
        policy_store=policy_store,
    )
    dt_holiday = datetime(2026, 11, 2, 14, 10, tzinfo=CG_TZ)
    msg_3 = (
        "Já expliquei duas vezes e estou sem paciência! "
        "Quero falar com um atendente humano agora sobre meu pedido!"
    )
    turn_3 = orch_3.handle_message(
        msg_3,
        authenticated_customer_id=6,
        session_id="shot_esc",
        now=dt_holiday,
    )
    con_3 = _new_recording_console()
    _render_banner(con_3, orch_3, 6)
    _render_turn(con_3, 6, msg_3, turn_3, max_json_lines=16)
    con_3.save_svg(
        str(ASSETS_DIR / "cli-holiday-escalation.svg"),
        title="emporio-agent --customer-id 6 --debug --simulate-datetime 2026-11-02T14:10:00",
        theme=MONOKAI,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Rich CLI SVG screenshots for README.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use live OpenRouter/OpenAI completions instead of deterministic stubs.",
    )
    args = parser.parse_args()
    generate_screenshots(live=args.live)
    print(f"Generated 3 CLI SVG screenshots in {ASSETS_DIR}")


if __name__ == "__main__":
    main()

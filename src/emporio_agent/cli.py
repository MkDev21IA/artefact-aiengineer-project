"""Interactive Rich CLI and one-shot runner for the Emporio da Musica AI Agent."""

import argparse
import sys
from datetime import datetime

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from emporio_agent.agent.orchestrator import AgentTurnResponse, EmporioAgentOrchestrator
from emporio_agent.config import get_settings


def build_arg_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="emporio-agent",
        description="Conversational AI support agent for Emporio da Musica (Campo Grande/MS).",
    )
    parser.add_argument(
        "--customer-id",
        type=int,
        default=1,
        help="Authenticated customer_id from customers.csv (default: 1).",
    )
    parser.add_argument(
        "--simulate-datetime",
        type=str,
        default=None,
        help="Optional ISO-8601 datetime override (e.g. '2026-11-03T14:30:00') to test hours.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Display LGPD PII masking, Semantic Router verdict, and raw tool JSON payloads.",
    )
    parser.add_argument(
        "--message",
        type=str,
        default=None,
        help="Send a single message non-interactively and exit.",
    )
    return parser


def _parse_simulated_datetime(raw_iso: str | None) -> datetime | None:
    if not raw_iso:
        return None
    return datetime.fromisoformat(raw_iso)


def _render_debug_panel(console: Console, turn: AgentTurnResponse) -> None:
    """Print LGPD masking telemetry, intent routing, and JSON tool traces."""
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
        console.print(
            Panel(
                Syntax(trace.output_json, "json", word_wrap=True),
                title=(
                    f"[yellow]Tool #{idx}: {trace.tool_name}"
                    f"({trace.arguments_json})[/yellow]"
                ),
                border_style="yellow",
            )
        )


def _render_customers_table(console: Console, orchestrator: EmporioAgentOrchestrator) -> None:
    table = Table(title="Sample Authenticated Customers (customers.csv)")
    table.add_column("customer_id", style="cyan", justify="right")
    table.add_column("First Name", style="green")
    table.add_column("City", style="magenta")
    for prof in orchestrator.customer_repo.list_sample_customers(limit=12):
        table.add_row(str(prof.customer_id), prof.first_name, prof.city)
    console.print(table)


def run_cli(
    argv: list[str] | None = None,
    orchestrator: EmporioAgentOrchestrator | None = None,
    console: Console | None = None,
) -> int:
    """Execute interactive or single-message CLI loop."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    out = console or Console()

    try:
        sim_dt = _parse_simulated_datetime(args.simulate_datetime)
    except ValueError as exc:
        out.print(f"[bold red]Invalid --simulate-datetime:[/bold red] {exc}")
        return 2

    orch = orchestrator or EmporioAgentOrchestrator(settings=get_settings())
    active_cid: int = args.customer_id
    debug_enabled: bool = args.debug
    active_session_id: str | None = None

    if args.message is not None:
        try:
            turn = orch.handle_message(
                user_message=args.message,
                authenticated_customer_id=active_cid,
                now=sim_dt,
            )
        except RuntimeError as exc:
            out.print(f"[bold red]Configuration Error:[/bold red] {exc}")
            return 1

        if debug_enabled:
            _render_debug_panel(out, turn)
        out.print(
            Panel(
                Markdown(turn.reply_pt_br),
                title="[bold green]Empório da Música[/bold green]",
                border_style="green",
            )
        )
        return 0

    profile = orch.customer_repo.get_customer_by_id(active_cid)
    cust_label = f"{profile.first_name} ({profile.city})" if profile else "Guest"
    out.print(
        Panel(
            f"[bold]Empório da Música — Assistente Virtual[/bold]\n"
            f"Cliente autenticado: [cyan]#{active_cid} — {cust_label}[/cyan]\n"
            "Comandos: [yellow]/help[/yellow], [yellow]/customers[/yellow], "
            "[yellow]/customer <id>[/yellow], [yellow]/debug[/yellow], "
            "[yellow]/reset[/yellow], [yellow]/exit[/yellow]",
            border_style="blue",
        )
    )

    while True:
        try:
            raw_line = out.input(f"[bold cyan][Cliente #{active_cid}]>[/bold cyan] ")
        except (EOFError, KeyboardInterrupt):
            out.print("\nEncerrando sessão efêmera. Até logo!")
            break

        cmd = raw_line.strip()
        if cmd.lower() in {"/exit", "/quit"}:
            if active_session_id:
                orch.session_store.reset_session(active_session_id)
            out.print("Sessão encerrada e dados efêmeros limpos. Até mais!")
            break

        if cmd.lower() == "/help":
            out.print(
                "Comandos disponíveis:\n"
                "  /customers      — Lista clientes de exemplo para teste\n"
                "  /customer <id>  — Troca o cliente autenticado (nova sessão isolada)\n"
                "  /debug          — Liga/desliga inspeção de LGPD, Router e JSON das tools\n"
                "  /reset          — Limpa histórico e vault de PII da sessão atual\n"
                "  /exit           — Encerra o chat"
            )
            continue

        if cmd.lower() == "/customers":
            _render_customers_table(out, orch)
            continue

        if cmd.lower() == "/debug":
            debug_enabled = not debug_enabled
            state_str = "ATIVADO" if debug_enabled else "DESATIVADO"
            out.print(f"[cyan]Modo debug {state_str}.[/cyan]")
            continue

        if cmd.lower() == "/reset":
            if active_session_id:
                orch.session_store.reset_session(active_session_id)
                active_session_id = None
            out.print("[cyan]Sessão e vault de PII reiniciados com sucesso.[/cyan]")
            continue

        if cmd.lower().startswith("/customer "):
            parts = cmd.split()
            if len(parts) == 2 and parts[1].isdigit():
                if active_session_id:
                    orch.session_store.reset_session(active_session_id)
                    active_session_id = None
                active_cid = int(parts[1])
                prof = orch.customer_repo.get_customer_by_id(active_cid)
                lbl = f"{prof.first_name} ({prof.city})" if prof else "Desconhecido"
                out.print(
                    f"[green]Sessão isolada iniciada para Cliente #{active_cid} — {lbl}[/green]"
                )
            else:
                out.print("[red]Uso: /customer <id_numerico>[/red]")
            continue

        try:
            turn = orch.handle_message(
                user_message=raw_line,
                authenticated_customer_id=active_cid,
                session_id=active_session_id,
                now=sim_dt,
            )
            active_session_id = turn.session_id
        except RuntimeError as exc:
            out.print(f"[bold red]Erro de configuração:[/bold red] {exc}")
            continue

        if debug_enabled:
            _render_debug_panel(out, turn)

        out.print(
            Panel(
                Markdown(turn.reply_pt_br),
                title="[bold green]Empório da Música[/bold green]",
                border_style="green",
            )
        )

    return 0


def main() -> None:
    """Entrypoint registered in `pyproject.toml` (`emporio-agent`)."""
    sys.exit(run_cli())


if __name__ == "__main__":
    main()

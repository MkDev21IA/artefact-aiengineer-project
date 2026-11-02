"""Generate end-to-end Markdown conversation transcripts in `examples/`.

Executes the real `EmporioAgentOrchestrator` pipeline (input validation, LGPD Presidio
PII vault, Semantic Router FastEmbed guardrails, Polars repositories, ChromaDB policy
RAG, and `America/Campo_Grande` business-hours escalation).

Usage:
- Offline deterministic mode (uses scripted LLM tool calls + real backend execution):
    uv run python scripts/generate_examples.py
- Live LLM API mode (uses configured `OPENAI_API_KEY` in `.env`):
    uv run python scripts/generate_examples.py --live
"""

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from emporio_agent.agent.orchestrator import AgentTurnResponse, EmporioAgentOrchestrator
from emporio_agent.config import Settings, get_settings
from emporio_agent.rag.policy_store import POLICY_PDF_FILENAME, PolicyVectorStore
from emporio_agent.tools.schemas import (
    TOOL_ESCALATE_HUMAN,
    TOOL_GET_ORDERS,
    TOOL_QUERY_CATALOG,
    TOOL_SEARCH_POLICIES,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT_DIR / "documents"
EXAMPLES_DIR = ROOT_DIR / "examples"
CG_TZ = ZoneInfo("America/Campo_Grande")


@dataclass
class _ScriptedFn:
    name: str
    arguments: str


@dataclass
class _ScriptedCall:
    id: str
    function: _ScriptedFn


@dataclass
class _ScriptedMsg:
    content: str | None = None
    tool_calls: list[_ScriptedCall] | None = None


@dataclass
class _ScriptedChoice:
    message: _ScriptedMsg


@dataclass
class _ScriptedCompletion:
    choices: list[_ScriptedChoice]


class _ScriptedOpenAIClient:
    """Deterministic LLM stub driving real backend tools when running without an API key."""

    def __init__(self, steps: list[_ScriptedMsg]) -> None:
        self._steps = list(steps)
        self.chat = self
        self.completions = self

    def create(self, **_kwargs: Any) -> _ScriptedCompletion:
        msg = self._steps.pop(0)
        return _ScriptedCompletion(choices=[_ScriptedChoice(message=msg)])


def _format_turn_markdown(idx: int, user_text: str, turn: AgentTurnResponse) -> str:
    clock_str = turn.store_snapshot.formatted_datetime_pt_br
    state_str = turn.store_snapshot.status.value
    lines = [
        f"### Turn {idx}",
        "",
        f"**Customer (`#{turn.authenticated_customer_id}`):**",
        f"> {user_text}",
        "",
        "**Observability & Guardrail Trace:**",
        (
            f"- **Store Clock (`America/Campo_Grande`):** `{clock_str}` "
            f"(`{state_str}` — {turn.store_snapshot.status_reason})"
        ),
        f"- **Input Validator:** `{turn.input_check_status.value}`",
        f"- **LGPD Masked Payload:** `{turn.masked_user_input}`",
        f"- **Detected PII Entities:** `{turn.detected_pii_entities}`",
    ]
    if turn.route_verdict is not None:
        lines.append(
            f"- **Semantic Router:** `{turn.route_verdict.value}` "
            f"(matched route: `{turn.matched_route}`)"
        )
    if turn.tool_traces:
        lines.append("- **Executed Function Calls (Real Backend JSON):**")
        for trace in turn.tool_traces:
            parsed = json.loads(trace.output_json)
            pretty_json = json.dumps(parsed, ensure_ascii=False, indent=2)
            if len(pretty_json) > 1600:
                pretty_json = pretty_json[:1600] + "\n  ... (truncated for readability)\n}"
            lines.extend(
                [
                    f"  - `{trace.tool_name}({trace.arguments_json})`:",
                    "```json",
                    pretty_json,
                    "```",
                ]
            )
    lines.extend(
        [
            "",
            "**Empório da Música Assistant (`pt-BR`):**",
            "",
            turn.reply_pt_br,
            "",
            "---",
            "",
        ]
    )
    return "\n".join(lines)


def _write_scenario(
    filename: str,
    title: str,
    summary: str,
    turns_md: list[str],
) -> None:
    EXAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    header = [f"# {title}", "", summary, "", "---", ""]
    (EXAMPLES_DIR / filename).write_text(
        "\n".join(header + turns_md).rstrip() + "\n",
        encoding="utf-8",
    )


def generate_all(live: bool = False) -> None:
    policy_store = PolicyVectorStore(pdf_path=DOCS_DIR / POLICY_PDF_FILENAME, ephemeral=True)
    base_settings = (
        get_settings() if live else Settings(openai_api_key="offline", documents_dir=DOCS_DIR)
    )

    # Scenario 1: Catalog <= R$1000, Takamine GD20, Taylor 110e Promo & PIX Rule
    reply_1_a = "\n".join(
        [
            "Olá, <CUSTOMER_NAME>! Temos ótimas opções de violões em estoque até R$ 1.000,00:",
            "",
            "**Cordas de Nylon (Clássicos):**",
            "- **Tagima Memphis AC-39 Nylon:** R$ 429,90 (R$ 408,40 no PIX) — 15 un.",
            "- **Rozini RC-104 Nylon Estudante:** R$ 459,90 (R$ 436,90 no PIX) — 10 un.",
            "- **Yamaha C40 Nylon Natural:** R$ 599,90 (R$ 569,90 no PIX) — 12 un.",
            "- **Giannini GN-15 Nylon Cedro:** R$ 689,00 (R$ 654,55 no PIX) — 6 un.",
            "- **Yamaha C70 Nylon (tampo sólido):** R$ 849,00 (R$ 806,55 no PIX) — 8 un.",
            "",
            "**Cordas de Aço (Folk / Dreadnought / Eletroacústico / 7 Cordas):**",
            "- **Shelby SN-100 Folk Aço Preto:** R$ 549,00 (R$ 521,55 no PIX) — 5 un.",
            "- **Giannini GF-1R Aço Folk Sunburst:** R$ 579,90 (R$ 550,90 no PIX) — 7 un.",
            "- **Yamaha F310 Aço Natural:** R$ 699,90 (R$ 664,90 no PIX) — 14 un.",
            "- **Tagima Dallas Tuner Aço:** R$ 749,00 (R$ 711,55 no PIX) — 9 un.",
            "- **Shelby SGD-195E Eletroacústico:** R$ 799,00 (R$ 759,05 no PIX) — 6 un.",
            "- **Tagima Woodstock Dreadnought:** R$ 899,00 (R$ 854,05 no PIX) — 11 un.",
            "- **Tagima TW-7 (7 Cordas Aço):** R$ 999,00 (R$ 949,05 no PIX) — 6 un.",
            "",
            "Você prefere cordas de nylon (mais macias para começar) ou de aço?",
        ]
    )
    reply_1_b = "\n".join(
        [
            "Excelentes escolhas, <CUSTOMER_NAME>! Consultei os dois modelos agora no sistema:",
            "",
            "1. **Takamine GD20 Dreadnought Natural** (5 unidades em estoque):",
            "   - A campanha anterior de *Oferta Especial (12%)* já está **encerrada**.",
            "   - **Preço atual de tabela:** R$ 2.199,00 (em até 12x sem juros no cartão).",
            "   - **No PIX (5% de desconto à vista):** **R$ 2.089,05**.",
            "",
            "2. **Taylor 110e Dreadnought Natural — Eletroacústico ES2** (4 un. em estoque):",
            "   - Está com promoção ativa de **Desconto Exclusivo de 8%**!",
            "   - **Preço original:** R$ 5.999,00 -> **Preço promocional:** **R$ 5.519,08**.",
            "   - *Importante:* conforme nossa política (Seção 6.2), promoções não são "
            "cumulativas, então o desconto de 5% do PIX não se aplica sobre o valor já "
            "promocional (no PIX ou cartão ele sai pelos mesmos **R$ 5.519,08**).",
            "",
            "Quer que eu simule o parcelamento de algum deles?",
        ]
    )
    script_1 = _ScriptedOpenAIClient(
        [
            _ScriptedMsg(
                tool_calls=[
                    _ScriptedCall(
                        id="c1_1",
                        function=_ScriptedFn(
                            TOOL_QUERY_CATALOG,
                            json.dumps(
                                {
                                    "query": "violões",
                                    "max_price_brl": 1000.0,
                                    "only_available": True,
                                    "limit": 12,
                                }
                            ),
                        ),
                    )
                ]
            ),
            _ScriptedMsg(content=reply_1_a),
            _ScriptedMsg(
                tool_calls=[
                    _ScriptedCall(
                        id="c1_2",
                        function=_ScriptedFn(
                            TOOL_QUERY_CATALOG,
                            json.dumps({"query": "Takamine GD20"}),
                        ),
                    ),
                    _ScriptedCall(
                        id="c1_3",
                        function=_ScriptedFn(
                            TOOL_QUERY_CATALOG,
                            json.dumps({"query": "Taylor 110e"}),
                        ),
                    ),
                ]
            ),
            _ScriptedMsg(content=reply_1_b),
        ]
    )
    orch_1 = EmporioAgentOrchestrator(
        settings=base_settings,
        openai_client=None if live else script_1,
        policy_store=policy_store,
    )
    dt_open = datetime(2026, 11, 3, 10, 15, tzinfo=CG_TZ)
    q1_a = "Oi! Quais opções de violões disponíveis vocês têm custando até R$ 1000?"
    t1_a = orch_1.handle_message(q1_a, authenticated_customer_id=1, session_id="ex1", now=dt_open)
    q1_b = (
        "Show! E subindo o orçamento: quanto custa o Takamine GD20 e o Taylor 110e pagando à "
        "vista no PIX? A promoção de Oferta Especial do Takamine ainda está valendo?"
    )
    t1_b = orch_1.handle_message(q1_b, authenticated_customer_id=1, session_id="ex1", now=dt_open)
    _write_scenario(
        "01-catalog-promotions-and-pix-rules.md",
        "Example 01 — Product Catalog, Active vs. Expired Promotions, and PIX Non-Cumulativity",
        (
            "Demonstrates real-time `Polars` catalog querying for acoustic guitars up to R$ 1,000 "
            "(excluding out-of-stock/discontinued rows), exact pricing for `Takamine GD20`, "
            "transparent handling of expired vs. active promotions (`Taylor 110e` 8% off), and "
            "enforcement of Policy Section 6.2 (5% PIX discount does not stack on active promos)."
        ),
        [_format_turn_markdown(1, q1_a, t1_a), _format_turn_markdown(2, q1_b, t1_b)],
    )

    # Scenario 2: Order Tracking + Cross-Referenced Online Regret Return Policy
    reply_2_a = "\n".join(
        [
            "Boa tarde, <CUSTOMER_NAME>! Tudo ótimo por aqui!",
            "",
            "Consultei seu pedido no sistema:",
            "- **Pedido #8** (`15/02/2026`): **1x Ukulele Kala KA-C Concert Mogno** "
            "(R$ 349,90 no débito).",
            "- **Status atual:** Enviado (`shipped`).",
            "- **Código de rastreamento:** `BRJL5544332BR` (acompanhe pelos Correios ou Jadlog).",
            "",
            "Sobre nossa loja física em **Campo Grande/MS**:",
            "- **Endereço:** Rua 14 de Maio, 3200 — Centro, Campo Grande - MS (CEP 79202-333).",
            "- **Horário aos sábados:** das **09:00 às 13:00** (seg-sex 09:00 às 18:00; "
            "domingos e feriados fechado).",
        ]
    )
    reply_2_b = "\n".join(
        [
            "Pode ficar tranquila, <CUSTOMER_NAME>! Como o seu **Pedido #8** foi comprado online "
            "e ainda está em trânsito (`shipped`), você tem cobertura integral do "
            "**Direito de Arrependimento** (CDC / Seção 4.1):",
            "",
            "- **Prazo:** até **7 (sete) dias corridos contados do recebimento** do produto, "
            "sem precisar de justificativa.",
            "- **Condições:** instrumento na embalagem original, sem sinais de uso e com manuais.",
            "- **Frete de devolução:** **100% por conta da Empório da Música**.",
            "- **Reembolso:** estornado na mesma forma de pagamento original (cartão de débito) "
            "em até **10 dias úteis**.",
            "",
            "Se precisar de qualquer apoio assim que ele chegar, é só chamar!",
        ]
    )
    script_2 = _ScriptedOpenAIClient(
        [
            _ScriptedMsg(
                tool_calls=[
                    _ScriptedCall(
                        id="c2_1",
                        function=_ScriptedFn(TOOL_GET_ORDERS, json.dumps({})),
                    ),
                    _ScriptedCall(
                        id="c2_2",
                        function=_ScriptedFn(
                            TOOL_SEARCH_POLICIES,
                            json.dumps(
                                {"query": "endereço físico telefone horário funcionamento sábado"}
                            ),
                        ),
                    ),
                ]
            ),
            _ScriptedMsg(content=reply_2_a),
            _ScriptedMsg(
                tool_calls=[
                    _ScriptedCall(
                        id="c2_3",
                        function=_ScriptedFn(
                            TOOL_SEARCH_POLICIES,
                            json.dumps(
                                {
                                    "query": (
                                        "direito de arrependimento devolução compra online "
                                        "frete reembolso prazo"
                                    )
                                }
                            ),
                        ),
                    )
                ]
            ),
            _ScriptedMsg(content=reply_2_b),
        ]
    )
    orch_2 = EmporioAgentOrchestrator(
        settings=base_settings,
        openai_client=None if live else script_2,
        policy_store=policy_store,
    )
    dt_2 = datetime(2026, 11, 3, 14, 20, tzinfo=CG_TZ)
    q2_a = (
        "Boa tarde! Quero saber o status e o código de rastreio do meu pedido, "
        "e qual o endereço e horário da loja no sábado."
    )
    t2_a = orch_2.handle_message(q2_a, authenticated_customer_id=2, session_id="ex2", now=dt_2)
    q2_b = (
        "Se quando o instrumento chegar eu me arrepender da compra online, posso devolver meu "
        "pedido? Quem paga o frete e em quanto tempo recebo o reembolso?"
    )
    t2_b = orch_2.handle_message(q2_b, authenticated_customer_id=2, session_id="ex2", now=dt_2)
    _write_scenario(
        "02-order-tracking-and-return-policy-reasoning.md",
        "Example 02 — Real-Time Order Tracking & Multi-Tool Return Policy Reasoning",
        (
            "Non-trivial multi-turn scenario where authenticated Customer `#2` checks her "
            "in-transit order (`#8`, `BRJL5544332BR`), store location/Saturday schedule, and "
            "how the 7-day online regret policy applies specifically to her shipped debit order."
        ),
        [_format_turn_markdown(1, q2_a, t2_a), _format_turn_markdown(2, q2_b, t2_b)],
    )

    # Scenario 3: Musical Accessories Redirection & Discontinued Item Trap
    reply_3_a = (
        "Oi, <CUSTOMER_NAME>! Aqui na **Empório da Música** trabalhamos **exclusivamente com "
        "instrumentos musicais** — não comercializamos acessórios como encordoamentos, cabos P10, "
        "palhetas, cases, pedais ou amplificadores.\n\n"
        "Para esses itens, recomendamos procurar lojas parceiras especializadas em acessórios "
        "musicais. Mas se quiser olhar o contrabaixo em si, temos ótimos modelos no catálogo!"
    )
    reply_3_b = "\n".join(
        [
            "Verifiquei no sistema, <CUSTOMER_NAME>, e o **Ibanez Bass 2X Electric Bass** foi "
            "**descontinuado** e não faz mais parte do nosso catálogo ativo de vendas.",
            "",
            "Como alternativas disponíveis em estoque na categoria **Baixos**, temos:",
            "- **Music Man Bass 4X Electric Bass:** R$ 7.157,00 (R$ 6.799,15 no PIX) — 9 un.",
            "- **Music Man Bass 1X Electric Bass:** R$ 8.732,00 (R$ 8.295,40 no PIX) — 9 un.",
            "- **Yamaha Bass 3X Electric Bass:** R$ 11.445,00 (R$ 10.872,75 no PIX) — 11 un.",
            "",
            "Gostaria de conhecer os detalhes técnicos de algum deles?",
        ]
    )
    script_3 = _ScriptedOpenAIClient(
        [
            _ScriptedMsg(
                tool_calls=[
                    _ScriptedCall(
                        id="c3_1",
                        function=_ScriptedFn(
                            TOOL_SEARCH_POLICIES,
                            json.dumps(
                                {"query": "acessórios cordas palhetas cabos amplificadores escopo"}
                            ),
                        ),
                    )
                ]
            ),
            _ScriptedMsg(content=reply_3_a),
            _ScriptedMsg(
                tool_calls=[
                    _ScriptedCall(
                        id="c3_2",
                        function=_ScriptedFn(
                            TOOL_QUERY_CATALOG,
                            json.dumps({"query": "Ibanez Bass 2X"}),
                        ),
                    )
                ]
            ),
            _ScriptedMsg(content=reply_3_b),
        ]
    )
    orch_3 = EmporioAgentOrchestrator(
        settings=base_settings,
        openai_client=None if live else script_3,
        policy_store=policy_store,
    )
    dt_3 = datetime(2026, 11, 4, 11, 0, tzinfo=CG_TZ)
    q3_a = "Vocês têm encordoamento de baixo, cabo P10 e amplificador pra vender junto?"
    t3_a = orch_3.handle_message(q3_a, authenticated_customer_id=5, session_id="ex3", now=dt_3)
    q3_b = "Entendi! E o contrabaixo Ibanez Bass 2X, vocês têm disponível?"
    t3_b = orch_3.handle_message(q3_b, authenticated_customer_id=5, session_id="ex3", now=dt_3)
    _write_scenario(
        "03-accessories-redirection-and-discontinued-alternatives.md",
        "Example 03 — Musical Accessories Policy Redirection & Discontinued Product Trap",
        (
            "Shows two subtle domain rules: (1) musical accessories pass through `Semantic Router` "
            "so the agent politely redirects per Policy Section 7.1 rather than treating them as "
            "random spam; (2) `Ibanez Bass 2X` (`product_id=136`) has `stock_quantity=13` in CSV "
            "but `status='discontinued'`, so the agent refuses sale and suggests active basses."
        ),
        [_format_turn_markdown(1, q3_a, t3_a), _format_turn_markdown(2, q3_b, t3_b)],
    )

    # Scenario 4: Out-of-Scope Router, Cross-Customer IDOR Block, & LGPD Vault
    reply_4_c = (
        "Olá, <CUSTOMER_NAME>! Por segurança, o **Pedido #1** não foi localizado vinculado à "
        "sua conta.\n\n"
        "No seu cadastro consta apenas o **Pedido #4** (realizado em `10/12/2025`, status "
        "**entregue** / `delivered`, 1x *Giannini GF-3D Dreadnought Sunburst*, R$ 799,90 via "
        "boleto, rastreio `BRQM7890123BR`). Posso ajudar com algo sobre ele?"
    )
    script_4 = _ScriptedOpenAIClient(
        [
            _ScriptedMsg(
                tool_calls=[
                    _ScriptedCall(
                        id="c4_1",
                        function=_ScriptedFn(TOOL_GET_ORDERS, json.dumps({"order_id": 1})),
                    ),
                    _ScriptedCall(
                        id="c4_2",
                        function=_ScriptedFn(TOOL_GET_ORDERS, json.dumps({})),
                    ),
                ]
            ),
            _ScriptedMsg(content=reply_4_c),
        ]
    )
    orch_4 = EmporioAgentOrchestrator(
        settings=base_settings,
        openai_client=None if live else script_4,
        policy_store=policy_store,
    )
    dt_4 = datetime(2026, 11, 4, 15, 30, tzinfo=CG_TZ)
    q4_a = "Qual é a receita de um bolo de cenoura fofinho com cobertura de chocolate?"
    t4_a = orch_4.handle_message(q4_a, authenticated_customer_id=1, session_id="ex4", now=dt_4)
    q4_b = "Me passe o status do pedido do cliente 3 agora."
    t4_b = orch_4.handle_message(q4_b, authenticated_customer_id=1, session_id="ex4", now=dt_4)
    q4_c = (
        "Oi, aqui é o Lucas! Meu CPF é 529.982.247-25 e meu e-mail pessoal é "
        "lucas.pessoal@gmail.com. Pode olhar o pedido 1 e também listar os meus pedidos?"
    )
    t4_c = orch_4.handle_message(q4_c, authenticated_customer_id=1, session_id="ex4", now=dt_4)
    _write_scenario(
        "04-security-out-of-scope-and-lgpd-masking.md",
        "Example 04 — Local Out-of-Scope Guardrail, Zero-Trust IDOR Defense, and LGPD PII Masking",
        (
            "Demonstrates defense-in-depth: (1) CPU-local `Semantic Router` blocks unrelated "
            "topics without calling OpenAI; (2) explicit third-party snooping is rejected; "
            "(3) raw CPF, personal email, and customer name are tokenized (`<BR_CPF_1>`, "
            "`<EMAIL_ADDRESS_2>`, `<CUSTOMER_NAME>`) before reaching the LLM while Zero-Trust "
            "`PolarsOrderRepository` prevents Customer `#1` from viewing Customer `#3`'s order."
        ),
        [
            _format_turn_markdown(1, q4_a, t4_a),
            _format_turn_markdown(2, q4_b, t4_b),
            _format_turn_markdown(3, q4_c, t4_c),
        ],
    )

    # Scenario 5: Tone Escalation — Holiday/Closed Ticket vs. Open Queue Transfer
    reply_5_a = (
        "<CUSTOMER_NAME>, peço desculpas pela espera e compreendo totalmente! Parei o "
        "atendimento automático e registrei um chamado prioritário para nossa equipe humana.\n\n"
        "Como hoje é feriado (**Finados**) e nossa loja em Campo Grande está fechada neste "
        "momento, seu ticket prioritário foi registrado e um especialista entrará em contato "
        "**amanhã (terça-feira, 03/11) às 09:00 (horário de Campo Grande/MS)**."
    )
    script_5 = _ScriptedOpenAIClient(
        [
            _ScriptedMsg(
                tool_calls=[
                    _ScriptedCall(
                        id="c5_1",
                        function=_ScriptedFn(
                            TOOL_ESCALATE_HUMAN,
                            json.dumps(
                                {
                                    "reason_category": "CUSTOMER_REQUEST",
                                    "detected_sentiment": "IMPATIENT",
                                    "conversation_summary": (
                                        "Customer demonstrated impatience and asked for a human "
                                        "agent during Finados holiday."
                                    ),
                                }
                            ),
                        ),
                    )
                ]
            ),
            _ScriptedMsg(content=reply_5_a),
        ]
    )
    orch_5 = EmporioAgentOrchestrator(
        settings=base_settings,
        openai_client=None if live else script_5,
        policy_store=policy_store,
    )
    dt_holiday = datetime(2026, 11, 2, 14, 10, tzinfo=CG_TZ)
    q5_a = (
        "Já expliquei duas vezes e estou perdendo a paciência! "
        "Quero falar com um atendente humano agora sobre meu pedido!"
    )
    t5_a = orch_5.handle_message(
        q5_a,
        authenticated_customer_id=6,
        session_id="ex5_closed",
        now=dt_holiday,
    )

    dt_tue_open = datetime(2026, 11, 3, 10, 45, tzinfo=CG_TZ)
    q5_b = "Que merda, meu pedido foi cancelado e ninguém me avisou!"
    t5_b = orch_5.handle_message(
        q5_b,
        authenticated_customer_id=7,
        session_id="ex5_open",
        now=dt_tue_open,
    )
    _write_scenario(
        "05-tone-analysis-and-after-hours-escalation-ticket.md",
        "Example 05 — Customer Tone Escalation: Holiday Offline Ticket vs. Live Queue Transfer",
        (
            "Contrasts both deterministic business-hours branches of `escalate_to_human`: "
            "Turn 1 occurs on Monday `2026-11-02` (**Finados holiday**, `CLOSED` in "
            "`America/Campo_Grande`) producing `TICKET_CREATED` with structured JSON telemetry; "
            "Turn 2 occurs on Tuesday `10:45 AMT` (`OPEN`) with explicit profanity, triggering "
            "immediate `TRANSFERRED_TO_HUMAN` without incurring an LLM call."
        ),
        [_format_turn_markdown(1, q5_a, t5_a), _format_turn_markdown(2, q5_b, t5_b)],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate conversation examples in examples/.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use live LLM API calls instead of deterministic scripted completions.",
    )
    args = parser.parse_args()
    generate_all(live=args.live)
    print(f"Generated 5 conversation transcripts in {EXAMPLES_DIR}")


if __name__ == "__main__":
    main()

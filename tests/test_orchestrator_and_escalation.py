"""End-to-end deterministic tests for the custom OpenAI orchestrator, LGPD, and escalation."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from emporio_agent.agent.orchestrator import EmporioAgentOrchestrator
from emporio_agent.agent.session import EphemeralSessionStore
from emporio_agent.config import Settings
from emporio_agent.domain.models import ResolutionAction, StoreStatus
from emporio_agent.guardrails.input_validator import InputCheckStatus
from emporio_agent.guardrails.intent_router import RouteVerdict
from emporio_agent.rag.policy_store import POLICY_PDF_FILENAME, PolicyVectorStore
from emporio_agent.tools.schemas import (
    TOOL_ESCALATE_HUMAN,
    TOOL_GET_ORDERS,
    TOOL_QUERY_CATALOG,
)

DOCS_DIR = Path(__file__).resolve().parents[1] / "documents"
CG_TZ = ZoneInfo("America/Campo_Grande")


@dataclass
class _FakeFunction:
    name: str
    arguments: str


@dataclass
class _FakeToolCall:
    id: str
    function: _FakeFunction


@dataclass
class _FakeMessage:
    content: str | None = None
    tool_calls: list[_FakeToolCall] | None = None


@dataclass
class _FakeChoice:
    message: _FakeMessage


@dataclass
class _FakeCompletionResponse:
    choices: list[_FakeChoice]


class FakeOpenAIClient:
    """Deterministic test double capturing outbound `messages` payloads."""

    def __init__(self, scripted_messages: list[_FakeMessage]) -> None:
        self._queue = list(scripted_messages)
        self.captured_calls: list[dict[str, Any]] = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs: Any) -> _FakeCompletionResponse:
        self.captured_calls.append(kwargs)
        next_msg = self._queue.pop(0)
        return _FakeCompletionResponse(choices=[_FakeChoice(message=next_msg)])


@pytest.fixture(scope="module")
def ephemeral_policy_store() -> PolicyVectorStore:
    return PolicyVectorStore(pdf_path=DOCS_DIR / POLICY_PDF_FILENAME, ephemeral=True)


def _make_orchestrator(
    fake_client: FakeOpenAIClient,
    policy_store: PolicyVectorStore,
    max_history_messages: int = 20,
) -> EmporioAgentOrchestrator:
    settings = Settings(
        openai_api_key="test-key",
        documents_dir=DOCS_DIR,
        max_history_messages=max_history_messages,
    )
    return EmporioAgentOrchestrator(
        settings=settings,
        openai_client=fake_client,
        policy_store=policy_store,
        session_store=EphemeralSessionStore(max_history_messages=max_history_messages),
    )


def test_pii_never_reaches_openai_payload_and_greeting_token_is_unmasked(
    ephemeral_policy_store: PolicyVectorStore,
) -> None:
    fake_llm = FakeOpenAIClient(
        scripted_messages=[
            _FakeMessage(
                content=(
                    "Olá, <CUSTOMER_NAME>! Recebi seu CPF <BR_CPF_1> e verifiquei seu pedido."
                ),
            ),
        ]
    )
    orch = _make_orchestrator(fake_llm, ephemeral_policy_store)

    # Customer #1 is Lucas Mendes da Silva
    res = orch.handle_message(
        user_message=(
            "Oi, aqui é o Lucas! Meu CPF é 529.982.247-25 e meu e-mail é "
            "lucas.pessoal@gmail.com, preciso saber sobre violões."
        ),
        authenticated_customer_id=1,
        now=datetime(2026, 11, 3, 14, 0, tzinfo=CG_TZ),
    )

    # Verify outbound payload sent to OpenAI contained ZERO raw PII
    assert len(fake_llm.captured_calls) == 1
    sent_payload_str = json.dumps(fake_llm.captured_calls[0]["messages"], ensure_ascii=False)
    assert "529.982.247-25" not in sent_payload_str
    assert "lucas.pessoal@gmail.com" not in sent_payload_str
    assert "Lucas" not in sent_payload_str
    assert "<BR_CPF_1>" in sent_payload_str
    # `_1` is pre-bound to Lucas's profile email (`lucas.mendes@jmail.com`); new email gets `_2`
    assert "<EMAIL_ADDRESS_2>" in sent_payload_str
    assert "<CUSTOMER_NAME>" in sent_payload_str

    # Verify final reply delivered back to Customer #1 has tokens cleanly restored
    assert res.reply_pt_br == (
        "Olá, Lucas! Recebi seu CPF 529.982.247-25 e verifiquei seu pedido."
    )


def test_orchestrator_executes_catalog_and_zero_trust_order_tool_loop(
    ephemeral_policy_store: PolicyVectorStore,
) -> None:
    fake_llm = FakeOpenAIClient(
        scripted_messages=[
            _FakeMessage(
                tool_calls=[
                    _FakeToolCall(
                        id="call_cat_1",
                        function=_FakeFunction(
                            name=TOOL_QUERY_CATALOG,
                            arguments=json.dumps({"query": "Takamine GD20"}),
                        ),
                    ),
                    _FakeToolCall(
                        id="call_ord_1",
                        function=_FakeFunction(
                            name=TOOL_GET_ORDERS,
                            arguments=json.dumps({"order_id": 1}),  # Belongs to Customer #3!
                        ),
                    ),
                ]
            ),
            _FakeMessage(
                content=(
                    "O Takamine GD20 custa R$ 2.199,00 (ou R$ 2.089,05 no PIX). "
                    "Quanto ao pedido #1, ele não consta na sua conta."
                )
            ),
        ]
    )
    orch = _make_orchestrator(fake_llm, ephemeral_policy_store)

    # Authenticated as Customer #1 (Lucas) attempting to inspect Order #1 (Pedro's order)
    res = orch.handle_message(
        user_message="Quanto custa o Takamine GD20? E veja o pedido 1 pra mim.",
        authenticated_customer_id=1,
        now=datetime(2026, 11, 3, 15, 0, tzinfo=CG_TZ),
    )

    assert len(res.tool_traces) == 2
    order_trace = next(t for t in res.tool_traces if t.tool_name == TOOL_GET_ORDERS)
    order_out = json.loads(order_trace.output_json)
    assert order_out["orders"] == []
    assert order_out["not_found_for_authenticated_customer"] is True
    assert "BRAB1234567BR" not in order_trace.output_json  # Customer #3's tracking code protected!


def test_llm_tone_escalation_creates_ticket_outside_business_hours(
    ephemeral_policy_store: PolicyVectorStore,
) -> None:
    fake_llm = FakeOpenAIClient(
        scripted_messages=[
            _FakeMessage(
                tool_calls=[
                    _FakeToolCall(
                        id="call_esc_1",
                        function=_FakeFunction(
                            name=TOOL_ESCALATE_HUMAN,
                            arguments=json.dumps(
                                {
                                    "reason_category": "CUSTOMER_REQUEST",
                                    "detected_sentiment": "IMPATIENT",
                                    "conversation_summary": (
                                        "Customer is impatient and explicitly asked for a human."
                                    ),
                                }
                            ),
                        ),
                    )
                ]
            ),
            _FakeMessage(
                content=(
                    "Registrei seu atendimento prioritário para falar com nossa equipe humana!"
                )
            ),
        ]
    )
    orch = _make_orchestrator(fake_llm, ephemeral_policy_store)

    # Sunday 2026-11-08 at 19:00 AMT -> Store CLOSED
    res = orch.handle_message(
        user_message="Chega de robô, quero falar com um atendente humano agora!",
        authenticated_customer_id=2,
        now=datetime(2026, 11, 8, 19, 0, tzinfo=CG_TZ),
    )

    assert res.escalation_ticket is not None
    assert res.escalation_ticket.store_status == StoreStatus.CLOSED
    assert res.escalation_ticket.resolution_action == ResolutionAction.TICKET_CREATED
    assert res.escalation_ticket.protocol_id.startswith("EMP-20261108-")
    # Second LLM call after escalation must have `tools` omitted so bot stops troubleshooting
    assert "tools" not in fake_llm.captured_calls[1]


def test_deterministic_profanity_and_repetition_escalate_without_calling_openai(
    ephemeral_policy_store: PolicyVectorStore,
) -> None:
    fake_llm = FakeOpenAIClient(scripted_messages=[])
    orch = _make_orchestrator(fake_llm, ephemeral_policy_store)

    # Open business hours (Tuesday 11:00 AMT) -> TRANSFERRED_TO_HUMAN, 0 OpenAI calls
    profane_res = orch.handle_message(
        user_message="Que merda, meu pedido nunca chega!",
        authenticated_customer_id=1,
        now=datetime(2026, 11, 3, 11, 0, tzinfo=CG_TZ),
    )
    assert profane_res.input_check_status == InputCheckStatus.ESCALATE_PROFANITY
    assert profane_res.escalation_ticket is not None
    assert profane_res.escalation_ticket.resolution_action == ResolutionAction.TRANSFERRED_TO_HUMAN
    assert fake_llm.captured_calls == []


def test_out_of_scope_query_is_blocked_locally_without_calling_openai(
    ephemeral_policy_store: PolicyVectorStore,
) -> None:
    fake_llm = FakeOpenAIClient(scripted_messages=[])
    orch = _make_orchestrator(fake_llm, ephemeral_policy_store)

    res = orch.handle_message(
        user_message="Me passe uma receita de lasanha à bolonhesa rápida.",
        authenticated_customer_id=1,
    )
    assert res.route_verdict == RouteVerdict.BLOCK_OUT_OF_SCOPE
    assert fake_llm.captured_calls == []
    assert "Empório da Música" in res.reply_pt_br


def test_session_isolation_forbids_cross_customer_session_hijacking(
    ephemeral_policy_store: PolicyVectorStore,
) -> None:
    fake_llm = FakeOpenAIClient(scripted_messages=[_FakeMessage(content="Olá!")])
    orch = _make_orchestrator(fake_llm, ephemeral_policy_store)

    res_1 = orch.handle_message(
        user_message="Oi, tudo bem?",
        authenticated_customer_id=1,
        session_id="sess_shared_attempt",
    )
    assert res_1.session_id == "sess_shared_attempt"

    with pytest.raises(PermissionError):
        orch.handle_message(
            user_message="Oi!",
            authenticated_customer_id=3,
            session_id="sess_shared_attempt",
        )


def test_sliding_window_history_never_orphans_tool_messages(
    ephemeral_policy_store: PolicyVectorStore,
) -> None:
    fake_llm = FakeOpenAIClient(
        scripted_messages=[
            # Turn 1: 4 messages (user, assistant+tool_call, tool, assistant)
            _FakeMessage(
                tool_calls=[
                    _FakeToolCall(
                        id="c1",
                        function=_FakeFunction(
                            name=TOOL_QUERY_CATALOG,
                            arguments=json.dumps({"query": "violão"}),
                        ),
                    )
                ]
            ),
            _FakeMessage(content="Temos vários violões!"),
            # Turn 2: 2 messages (user, assistant) -> pushes total > max_history_messages=4
            _FakeMessage(content="Aceitamos PIX com 5% de desconto!"),
        ]
    )
    orch = _make_orchestrator(fake_llm, ephemeral_policy_store, max_history_messages=4)

    orch.handle_message("Quais violões vocês têm?", authenticated_customer_id=1, session_id="s_win")
    orch.handle_message("Sim, obrigado!", authenticated_customer_id=1, session_id="s_win")

    sess = orch.session_store.get_or_create_session(authenticated_customer_id=1, session_id="s_win")
    hist = sess.get_openai_history()
    # First message in history must NEVER be an orphaned `role == "tool"`
    assert hist[0]["role"] == "user"


def test_openrouter_and_openai_provider_resolution() -> None:
    # Direct OpenAI key (isolated from any local `.env` file via `_env_file=None`)
    s_openai = Settings(_env_file=None, openai_api_key="sk-proj-abc123", openrouter_api_key="")
    assert s_openai.is_openrouter is False
    assert s_openai.effective_base_url is None
    assert s_openai.effective_model == "gpt-4o-mini"

    # Dedicated OPENROUTER_API_KEY auto-configures base_url and `openai/` prefix
    s_or = Settings(_env_file=None, openrouter_api_key="sk-or-v1-xyz789", openai_api_key="")
    assert s_or.is_openrouter is True
    assert s_or.effective_base_url == "https://openrouter.ai/api/v1"
    assert s_or.effective_model == "openai/gpt-4o-mini"

    # Pasting an `sk-or-` key into OPENAI_API_KEY also auto-routes safely to OpenRouter
    s_or_fallback = Settings(
        _env_file=None,
        openai_api_key="sk-or-v1-fallback",
        openrouter_api_key="",
        openai_model="google/gemini-2.5-flash",
    )
    assert s_or_fallback.is_openrouter is True
    assert s_or_fallback.effective_base_url == "https://openrouter.ai/api/v1"
    assert s_or_fallback.effective_model == "google/gemini-2.5-flash"

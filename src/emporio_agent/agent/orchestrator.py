"""Custom native OpenAI tool-calling orchestrator for Emporio da Musica.

Pipeline per customer turn:
1. Ephemeral Session & Zero-Trust Identity lookup (`authenticated_customer_id`)
2. Deterministic Input Validation (`validate_user_input`)
3. LGPD Runtime PII Masking (`SessionPIIVault.mask_text`)
4. Pre-screened Immediate Escalation (explicit profanity or 3x identical repetition)
5. Local Semantic Guardrail (`LocalIntentGuardrail.evaluate` via FastEmbed)
6. Dynamic English System Prompt + Bounded OpenAI Function-Calling Loop
7. Local Reversible PII Unmasking (`SessionPIIVault.unmask_text`)
"""

from datetime import datetime
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from emporio_agent.agent.prompts import build_dynamic_system_prompt
from emporio_agent.agent.session import CustomerSession, EphemeralSessionStore
from emporio_agent.config import Settings, get_settings
from emporio_agent.data.csv_repositories import (
    PolarsCatalogRepository,
    PolarsCustomerRepository,
    PolarsOrderRepository,
)
from emporio_agent.domain.business_hours import BusinessHoursService
from emporio_agent.domain.models import (
    DetectedSentiment,
    EscalationReasonCategory,
    EscalationTicketPayload,
    ResolutionAction,
    StoreOperationalSnapshot,
)
from emporio_agent.guardrails.input_validator import (
    InputCheckStatus,
    validate_user_input,
)
from emporio_agent.guardrails.intent_router import LocalIntentGuardrail, RouteVerdict
from emporio_agent.rag.policy_store import POLICY_PDF_FILENAME, PolicyVectorStore
from emporio_agent.tools.handlers import ToolDispatcher
from emporio_agent.tools.schemas import get_openai_tool_definitions


class ToolCallTrace(BaseModel):
    """Audit record of a single function call executed during an orchestrator turn."""

    model_config = ConfigDict(frozen=True)

    tool_name: str
    arguments_json: str
    output_json: str


class AgentTurnResponse(BaseModel):
    """Complete structured output and observability trace for a single customer turn."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    authenticated_customer_id: int
    reply_pt_br: str
    masked_user_input: str
    detected_pii_entities: list[str] = Field(default_factory=list)
    input_check_status: InputCheckStatus
    route_verdict: RouteVerdict | None = None
    matched_route: str | None = None
    tool_traces: list[ToolCallTrace] = Field(default_factory=list)
    escalation_ticket: EscalationTicketPayload | None = None
    store_snapshot: StoreOperationalSnapshot


def _format_deterministic_escalation_reply(
    ticket: EscalationTicketPayload,
    customer_first_name: str | None,
) -> str:
    """Build a warm, empathetic `pt-BR` handoff/ticket message for pre-screened escalations."""
    prefix = f"{customer_first_name}, entendo" if customer_first_name else "Entendo"
    if ticket.resolution_action == ResolutionAction.TRANSFERRED_TO_HUMAN:
        return (
            f"{prefix} perfeitamente a sua situação e peço desculpas pelo transtorno. "
            f"Parei o atendimento automático agora mesmo e já estou te transferindo para um "
            f"especialista humano da nossa equipe (Protocolo **{ticket.protocol_id}**). "
            "Um atendente assumirá a conversa em instantes!"
        )
    return (
        f"{prefix} perfeitamente a sua situação e peço desculpas pelo transtorno. "
        f"Registrei agora mesmo um atendimento prioritário para você sob o protocolo "
        f"**{ticket.protocol_id}**. Como estamos fora do nosso horário de expediente neste "
        f"momento, um especialista humano entrará em contato **{ticket.next_opening_pt_br}** "
        "para resolver tudo com prioridade."
    )


class EmporioAgentOrchestrator:
    """High-control conversational orchestrator built directly on `openai` and `pydantic`."""

    def __init__(
        self,
        settings: Settings | None = None,
        openai_client: Any | None = None,
        catalog_repo: PolarsCatalogRepository | None = None,
        order_repo: PolarsOrderRepository | None = None,
        customer_repo: PolarsCustomerRepository | None = None,
        policy_store: PolicyVectorStore | None = None,
        business_hours: BusinessHoursService | None = None,
        intent_guardrail: LocalIntentGuardrail | None = None,
        session_store: EphemeralSessionStore | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        docs_dir = self._settings.documents_dir

        self._catalog_repo = catalog_repo or PolarsCatalogRepository(docs_dir)
        self._order_repo = order_repo or PolarsOrderRepository(docs_dir)
        self._customer_repo = customer_repo or PolarsCustomerRepository(docs_dir)
        self._policy_store = policy_store or PolicyVectorStore(
            pdf_path=docs_dir / POLICY_PDF_FILENAME,
            persist_dir=self._settings.chroma_persist_dir,
            embedding_model_name=self._settings.embedding_model_name,
        )
        self._business_hours = business_hours or BusinessHoursService(
            timezone_name=self._settings.store_timezone,
        )
        self._intent_guardrail = intent_guardrail or LocalIntentGuardrail(
            embedding_model_name=self._settings.embedding_model_name,
        )
        self._session_store = session_store or EphemeralSessionStore(
            ttl_seconds=self._settings.session_ttl_seconds,
            max_history_messages=self._settings.max_history_messages,
        )
        self._dispatcher = ToolDispatcher(
            catalog_repo=self._catalog_repo,
            order_repo=self._order_repo,
            policy_store=self._policy_store,
            business_hours=self._business_hours,
        )
        self._tool_defs = get_openai_tool_definitions()
        self._openai_client = openai_client

    @property
    def dispatcher(self) -> ToolDispatcher:
        """Expose dispatcher for telemetry inspection."""
        return self._dispatcher

    @property
    def customer_repo(self) -> PolarsCustomerRepository:
        """Expose backend customer repository for CLI profile display."""
        return self._customer_repo

    @property
    def session_store(self) -> EphemeralSessionStore:
        """Expose ephemeral session store for lifecycle control."""
        return self._session_store

    def _get_openai_client(self) -> Any:
        if self._openai_client is not None:
            return self._openai_client
        if not self._settings.openai_api_key.strip():
            raise RuntimeError(
                "OPENAI_API_KEY is not configured. Copy `.env.example` to `.env` and set "
                "a valid OpenAI API key to run live LLM completions."
            )
        self._openai_client = OpenAI(api_key=self._settings.openai_api_key)
        return self._openai_client

    def handle_message(
        self,
        user_message: str,
        authenticated_customer_id: int,
        session_id: str | None = None,
        now: datetime | None = None,
    ) -> AgentTurnResponse:
        """Process a customer message end-to-end with guardrails, LGPD masking, and tools."""
        profile = self._customer_repo.get_customer_by_id(authenticated_customer_id)
        session = self._session_store.get_or_create_session(
            authenticated_customer_id=authenticated_customer_id,
            session_id=session_id,
            customer_profile=profile,
        )
        snapshot = self._business_hours.get_snapshot(now=now)

        # 1. Mask PII BEFORE repetition/router/LLM so raw PII never leaks into buffers or APIs
        # First check empty/control/length/gibberish on raw structural shape
        pre_check = validate_user_input(
            user_message,
            prior_user_messages=session.prior_user_messages,
        )
        if not pre_check.is_allowed:
            masked_preview = session.pii_vault.mask_text(user_message)
            return AgentTurnResponse(
                session_id=session.session_id,
                authenticated_customer_id=authenticated_customer_id,
                reply_pt_br=pre_check.suggested_reply_pt_br or "",
                masked_user_input=masked_preview.masked_text,
                detected_pii_entities=masked_preview.detected_entity_types,
                input_check_status=pre_check.status,
                store_snapshot=snapshot,
            )

        mask_outcome = session.pii_vault.mask_text(user_message)
        masked_input = mask_outcome.masked_text

        # Re-check repetition on masked input so varied CPFs/emails normalize cleanly
        post_mask_check = validate_user_input(
            masked_input,
            prior_user_messages=session.prior_user_messages,
        )
        effective_status = (
            pre_check.status
            if pre_check.requires_immediate_escalation
            else post_mask_check.status
        )
        session.record_user_utterance_for_repetition(masked_input)

        # 2. Deterministic safety net for explicit profanity or 3x message repetition
        if (
            pre_check.requires_immediate_escalation
            or post_mask_check.requires_immediate_escalation
        ):
            return self._handle_deterministic_escalation(
                session=session,
                masked_input=masked_input,
                detected_entities=mask_outcome.detected_entity_types,
                check_status=effective_status,
                snapshot=snapshot,
                now=now,
            )

        # 3. Local Semantic Router guardrail (blocks random topics & jailbreaks on CPU)
        route_res = self._intent_guardrail.evaluate(masked_input)
        if route_res.verdict != RouteVerdict.ALLOW_IN_SCOPE:
            refusal = route_res.refusal_reply_pt_br or ""
            session.append_turn_group(
                [
                    {"role": "user", "content": masked_input},
                    {"role": "assistant", "content": refusal},
                ]
            )
            return AgentTurnResponse(
                session_id=session.session_id,
                authenticated_customer_id=authenticated_customer_id,
                reply_pt_br=refusal,
                masked_user_input=masked_input,
                detected_pii_entities=mask_outcome.detected_entity_types,
                input_check_status=effective_status,
                route_verdict=route_res.verdict,
                matched_route=route_res.matched_route,
                store_snapshot=snapshot,
            )

        # 4. Dynamic English System Prompt + Bounded Native OpenAI Tool Loop
        system_prompt = build_dynamic_system_prompt(
            snapshot=snapshot,
            customer_city=profile.city if profile else None,
            has_bound_customer_name=profile is not None,
            active_escalation=session.active_escalation,
        )

        turn_messages: list[dict[str, Any]] = [{"role": "user", "content": masked_input}]
        tool_traces: list[ToolCallTrace] = []
        triggered_ticket: EscalationTicketPayload | None = None
        client = self._get_openai_client()

        final_masked_reply = ""
        for _iteration in range(self._settings.max_tool_iterations):
            request_messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                *session.get_openai_history(),
                *turn_messages,
            ]
            # Once escalation has fired in this turn, disable further tool calls so LLM closes out
            tools_param = None if triggered_ticket is not None else self._tool_defs

            completion_kwargs: dict[str, Any] = {
                "model": self._settings.openai_model,
                "messages": request_messages,
                "temperature": 0.2,
            }
            if tools_param is not None:
                completion_kwargs["tools"] = tools_param
                completion_kwargs["tool_choice"] = "auto"

            response = client.chat.completions.create(**completion_kwargs)
            choice_msg = response.choices[0].message
            tool_calls = getattr(choice_msg, "tool_calls", None) or []

            if not tool_calls:
                final_masked_reply = (choice_msg.content or "").strip()
                turn_messages.append({"role": "assistant", "content": final_masked_reply})
                break

            assistant_tool_msg: dict[str, Any] = {
                "role": "assistant",
                "content": choice_msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            }
            turn_messages.append(assistant_tool_msg)

            for tc in tool_calls:
                outcome = self._dispatcher.dispatch(
                    tool_name=tc.function.name,
                    raw_arguments_json=tc.function.arguments,
                    authenticated_customer_id=authenticated_customer_id,
                    now=now,
                )
                tool_traces.append(
                    ToolCallTrace(
                        tool_name=outcome.tool_name,
                        arguments_json=tc.function.arguments,
                        output_json=outcome.json_payload,
                    )
                )
                if outcome.escalation_ticket is not None:
                    triggered_ticket = outcome.escalation_ticket
                    session.active_escalation = outcome.escalation_ticket

                turn_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": outcome.json_payload,
                    }
                )
        else:
            # Fallback if max_tool_iterations reached without final plain assistant reply
            if triggered_ticket is not None:
                final_masked_reply = triggered_ticket.customer_guidance_pt_br
            else:
                final_masked_reply = (
                    "Desculpe, precisei consultar muitas informações seguidas. "
                    "Poderia confirmar exatamente qual modelo ou pedido deseja verificar?"
                )
            turn_messages.append({"role": "assistant", "content": final_masked_reply})

        session.append_turn_group(turn_messages)
        unmasked_reply = session.pii_vault.unmask_text(final_masked_reply)

        return AgentTurnResponse(
            session_id=session.session_id,
            authenticated_customer_id=authenticated_customer_id,
            reply_pt_br=unmasked_reply,
            masked_user_input=masked_input,
            detected_pii_entities=mask_outcome.detected_entity_types,
            input_check_status=effective_status,
            route_verdict=route_res.verdict,
            matched_route=route_res.matched_route,
            tool_traces=tool_traces,
            escalation_ticket=triggered_ticket,
            store_snapshot=snapshot,
        )

    def _handle_deterministic_escalation(
        self,
        session: CustomerSession,
        masked_input: str,
        detected_entities: list[str],
        check_status: InputCheckStatus,
        snapshot: StoreOperationalSnapshot,
        now: datetime | None,
    ) -> AgentTurnResponse:
        """Immediately invoke `escalate_to_human` without risking LLM hesitation."""
        if check_status == InputCheckStatus.ESCALATE_PROFANITY:
            reason = EscalationReasonCategory.FRUSTRATION_OR_PROFANITY
            sentiment = DetectedSentiment.ANGRY_OR_PROFANE
            summary = f"Pre-screened customer profanity/hostility: '{masked_input[:160]}'"
        else:
            reason = EscalationReasonCategory.EXCESSIVE_REPETITION
            sentiment = DetectedSentiment.IMPATIENT
            summary = f"Customer repeated identical message >= 3 times: '{masked_input[:160]}'"

        ticket = self._dispatcher.execute_escalation(
            reason_category=reason,
            detected_sentiment=sentiment,
            conversation_summary=summary,
            now=now,
        )
        session.active_escalation = ticket

        first_name = session.customer_profile.first_name if session.customer_profile else None
        reply = _format_deterministic_escalation_reply(ticket, first_name)

        trace = ToolCallTrace(
            tool_name="escalate_to_human",
            arguments_json=ticket.model_dump_json(
                include={"reason_category", "detected_sentiment", "conversation_summary"}
            ),
            output_json=ticket.model_dump_json(),
        )
        session.append_turn_group(
            [
                {"role": "user", "content": masked_input},
                {"role": "assistant", "content": reply},
            ]
        )

        return AgentTurnResponse(
            session_id=session.session_id,
            authenticated_customer_id=session.authenticated_customer_id,
            reply_pt_br=reply,
            masked_user_input=masked_input,
            detected_pii_entities=detected_entities,
            input_check_status=check_status,
            tool_traces=[trace],
            escalation_ticket=ticket,
            store_snapshot=snapshot,
        )

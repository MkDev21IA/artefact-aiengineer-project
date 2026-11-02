"""Deterministic execution dispatcher returning structured JSON payloads for LLM tools."""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

from emporio_agent.data.interfaces import CatalogRepository, OrderRepository
from emporio_agent.domain.business_hours import BusinessHoursService
from emporio_agent.domain.models import (
    DetectedSentiment,
    EscalationReasonCategory,
    EscalationTicketPayload,
    ResolutionAction,
    StoreStatus,
)
from emporio_agent.rag.policy_store import PolicyVectorStore
from emporio_agent.tools.schemas import (
    TOOL_ESCALATE_HUMAN,
    TOOL_GET_ORDERS,
    TOOL_QUERY_CATALOG,
    TOOL_SEARCH_POLICIES,
    EscalateToHumanArgs,
    GetCustomerOrdersArgs,
    QueryProductCatalogArgs,
    SearchStorePoliciesArgs,
)


@dataclass(frozen=True)
class ToolExecutionOutcome:
    """Result of executing a single validated function call."""

    tool_name: str
    json_payload: str
    escalation_ticket: EscalationTicketPayload | None = None


class ToolDispatcher:
    """Validates tool arguments with Pydantic v2 and executes backend handlers."""

    def __init__(
        self,
        catalog_repo: CatalogRepository,
        order_repo: OrderRepository,
        policy_store: PolicyVectorStore,
        business_hours: BusinessHoursService,
    ) -> None:
        self._catalog_repo = catalog_repo
        self._order_repo = order_repo
        self._policy_store = policy_store
        self._business_hours = business_hours
        self._escalation_telemetry: list[EscalationTicketPayload] = []

    @property
    def escalation_telemetry(self) -> tuple[EscalationTicketPayload, ...]:
        """Return recorded escalation/ticket events for business metrics and auditing."""
        return tuple(self._escalation_telemetry)

    def execute_escalation(
        self,
        reason_category: EscalationReasonCategory,
        detected_sentiment: DetectedSentiment,
        conversation_summary: str,
        now: datetime | None = None,
    ) -> EscalationTicketPayload:
        """Deterministically transfer to live queue (`OPEN`) or create ticket (`CLOSED`)."""
        snap = self._business_hours.get_snapshot(now=now)
        short_code = uuid.uuid4().hex[:6].upper()
        date_compact = snap.current_iso_datetime[:10].replace("-", "")
        protocol_id = f"EMP-{date_compact}-{short_code}"

        if snap.status == StoreStatus.OPEN:
            action = ResolutionAction.TRANSFERRED_TO_HUMAN
            guidance = (
                f"Transferência para atendente humano iniciada (Protocolo {protocol_id}). "
                "Nossa equipe está em horário de atendimento e assumirá a conversa em instantes "
                "(tempo médio de resposta de até 5 minutos)."
            )
        else:
            action = ResolutionAction.TICKET_CREATED
            guidance = (
                f"Ticket prioritário registrado sob o protocolo {protocol_id}. "
                f"No momento a loja está fora do expediente; um especialista humano entrará em "
                f"contato {snap.next_opening_pt_br}."
            )

        payload = EscalationTicketPayload(
            protocol_id=protocol_id,
            timestamp_iso=snap.current_iso_datetime,
            store_status=snap.status,
            resolution_action=action,
            reason_category=reason_category,
            detected_sentiment=detected_sentiment,
            conversation_summary=conversation_summary,
            next_opening_pt_br=snap.next_opening_pt_br,
            customer_guidance_pt_br=guidance,
        )
        self._escalation_telemetry.append(payload)
        return payload

    def dispatch(
        self,
        tool_name: str,
        raw_arguments_json: str,
        authenticated_customer_id: int,
        now: datetime | None = None,
    ) -> ToolExecutionOutcome:
        """Parse JSON arguments, enforce Zero-Trust customer scope, and return JSON string."""
        try:
            parsed_dict = json.loads(raw_arguments_json) if raw_arguments_json.strip() else {}
        except json.JSONDecodeError as exc:
            err_json = json.dumps(
                {"error": "INVALID_JSON_ARGUMENTS", "details": str(exc)},
                ensure_ascii=False,
            )
            return ToolExecutionOutcome(tool_name=tool_name, json_payload=err_json)

        try:
            if tool_name == TOOL_SEARCH_POLICIES:
                args = SearchStorePoliciesArgs.model_validate(parsed_dict)
                chunks = self._policy_store.search_policies(query=args.query, top_k=args.top_k)
                body = {
                    "tool": TOOL_SEARCH_POLICIES,
                    "results": [c.model_dump(mode="json") for c in chunks],
                }
                return ToolExecutionOutcome(
                    tool_name=tool_name,
                    json_payload=json.dumps(body, ensure_ascii=False),
                )

            if tool_name == TOOL_QUERY_CATALOG:
                args = QueryProductCatalogArgs.model_validate(parsed_dict)
                res = self._catalog_repo.search_products(
                    query=args.query,
                    category=args.category,
                    min_price_brl=args.min_price_brl,
                    max_price_brl=args.max_price_brl,
                    only_available=args.only_available,
                    only_active_promotions=args.only_active_promotions,
                    limit=args.limit,
                )
                body = {
                    "tool": TOOL_QUERY_CATALOG,
                    **res.model_dump(mode="json"),
                    "categories_overview": (
                        self._catalog_repo.list_categories() if not res.items else []
                    ),
                }
                return ToolExecutionOutcome(
                    tool_name=tool_name,
                    json_payload=json.dumps(body, ensure_ascii=False),
                )

            if tool_name == TOOL_GET_ORDERS:
                args = GetCustomerOrdersArgs.model_validate(parsed_dict)
                order_res = self._order_repo.get_orders_for_customer(
                    authenticated_customer_id=authenticated_customer_id,
                    order_id=args.order_id,
                )
                body = {
                    "tool": TOOL_GET_ORDERS,
                    **order_res.model_dump(mode="json"),
                }
                if order_res.not_found_for_authenticated_customer:
                    body["security_notice"] = (
                        "No order with this ID exists under the authenticated customer's account. "
                        "Inform the customer politely that this order was not found in their "
                        "account and offer to list their own orders."
                    )
                return ToolExecutionOutcome(
                    tool_name=tool_name,
                    json_payload=json.dumps(body, ensure_ascii=False),
                )

            if tool_name == TOOL_ESCALATE_HUMAN:
                args = EscalateToHumanArgs.model_validate(parsed_dict)
                ticket = self.execute_escalation(
                    reason_category=args.reason_category,
                    detected_sentiment=args.detected_sentiment,
                    conversation_summary=args.conversation_summary,
                    now=now,
                )
                body = {
                    "tool": TOOL_ESCALATE_HUMAN,
                    **ticket.model_dump(mode="json"),
                }
                return ToolExecutionOutcome(
                    tool_name=tool_name,
                    json_payload=json.dumps(body, ensure_ascii=False),
                    escalation_ticket=ticket,
                )

            unknown = json.dumps(
                {"error": "UNKNOWN_TOOL", "tool_name": tool_name},
                ensure_ascii=False,
            )
            return ToolExecutionOutcome(tool_name=tool_name, json_payload=unknown)

        except ValidationError as exc:
            val_err = json.dumps(
                {"error": "SCHEMA_VALIDATION_ERROR", "details": exc.errors()},
                ensure_ascii=False,
                default=str,
            )
            return ToolExecutionOutcome(tool_name=tool_name, json_payload=val_err)

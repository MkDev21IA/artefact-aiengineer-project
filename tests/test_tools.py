"""Unit and integration tests for Pydantic tool schemas and JSON `ToolDispatcher`."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from emporio_agent.data.csv_repositories import (
    PolarsCatalogRepository,
    PolarsOrderRepository,
)
from emporio_agent.domain.business_hours import BusinessHoursService
from emporio_agent.domain.models import ResolutionAction, StoreStatus
from emporio_agent.rag.policy_store import POLICY_PDF_FILENAME, PolicyVectorStore
from emporio_agent.tools.handlers import ToolDispatcher
from emporio_agent.tools.schemas import (
    TOOL_ESCALATE_HUMAN,
    TOOL_GET_ORDERS,
    TOOL_QUERY_CATALOG,
    TOOL_SEARCH_POLICIES,
    get_openai_tool_definitions,
)

DOCS_DIR = Path(__file__).resolve().parents[1] / "documents"
CG_TZ = ZoneInfo("America/Campo_Grande")


@pytest.fixture(scope="module")
def dispatcher() -> ToolDispatcher:
    return ToolDispatcher(
        catalog_repo=PolarsCatalogRepository(DOCS_DIR),
        order_repo=PolarsOrderRepository(DOCS_DIR),
        policy_store=PolicyVectorStore(DOCS_DIR / POLICY_PDF_FILENAME, ephemeral=True),
        business_hours=BusinessHoursService(),
    )


def test_openai_tool_schemas_never_expose_customer_id_or_pii_parameters() -> None:
    defs = get_openai_tool_definitions()
    assert len(defs) == 4

    orders_def = next(d for d in defs if d["function"]["name"] == TOOL_GET_ORDERS)
    props = orders_def["function"]["parameters"]["properties"]
    assert "customer_id" not in props
    assert "cpf" not in props
    assert "email" not in props
    assert set(props.keys()) == {"order_id"}


def test_dispatcher_returns_strict_json_for_policies_catalog_and_orders(
    dispatcher: ToolDispatcher,
) -> None:
    pol = dispatcher.dispatch(
        TOOL_SEARCH_POLICIES,
        json.dumps({"query": "prazo devolução arrependimento"}),
        authenticated_customer_id=1,
    )
    pol_data = json.loads(pol.json_payload)
    assert pol_data["tool"] == TOOL_SEARCH_POLICIES
    assert len(pol_data["results"]) >= 1

    cat = dispatcher.dispatch(
        TOOL_QUERY_CATALOG,
        json.dumps({"query": "Takamine GD20"}),
        authenticated_customer_id=1,
    )
    cat_data = json.loads(cat.json_payload)
    assert cat_data["items"][0]["product_id"] == 95
    assert cat_data["items"][0]["list_price_brl"] == 2199.0

    # Extra injected `customer_id` in LLM JSON must fail strict `extra="forbid"` validation!
    forged = dispatcher.dispatch(
        TOOL_GET_ORDERS,
        json.dumps({"order_id": 1, "customer_id": 3}),
        authenticated_customer_id=1,
    )
    forged_data = json.loads(forged.json_payload)
    assert forged_data["error"] == "SCHEMA_VALIDATION_ERROR"


def test_escalation_tool_differentiates_open_live_transfer_vs_closed_ticket(
    dispatcher: ToolDispatcher,
) -> None:
    # Tuesday 14:00 AMT -> Store OPEN -> TRANSFERRED_TO_HUMAN
    open_dt = datetime(2026, 11, 3, 14, 0, tzinfo=CG_TZ)
    open_out = dispatcher.dispatch(
        TOOL_ESCALATE_HUMAN,
        json.dumps(
            {
                "reason_category": "CUSTOMER_REQUEST",
                "detected_sentiment": "NEUTRAL",
                "conversation_summary": "Customer asked to speak with a specialist about setup.",
            }
        ),
        authenticated_customer_id=1,
        now=open_dt,
    )
    open_json = json.loads(open_out.json_payload)
    assert open_json["store_status"] == StoreStatus.OPEN
    assert open_json["resolution_action"] == ResolutionAction.TRANSFERRED_TO_HUMAN
    assert open_json["protocol_id"].startswith("EMP-20261103-")

    # Tuesday 21:30 AMT -> Store CLOSED -> TICKET_CREATED
    closed_dt = datetime(2026, 11, 3, 21, 30, tzinfo=CG_TZ)
    closed_out = dispatcher.dispatch(
        TOOL_ESCALATE_HUMAN,
        json.dumps(
            {
                "reason_category": "FRUSTRATION_OR_PROFANITY",
                "detected_sentiment": "FRUSTRATED",
                "conversation_summary": "Customer frustrated outside business hours.",
            }
        ),
        authenticated_customer_id=1,
        now=closed_dt,
    )
    closed_json = json.loads(closed_out.json_payload)
    assert closed_json["store_status"] == StoreStatus.CLOSED
    assert closed_json["resolution_action"] == ResolutionAction.TICKET_CREATED
    assert "amanhã" in closed_json["next_opening_pt_br"]
    assert len(dispatcher.escalation_telemetry) >= 2

"""Strict Pydantic v2 tool argument schemas for OpenAI function calling.

Security invariant (Zero-Trust Identity Isolation):
`GetCustomerOrdersArgs` deliberately omits `customer_id`, `cpf`, `email`, and `name`.
The authenticated customer identity is injected exclusively by the Python backend.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from emporio_agent.domain.models import DetectedSentiment, EscalationReasonCategory

TOOL_SEARCH_POLICIES = "search_store_policies"
TOOL_QUERY_CATALOG = "query_product_catalog"
TOOL_GET_ORDERS = "get_customer_orders"
TOOL_ESCALATE_HUMAN = "escalate_to_human"


class SearchStorePoliciesArgs(BaseModel):
    """Arguments for searching Emporio da Musica's official store policies PDF."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        ...,
        min_length=2,
        description=(
            "Search query in Portuguese about store policies: business hours, physical "
            "address/contacts, payment & installment rules, PIX discount, exchanges/returns, "
            "7-day online regret, warranty, shipping/CEP carriers, promotions, or accessories."
        ),
    )
    top_k: int = Field(
        default=3,
        ge=1,
        le=6,
        description="Number of relevant policy sections to retrieve.",
    )


class QueryProductCatalogArgs(BaseModel):
    """Arguments for querying musical instruments, stock, categories, and promotions."""

    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(
        default=None,
        description=(
            "Search keywords in Portuguese (instrument type, brand, model, wood, or strings, "
            "e.g. 'violões', 'Takamine GD20', 'guitarra Fender', 'ukulele concert', 'saxofone')."
        ),
    )
    category: str | None = Field(
        default=None,
        description=(
            "Optional category name filter (e.g., 'Guitarras', 'Baixos', "
            "'Baterias e Percussão', 'Teclados e Pianos', 'Violões', 'Ukuleles')."
        ),
    )
    min_price_brl: float | None = Field(
        default=None,
        ge=0,
        description="Optional minimum effective price in BRL.",
    )
    max_price_brl: float | None = Field(
        default=None,
        ge=0,
        description="Optional maximum effective price in BRL.",
    )
    only_available: bool = Field(
        default=False,
        description="Set True when the customer asks exclusively for available/in-stock options.",
    )
    only_active_promotions: bool = Field(
        default=False,
        description="Set True when the customer asks which instruments have active promotions.",
    )
    limit: int = Field(
        default=8,
        ge=1,
        le=20,
        description="Maximum number of matching catalog items to return.",
    )


class GetCustomerOrdersArgs(BaseModel):
    """Arguments for retrieving orders belonging ONLY to the authenticated customer."""

    model_config = ConfigDict(extra="forbid")

    order_id: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Optional specific order number requested by the customer. "
            "Omit to list all orders belonging to the authenticated session customer."
        ),
    )


class EscalateToHumanArgs(BaseModel):
    """Arguments for halting bot resolution and escalating to human support / offline ticket."""

    model_config = ConfigDict(extra="forbid")

    reason_category: EscalationReasonCategory = Field(
        ...,
        description="Structured business metric reason explaining why escalation was triggered.",
    )
    detected_sentiment: DetectedSentiment = Field(
        ...,
        description="Detected emotional tone of the customer at the moment of escalation.",
    )
    conversation_summary: str = Field(
        ...,
        min_length=5,
        max_length=500,
        description="Concise summary of the customer's issue and context for the human agent.",
    )


def _pydantic_to_openai_tool(
    name: str,
    description: str,
    model_cls: type[BaseModel],
) -> dict[str, Any]:
    schema = model_cls.model_json_schema()
    schema.pop("title", None)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": schema,
        },
    }


def get_openai_tool_definitions() -> list[dict[str, Any]]:
    """Return the complete list of OpenAI function-calling tool definitions."""
    return [
        _pydantic_to_openai_tool(
            name=TOOL_SEARCH_POLICIES,
            description=(
                "Search Emporio da Musica's official store policy manual (PDF) for rules on "
                "business hours, store address/contacts, payment methods, credit card installment "
                "minimums, PIX rules, shipping/carriers, exchanges, 7-day online regret returns, "
                "warranty coverage, promotional rules, and non-instrument accessory policies."
            ),
            model_cls=SearchStorePoliciesArgs,
        ),
        _pydantic_to_openai_tool(
            name=TOOL_QUERY_CATALOG,
            description=(
                "Query the structured musical instrument catalog (CSV/Polars) for exact prices, "
                "stock availability, technical specs, active vs. expired promotions, "
                "non-cumulative PIX prices, and in-stock alternatives for unavailable models. "
                "Always call this tool before stating any product price, discount, or stock status."
            ),
            model_cls=QueryProductCatalogArgs,
        ),
        _pydantic_to_openai_tool(
            name=TOOL_GET_ORDERS,
            description=(
                "Retrieve order status, payment method, line items, tracking code, estimated "
                "delivery, and cancellation notes strictly for the currently authenticated "
                "customer session."
            ),
            model_cls=GetCustomerOrdersArgs,
        ),
        _pydantic_to_openai_tool(
            name=TOOL_ESCALATE_HUMAN,
            description=(
                "Immediately stop automated troubleshooting and transfer to a human specialist "
                "(when the store is OPEN) or register a prioritized support ticket (when CLOSED). "
                "Invoke immediately upon customer request, profanity, impatience, complaints, or "
                "excessive message repetition."
            ),
            model_cls=EscalateToHumanArgs,
        ),
    ]

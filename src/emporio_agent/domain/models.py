"""Strongly typed Pydantic v2 domain entities and tool payload models."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StoreStatus(StrEnum):
    """Operational availability of live human support and physical store."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"


class StoreOperationalSnapshot(BaseModel):
    """Point-in-time operational context injected into the dynamic System Prompt."""

    model_config = ConfigDict(frozen=True)

    current_iso_datetime: str
    formatted_datetime_pt_br: str
    day_of_week_en: str
    day_of_week_pt_br: str
    timezone: str
    status: StoreStatus
    is_holiday: bool = False
    holiday_name: str | None = None
    status_reason: str
    next_opening_pt_br: str
    schedule_summary: str


class ProductStatus(StrEnum):
    """Lifecycle status values observed in `products.csv`."""

    ACTIVE = "active"
    DISCONTINUED = "discontinued"
    COMING_SOON = "coming_soon"


class PromotionSummary(BaseModel):
    """Promotional campaign metadata associated with a catalog product."""

    model_config = ConfigDict(frozen=True)

    promotion_id: int
    discount_percent: float = Field(ge=0, le=100)
    description: str
    is_active: bool


class ProductCatalogItem(BaseModel):
    """Enriched musical instrument record combining product, category, and pricing rules."""

    model_config = ConfigDict(frozen=True)

    product_id: int
    name: str
    category_id: int
    category_name: str
    description: str
    specs: dict[str, str] = Field(default_factory=dict)
    status: ProductStatus
    stock_quantity: int = Field(ge=0)
    is_available_for_sale: bool
    list_price_brl: float = Field(ge=0)
    active_promotion: PromotionSummary | None = None
    expired_promotions: list[PromotionSummary] = Field(default_factory=list)
    effective_price_brl: float = Field(ge=0)
    pix_price_brl: float = Field(ge=0)
    pix_discount_applicable: bool


class CustomerProfile(BaseModel):
    """Internal customer record kept inside the backend session layer (never sent raw to LLM)."""

    model_config = ConfigDict(frozen=True)

    customer_id: int
    name: str
    first_name: str
    phone: str
    email: str
    city: str


class OrderStatus(StrEnum):
    """Order lifecycle status values observed in `orders.csv`."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class OrderLineItem(BaseModel):
    """Individual musical instrument line item inside a customer order."""

    model_config = ConfigDict(frozen=True)

    product_id: int
    product_name: str
    quantity: int = Field(gt=0)
    unit_list_price_brl: float = Field(ge=0)


class CustomerOrder(BaseModel):
    """Sanitized order view scoped strictly to the authenticated session customer."""

    model_config = ConfigDict(frozen=True)

    order_id: int
    customer_id: int
    order_date: str
    status: OrderStatus
    total_brl: float = Field(ge=0)
    payment_method: str
    tracking_code: str | None = None
    estimated_delivery: str | None = None
    notes: str | None = None
    items: list[OrderLineItem] = Field(default_factory=list)


class PolicyChunk(BaseModel):
    """Sanitized store policy fragment retrieved from ChromaDB."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    section_title: str
    page_number: int = Field(ge=1)
    content: str
    distance: float | None = None


class EscalationReasonCategory(StrEnum):
    """Structured business metric categories for human handoff and offline tickets."""

    CUSTOMER_REQUEST = "CUSTOMER_REQUEST"
    FRUSTRATION_OR_PROFANITY = "FRUSTRATION_OR_PROFANITY"
    EXCESSIVE_REPETITION = "EXCESSIVE_REPETITION"
    COMPLAINT = "COMPLAINT"
    POLICY_AMBIGUITY_OR_COMPLEX_CASE = "POLICY_AMBIGUITY_OR_COMPLEX_CASE"


class DetectedSentiment(StrEnum):
    """Customer emotional tone classification captured at escalation time."""

    NEUTRAL = "NEUTRAL"
    IMPATIENT = "IMPATIENT"
    FRUSTRATED = "FRUSTRATED"
    ANGRY_OR_PROFANE = "ANGRY_OR_PROFANE"


class ResolutionAction(StrEnum):
    """Deterministic escalation outcome governed by store business hours."""

    TRANSFERRED_TO_HUMAN = "TRANSFERRED_TO_HUMAN"
    TICKET_CREATED = "TICKET_CREATED"


class EscalationTicketPayload(BaseModel):
    """Structured JSON telemetry returned when `escalate_to_human` is invoked."""

    model_config = ConfigDict(frozen=True)

    protocol_id: str
    timestamp_iso: str
    store_status: StoreStatus
    resolution_action: ResolutionAction
    reason_category: EscalationReasonCategory
    detected_sentiment: DetectedSentiment
    conversation_summary: str
    next_opening_pt_br: str
    customer_guidance_pt_br: str

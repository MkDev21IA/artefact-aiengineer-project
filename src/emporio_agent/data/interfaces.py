"""Abstract repository interfaces decoupling agent logic from underlying storage.

In production, these protocols allow swapping local CSV files (`documents/`) for a
cloud Data Lake / Data Warehouse (BigQuery, Snowflake, Databricks, PostgreSQL)
without modifying orchestrator or tool code.
"""

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from emporio_agent.domain.models import (
    CustomerOrder,
    CustomerProfile,
    ProductCatalogItem,
)


class CatalogSearchResult(BaseModel):
    """Structured result returned by `CatalogRepository.search_products`."""

    model_config = ConfigDict(frozen=True)

    items: list[ProductCatalogItem] = Field(default_factory=list)
    suggested_alternatives: list[ProductCatalogItem] = Field(default_factory=list)
    matched_empty_categories: list[str] = Field(default_factory=list)
    total_matches: int = 0


class OrderLookupResult(BaseModel):
    """Zero-trust result for authenticated customer order queries."""

    model_config = ConfigDict(frozen=True)

    orders: list[CustomerOrder] = Field(default_factory=list)
    requested_order_id: int | None = None
    not_found_for_authenticated_customer: bool = False


class CatalogRepository(Protocol):
    """Read-only interface for public product catalog, categories, and promotions."""

    def search_products(
        self,
        query: str | None = None,
        category: str | None = None,
        min_price_brl: float | None = None,
        max_price_brl: float | None = None,
        only_available: bool = False,
        only_active_promotions: bool = False,
        limit: int = 10,
    ) -> CatalogSearchResult:
        """Search instruments with deterministic pricing, promotion, and stock logic."""
        ...

    def get_product_by_id(self, product_id: int) -> ProductCatalogItem | None:
        """Retrieve a single enriched catalog item by `product_id`."""
        ...

    def list_categories(self) -> list[dict[str, object]]:
        """List all store musical instrument categories and active in-stock counts."""
        ...


class OrderRepository(Protocol):
    """Zero-trust interface for private customer order queries."""

    def get_orders_for_customer(
        self,
        authenticated_customer_id: int,
        order_id: int | None = None,
    ) -> OrderLookupResult:
        """Return orders strictly belonging to `authenticated_customer_id`."""
        ...


class CustomerRepository(Protocol):
    """Backend-only customer profile lookup (never exposed directly to LLM tools)."""

    def get_customer_by_id(self, customer_id: int) -> CustomerProfile | None:
        """Retrieve internal customer profile to initialize session PII vault."""
        ...

"""Tests for Polars repositories, dataset anomalies, promotion math, and Zero-Trust isolation."""

from pathlib import Path

import pytest

from emporio_agent.data.csv_repositories import (
    PolarsCatalogRepository,
    PolarsCustomerRepository,
    PolarsOrderRepository,
)
from emporio_agent.domain.models import OrderStatus, ProductStatus

DOCS_DIR = Path(__file__).resolve().parents[1] / "documents"


@pytest.fixture(scope="module")
def catalog_repo() -> PolarsCatalogRepository:
    return PolarsCatalogRepository(DOCS_DIR)


@pytest.fixture(scope="module")
def order_repo() -> PolarsOrderRepository:
    return PolarsOrderRepository(DOCS_DIR)


@pytest.fixture(scope="module")
def customer_repo() -> PolarsCustomerRepository:
    return PolarsCustomerRepository(DOCS_DIR)


def test_acoustic_guitars_available_up_to_1000_brl(catalog_repo: PolarsCatalogRepository) -> None:
    # Tests Challenge Scenario #1: "Quais opções de violões disponíveis custando até R$1000?"
    res = catalog_repo.search_products(
        query="violões",
        max_price_brl=1000.0,
        only_available=True,
        limit=20,
    )
    assert res.total_matches >= 8
    for item in res.items:
        assert item.category_name == "Violões"
        assert item.is_available_for_sale is True
        assert item.effective_price_brl <= 1000.0
    # Product 96 (stock 0) and Product 113 (discontinued) must NEVER appear in available list
    returned_ids = {it.product_id for it in res.items}
    assert 96 not in returned_ids
    assert 113 not in returned_ids


def test_takamine_gd20_exact_price_and_pix_discount(catalog_repo: PolarsCatalogRepository) -> None:
    # Directly tests Challenge Scenario #3: "Quanto custa o Takamine GD20?"
    res = catalog_repo.search_products(query="Takamine GD20")
    assert res.items
    gd20 = res.items[0]
    assert gd20.product_id == 95
    assert gd20.list_price_brl == 2199.0
    assert gd20.active_promotion is None
    assert len(gd20.expired_promotions) == 1  # Expired "Oferta Especial" 12%
    assert gd20.effective_price_brl == 2199.0
    assert gd20.pix_discount_applicable is True
    assert gd20.pix_price_brl == round(2199.0 * 0.95, 2)


def test_active_promotion_prevents_pix_stacking_per_policy_section_6_2(
    catalog_repo: PolarsCatalogRepository,
) -> None:
    # Product 94 (Taylor 110e) has an active 8% promotion (promotion_id=24)
    taylor = catalog_repo.get_product_by_id(94)
    assert taylor is not None
    assert taylor.active_promotion is not None
    assert taylor.active_promotion.discount_percent == 8.0
    assert taylor.list_price_brl == 5999.0
    assert taylor.effective_price_brl == 5519.08
    # PIX 5% must NOT stack on already discounted promotional price
    assert taylor.pix_discount_applicable is False
    assert taylor.pix_price_brl == 5519.08


def test_discontinued_product_with_positive_stock_is_not_available_and_suggests_alternatives(
    catalog_repo: PolarsCatalogRepository,
) -> None:
    # Trap in products.csv: product_id=136 has stock_quantity=13 BUT status="discontinued"
    res = catalog_repo.search_products(query="Ibanez Bass 2X")
    assert res.items
    ibanez_bass = res.items[0]
    assert ibanez_bass.product_id == 136
    assert ibanez_bass.status == ProductStatus.DISCONTINUED
    assert ibanez_bass.stock_quantity == 13
    assert ibanez_bass.is_available_for_sale is False
    # Repository must automatically suggest active in-stock basses (category_id=2)
    assert len(res.suggested_alternatives) >= 1
    assert all(alt.is_available_for_sale for alt in res.suggested_alternatives)
    assert all(alt.category_id == 2 for alt in res.suggested_alternatives)


def test_out_of_stock_and_coming_soon_products_flag_unavailability_with_alternatives(
    catalog_repo: PolarsCatalogRepository,
) -> None:
    out_of_stock = catalog_repo.search_products(query="Giannini GF-3D")
    assert out_of_stock.items[0].product_id == 96
    assert out_of_stock.items[0].is_available_for_sale is False
    assert len(out_of_stock.suggested_alternatives) >= 1

    coming_soon = catalog_repo.search_products(query="Kala KA-ABP-CTG")
    assert coming_soon.items[0].product_id == 130
    assert coming_soon.items[0].status == ProductStatus.COMING_SOON
    assert coming_soon.items[0].is_available_for_sale is False


def test_empty_wind_and_orchestral_categories_are_explicitly_reported(
    catalog_repo: PolarsCatalogRepository,
) -> None:
    # Categories 6, 7, 8 exist in categories.csv with 0 products in products.csv
    sax = catalog_repo.search_products(query="saxofone")
    assert sax.items == []
    assert "Instrumentos de Sopro (Madeiras)" in sax.matched_empty_categories

    violin = catalog_repo.search_products(query="violinos")
    assert violin.items == []
    assert "Cordas Orquestrais" in violin.matched_empty_categories


def test_customer_order_isolation_blocks_cross_customer_idor(
    order_repo: PolarsOrderRepository,
) -> None:
    # Order #1 belongs to customer_id=3 (Pedro Henrique Oliveira), NOT customer_id=1 (Lucas)
    lucas_snooping = order_repo.get_orders_for_customer(authenticated_customer_id=1, order_id=1)
    assert lucas_snooping.orders == []
    assert lucas_snooping.not_found_for_authenticated_customer is True

    # Customer #1 querying their own orders only gets Order #4
    lucas_own = order_repo.get_orders_for_customer(authenticated_customer_id=1)
    assert len(lucas_own.orders) == 1
    assert lucas_own.orders[0].order_id == 4
    assert lucas_own.orders[0].customer_id == 1
    assert lucas_own.orders[0].items[0].product_name == "Giannini GF-3D Dreadnought Sunburst"


def test_multi_order_and_multi_item_enrichment(
    order_repo: PolarsOrderRepository,
    customer_repo: PolarsCustomerRepository,
) -> None:
    # Customer #7 (Thiago) has two orders: #2 (delivered) and #20 (cancelled, 2 items)
    thiago_orders = order_repo.get_orders_for_customer(authenticated_customer_id=7)
    assert len(thiago_orders.orders) == 2

    order_20 = next(o for o in thiago_orders.orders if o.order_id == 20)
    assert order_20.status == OrderStatus.CANCELLED
    assert order_20.notes == "Endereço de entrega inválido informado pelo cliente"
    assert len(order_20.items) == 2

    profile = customer_repo.get_customer_by_id(7)
    assert profile is not None
    assert profile.first_name == "Thiago"
    assert profile.city == "Campo Grande"

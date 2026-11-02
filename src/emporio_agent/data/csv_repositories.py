"""Polars-backed CSV implementations of Catalog, Order, and Customer repositories."""

import json
import re
import unicodedata
from pathlib import Path

import polars as pl

from emporio_agent.data.interfaces import CatalogSearchResult, OrderLookupResult
from emporio_agent.domain.models import (
    CustomerOrder,
    CustomerProfile,
    OrderLineItem,
    OrderStatus,
    ProductCatalogItem,
    ProductStatus,
    PromotionSummary,
)

CATEGORIES_FILENAME = "desafio_tecnico_ai_eng - categories.csv"
PRODUCTS_FILENAME = "desafio_tecnico_ai_eng - products.csv"
PROMOTIONS_FILENAME = "desafio_tecnico_ai_eng - promotions.csv"
CUSTOMERS_FILENAME = "desafio_tecnico_ai_eng - customers.csv"
ORDERS_FILENAME = "desafio_tecnico_ai_eng - orders.csv"
ORDER_ITEMS_FILENAME = "desafio_tecnico_ai_eng - order_items.csv"

STOPWORDS_PT: frozenset[str] = frozenset(
    {
        "a",
        "as",
        "o",
        "os",
        "de",
        "da",
        "das",
        "do",
        "dos",
        "e",
        "em",
        "para",
        "com",
        "por",
        "um",
        "uma",
        "uns",
        "umas",
        "tem",
        "quero",
        "opcao",
        "opcoes",
        "instrumento",
        "instrumentos",
        "modelo",
        "modelos",
    }
)


def normalize_pt_text(value: str) -> str:
    """Strip accents, lowercase, and collapse whitespace for resilient PT-BR matching."""
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9\s-]", " ", ascii_only.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def stem_pt_token(token: str) -> str:
    """Normalize common Portuguese plural endings (`violões` -> `violao`, `baixos` -> `baixo`)."""
    if len(token) <= 3:
        return token
    if token.endswith("oes"):
        return f"{token[:-3]}ao"
    if token.endswith("aes"):
        return f"{token[:-3]}ao"
    if token.endswith("ais") and len(token) > 4:
        return f"{token[:-3]}al"
    if token.endswith("eis") and len(token) > 4:
        return f"{token[:-3]}el"
    if token.endswith("res") and len(token) > 4:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and not token.isdigit():
        return token[:-1]
    return token


def tokenize_search_query(query: str) -> list[str]:
    """Extract normalized, stemmed search tokens excluding trivial Portuguese stopwords."""
    norm = normalize_pt_text(query)
    tokens: list[str] = []
    for raw_tok in norm.split():
        if raw_tok in STOPWORDS_PT:
            continue
        stemmed = stem_pt_token(raw_tok)
        if stemmed and stemmed not in STOPWORDS_PT:
            tokens.append(stemmed)
    return tokens


PRIMARY_INSTRUMENT_CATEGORY_MAP: dict[str, int] = {
    "guitarra": 1,
    "baixo": 2,
    "contrabaixo": 2,
    "bateria": 3,
    "percussao": 3,
    "teclado": 4,
    "piano": 4,
    "sintetizador": 4,
    "violao": 5,
    "ukulele": 9,
}


def _token_matches_blob(tok: str, blob_words: set[str], blob_str: str) -> bool:
    """Match whole stemmed/raw words, falling back to prefix match for model numbers."""
    if tok in blob_words:
        return True
    if any(ch.isdigit() for ch in tok) and tok in blob_str:
        return True
    return any(len(tok) >= 4 and w.startswith(tok) for w in blob_words)
def _parse_specs_json(raw_specs: str | None) -> dict[str, str]:
    if not raw_specs:
        return {}
    try:
        parsed = json.loads(raw_specs)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
    except json.JSONDecodeError:
        pass
    return {}


class PolarsCatalogRepository:
    """Fast, strictly typed Polars adapter for products, categories, and promotions."""

    def __init__(self, documents_dir: Path) -> None:
        self._documents_dir = documents_dir
        self._categories_df = pl.read_csv(documents_dir / CATEGORIES_FILENAME)
        self._products_df = pl.read_csv(documents_dir / PRODUCTS_FILENAME)
        self._promotions_df = pl.read_csv(documents_dir / PROMOTIONS_FILENAME)

        self._items_by_id: dict[int, ProductCatalogItem] = {}
        self._search_blobs_by_id: dict[int, str] = {}
        self._category_search_blobs: list[tuple[int, str, str]] = []
        self._build_enriched_catalog()

    def _build_enriched_catalog(self) -> None:
        """Join products, categories, and grouped promotions with deterministic price math."""
        active_promos: dict[int, PromotionSummary] = {}
        expired_promos: dict[int, list[PromotionSummary]] = {}

        for row in self._promotions_df.iter_rows(named=True):
            pid = int(row["product_id"])
            is_act = bool(int(row["is_active"]))
            summary = PromotionSummary(
                promotion_id=int(row["promotion_id"]),
                discount_percent=float(row["discount_percent"]),
                description=str(row["description"]),
                is_active=is_act,
            )
            if is_act:
                # Keep highest discount if multiple active promotions ever exist
                current = active_promos.get(pid)
                if current is None or summary.discount_percent > current.discount_percent:
                    active_promos[pid] = summary
            else:
                expired_promos.setdefault(pid, []).append(summary)

        joined_df = self._products_df.join(
            self._categories_df.rename({"name": "category_name", "description": "category_desc"}),
            on="category_id",
            how="left",
        ).sort("price_brl")

        for cat_row in self._categories_df.iter_rows(named=True):
            cid = int(cat_row["category_id"])
            cname = str(cat_row["name"])
            cdesc = str(cat_row["description"] or "")
            raw_cat_blob = normalize_pt_text(f"{cname} {cdesc}")
            stemmed_cat_blob = " ".join(stem_pt_token(t) for t in raw_cat_blob.split())
            self._category_search_blobs.append((cid, cname, f"{raw_cat_blob} {stemmed_cat_blob}"))

        for row in joined_df.iter_rows(named=True):
            pid = int(row["product_id"])
            status = ProductStatus(str(row["status"]).strip())
            stock = int(row["stock_quantity"])
            list_price = round(float(row["price_brl"]), 2)
            is_available = status == ProductStatus.ACTIVE and stock > 0

            active_promo = active_promos.get(pid) if status == ProductStatus.ACTIVE else None
            expired_list = expired_promos.get(pid, [])

            if active_promo is not None:
                effective_price = round(
                    list_price * (1.0 - active_promo.discount_percent / 100.0),
                    2,
                )
                pix_applicable = False
                pix_price = effective_price
            else:
                effective_price = list_price
                pix_applicable = True
                pix_price = round(list_price * 0.95, 2)

            specs_dict = _parse_specs_json(row.get("specs"))
            cname = str(row.get("category_name") or "Outros")
            desc = str(row.get("description") or "")
            name = str(row["name"])

            item = ProductCatalogItem(
                product_id=pid,
                name=name,
                category_id=int(row["category_id"]),
                category_name=cname,
                description=desc,
                specs=specs_dict,
                status=status,
                stock_quantity=stock,
                is_available_for_sale=is_available,
                list_price_brl=list_price,
                active_promotion=active_promo,
                expired_promotions=expired_list,
                effective_price_brl=effective_price,
                pix_price_brl=pix_price,
                pix_discount_applicable=pix_applicable,
            )
            self._items_by_id[pid] = item

            specs_str = " ".join(f"{k} {v}" for k, v in specs_dict.items())
            promo_names = " ".join(
                p.description for p in ([active_promo] if active_promo else []) + expired_list
            )
            raw_blob = normalize_pt_text(f"{name} {cname} {desc} {specs_str} {promo_names}")
            stemmed_blob = " ".join(stem_pt_token(t) for t in raw_blob.split())
            self._search_blobs_by_id[pid] = f"{raw_blob} {stemmed_blob}"

    def get_product_by_id(self, product_id: int) -> ProductCatalogItem | None:
        """Retrieve a single enriched catalog item by `product_id`."""
        return self._items_by_id.get(product_id)

    def list_categories(self) -> list[dict[str, object]]:
        """List all categories with total product counts and active in-stock counts."""
        counts_by_cat: dict[int, dict[str, int]] = {}
        for item in self._items_by_id.values():
            bucket = counts_by_cat.setdefault(item.category_id, {"total": 0, "available": 0})
            bucket["total"] += 1
            if item.is_available_for_sale:
                bucket["available"] += 1

        categories_out: list[dict[str, object]] = []
        for row in self._categories_df.sort("category_id").iter_rows(named=True):
            cid = int(row["category_id"])
            stats = counts_by_cat.get(cid, {"total": 0, "available": 0})
            categories_out.append(
                {
                    "category_id": cid,
                    "name": str(row["name"]),
                    "description": str(row["description"]),
                    "total_registered_products": stats["total"],
                    "available_in_stock_products": stats["available"],
                }
            )
        return categories_out

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
        """Search instruments with deterministic pricing, promotions, and alternatives."""
        query_tokens = tokenize_search_query(query) if query else []
        category_tokens = tokenize_search_query(category) if category else []

        inferred_cat_ids: set[int] = set()
        if not category_tokens and query_tokens:
            for tok in query_tokens:
                mapped_cid = PRIMARY_INSTRUMENT_CATEGORY_MAP.get(tok)
                if mapped_cid is not None:
                    inferred_cat_ids.add(mapped_cid)

        scored_matches: list[tuple[int, ProductCatalogItem]] = []
        for pid, item in self._items_by_id.items():
            if only_available and not item.is_available_for_sale:
                continue
            if only_active_promotions and item.active_promotion is None:
                continue
            if min_price_brl is not None and item.effective_price_brl < min_price_brl:
                continue
            if max_price_brl is not None and item.effective_price_brl > max_price_brl:
                continue
            if inferred_cat_ids and item.category_id not in inferred_cat_ids:
                continue

            if category_tokens:
                cat_norm = normalize_pt_text(item.category_name)
                cat_words = set(cat_norm.split()) | {stem_pt_token(t) for t in cat_norm.split()}
                if not all(
                    _token_matches_blob(tok, cat_words, cat_norm) for tok in category_tokens
                ):
                    continue

            blob = self._search_blobs_by_id[pid]
            blob_words = set(blob.split())
            if query_tokens:
                matched_count = sum(
                    1 for tok in query_tokens if _token_matches_blob(tok, blob_words, blob)
                )
                if matched_count == 0:
                    continue
                # Require all tokens when <= 2 tokens; otherwise require majority match
                required = len(query_tokens) if len(query_tokens) <= 2 else len(query_tokens) - 1
                if matched_count < required:
                    continue
                # Boost exact model/name matches over description-only matches
                name_blob = normalize_pt_text(item.name)
                name_words = set(name_blob.split()) | {
                    stem_pt_token(t) for t in name_blob.split()
                }
                name_hits = sum(
                    2 for tok in query_tokens if _token_matches_blob(tok, name_words, name_blob)
                )
                score = matched_count * 10 + name_hits + (1 if item.is_available_for_sale else 0)
            else:
                score = 1 if item.is_available_for_sale else 0

            scored_matches.append((score, item))

        scored_matches.sort(key=lambda pair: (-pair[0], pair[1].effective_price_brl))
        all_matched_items = [pair[1] for pair in scored_matches]
        top_items = all_matched_items[: max(1, limit)]

        # Detect queries targeting registered categories that currently have 0 products (6, 7, 8)
        empty_cats: list[str] = []
        combined_tokens = query_tokens + category_tokens
        if combined_tokens and not top_items:
            product_cat_ids = {it.category_id for it in self._items_by_id.values()}
            for cid, cname, cat_blob in self._category_search_blobs:
                if cid not in product_cat_ids and any(tok in cat_blob for tok in combined_tokens):
                    empty_cats.append(cname)

        # Attach in-stock active alternatives if any top match is unavailable/discontinued
        alternatives: list[ProductCatalogItem] = []
        unavailable_targets = [it for it in top_items if not it.is_available_for_sale]
        if unavailable_targets:
            target = unavailable_targets[0]
            returned_ids = {it.product_id for it in top_items}
            candidates = [
                it
                for it in self._items_by_id.values()
                if it.category_id == target.category_id
                and it.is_available_for_sale
                and it.product_id not in returned_ids
            ]
            candidates.sort(key=lambda it: abs(it.effective_price_brl - target.effective_price_brl))
            alternatives = candidates[:3]

        return CatalogSearchResult(
            items=top_items,
            suggested_alternatives=alternatives,
            matched_empty_categories=empty_cats,
            total_matches=len(all_matched_items),
        )


class PolarsOrderRepository:
    """Zero-trust Polars adapter scoping all order queries to `authenticated_customer_id`."""

    def __init__(self, documents_dir: Path) -> None:
        self._orders_df = pl.read_csv(
            documents_dir / ORDERS_FILENAME,
            schema_overrides={
                "tracking_code": pl.Utf8,
                "estimated_delivery": pl.Utf8,
                "notes": pl.Utf8,
            },
        )
        self._order_items_df = pl.read_csv(documents_dir / ORDER_ITEMS_FILENAME)
        self._products_df = pl.read_csv(documents_dir / PRODUCTS_FILENAME).select(
            ["product_id", "name", "price_brl"]
        )

    def get_orders_for_customer(
        self,
        authenticated_customer_id: int,
        order_id: int | None = None,
    ) -> OrderLookupResult:
        """Return orders strictly filtered by `authenticated_customer_id` in Polars."""
        predicate = pl.col("customer_id") == authenticated_customer_id
        if order_id is not None:
            predicate = predicate & (pl.col("order_id") == order_id)

        filtered_orders = self._orders_df.filter(predicate).sort("order_date", descending=True)

        if filtered_orders.is_empty():
            return OrderLookupResult(
                orders=[],
                requested_order_id=order_id,
                not_found_for_authenticated_customer=order_id is not None,
            )

        matched_order_ids = filtered_orders["order_id"].to_list()
        enriched_items_df = (
            self._order_items_df.filter(pl.col("order_id").is_in(matched_order_ids))
            .join(self._products_df, on="product_id", how="left")
            .sort("order_id")
        )

        line_items_by_order: dict[int, list[OrderLineItem]] = {}
        for item_row in enriched_items_df.iter_rows(named=True):
            oid = int(item_row["order_id"])
            line_items_by_order.setdefault(oid, []).append(
                OrderLineItem(
                    product_id=int(item_row["product_id"]),
                    product_name=str(item_row.get("name") or f"Product #{item_row['product_id']}"),
                    quantity=int(item_row["quantity"]),
                    unit_list_price_brl=round(float(item_row.get("price_brl") or 0.0), 2),
                )
            )

        orders_out: list[CustomerOrder] = []
        for row in filtered_orders.iter_rows(named=True):
            oid = int(row["order_id"])
            tracking = str(row["tracking_code"]).strip() if row.get("tracking_code") else None
            est_deliv = (
                str(row["estimated_delivery"]).strip() if row.get("estimated_delivery") else None
            )
            notes = str(row["notes"]).strip() if row.get("notes") else None

            orders_out.append(
                CustomerOrder(
                    order_id=oid,
                    customer_id=int(row["customer_id"]),
                    order_date=str(row["order_date"]),
                    status=OrderStatus(str(row["status"]).strip()),
                    total_brl=round(float(row["total_brl"]), 2),
                    payment_method=str(row["payment_method"]),
                    tracking_code=tracking or None,
                    estimated_delivery=est_deliv or None,
                    notes=notes or None,
                    items=line_items_by_order.get(oid, []),
                )
            )

        return OrderLookupResult(
            orders=orders_out,
            requested_order_id=order_id,
            not_found_for_authenticated_customer=False,
        )


class PolarsCustomerRepository:
    """Internal Polars lookup for customer session binding (`customers.csv`)."""

    def __init__(self, documents_dir: Path) -> None:
        self._customers_df = pl.read_csv(documents_dir / CUSTOMERS_FILENAME)

    def get_customer_by_id(self, customer_id: int) -> CustomerProfile | None:
        """Fetch internal customer record by `customer_id`."""
        matched = self._customers_df.filter(pl.col("customer_id") == customer_id)
        if matched.is_empty():
            return None
        row = matched.row(0, named=True)
        full_name = str(row["name"]).strip()
        first_name = full_name.split()[0] if full_name else "Cliente"
        return CustomerProfile(
            customer_id=int(row["customer_id"]),
            name=full_name,
            first_name=first_name,
            phone=str(row["phone"]).strip(),
            email=str(row["email"]).strip(),
            city=str(row["city"]).strip(),
        )

    def list_sample_customers(self, limit: int = 10) -> list[CustomerProfile]:
        """Return sample customer profiles for CLI demo selection."""
        profiles: list[CustomerProfile] = []
        for row in self._customers_df.head(limit).iter_rows(named=True):
            full_name = str(row["name"]).strip()
            profiles.append(
                CustomerProfile(
                    customer_id=int(row["customer_id"]),
                    name=full_name,
                    first_name=full_name.split()[0] if full_name else "Cliente",
                    phone=str(row["phone"]).strip(),
                    email=str(row["email"]).strip(),
                    city=str(row["city"]).strip(),
                )
            )
        return profiles

# ADR-0004: Store Operational Timezone (`America/Campo_Grande`) and Dataset Anomaly Handling

- **Status:** Accepted
- **Date:** 2026-11-02
- **Deciders:** MkDev21IA

## Context

Inspection of the official challenge materials in `documents/` (`políticas_da_loja.pdf` and the six operational CSV tables) surfaced concrete domain rules and intentional dataset edge cases that directly impact customer support accuracy:

1. **Physical Location & Business Hours:**
   - **Empório da Música** is headquartered in **Campo Grande, Mato Grosso do Sul (MS)** (`Rua 14 de Maio, 3200 — Centro`).
   - Store and WhatsApp support hours are Monday–Friday `09:00–18:00`, Saturday `09:00–13:00`, and closed on Sundays/holidays.
   - Mato Grosso do Sul operates in the **`America/Campo_Grande` (AMT, UTC-4)** timezone—one hour behind Brasília (`America/Sao_Paulo`, UTC-3). Using Brasília time would cause a 1-hour drift when opening/closing live human handoff queues.
2. **Dataset Edge Cases & Anomalies:**
   - **Status vs. Stock Mismatch:** Some products carry `stock_quantity > 0` while marked `status="discontinued"` (e.g., `product_id=136`) or `status="coming_soon"` (`product_id=130`), whereas active items may have `stock_quantity=0` (`product_id=96`).
   - **Empty Categories:** Categories `6`, `7`, and `8` exist in `categories.csv` with zero rows in `products.csv`.
   - **Active vs. Expired Promotions & Non-Cumulativity:** Only 4 of 25 rows in `promotions.csv` have `is_active=1`. Per store policy (§6.2, §7.3), expired promotions must never be honored, promotional prices must display original price + discount %, and the 5% PIX discount does not stack on promotional items.
   - **Synthetic Text Mismatches:** Products `135–144` contain synthetic brand/spec discrepancies between `name`, `description`, and `specs`.
   - **Musical Accessories Scope:** Store policy (§1, §7.1) explicitly states the store sells **only musical instruments** and does **not** sell accessories (strings, picks, cables, cases, pedals, amplifiers), requiring polite redirection rather than generic off-topic blocking.

## Decision

1. **Default Operational Timezone:** Set `America/Campo_Grande` (`zoneinfo.ZoneInfo("America/Campo_Grande")`) as the default store timezone in `config.py` (configurable via `STORE_TIMEZONE`), governing both dynamic System Prompt timestamps and deterministic business-hours checks during human escalation.
2. **Deterministic Catalog & Pricing Calculations in `Polars`:**
   - Compute `is_available_for_sale` strictly as `status == "active" and stock_quantity > 0`, while still returning `discontinued`, `out_of_stock`, and `coming_soon` metadata so the agent can follow §7.3 (explain unavailability and suggest active in-stock alternatives in the same category).
   - Pre-calculate exact promotional prices and PIX-eligible prices (`5%` off only when no active promotion applies) in `Polars` so the LLM never performs unverified arithmetic.
3. **Accessories Treated as In-Scope Policy Redirection:** Configure `semantic-router` so queries about musical accessories pass through to the agent for polite policy-grounded redirection rather than being blocked as unrelated spam.
4. **Version Fictional Challenge Datasets:** Track the fictional CSVs and policy PDF in `documents/` so evaluators can clone the public repository and execute tests and conversations out-of-the-box.

## Consequences

### Positive
- Eliminates 1-hour timezone bugs on live queue transfer vs. offline ticket creation.
- Prevents the LLM from selling discontinued items, honoring expired promotions, miscalculating PIX discounts, or mishandling accessory inquiries.
- Enables zero-friction reproducibility upon `git clone`.

### Negative / Trade-offs
- Evaluators testing from `America/Sao_Paulo` will see Campo Grande local store hours (UTC-4) reflected in timestamps; documented clearly in `README.md` and `.env.example`.

# Example 01 — Product Catalog, Active vs. Expired Promotions, and PIX Non-Cumulativity

Demonstrates real-time `Polars` catalog querying for acoustic guitars up to R$ 1,000 (excluding out-of-stock/discontinued rows), exact pricing for `Takamine GD20`, transparent handling of expired vs. active promotions (`Taylor 110e` 8% off), and enforcement of Policy Section 6.2 (5% PIX discount does not stack on active promos).

---

### Turn 1

**Customer (`#1`):**
> Oi! Quais opções de violões disponíveis vocês têm custando até R$ 1000?

**Observability & Guardrail Trace:**
- **Store Clock (`America/Campo_Grande`):** `03/11/2026 10:15` (`OPEN` — Within weekday business hours (09:00-18:00))
- **Input Validator:** `VALID`
- **LGPD Masked Payload:** `Oi! Quais opções de violões disponíveis vocês têm custando até R$ 1000?`
- **Detected PII Entities:** `[]`
- **Semantic Router:** `ALLOW_IN_SCOPE` (matched route: `in_scope_emporio_store`)
- **Executed Function Calls (Real Backend JSON):**
  - `query_product_catalog({"query": "viol\u00f5es", "max_price_brl": 1000.0, "only_available": true, "limit": 12})`:
```json
{
  "tool": "query_product_catalog",
  "items": [
    {
      "product_id": 83,
      "name": "Tagima Memphis AC-39 Nylon Natural",
      "category_id": 5,
      "category_name": "Violões",
      "description": "Violão clássico com cordas de nylon, perfeito para quem está começando. Corpo leve e confortável para longas sessões de estudo.",
      "specs": {
        "top": "Linden",
        "back_sides": "Linden",
        "neck": "Nato",
        "strings": "nylon",
        "scale": "650mm",
        "electronics": "no",
        "color": "Natural"
      },
      "status": "active",
      "stock_quantity": 15,
      "is_available_for_sale": true,
      "list_price_brl": 429.9,
      "active_promotion": null,
      "expired_promotions": [],
      "effective_price_brl": 429.9,
      "pix_price_brl": 408.4,
      "pix_discount_applicable": true
    },
    {
      "product_id": 85,
      "name": "Rozini RC-104 Nylon Estudante",
      "category_id": 5,
      "category_name": "Violões",
      "description": "Violão clássico de estudo da Rozini, feito com madeiras nacionais. Acabamento caprichado e preço acessível para iniciantes.",
      "specs": {
        "top": "Spruce",
        "back_sides": "Sapele",
        "neck": "Nato",
        "strings": "nylon",
        "scale": "650mm",
        "electronics": "no",
        "color": "Natural"
      },
      "status": "active",
      "stock_quantity": 10,
      "is_available_for_sale": true,
      "list_price_brl": 459.9,
      "active_promotion": null,
      "expired_promotions": [],
      "effective_price_brl": 459.9,
      "pix_price_brl":
  ... (truncated for readability)
}
```

**Empório da Música Assistant (`pt-BR`):**

Olá, Lucas! Temos ótimas opções de violões em estoque até R$ 1.000,00:

**Cordas de Nylon (Clássicos):**
- **Tagima Memphis AC-39 Nylon:** R$ 429,90 (R$ 408,40 no PIX) — 15 un.
- **Rozini RC-104 Nylon Estudante:** R$ 459,90 (R$ 436,90 no PIX) — 10 un.
- **Yamaha C40 Nylon Natural:** R$ 599,90 (R$ 569,90 no PIX) — 12 un.
- **Giannini GN-15 Nylon Cedro:** R$ 689,00 (R$ 654,55 no PIX) — 6 un.
- **Yamaha C70 Nylon (tampo sólido):** R$ 849,00 (R$ 806,55 no PIX) — 8 un.

**Cordas de Aço (Folk / Dreadnought / Eletroacústico / 7 Cordas):**
- **Shelby SN-100 Folk Aço Preto:** R$ 549,00 (R$ 521,55 no PIX) — 5 un.
- **Giannini GF-1R Aço Folk Sunburst:** R$ 579,90 (R$ 550,90 no PIX) — 7 un.
- **Yamaha F310 Aço Natural:** R$ 699,90 (R$ 664,90 no PIX) — 14 un.
- **Tagima Dallas Tuner Aço:** R$ 749,00 (R$ 711,55 no PIX) — 9 un.
- **Shelby SGD-195E Eletroacústico:** R$ 799,00 (R$ 759,05 no PIX) — 6 un.
- **Tagima Woodstock Dreadnought:** R$ 899,00 (R$ 854,05 no PIX) — 11 un.
- **Tagima TW-7 (7 Cordas Aço):** R$ 999,00 (R$ 949,05 no PIX) — 6 un.

Você prefere cordas de nylon (mais macias para começar) ou de aço?

---

### Turn 2

**Customer (`#1`):**
> Show! E subindo o orçamento: quanto custa o Takamine GD20 e o Taylor 110e pagando à vista no PIX? A promoção de Oferta Especial do Takamine ainda está valendo?

**Observability & Guardrail Trace:**
- **Store Clock (`America/Campo_Grande`):** `03/11/2026 10:15` (`OPEN` — Within weekday business hours (09:00-18:00))
- **Input Validator:** `VALID`
- **LGPD Masked Payload:** `Show! E subindo o orçamento: quanto custa o Takamine GD20 e o Taylor 110e pagando à vista no PIX? A promoção de Oferta Especial do Takamine ainda está valendo?`
- **Detected PII Entities:** `[]`
- **Semantic Router:** `ALLOW_IN_SCOPE` (matched route: `in_scope_emporio_store`)
- **Executed Function Calls (Real Backend JSON):**
  - `query_product_catalog({"query": "Takamine GD20"})`:
```json
{
  "tool": "query_product_catalog",
  "items": [
    {
      "product_id": 95,
      "name": "Takamine GD20 Dreadnought Natural",
      "category_id": 5,
      "category_name": "Violões",
      "description": "Violão dreadnought Takamine com tampo sólido em cedro. Excelente para fingerstyle com timbre quente e definido.",
      "specs": {
        "top": "Cedro Sólido",
        "back_sides": "Sapele",
        "neck": "Mogno",
        "strings": "steel",
        "scale": "650mm",
        "electronics": "no",
        "color": "Natural"
      },
      "status": "active",
      "stock_quantity": 5,
      "is_available_for_sale": true,
      "list_price_brl": 2199.0,
      "active_promotion": null,
      "expired_promotions": [
        {
          "promotion_id": 4,
          "discount_percent": 12.0,
          "description": "Oferta Especial",
          "is_active": false
        }
      ],
      "effective_price_brl": 2199.0,
      "pix_price_brl": 2089.05,
      "pix_discount_applicable": true
    }
  ],
  "suggested_alternatives": [],
  "matched_empty_categories": [],
  "total_matches": 1,
  "categories_overview": []
}
```
  - `query_product_catalog({"query": "Taylor 110e"})`:
```json
{
  "tool": "query_product_catalog",
  "items": [
    {
      "product_id": 94,
      "name": "Taylor 110e Dreadnought Natural",
      "category_id": 5,
      "category_name": "Violões",
      "description": "Violão dreadnought Taylor com tampo sólido em Sitka Spruce e eletrônica ES2. Perfeito para palco com som amplificado de alta fidelidade.",
      "specs": {
        "top": "Sitka Spruce Sólido",
        "back_sides": "Walnut",
        "neck": "Tropical Mahogany",
        "strings": "steel",
        "scale": "648mm",
        "electronics": "yes",
        "color": "Natural"
      },
      "status": "active",
      "stock_quantity": 4,
      "is_available_for_sale": true,
      "list_price_brl": 5999.0,
      "active_promotion": {
        "promotion_id": 24,
        "discount_percent": 8.0,
        "description": "Desconto Exclusivo",
        "is_active": true
      },
      "expired_promotions": [],
      "effective_price_brl": 5519.08,
      "pix_price_brl": 5519.08,
      "pix_discount_applicable": false
    }
  ],
  "suggested_alternatives": [],
  "matched_empty_categories": [],
  "total_matches": 1,
  "categories_overview": []
}
```

**Empório da Música Assistant (`pt-BR`):**

Excelentes escolhas, Lucas! Consultei os dois modelos agora no sistema:

1. **Takamine GD20 Dreadnought Natural** (5 unidades em estoque):
   - A campanha anterior de *Oferta Especial (12%)* já está **encerrada**.
   - **Preço atual de tabela:** R$ 2.199,00 (em até 12x sem juros no cartão).
   - **No PIX (5% de desconto à vista):** **R$ 2.089,05**.

2. **Taylor 110e Dreadnought Natural — Eletroacústico ES2** (4 un. em estoque):
   - Está com promoção ativa de **Desconto Exclusivo de 8%**!
   - **Preço original:** R$ 5.999,00 -> **Preço promocional:** **R$ 5.519,08**.
   - *Importante:* conforme nossa política (Seção 6.2), promoções não são cumulativas, então o desconto de 5% do PIX não se aplica sobre o valor já promocional (no PIX ou cartão ele sai pelos mesmos **R$ 5.519,08**).

Quer que eu simule o parcelamento de algum deles?

---

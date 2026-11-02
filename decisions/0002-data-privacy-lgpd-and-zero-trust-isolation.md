# ADR-0002: LGPD Privacy by Design and Zero-Trust Customer Isolation

- **Status:** Accepted
- **Date:** 2026-11-02
- **Deciders:** MkDev21IA

## Context

Customer support conversations in retail involve personal and financial data protected under Brazil's **LGPD (Lei Geral de Proteção de Dados — Law No. 13.709/2018)**, such as CPFs, phone numbers, email addresses, residential addresses, banking/PIX keys, credit card numbers, and private purchase histories.

Two major risks arise in LLM-backed support agents:
1. **External PII Exposure:** Sending raw customer PII to third-party LLM APIs or indexing accidental PII inside RAG vector stores.
2. **Cross-Customer Data Leakage (IDOR via Prompt Injection):** Allowing the LLM to specify `customer_id` or `cpf` in tool arguments enables adversarial users to trick the model into querying another customer's orders.

## Decision

1. **Runtime Reversible PII Masking (`Presidio` + Custom PT-BR Recognizers):**
   - Integrate `presidio-analyzer` and `presidio-anonymizer` backed by `pt_core_news_sm` and custom deterministic `PatternRecognizer` classes for Brazilian entities (CPF with Modulo-11 checksum, RG, BR Phone/WhatsApp, CEP, Credit Card Luhn, PIX/Bank data, and Personal Email).
   - Replace detected entities with session-scoped tokens (e.g., `<CPF_1>`, `<PHONE_1>`) **before** any external OpenAI API call.
2. **RAG & Dataset Sanitization with Institutional Allowlist:**
   - Sanitize policy PDFs and CSV inputs prior to vector indexing while allowlisting **Empório da Música**'s public corporate data (official store address, store phone, corporate CNPJ/PIX) so legitimate store info remains retrievable.
3. **Zero-Trust Identity Isolation:**
   - Bind `authenticated_customer_id` exclusively inside the backend `SessionContext`. Private data tools (`get_customer_orders`) never accept `customer_id` from the LLM; queries are deterministically filtered in Python by the session's authenticated identity.
4. **Ephemeral Retention:**
   - Store conversation histories and PII token vaults strictly in ephemeral memory with automatic TTL expiration.

## Consequences

### Positive
- External LLM providers never inspect raw customer PII.
- Prompt injection cannot escalate privileges or read another customer's records.
- Store contact details remain intact in RAG answers thanks to institutional allowlisting.

### Negative / Trade-offs
- Out-of-the-box Presidio lacks native Brazilian document checksums, requiring custom `PatternRecognizer` implementations and dedicated unit tests in `tests/`.

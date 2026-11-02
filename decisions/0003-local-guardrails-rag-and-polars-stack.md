# ADR-0003: Local Semantic Guardrails, Shared Multilingual Embeddings, and Polars Repositories

- **Status:** Accepted
- **Date:** 2026-11-02
- **Deciders:** MkDev21IA

## Context

The agent needs fast out-of-scope rejection, local PDF policy retrieval, and structured CSV querying (product catalog availability/pricing and customer orders) that reflects production-grade Data Engineering architecture without excessive RAM overhead or cloud database dependencies during evaluation.

## Decision

1. **`Polars` behind Repository Interfaces (`CatalogRepository`, `OrderRepository`):**
   - Use `polars` (Rust/Apache Arrow) instead of `pandas` for strict schema validation, fast filtering, and low memory usage.
   - Encapsulate data access behind abstract Repository protocols so local CSV adapters (`documents/`) can be swapped for a cloud Data Lake / Data Warehouse (e.g., BigQuery, Snowflake, PostgreSQL) without touching orchestrator or tool logic.
2. **Local Policy RAG (`PyMuPDF` + `ChromaDB`):**
   - Extract layout-clean text from policy PDFs with `PyMuPDF` (`fitz`) and index sanitized chunks in embedded `ChromaDB` targeting Python `3.12+`.
3. **Local Guardrails & Shared Multilingual ONNX Embeddings (`semantic-router` + `FastEmbed`):**
   - Configure a single multilingual `FastEmbed` model (`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`) to accurately represent Brazilian Portuguese queries on CPU.
   - Reuse this exact encoder instance across both `semantic-router` (blocking off-topic/random subjects and adversarial jailbreaks while permitting short multi-turn follow-ups) and `ChromaDB` vector indexing.

## Consequences

### Positive
- Zero external API cost or network latency for intent classification and vector embeddings.
- Loading one shared ONNX embedding model cuts memory consumption in half compared to separate RAG and router encoders.
- Clean separation between AI orchestration and Data Engineering storage layers.

### Negative / Trade-offs
- Semantic threshold tuning is required in `semantic-router` so brief valid replies (e.g., an order number or *"Sim"*) are not falsely flagged as out-of-scope; covered via dedicated test cases in `tests/`.

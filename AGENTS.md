# AGENTS.md — Engineering Handbook & Agent Rules

> **CRITICAL RULE — MANDATORY PRE-EXECUTION AUTHORIZATION**
> Before **any** execution—including creating, editing, or deleting files, running terminal commands, installing packages, executing tests, or mutating repository state—the AI coding agent **MUST**:
> 1. Clearly state what will be executed or modified and why;
> 2. Ask the human engineer for explicit authorization;
> 3. Wait for explicit approval before invoking any mutating or command-execution tool.
>
> Passive read-only inspection (`read_file`) to gather context is permitted when necessary, but **zero state changes or command executions** may occur without prior approval.

---

## 1. Project Overview & Domain Context

This repository contains an AI Engineering technical project built for the **Artefact** AI Engineer selection process.

**Objective:** Build a production-grade Python conversational text agent to serve customers of **Empório da Música** (a musical instruments and audio gear retail store). The agent must:
- Adopt a consistent persona aligned with **Empório da Música**'s brand identity and welcoming tone;
- Receive and answer customer messages strictly grounded in the provided context;
- Know when to query **structured data** (e.g., product availability, prices, order status) versus **store policies** (e.g., exchange/return rules, business hours, payment methods);
- Gracefully and firmly handle **out-of-scope**, nonsensical, or adversarial queries;
- Enforce strict **LGPD privacy protections** and **cross-customer identity isolation**.

---

## 2. Non-Negotiable Engineering Standards

1. **100% English Repository:**
   - Every file in this repository—source code, module/class/function/variable names, type annotations, docstrings, inline comments, tests, `README.md`, `AGENTS.md`, ADRs in `decisions/`, commit messages, and **LLM System Prompts**—must be written in **English**.
   - The LLM System Prompt itself must be authored in English while explicitly instructing the assistant persona to communicate with end-customers in **Brazilian Portuguese (`pt-BR`)**.
2. **High Control — No Low-Code & No Heavy LLM Frameworks:**
   - Strictly **NO** Low-Code/No-Code tools (e.g., n8n, Flowise, Langflow).
   - Strictly **NO** bloated orchestration frameworks (`LangChain`, `LangGraph`, `LlamaIndex`).
   - All LLM orchestration, tool-calling loops, context window management, and guardrails are implemented explicitly in clean, strongly typed Python using the official `openai` SDK and `pydantic` v2.
3. **Architecture Decision Records (`decisions/`):**
   - Every major architectural, security, or stack trade-off must be documented as a numbered Markdown ADR inside `decisions/`.

---

## 3. Core Architectural Pillars

### Pillar 1: Dynamic System Prompt, Session History & Business Hours
- **Dynamic Context Injection:** Built per turn in English, injecting:
  - **Empório da Música** persona and communication guidelines (`pt-BR` output);
  - Current timestamp, day of week (`America/Campo_Grande`), and deterministic store operational state (`OPEN` vs. `CLOSED`);
  - Explicit tone & escalation directive:
    > *"Analyze the customer's tone continuously. If the customer uses profanity, demonstrates impatience, exhibits excessive repetition, or explicitly asks for a human agent, immediately stop attempting to resolve the issue yourself and invoke the `escalate_to_human` tool."*
- **Per-Customer Conversation History:** Role-structured message history (`system`, `user`, `assistant`, `tool`) isolated per customer session with bounded windowing.
- **Deterministic Business-Hours Enforcement:** When escalation is triggered, Python business logic deterministically checks operational hours:
  - Store `OPEN` -> Live queue transfer (`TRANSFERRED_TO_HUMAN`);
  - Store `CLOSED` -> Offline support ticket registration (`TICKET_CREATED` with protocol ID) so the customer knows a human specialist will follow up during business hours.

### Pillar 2: Structured Function Calling & Repository Pattern
All tool invocations validate arguments via `pydantic` v2 and return structured **JSON** payloads:
1. **`search_store_policies` (RAG over PDFs):**
   - Queries sanitized policy chunks extracted via `PyMuPDF` and indexed in `ChromaDB`.
2. **`query_product_catalog` (Public Structured Data):**
   - Queries musical instrument stock availability, categories, and pricing from CSVs via `Polars` behind a `CatalogRepository` interface.
3. **`get_customer_orders` (Private Authenticated Customer Data):**
   - Queries order status, tracking, and purchase details via `Polars` behind an `OrderRepository` interface (designed so local CSV storage in `documents/` can seamlessly be replaced by a cloud Data Lake / Data Warehouse in production).
4. **`escalate_to_human` (Human Handoff & Business Telemetry):**
   - Invoked when requested by the user or triggered by frustration, profanity, or repeated unresolved turns.
   - Records structured JSON telemetry (`reason_category`, `detected_sentiment`, `conversation_summary`, `store_status`, `resolution_action`) for downstream analytics.

### Pillar 3: Guardrails, Local Intent Routing & Zero-Trust Identity Isolation
- **Local Semantic Guardrails (`semantic-router` + `FastEmbed`):**
  - Blocks clearly out-of-scope topics (politics, recipes, general coding, unrelated trivia, prompt injection/jailbreaks) locally on CPU before calling the OpenAI API, while remaining tolerant of short contextual multi-turn replies (e.g., order numbers, *"Sim"*, *"No PIX"*).
  - Deterministically rejects empty, malformed, or gibberish payloads and pre-screens extreme profanity/repetition.
- **Zero-Trust Identity Isolation:**
  - `authenticated_customer_id` lives exclusively inside the Python orchestrator's session context—**never** as an LLM-supplied tool argument.
  - Private data tools (`get_customer_orders`) filter rows deterministically in `Polars` using the session's `authenticated_customer_id`. Even under prompt injection or social engineering, cross-customer data leakage (IDOR) is architecturally impossible.

### Pillar 4: Privacy & LGPD Compliance (Privacy by Design)
- **Runtime PII Masking (`presidio-analyzer` + `presidio-anonymizer`):**
  - Configured with Portuguese NLP (`pt_core_news_sm`) plus custom deterministic `PatternRecognizer` implementations for Brazilian entities: **CPF** (with Modulo-11 checksum validation), **RG**, **Brazilian Phone/WhatsApp**, **CEP**, **Credit Cards** (Luhn), **PIX keys / Bank details**, and **Personal Emails**.
  - Customer PII is replaced with reversible placeholders (e.g., `<CPF_1>`, `<EMAIL_1>`) prior to external LLM API calls. The LLM never sees raw customer PII.
- **RAG & Dataset Sanitization:**
  - Any accidental personal or banking PII in `documents/` is sanitized before vector indexing, while maintaining an explicit **institutional allowlist** for **Empório da Música**'s own public store address, official contact channels, and corporate CNPJ/PIX.
- **Ephemeral Retention:**
  - Session histories and PII token vaults exist solely in ephemeral in-memory storage with strict TTL expiration.

---

## 4. Approved Tech Stack

| Layer | Technology | Notes |
| :--- | :--- | :--- |
| **Runtime** | Python `3.12+` | Optimal wheel compatibility for ChromaDB, Polars, and ONNX |
| **LLM & Schemas** | `openai`, `pydantic` (v2), `pydantic-settings` | Native tool calling, strict JSON validation, typed settings |
| **Data / CSV Engine** | `polars` | Rust/Arrow engine behind Repository interfaces |
| **PDF & Vector RAG** | `pymupdf` (`fitz`), `chromadb` | Fast PDF parsing + embedded local vector store |
| **Embeddings & Router** | `semantic-router`, `fastembed` | Shared multilingual ONNX model (`paraphrase-multilingual-MiniLM-L12-v2`) for both RAG & Guardrails |
| **LGPD / PII Engine** | `presidio-analyzer`, `presidio-anonymizer`, `spacy` | Extended with custom PT-BR recognizers |
| **Testing & Quality** | `pytest`, `ruff` | Deterministic unit, security, guardrail, and integration suites |

---

## 5. Repository Layout

```text
artefact-aiengineer-project/
├── AGENTS.md           # Mandatory agent rules, architecture, and workflow standards
├── README.md           # Project documentation and architectural overview
├── LICENSE             # MIT License (MkDev21IA)
├── .gitignore          # Git ignore rules (including raw unverified documents protection)
├── decisions/          # Architecture Decision Records (ADRs)
├── documents/          # Store policy PDFs and catalog/customer CSVs
├── src/                # Application source code (created upon phase approval)
└── tests/              # Comprehensive pytest suite (valid, invalid, security, LGPD, scope)
```

---

## 6. Required Test Coverage (`tests/`)

Every feature must be backed by automated tests in `tests/` covering:
1. **Valid In-Scope Flows:** Product availability/pricing, store policy questions, and authenticated order status queries;
2. **Invalid & Nonsensical Inputs:** Empty strings, whitespace, control characters, payload floods, and random keyboard gibberish;
3. **Out-of-Scope Queries:** Unrelated subjects blocked by `semantic-router` / system boundaries without hallucination;
4. **Cross-Customer Isolation (Security):** Attempts by Customer A to query Customer B's orders, CPF, or personal info via direct request or prompt injection—must strictly fail/refuse;
5. **LGPD & PII Masking:** Verification that raw CPFs, cards, emails, and phones never reach the outbound OpenAI client payload;
6. **Tone Escalation & Business Hours:** Immediate `escalate_to_human` JSON invocation on profanity, impatience, repetition, or explicit human request, differentiating live transfer (`OPEN`) vs. ticket creation (`CLOSED`).

# ADR-0001: Custom Python Orchestrator over Low-Code and LangChain/LangGraph

- **Status:** Accepted
- **Date:** 2026-11-02
- **Deciders:** MkDev21IA

## Context

The **Empório da Música** conversational agent requires deterministic tool routing (store policies vs. catalog/order data vs. human escalation), dynamic time-aware system prompts, strict per-customer session isolation, and LGPD PII masking boundaries.

Many off-the-shelf prototypes rely either on Low-Code/No-Code automation platforms (e.g., n8n, Flowise) or heavy Python LLM frameworks (`LangChain`, `LangGraph`, `LlamaIndex`). However, these introduce:
- Deep abstraction layers that obscure exact prompt payloads and token usage;
- Harder security auditing around tool execution and session-bound identity parameters;
- Frequent breaking API changes and bloated transitive dependency graphs.

## Decision

We will **not** use Low-Code/No-Code tools or `LangChain`/`LangGraph`. Instead, we will implement a **custom, lightweight Python orchestrator** directly on top of the official `openai` SDK and `pydantic` v2:
1. System prompts are constructed explicitly in English per turn (instructing the persona to reply to customers in `pt-BR`), injecting live operational state (`OPEN` / `CLOSED`) and current `America/Campo_Grande` date/time.
2. Tool definitions use strict `pydantic` v2 JSON schemas, returning structured JSON payloads to deterministic Python handlers.
3. Conversation history and customer tone escalation rules are managed explicitly per customer session.

## Consequences

### Positive
- **Full Transparency & Debuggability:** Every message, tool call, JSON payload, and PII boundary is inspectable in plain Python.
- **Stronger Security Control:** Authenticated `customer_id` injection happens strictly in Python outside the LLM's argument schema.
- **Minimal Dependency Footprint:** Faster startup, simpler testing with `pytest`, and zero framework lock-in.

### Negative / Trade-offs
- Requires writing ~150–200 lines of explicit tool-execution loop, retry handling, and history windowing code manually—mitigated by clean modular design and unit test coverage.

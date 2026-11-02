"""Shared pytest fixtures and test configuration.

Planned test suites in `tests/`:
- Valid in-scope queries (catalog availability/pricing, store policies, authenticated orders)
- Invalid, empty, malformed, and nonsensical/gibberish inputs
- Out-of-scope topic rejection via local semantic guardrails
- Cross-customer identity isolation (IDOR & prompt injection attempts)
- LGPD PII masking & RAG sanitization verification
- Tone/frustration detection, human escalation JSON payloads, and out-of-hours ticketing
"""

"""Reversible ephemeral PII vault and RAG document sanitizer using Microsoft Presidio.

Guarantees two LGPD boundaries:
1. Runtime Boundary (`SessionPIIVault`): Replaces customer PII with reversible placeholders
   (`<BR_CPF_1>`, `<EMAIL_ADDRESS_1>`, `<BR_PHONE_1>`, `<CUSTOMER_NAME>`) prior to OpenAI API
   calls, restoring authorized tokens locally before returning responses to the same session.
2. Knowledge Base Boundary (`sanitize_document_text`): Permanently redacts accidental personal
   or banking PII before indexing into ChromaDB, while preserving Emporio da Musica's official
   public address, CNPJ, email, and phone/WhatsApp contacts via allowlist.
"""

import re
import time
from dataclasses import dataclass, field

from presidio_analyzer import AnalyzerEngine, RecognizerResult
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

from emporio_agent.domain.models import CustomerProfile
from emporio_agent.privacy.recognizers import (
    STORE_INSTITUTIONAL_ALLOWLIST_RAW,
    build_pt_analyzer_engine,
    filter_allowlisted_results,
)

CUSTOMER_NAME_TOKEN = "<CUSTOMER_NAME>"

SUPPORTED_LGPD_ENTITIES: list[str] = [
    "BR_CPF",
    "BR_CNPJ",
    "BR_RG",
    "BR_PHONE",
    "BR_CEP",
    "CREDIT_CARD",
    "BR_BANK_PIX",
    "EMAIL_ADDRESS",
]


def _normalize_pii_key(entity_type: str, raw_value: str) -> str:
    """Create a canonical lookup key so identical PII across turns gets the same placeholder."""
    cleaned = raw_value.strip()
    if entity_type in {"BR_CPF", "BR_CNPJ", "BR_PHONE", "BR_CEP", "CREDIT_CARD"}:
        return f"{entity_type}:{re.sub(r'\D', '', cleaned)}"
    return f"{entity_type}:{cleaned.lower()}"


def _resolve_non_overlapping_spans(results: list[RecognizerResult]) -> list[RecognizerResult]:
    """Select highest-priority non-overlapping spans sorted left-to-right."""
    if not results:
        return []

    # Sort by start ascending, highest score first, longest span first
    ranked = sorted(
        results,
        key=lambda r: (r.start, -r.score, -(r.end - r.start)),
    )
    resolved: list[RecognizerResult] = []
    for candidate in ranked:
        if not resolved:
            resolved.append(candidate)
            continue
        prev = resolved[-1]
        if candidate.start >= prev.end:
            resolved.append(candidate)
            continue
        # Overlapping span: keep whichever has higher score (or longer span on tie)
        cand_rank = (candidate.score, candidate.end - candidate.start)
        prev_rank = (prev.score, prev.end - prev.start)
        if cand_rank > prev_rank:
            resolved[-1] = candidate
    return resolved


@dataclass
class MaskingOutcome:
    """Result of masking a single text payload inside a `SessionPIIVault`."""

    masked_text: str
    detected_entity_types: list[str] = field(default_factory=list)
    masked_count: int = 0


class SessionPIIVault:
    """Session-isolated, TTL-bounded reversible PII vault.

    Raw PII values live strictly in memory inside this session instance and are purged
    automatically upon expiration or explicit clearing.
    """

    def __init__(
        self,
        ttl_seconds: int = 1800,
        analyzer: AnalyzerEngine | None = None,
        anonymizer: AnonymizerEngine | None = None,
        clock_fn=time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._analyzer = analyzer or build_pt_analyzer_engine()
        self._anonymizer = anonymizer or AnonymizerEngine()
        self._clock_fn = clock_fn
        self._last_touched: float = self._clock_fn()

        self._token_to_raw: dict[str, str] = {}
        self._norm_key_to_token: dict[str, str] = {}
        self._entity_counters: dict[str, int] = {}
        self._customer_name_variants: list[str] = []

    def is_expired(self) -> bool:
        """Check whether this ephemeral vault has exceeded its inactivity TTL."""
        return (self._clock_fn() - self._last_touched) > self._ttl_seconds

    def clear(self) -> None:
        """Wipe all stored PII mappings from memory immediately."""
        self._token_to_raw.clear()
        self._norm_key_to_token.clear()
        self._entity_counters.clear()
        self._customer_name_variants.clear()

    def _ensure_active(self) -> None:
        if self.is_expired():
            self.clear()
        self._last_touched = self._clock_fn()

    def bind_customer_profile(self, profile: CustomerProfile) -> None:
        """Pre-register the authenticated customer's own PII inside this session vault.

        Allows the System Prompt to address `<CUSTOMER_NAME>` safely without sending the
        customer's real name, phone, or email to external LLM APIs.
        """
        self._ensure_active()
        self._token_to_raw[CUSTOMER_NAME_TOKEN] = profile.first_name

        variants = {profile.name.strip(), profile.first_name.strip()}
        # Longest first so full name matches before first name
        self._customer_name_variants = sorted(
            (v for v in variants if len(v) >= 2),
            key=len,
            reverse=True,
        )

        if profile.phone.strip():
            self._get_or_create_token("BR_PHONE", profile.phone.strip())
        if profile.email.strip():
            self._get_or_create_token("EMAIL_ADDRESS", profile.email.strip())

    def _get_or_create_token(self, entity_type: str, raw_value: str) -> str:
        norm_key = _normalize_pii_key(entity_type, raw_value)
        existing = self._norm_key_to_token.get(norm_key)
        if existing is not None:
            return existing

        next_idx = self._entity_counters.get(entity_type, 0) + 1
        self._entity_counters[entity_type] = next_idx
        token = f"<{entity_type}_{next_idx}>"
        self._norm_key_to_token[norm_key] = token
        self._token_to_raw[token] = raw_value
        return token

    def mask_text(self, text: str) -> MaskingOutcome:
        """Mask all personal/financial PII in `text` prior to external LLM transmission."""
        self._ensure_active()
        if not text:
            return MaskingOutcome(masked_text=text)

        raw_results = self._analyzer.analyze(
            text=text,
            language="pt",
            entities=SUPPORTED_LGPD_ENTITIES,
            allow_list=list(STORE_INSTITUTIONAL_ALLOWLIST_RAW),
        )
        filtered = filter_allowlisted_results(text, raw_results)
        resolved = _resolve_non_overlapping_spans(filtered)

        # Pre-assign tokens left-to-right for deterministic `<ENTITY_1>`, `<ENTITY_2>` numbering
        span_tokens: dict[tuple[int, int], str] = {}
        detected_types: list[str] = []
        for res in resolved:
            raw_span = text[res.start : res.end]
            token = self._get_or_create_token(res.entity_type, raw_span)
            span_tokens[(res.start, res.end)] = token
            detected_types.append(res.entity_type)

        if resolved:
            operators: dict[str, OperatorConfig] = {}
            for entity_name in SUPPORTED_LGPD_ENTITIES:
                operators[entity_name] = OperatorConfig(
                    "custom",
                    {"lambda": lambda val, ent=entity_name: self._get_or_create_token(ent, val)},
                )
            anonymized = self._anonymizer.anonymize(
                text=text,
                analyzer_results=resolved,
                operators=operators,
            )
            working_text = anonymized.text
        else:
            working_text = text

        name_hits = 0
        for variant in self._customer_name_variants:
            pattern = re.compile(rf"\b{re.escape(variant)}\b", flags=re.IGNORECASE)
            working_text, count = pattern.subn(CUSTOMER_NAME_TOKEN, working_text)
            name_hits += count

        if name_hits > 0:
            detected_types.append("CUSTOMER_NAME")

        return MaskingOutcome(
            masked_text=working_text,
            detected_entity_types=detected_types,
            masked_count=len(resolved) + name_hits,
        )

    def unmask_text(self, text: str) -> str:
        """Restore session-authorized placeholders (`<CUSTOMER_NAME>`, `<BR_CPF_1>`, etc.)."""
        self._ensure_active()
        if not text or not self._token_to_raw:
            return text

        restored = text
        for token, raw_value in self._token_to_raw.items():
            if token in restored:
                restored = restored.replace(token, raw_value)
        return restored

    @property
    def active_tokens(self) -> tuple[str, ...]:
        """Return currently registered placeholder keys (without exposing raw PII)."""
        self._ensure_active()
        return tuple(self._token_to_raw.keys())


def sanitize_document_text(
    text: str,
    analyzer: AnalyzerEngine | None = None,
    anonymizer: AnonymizerEngine | None = None,
) -> str:
    """Irreversibly sanitize document text prior to RAG vector indexing.

    Scrubs accidental personal CPFs, RGs, personal emails, phones, cards, and PIX/bank
    details while preserving Emporio da Musica's official contacts, CNPJ, and CEP.
    """
    if not text:
        return text

    engine = analyzer or build_pt_analyzer_engine()
    anon = anonymizer or AnonymizerEngine()

    entities_for_docs = [*SUPPORTED_LGPD_ENTITIES, "PERSON"]
    raw_results = engine.analyze(
        text=text,
        language="pt",
        entities=entities_for_docs,
        allow_list=list(STORE_INSTITUTIONAL_ALLOWLIST_RAW),
    )
    # Require higher score threshold for noisy PERSON NER on store documents
    filtered: list[RecognizerResult] = []
    for res in filter_allowlisted_results(text, raw_results):
        if res.entity_type == "PERSON" and res.score < 0.85:
            continue
        filtered.append(res)

    resolved = _resolve_non_overlapping_spans(filtered)
    if not resolved:
        return text

    operators = {
        ent: OperatorConfig("replace", {"new_value": f"[REDACTED_{ent}]"})
        for ent in entities_for_docs
    }
    return anon.anonymize(
        text=text,
        analyzer_results=resolved,
        operators=operators,
    ).text

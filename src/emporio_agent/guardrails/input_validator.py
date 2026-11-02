"""Deterministic input validation, gibberish rejection, and escalation pre-screening."""

import re
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from emporio_agent.data.csv_repositories import normalize_pt_text


class InputCheckStatus(StrEnum):
    """Outcome categories produced by the deterministic input validator."""

    VALID = "VALID"
    REJECT_EMPTY = "REJECT_EMPTY"
    REJECT_TOO_LONG = "REJECT_TOO_LONG"
    REJECT_CONTROL_CHARS = "REJECT_CONTROL_CHARS"
    REJECT_GIBBERISH = "REJECT_GIBBERISH"
    BLOCK_CROSS_CUSTOMER = "BLOCK_CROSS_CUSTOMER"
    ESCALATE_PROFANITY = "ESCALATE_PROFANITY"
    ESCALATE_REPETITION = "ESCALATE_REPETITION"


class InputValidationResult(BaseModel):
    """Structured result of deterministic pre-LLM input inspection."""

    model_config = ConfigDict(frozen=True)

    status: InputCheckStatus
    is_allowed: bool
    requires_immediate_escalation: bool = False
    reason: str
    suggested_reply_pt_br: str | None = None


CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
REPEATED_CHAR_RE = re.compile(r"(.)\1{6,}", re.IGNORECASE)
CONSONANT_CLUSTER_RE = re.compile(r"[bcdfghjklmnpqrstvwxz]{5,}", re.IGNORECASE)
KEYBOARD_MASH_SUBSTRINGS: tuple[str, ...] = (
    "asdfg",
    "sdfgh",
    "dfghj",
    "fghjk",
    "qwert",
    "werty",
    "ertyu",
    "zxcvb",
    "xcvbn",
    "poiuy",
    "lkjhg",
)
VOWELS_PT = frozenset("aeiouáéíóúâêôãõàüAEIOUÁÉÍÓÚÂÊÔÃÕÀÜyY")

# Explicit Brazilian Portuguese profanity/slurs triggering immediate human escalation
PROFANITY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pat, re.IGNORECASE)
    for pat in (
        r"\bporra\b",
        r"\bcaralho\b",
        r"\bmerda\b",
        r"\bputa\s+que\s+pariu\b",
        r"\bpqp\b",
        r"\bcacete\b",
        r"\bfoda[-\s]?se\b",
        r"\bvai\s+se\s+foder\b",
        r"\bvsf\b",
        r"\bvtnc\b",
        r"\bvai\s+tomar\s+no\s+cu\b",
        r"\bimbecil\b",
        r"\bidiota\b",
        r"\bbosta\b",
        r"\blixo\s+de\s+(?:loja|atendimento|robo|bot)\b",
    )
)

# Explicit attempts to access third-party customer records or dump customer tables
CROSS_CUSTOMER_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pat, re.IGNORECASE)
    for pat in (
        r"\bpedidos?\s+d[eo]s?\s+outro[s]?\s+cliente[s]?\b",
        r"\bdados\s+d[eo]s?\s+outro[s]?\s+cliente[s]?\b",
        r"\bpedido\s+d[eo]\s+cliente\s+(?:id\s*)?#?\d+\b",
        r"\btodos\s+os\s+clientes\b",
        r"\blist(?:e|ar)\s+(?:os\s+)?clientes\b",
        r"\bcustomers\.csv\b",
        r"\borders\.csv\b",
        r"\b(?:telefone|email|e-mail|cpf|endereco|endereço|pedido)\s+d[oa]\s+cliente\s+[a-zà-ÿ]{3,}",
    )
)


def _is_gibberish(cleaned: str) -> bool:
    """Detect keyboard mashing and symbol-only noise without blocking short valid replies."""
    alnum_chars = [ch for ch in cleaned if ch.isalnum()]
    if not alnum_chars:
        return True

    # Short numeric replies (e.g., order ID "4" or "2025") are valid contextual answers
    if cleaned.lstrip("#").isdigit():
        return len(cleaned.lstrip("#")) > 14

    if REPEATED_CHAR_RE.search(cleaned):
        return True

    lowered = cleaned.lower()
    if any(mash in lowered for mash in KEYBOARD_MASH_SUBSTRINGS):
        return True

    alpha_words = re.findall(r"[A-Za-zÀ-ÿ]+", cleaned)
    if not alpha_words:
        return False

    # Flag pure-letter strings >= 6 chars that have 5+ consecutive consonants or near-zero vowels
    for word in alpha_words:
        if len(word) >= 6 and CONSONANT_CLUSTER_RE.search(word):
            return True

    total_letters = sum(len(w) for w in alpha_words)
    has_digits = any(ch.isdigit() for ch in cleaned)
    if total_letters >= 6 and not has_digits:
        vowel_count = sum(1 for w in alpha_words for ch in w if ch in VOWELS_PT)
        if (vowel_count / total_letters) < 0.15:
            return True

    return False


def validate_user_input(
    text: str,
    prior_user_messages: Sequence[str] = (),
    max_chars: int = 2000,
    max_identical_repeats: int = 3,
) -> InputValidationResult:
    """Run deterministic input hygiene, security, and escalation pre-checks."""
    if not text or not text.strip():
        return InputValidationResult(
            status=InputCheckStatus.REJECT_EMPTY,
            is_allowed=False,
            reason="Empty or whitespace-only message.",
            suggested_reply_pt_br=(
                "Olá! Não recebi o conteúdo da sua mensagem. "
                "Como posso te ajudar hoje aqui na Empório da Música?"
            ),
        )

    if CONTROL_CHAR_RE.search(text):
        return InputValidationResult(
            status=InputCheckStatus.REJECT_CONTROL_CHARS,
            is_allowed=False,
            reason="Input contains disallowed control characters.",
            suggested_reply_pt_br=(
                "Desculpe, sua mensagem contém caracteres inválidos que não consegui ler. "
                "Poderia enviá-la novamente em texto simples?"
            ),
        )

    cleaned = text.strip()
    if len(cleaned) > max_chars:
        return InputValidationResult(
            status=InputCheckStatus.REJECT_TOO_LONG,
            is_allowed=False,
            reason=f"Input length ({len(cleaned)}) exceeds {max_chars} character limit.",
            suggested_reply_pt_br=(
                "Sua mensagem ficou muito extensa para eu processar de uma vez. "
                "Poderia resumir sua dúvida em poucas frases?"
            ),
        )

    if _is_gibberish(cleaned):
        return InputValidationResult(
            status=InputCheckStatus.REJECT_GIBBERISH,
            is_allowed=False,
            reason="Input classified as non-meaningful symbols or keyboard gibberish.",
            suggested_reply_pt_br=(
                "Ops, não consegui entender sua mensagem! Você pode me contar com outras "
                "palavras qual instrumento, pedido ou dúvida sobre a loja você procura?"
            ),
        )

    for pattern in PROFANITY_PATTERNS:
        if pattern.search(cleaned):
            return InputValidationResult(
                status=InputCheckStatus.ESCALATE_PROFANITY,
                is_allowed=True,
                requires_immediate_escalation=True,
                reason="Explicit profanity or hostility detected in customer message.",
            )

    for pattern in CROSS_CUSTOMER_PATTERNS:
        if pattern.search(cleaned):
            return InputValidationResult(
                status=InputCheckStatus.BLOCK_CROSS_CUSTOMER,
                is_allowed=False,
                reason="Explicit attempt to query third-party customer records.",
                suggested_reply_pt_br=(
                    "Por segurança e em conformidade com a LGPD, só posso consultar informações "
                    "vinculadas ao seu próprio cadastro autenticado, nunca dados ou pedidos de "
                    "outros clientes. Posso verificar seus pedidos ou ajudar com um instrumento?"
                ),
            )

    # Check consecutive message repetition across session turns
    norm_current = normalize_pt_text(cleaned)
    min_prior = max_identical_repeats - 1
    if norm_current and len(norm_current) >= 4 and len(prior_user_messages) >= min_prior:
        recent_tail = prior_user_messages[-(max_identical_repeats - 1) :]
        if all(normalize_pt_text(prev) == norm_current for prev in recent_tail):
            return InputValidationResult(
                status=InputCheckStatus.ESCALATE_REPETITION,
                is_allowed=True,
                requires_immediate_escalation=True,
                reason=(
                    f"Customer repeated the same message {max_identical_repeats} "
                    "consecutive times."
                ),
            )

    return InputValidationResult(
        status=InputCheckStatus.VALID,
        is_allowed=True,
        reason="Input passed all deterministic checks.",
    )

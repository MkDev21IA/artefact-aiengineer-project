"""Custom Brazilian Portuguese (`pt`) Presidio recognizers and store allowlists.

Covers LGPD-protected personal and financial identifiers:
- `BR_CPF`: Formatted (`NNN.NNN.NNN-NN`) and unformatted 11-digit CPFs with Modulo-11 checksum
- `BR_CNPJ`: Corporate/MEI CNPJ (`NN.NNN.NNN/NNNN-NN`) with Modulo-11 checksum
- `BR_RG`: Brazilian state identity numbers (`RG`)
- `BR_PHONE`: Brazilian landline and WhatsApp/mobile numbers (`+55`, `(DD) 9XXXX-XXXX`)
- `BR_CEP`: Brazilian postal codes (`NNNNN-NNN`)
- `CREDIT_CARD`: Luhn-validated payment card numbers
- `BR_BANK_PIX`: Random EVP PIX keys (UUID), bank agency/account details, and boleto lines
- `EMAIL_ADDRESS`: Standard and accented domain email addresses
"""

import re
from functools import lru_cache

from presidio_analyzer import (
    AnalyzerEngine,
    Pattern,
    PatternRecognizer,
    RecognizerRegistry,
    RecognizerResult,
)
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import CreditCardRecognizer, SpacyRecognizer

# Official public institutional data of Emporio da Musica (`políticas_da_loja.pdf`)
STORE_INSTITUTIONAL_ALLOWLIST_RAW: frozenset[str] = frozenset(
    {
        "(67) 3341-4444",
        "67 3341-4444",
        "3341-4444",
        "(67) 3321-4500",
        "67 3321-4500",
        "3321-4500",
        "contato@emporiodamusica.com.br",
        "contato@empóriodamusica.com.br",
        "12.345.678/0001-90",
        "79202-333",
    }
)

STORE_ALLOWLIST_DIGITS: frozenset[str] = frozenset(
    {
        "6733414444",
        "33414444",
        "6733214500",
        "33214500",
        "12345678000190",
        "79202333",
    }
)

STORE_ALLOWLIST_LOWER: frozenset[str] = frozenset(
    item.lower() for item in STORE_INSTITUTIONAL_ALLOWLIST_RAW
)

# Musical instrument brands/models that must never be misclassified as `PERSON` NER
MUSICAL_BRAND_ALLOWLIST_LOWER: frozenset[str] = frozenset(
    {
        "martin",
        "taylor",
        "gibson",
        "les paul",
        "fender",
        "leo fender",
        "ibanez",
        "yamaha",
        "tagima",
        "memphis",
        "giannini",
        "rozini",
        "takamine",
        "crafter",
        "shelby",
        "kala",
        "ohana",
        "kalani",
        "roland",
        "korg",
        "nord",
        "pearl",
        "tama",
        "music man",
        "stingray",
        "stratocaster",
        "telecaster",
        "dreadnought",
        "emporio da musica",
        "empório da música",
    }
)

CARRIER_TRACKING_CODE_RE = re.compile(r"^BR[A-Z0-9]{9}BR$", re.IGNORECASE)
FORMATTED_CPF_RE = re.compile(r"^\d{3}\.\d{3}\.\d{3}-\d{2}$")


def is_valid_cpf_checksum(raw_text: str) -> bool:
    """Validate Brazilian CPF 11-digit Modulo-11 check digits."""
    digits = re.sub(r"\D", "", raw_text)
    if len(digits) != 11 or len(set(digits)) == 1:
        return False

    nums = [int(ch) for ch in digits]
    first_sum = sum(nums[i] * (10 - i) for i in range(9))
    first_check = (first_sum * 10) % 11
    if first_check == 10:
        first_check = 0
    if nums[9] != first_check:
        return False

    second_sum = sum(nums[i] * (11 - i) for i in range(10))
    second_check = (second_sum * 10) % 11
    if second_check == 10:
        second_check = 0
    return nums[10] == second_check


def is_valid_cnpj_checksum(raw_text: str) -> bool:
    """Validate Brazilian CNPJ 14-digit Modulo-11 check digits."""
    digits = re.sub(r"\D", "", raw_text)
    if len(digits) != 14 or len(set(digits)) == 1:
        return False

    nums = [int(ch) for ch in digits]
    weights_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    weights_2 = [6, *weights_1]

    rem_1 = sum(n * w for n, w in zip(nums[:12], weights_1, strict=True)) % 11
    check_1 = 0 if rem_1 < 2 else 11 - rem_1
    if nums[12] != check_1:
        return False

    rem_2 = sum(n * w for n, w in zip(nums[:13], weights_2, strict=True)) % 11
    check_2 = 0 if rem_2 < 2 else 11 - rem_2
    return nums[13] == check_2


class BrCpfRecognizer(PatternRecognizer):
    """Detects formatted and unformatted Brazilian CPFs.

    - Unformatted 11-digit sequences must pass Modulo-11 checksum validation to avoid
      masking arbitrary order numbers or barcodes.
    - Explicitly formatted `NNN.NNN.NNN-NN` sequences are boosted to 1.0 when valid,
      and kept at 0.85 even when check digits have a customer typo (fail-safe LGPD protection).
    """

    PATTERNS = [
        Pattern(
            name="cpf_formatted",
            regex=r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)",
            score=0.85,
        ),
        Pattern(
            name="cpf_unformatted",
            regex=r"(?<![\d./-])\d{11}(?![\d./-])",
            score=0.60,
        ),
    ]
    CONTEXT = ["cpf", "documento", "titular", "cadastro"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_CPF",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="pt",
        )

    def validate_result(self, pattern_text: str) -> bool | None:
        stripped = pattern_text.strip()
        if is_valid_cpf_checksum(stripped):
            return True
        if FORMATTED_CPF_RE.match(stripped):
            # Preserve 0.85 score for explicitly formatted CPFs (e.g., typo or synthetic test CPF)
            return None
        return False


class BrCnpjRecognizer(PatternRecognizer):
    """Detects formatted Brazilian CNPJs (`NN.NNN.NNN/NNNN-NN`)."""

    PATTERNS = [
        Pattern(
            name="cnpj_formatted",
            regex=r"(?<!\d)\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}(?!\d)",
            score=0.85,
        ),
    ]
    CONTEXT = ["cnpj", "empresa", "mei"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_CNPJ",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="pt",
        )

    def validate_result(self, pattern_text: str) -> bool | None:
        if is_valid_cnpj_checksum(pattern_text.strip()):
            return True
        return None


class BrRgRecognizer(PatternRecognizer):
    """Detects formatted Brazilian RG state ID numbers without matching prices."""

    PATTERNS = [
        Pattern(
            name="rg_formatted",
            regex=r"(?<![R$\d])\b\d{1,2}\.\d{3}\.\d{3}-[0-9Xx]\b",
            score=0.85,
        ),
    ]
    CONTEXT = ["rg", "identidade", "ssp"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_RG",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="pt",
        )


class BrPhoneRecognizer(PatternRecognizer):
    """Detects Brazilian landline and WhatsApp/mobile numbers."""

    PATTERNS = [
        Pattern(
            name="br_phone_with_ddd",
            regex=r"(?<!\d)(?:\+?55[\s-]?)?(?:\(\d{2}\)|\b\d{2})[\s-]?(?:9\d{4}|[2-8]\d{3})[-\s]?\d{4}(?!\d)",
            score=0.85,
        ),
    ]
    CONTEXT = ["telefone", "celular", "whatsapp", "whats", "contato", "zap", "ddd"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_PHONE",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="pt",
        )


class BrCepRecognizer(PatternRecognizer):
    """Detects Brazilian 8-digit postal codes (`NNNNN-NNN`)."""

    PATTERNS = [
        Pattern(
            name="br_cep",
            regex=r"(?<!\d)\d{5}-\d{3}(?!\d)",
            score=0.80,
        ),
    ]
    CONTEXT = ["cep", "endereço", "endereco", "entrega", "rua", "avenida"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_CEP",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="pt",
        )


class BrEmailRecognizer(PatternRecognizer):
    """Detects standard and accented-domain email addresses in Portuguese text."""

    PATTERNS = [
        Pattern(
            name="email_unicode",
            regex=r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9À-ÿ.-]+\.[A-Za-z]{2,}\b",
            score=0.95,
        ),
    ]
    CONTEXT = ["email", "e-mail", "correio"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="EMAIL_ADDRESS",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="pt",
        )


class BrBankAndPixRecognizer(PatternRecognizer):
    """Detects EVP random PIX keys (UUID), bank agency/account details, and boleto lines."""

    PATTERNS = [
        Pattern(
            name="pix_evp_uuid",
            regex=r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
            score=0.90,
        ),
        Pattern(
            name="br_bank_agency_account",
            regex=(
                r"(?i)\bag[êe]ncia\s*:?\s*\d{3,5}(?:-[0-9Xx])?"
                r"(?:[\s,;/e]+conta(?:\s+corrente|\s+poupan[çc]a)?\s*:?\s*\d{4,12}(?:-[0-9Xx])?)"
            ),
            score=0.90,
        ),
        Pattern(
            name="br_boleto_line",
            regex=r"(?<!\d)\d{5}\.?\d{5}\s+\d{5}\.?\d{6}\s+\d{5}\.?\d{6}\s+\d\s+\d{14}(?!\d)",
            score=0.95,
        ),
    ]
    CONTEXT = ["pix", "chave", "banco", "agência", "agencia", "conta", "boleto", "reembolso"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_BANK_PIX",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="pt",
        )


def is_allowlisted_span(span_text: str, entity_type: str) -> bool:
    """Return True if the matched span is public store data, a tracking code, or a brand."""
    cleaned = span_text.strip()
    lowered = cleaned.lower()

    if lowered in STORE_ALLOWLIST_LOWER:
        return True

    if CARRIER_TRACKING_CODE_RE.match(cleaned):
        return True

    if entity_type == "PERSON" and lowered in MUSICAL_BRAND_ALLOWLIST_LOWER:
        return True

    digits = re.sub(r"\D", "", cleaned)
    return bool(
        digits
        and digits in STORE_ALLOWLIST_DIGITS
        and entity_type in {"BR_PHONE", "BR_CNPJ", "BR_CEP"}
    )


def filter_allowlisted_results(
    text: str,
    results: list[RecognizerResult],
    score_threshold: float = 0.65,
) -> list[RecognizerResult]:
    """Filter out low-score detections, institutional store contacts, and brand names."""
    kept: list[RecognizerResult] = []
    for res in results:
        if res.score < score_threshold:
            continue
        span = text[res.start : res.end]
        if is_allowlisted_span(span, res.entity_type):
            continue
        kept.append(res)
    return kept


@lru_cache(maxsize=1)
def build_pt_analyzer_engine() -> AnalyzerEngine:
    """Build and cache a Portuguese-configured Presidio `AnalyzerEngine`."""
    nlp_configuration = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "pt", "model_name": "pt_core_news_sm"}],
        "ner_model_configuration": {
            "model_to_presidio_entity_mapping": {
                "PER": "PERSON",
                "PERSON": "PERSON",
                "LOC": "LOCATION",
                "ORG": "ORGANIZATION",
            },
            "labels_to_ignore": ["MISC"],
        },
    }
    nlp_engine = NlpEngineProvider(nlp_configuration=nlp_configuration).create_engine()

    registry = RecognizerRegistry(supported_languages=["pt"])
    registry.add_recognizer(SpacyRecognizer(supported_language="pt", supported_entities=["PERSON"]))
    registry.add_recognizer(CreditCardRecognizer(supported_language="pt"))
    registry.add_recognizer(BrCpfRecognizer())
    registry.add_recognizer(BrCnpjRecognizer())
    registry.add_recognizer(BrRgRecognizer())
    registry.add_recognizer(BrPhoneRecognizer())
    registry.add_recognizer(BrCepRecognizer())
    registry.add_recognizer(BrEmailRecognizer())
    registry.add_recognizer(BrBankAndPixRecognizer())

    return AnalyzerEngine(
        registry=registry,
        nlp_engine=nlp_engine,
        supported_languages=["pt"],
    )

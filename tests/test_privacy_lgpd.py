"""Comprehensive LGPD and PII masking/sanitization test suite."""

from emporio_agent.domain.models import CustomerProfile
from emporio_agent.privacy.pii_vault import (
    CUSTOMER_NAME_TOKEN,
    SessionPIIVault,
    sanitize_document_text,
)
from emporio_agent.privacy.recognizers import is_valid_cpf_checksum


def _sample_profile() -> CustomerProfile:
    return CustomerProfile(
        customer_id=1,
        name="Lucas Mendes da Silva",
        first_name="Lucas",
        phone="(67) 99812-3456",
        email="lucas.mendes@jmail.com",
        city="Campo Grande",
    )


def test_cpf_checksum_validation_distinguishes_valid_and_invalid_digits() -> None:
    assert is_valid_cpf_checksum("529.982.247-25") is True
    assert is_valid_cpf_checksum("52998224725") is True
    assert is_valid_cpf_checksum("111.111.111-11") is False
    assert is_valid_cpf_checksum("12345678900") is False


def test_runtime_vault_masks_cpf_email_phone_and_restores_reversibly() -> None:
    vault = SessionPIIVault()
    raw_msg = (
        "Oi! Meu CPF é 529.982.247-25, meu celular é (67) 99812-3456 "
        "e meu e-mail é cliente.teste@gmail.com."
    )
    outcome = vault.mask_text(raw_msg)

    assert "529.982.247-25" not in outcome.masked_text
    assert "99812-3456" not in outcome.masked_text
    assert "cliente.teste@gmail.com" not in outcome.masked_text
    assert "<BR_CPF_1>" in outcome.masked_text
    assert "<BR_PHONE_1>" in outcome.masked_text
    assert "<EMAIL_ADDRESS_1>" in outcome.masked_text

    # Repeating the unformatted CPF in a subsequent turn reuses `<BR_CPF_1>`
    followup = vault.mask_text("Confirmando o CPF sem pontos: 52998224725")
    assert followup.masked_text == "Confirmando o CPF sem pontos: <BR_CPF_1>"

    # Reversible unmasking restores exact customer values locally
    llm_reply = "Perfeito! Localizei o cadastro associado ao CPF <BR_CPF_1>."
    assert vault.unmask_text(llm_reply) == (
        "Perfeito! Localizei o cadastro associado ao CPF 529.982.247-25."
    )


def test_formatted_cpf_with_typo_is_still_masked_while_random_11_digits_are_not() -> None:
    vault = SessionPIIVault()
    typo_cpf = vault.mask_text("Meu CPF é 123.456.789-00, pode checar?")
    assert "123.456.789-00" not in typo_cpf.masked_text
    assert "<BR_CPF_1>" in typo_cpf.masked_text

    # Arbitrary invalid 11-digit number must NOT be falsely masked as CPF
    non_cpf = vault.mask_text("O código de barras do encarte é 12345678900.")
    assert "12345678900" in non_cpf.masked_text


def test_credit_card_pix_uuid_bank_account_and_cep_are_masked() -> None:
    vault = SessionPIIVault()
    msg = (
        "Paguei no cartão 4532 0151 1283 0366, minha chave PIX é "
        "123e4567-e89b-12d3-a456-426614174000, agência 0001 conta corrente 123456-7 "
        "e meu CEP é 79002-100."
    )
    outcome = vault.mask_text(msg)

    assert "4532 0151 1283 0366" not in outcome.masked_text
    assert "123e4567-e89b-12d3-a456-426614174000" not in outcome.masked_text
    assert "123456-7" not in outcome.masked_text
    assert "79002-100" not in outcome.masked_text
    assert "<CREDIT_CARD_1>" in outcome.masked_text
    assert "<BR_BANK_PIX_1>" in outcome.masked_text
    assert "<BR_CEP_1>" in outcome.masked_text


def test_store_institutional_contacts_and_tracking_codes_are_preserved() -> None:
    vault = SessionPIIVault()
    msg = (
        "Vi que o telefone da loja é (67) 3341-4444, WhatsApp (67) 3321-4500, "
        "CNPJ 12.345.678/0001-90, CEP 79202-333, e-mail contato@emporiodamusica.com.br "
        "e meu rastreio é BRAB1234567BR."
    )
    outcome = vault.mask_text(msg)
    assert outcome.masked_count == 0
    assert outcome.masked_text == msg


def test_musical_instrument_brands_are_never_corrupted_by_pii_masker() -> None:
    vault = SessionPIIVault()
    msg = "Quero saber o preço do Martin D-28, do Taylor 110e e da Gibson Les Paul Standard."
    outcome = vault.mask_text(msg)
    assert outcome.masked_text == msg


def test_bound_customer_profile_masks_name_and_unmasks_greeting_token() -> None:
    vault = SessionPIIVault()
    vault.bind_customer_profile(_sample_profile())

    user_turn = vault.mask_text("Oi, aqui é o Lucas Mendes da Silva!")
    assert "Lucas" not in user_turn.masked_text
    assert CUSTOMER_NAME_TOKEN in user_turn.masked_text

    assistant_reply = f"Olá, {CUSTOMER_NAME_TOKEN}! Como posso te ajudar hoje?"
    assert vault.unmask_text(assistant_reply) == "Olá, Lucas! Como posso te ajudar hoje?"


def test_cross_session_vaults_cannot_unmask_each_others_tokens() -> None:
    vault_a = SessionPIIVault()
    vault_b = SessionPIIVault()

    vault_a.mask_text("Meu CPF é 529.982.247-25")
    # Vault B never saw Customer A's CPF, so `<BR_CPF_1>` remains opaque in Session B
    leaked_attempt = "Tentando ler <BR_CPF_1> na sessão de outro cliente"
    assert vault_b.unmask_text(leaked_attempt) == leaked_attempt


def test_ephemeral_ttl_expiration_wipes_vault_memory() -> None:
    fake_now = [100.0]
    vault = SessionPIIVault(ttl_seconds=60, clock_fn=lambda: fake_now[0])
    vault.mask_text("Meu CPF é 529.982.247-25")
    assert len(vault.active_tokens) == 1

    # Advance clock past 60s TTL
    fake_now[0] = 165.0
    assert vault.is_expired() is True
    assert vault.active_tokens == ()
    assert vault.unmask_text("CPF: <BR_CPF_1>") == "CPF: <BR_CPF_1>"


def test_rag_document_sanitizer_redacts_accidental_pii_while_keeping_store_info() -> None:
    raw_doc_chunk = (
        "Empório da Música — CNPJ 12.345.678/0001-90 — Rua 14 de Maio, 3200, CEP 79202-333. "
        "Atendimento: (67) 3341-4444 / WhatsApp (67) 3321-4500 / contato@empóriodamusica.com.br. "
        "Exemplo interno acidental: cliente CPF 529.982.247-25, tel (11) 98877-6655, "
        "email joao.pessoal@gmail.com."
    )
    sanitized = sanitize_document_text(raw_doc_chunk)

    # Official store public info stays intact for RAG answers
    assert "12.345.678/0001-90" in sanitized
    assert "79202-333" in sanitized
    assert "(67) 3341-4444" in sanitized
    assert "(67) 3321-4500" in sanitized
    assert "contato@empóriodamusica.com.br" in sanitized

    # Accidental personal PII is permanently redacted before ChromaDB indexing
    assert "529.982.247-25" not in sanitized
    assert "98877-6655" not in sanitized
    assert "joao.pessoal@gmail.com" not in sanitized
    assert "[REDACTED_BR_CPF]" in sanitized
    assert "[REDACTED_BR_PHONE]" in sanitized
    assert "[REDACTED_EMAIL_ADDRESS]" in sanitized

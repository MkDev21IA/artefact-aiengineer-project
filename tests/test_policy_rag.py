"""Integration tests for PyMuPDF extraction, shared FastEmbed singleton, and ChromaDB RAG."""

from pathlib import Path

import pytest

from emporio_agent.rag.embeddings import (
    SharedChromaEmbeddingFunction,
    SharedFastEmbedEncoder,
    get_shared_text_embedding,
)
from emporio_agent.rag.policy_store import (
    POLICY_PDF_FILENAME,
    PolicyVectorStore,
    extract_sanitized_policy_chunks,
)

PDF_PATH = Path(__file__).resolve().parents[1] / "documents" / POLICY_PDF_FILENAME


@pytest.fixture(scope="module")
def policy_store() -> PolicyVectorStore:
    return PolicyVectorStore(pdf_path=PDF_PATH, ephemeral=True)


def test_shared_fastembed_model_is_loaded_once_across_rag_and_router() -> None:
    chroma_ef = SharedChromaEmbeddingFunction()
    router_enc = SharedFastEmbedEncoder()
    # Both components must share the exact same underlying ONNX TextEmbedding object in RAM
    assert chroma_ef._model is get_shared_text_embedding()
    assert router_enc._client is get_shared_text_embedding()


def test_section_chunker_preserves_store_contacts_and_cross_page_sections() -> None:
    chunks = extract_sanitized_policy_chunks(PDF_PATH)
    assert len(chunks) >= 10
    combined = "\n".join(c.content for c in chunks)

    # Store institutional allowlist items must remain intact after RAG sanitization
    assert "Rua 14 de Maio, 3200" in combined
    assert "(67) 3341-4444" in combined
    assert "(67) 3321-4500" in combined
    assert "12.345.678/0001-90" in combined

    # Cross-page Section 5 (p.4 -> p.5) must contain both heading and PAC/SEDEX/Jadlog table
    sec_5 = [c for c in chunks if c.section_title.startswith("5.")]
    assert sec_5
    sec_5_text = "\n".join(c.content for c in sec_5)
    assert "Jadlog" in sec_5_text
    assert "R$ 500,00" in sec_5_text


def test_rag_retrieves_store_address_and_business_hours(policy_store: PolicyVectorStore) -> None:
    # Directly tests Challenge Scenario #2: "Qual o endereço da loja?"
    addr_hits = policy_store.search_policies("Qual o endereço físico e telefone da loja?", top_k=3)
    joined_addr = "\n".join(h.content for h in addr_hits)
    assert "Rua 14 de Maio, 3200" in joined_addr
    assert "Campo Grande" in joined_addr

    hours_hits = policy_store.search_policies(
        "Qual é o horário de funcionamento no sábado?",
        top_k=2,
    )
    joined_hours = "\n".join(h.content for h in hours_hits)
    assert "09:00 às 13:00" in joined_hours


def test_rag_retrieves_return_and_regret_policy(policy_store: PolicyVectorStore) -> None:
    # Tests Challenge Scenario #4: "Me arrependi da minha compra, posso devolver meu pedido?"
    hits = policy_store.search_policies(
        "Me arrependi da minha compra online, posso devolver meu pedido?",
        top_k=3,
    )
    joined = "\n".join(h.content for h in hits)
    assert "7 (sete) dias corridos" in joined
    assert "10 dias úteis" in joined


def test_rag_retrieves_payment_installments_and_accessory_scope_rule(
    policy_store: PolicyVectorStore,
) -> None:
    pay_hits = policy_store.search_policies("Quais as formas de pagamento e parcelamento?", top_k=2)
    joined_pay = "\n".join(h.content for h in pay_hits)
    assert "5% de desconto" in joined_pay
    assert "12x sem juros" in joined_pay

    acc_hits = policy_store.search_policies(
        "Vocês vendem acessórios como cordas, palhetas, cabos ou amplificadores?",
        top_k=3,
    )
    joined_acc = "\n".join(h.content for h in acc_hits)
    assert "exclusivamente com instrumentos musicais" in joined_acc

"""Section-aware PyMuPDF policy parser and LGPD-sanitized ChromaDB vector store."""

import hashlib
import re
from pathlib import Path

import chromadb
import pymupdf

from emporio_agent.domain.models import PolicyChunk
from emporio_agent.privacy.pii_vault import sanitize_document_text
from emporio_agent.rag.embeddings import (
    DEFAULT_MULTILINGUAL_MODEL,
    SharedChromaEmbeddingFunction,
)

POLICY_PDF_FILENAME = "políticas_da_loja.pdf"
COLLECTION_NAME = "emporio_store_policies"

HEADER_NOISE_RE = re.compile(
    r"^Empório da Música\s+Manual de Políticas e Procedimentos\s*$",
    re.IGNORECASE,
)
PAGE_FOOTER_RE = re.compile(r"^Página\s+\d+\s*$", re.IGNORECASE)
TOP_SECTION_RE = re.compile(r"^(\d{1,2})\.\s+[A-ZÀ-Ÿ]")
SUB_SECTION_RE = re.compile(r"^(\d{1,2}\.\d+)\s+[A-ZÀ-Ÿ]")


def extract_sanitized_policy_chunks(
    pdf_path: Path,
    max_section_chars: int = 1800,
) -> list[PolicyChunk]:
    """Parse `políticas_da_loja.pdf` into cross-page section chunks and sanitize PII.

    Splitting by numbered document headings prevents page-break truncation of tables
    (e.g., Section 5.2 shipping carriers across pages 4-5, Section 9 LGPD bullets across 7-8).
    """
    annotated_lines: list[tuple[int, str]] = []
    with pymupdf.open(pdf_path) as doc:
        for page_idx, page in enumerate(doc, start=1):
            raw_page_text = page.get_text("text")
            for raw_line in raw_page_text.splitlines():
                cleaned_line = re.sub(r"[ \t\u200b]+", " ", raw_line).strip()
                if not cleaned_line:
                    continue
                if HEADER_NOISE_RE.match(cleaned_line) or PAGE_FOOTER_RE.match(cleaned_line):
                    continue
                annotated_lines.append((page_idx, cleaned_line))

    if not annotated_lines:
        return []

    raw_sections: list[tuple[str, int, list[str]]] = []
    current_title = "0. Identificação Institucional — Empório da Música"
    current_start_page = annotated_lines[0][0]
    current_buffer: list[str] = []

    for page_num, line in annotated_lines:
        if TOP_SECTION_RE.match(line):
            if current_buffer:
                raw_sections.append((current_title, current_start_page, current_buffer))
            current_title = line
            current_start_page = page_num
            current_buffer = [line]
        else:
            current_buffer.append(line)

    if current_buffer:
        raw_sections.append((current_title, current_start_page, current_buffer))

    final_chunks: list[PolicyChunk] = []
    for sec_idx, (sec_title, start_page, lines) in enumerate(raw_sections):
        joined = "\n".join(lines)
        if len(joined) <= max_section_chars:
            sanitized = sanitize_document_text(joined)
            final_chunks.append(
                PolicyChunk(
                    chunk_id=f"policy_sec_{sec_idx:02d}",
                    section_title=sec_title,
                    page_number=start_page,
                    content=sanitized,
                )
            )
            continue

        # Sub-split oversized top-level sections on `X.Y` subsection headings
        sub_chunks: list[tuple[str, list[str]]] = []
        sub_title = sec_title
        sub_buf: list[str] = []
        for line in lines:
            if SUB_SECTION_RE.match(line) and sub_buf:
                sub_chunks.append((sub_title, sub_buf))
                sub_title = f"{sec_title} — {line}"
                sub_buf = [sec_title, line]
            else:
                sub_buf.append(line)
        if sub_buf:
            sub_chunks.append((sub_title, sub_buf))

        for sub_idx, (stitle, slines) in enumerate(sub_chunks):
            sanitized = sanitize_document_text("\n".join(slines))
            final_chunks.append(
                PolicyChunk(
                    chunk_id=f"policy_sec_{sec_idx:02d}_{sub_idx:02d}",
                    section_title=stitle,
                    page_number=start_page,
                    content=sanitized,
                )
            )

    return final_chunks


class PolicyVectorStore:
    """ChromaDB-backed semantic search over sanitized Emporio da Musica policies."""

    def __init__(
        self,
        pdf_path: Path,
        persist_dir: Path | None = None,
        embedding_model_name: str = DEFAULT_MULTILINGUAL_MODEL,
        ephemeral: bool = False,
    ) -> None:
        self._pdf_path = pdf_path
        self._ef = SharedChromaEmbeddingFunction(model_name=embedding_model_name)

        if ephemeral or persist_dir is None:
            self._client = chromadb.EphemeralClient()
        else:
            persist_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(persist_dir))

        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=self._ef,
            metadata={"hnsw:space": "cosine"},
        )
        self._ensure_indexed()

    def _compute_pdf_sha256(self) -> str:
        return hashlib.sha256(self._pdf_path.read_bytes()).hexdigest()

    def _ensure_indexed(self) -> None:
        """Index sanitized policy chunks if collection is empty or PDF hash changed."""
        pdf_hash = self._compute_pdf_sha256()
        existing_count = self._collection.count()
        if existing_count > 0:
            peeked = self._collection.peek(limit=1)
            metas = peeked.get("metadatas") or []
            if metas and metas[0] and metas[0].get("source_sha256") == pdf_hash:
                return
            # Source PDF changed: reset collection cleanly
            existing_ids = self._collection.get()["ids"]
            if existing_ids:
                self._collection.delete(ids=existing_ids)

        chunks = extract_sanitized_policy_chunks(self._pdf_path)
        if not chunks:
            return

        self._collection.upsert(
            ids=[c.chunk_id for c in chunks],
            documents=[c.content for c in chunks],
            metadatas=[
                {
                    "section_title": c.section_title,
                    "page_number": c.page_number,
                    "source_sha256": pdf_hash,
                }
                for c in chunks
            ],
        )

    @property
    def chunk_count(self) -> int:
        """Return total number of indexed policy chunks."""
        return self._collection.count()

    def search_policies(self, query: str, top_k: int = 3) -> list[PolicyChunk]:
        """Retrieve top-k semantically relevant policy sections for a customer question."""
        cleaned_query = query.strip()
        if not cleaned_query or self._collection.count() == 0:
            return []

        n_results = min(max(1, top_k), self._collection.count())
        res = self._collection.query(
            query_texts=[cleaned_query],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )

        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]

        chunks_out: list[PolicyChunk] = []
        for idx, cid in enumerate(ids):
            meta = metas[idx] if idx < len(metas) and metas[idx] else {}
            dist_val = float(dists[idx]) if idx < len(dists) and dists[idx] is not None else None
            chunks_out.append(
                PolicyChunk(
                    chunk_id=str(cid),
                    section_title=str(meta.get("section_title") or "Política da Loja"),
                    page_number=int(meta.get("page_number") or 1),
                    content=str(docs[idx]),
                    distance=round(dist_val, 4) if dist_val is not None else None,
                )
            )
        return chunks_out

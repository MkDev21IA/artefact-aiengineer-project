"""Typed application configuration backed by environment variables and `.env`."""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class Settings(BaseSettings):
    """Centralized strongly typed configuration for the Emporio da Musica agent."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = Field(
        default="",
        description="OpenAI API key (also accepts `sk-or-...` OpenRouter keys).",
    )
    openrouter_api_key: str = Field(
        default="",
        description="Optional dedicated OpenRouter API key (`sk-or-v1-...`).",
    )
    openai_base_url: str | None = Field(
        default=None,
        description="Optional custom OpenAI-compatible base URL (e.g. OpenRouter, Ollama, vLLM).",
    )
    openai_model: str = Field(
        default="gpt-4o-mini",
        description="Chat completion model ID (e.g. `gpt-4o-mini` or `openai/gpt-4o-mini`).",
    )
    openrouter_model: str | None = Field(
        default=None,
        description="Optional OpenRouter model override (e.g. `openai/gpt-4o-mini`).",
    )
    store_timezone: str = Field(
        default="America/Campo_Grande",
        description="IANA timezone of Emporio da Musica headquarters in Campo Grande/MS.",
    )
    documents_dir: Path = Field(
        default=Path("documents"),
        description="Directory containing store policy PDFs and operational CSV datasets.",
    )
    chroma_persist_dir: Path = Field(
        default=Path(".chroma"),
        description="Local persistence directory for ChromaDB policy embeddings.",
    )
    session_ttl_seconds: int = Field(
        default=1800,
        gt=0,
        description="Ephemeral session and PII vault time-to-live in seconds.",
    )
    max_history_messages: int = Field(
        default=20,
        ge=4,
        description="Maximum non-system messages retained per customer conversation window.",
    )
    max_tool_iterations: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Maximum consecutive tool-calling turns per user message.",
    )
    embedding_model_name: str = Field(
        default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        description="Shared multilingual FastEmbed ONNX model for RAG and Semantic Router.",
    )

    @property
    def tzinfo(self) -> ZoneInfo:
        """Return the configured store IANA timezone object."""
        return ZoneInfo(self.store_timezone)

    @property
    def effective_api_key(self) -> str:
        """Resolve active API key preferring `OPENROUTER_API_KEY` or `OPENAI_API_KEY`."""
        if self.openrouter_api_key.strip():
            return self.openrouter_api_key.strip()
        return self.openai_api_key.strip()

    @property
    def is_openrouter(self) -> bool:
        """Return True when configured credentials or base URL target OpenRouter."""
        if self.openrouter_api_key.strip():
            return True
        if self.effective_api_key.startswith("sk-or-"):
            return True
        return bool(self.openai_base_url and "openrouter.ai" in self.openai_base_url.lower())

    @property
    def effective_base_url(self) -> str | None:
        """Resolve custom base URL, auto-routing to OpenRouter when an OpenRouter key is used."""
        if self.openai_base_url and self.openai_base_url.strip():
            return self.openai_base_url.strip()
        if self.is_openrouter:
            return OPENROUTER_DEFAULT_BASE_URL
        return None

    @property
    def effective_model(self) -> str:
        """Resolve model identifier, adding `openai/` prefix on OpenRouter when omitted."""
        if self.is_openrouter and self.openrouter_model and self.openrouter_model.strip():
            return self.openrouter_model.strip()
        raw_model = self.openai_model.strip() or "gpt-4o-mini"
        if self.is_openrouter and "/" not in raw_model:
            return f"openai/{raw_model}"
        return raw_model


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()

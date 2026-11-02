"""Typed application configuration backed by environment variables and `.env`."""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized strongly typed configuration for the Emporio da Musica agent."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = Field(
        default="",
        description="OpenAI API key used by the custom orchestrator.",
    )
    openai_model: str = Field(
        default="gpt-4o-mini",
        description="Chat completion model used for reasoning and tool calling.",
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()

"""Claim-extractor configuration (engine activation).

Everything is environment-driven and **defaults to safe**: with nothing
configured the system uses `NullExtractor`, which emits no claims at all,
and `shadow` mode, in which claims never reach reconciled availability
state. Turning the LLM on is a deliberate act, twice over.

    CLAIM_EXTRACTOR=null|ollama      default: null
    CLAIM_EXTRACTOR_MODE=shadow|active   default: shadow
    OLLAMA_BASE_URL=http://Desktop-HomePC.local:11434
    OLLAMA_MODEL=<model name>
    OLLAMA_TIMEOUT_SECONDS=120

The native Ollama API (`/api/chat`) is used rather than the
OpenAI-compatible `/v1` surface: `/api/chat` accepts a full JSON schema in
its `format` field, which is what constrains output to the existing
CandidateClaim shape. The `/v1` path would need a second, translated
schema definition, and the whole point of §3 is that there is exactly one.

No secret is read from, or written to, the repository.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

DEFAULT_OLLAMA_BASE_URL = "http://Desktop-HomePC.local:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1"
DEFAULT_TIMEOUT_SECONDS = 120


class ExtractorKind(str, Enum):
    NULL = "null"
    OLLAMA = "ollama"


class ExtractorMode(str, Enum):
    """Shadow claims are extracted, validated and recorded, but never
    promoted into reconciled availability state — so real reporting can be
    inspected before it is trusted."""

    SHADOW = "shadow"
    ACTIVE = "active"


@dataclass(frozen=True)
class ExtractorConfig:
    kind: str = ExtractorKind.NULL.value
    mode: str = ExtractorMode.SHADOW.value
    base_url: str = DEFAULT_OLLAMA_BASE_URL
    model: str = DEFAULT_OLLAMA_MODEL
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    @property
    def host(self) -> str:
        """Host identifier for provenance — never the full URL with any
        credentials a user might have embedded in it."""
        from urllib.parse import urlparse

        try:
            return urlparse(self.base_url).hostname or self.base_url
        except ValueError:
            return "unknown"

    @property
    def is_active(self) -> bool:
        return self.mode == ExtractorMode.ACTIVE.value


def from_env(env: dict[str, str] | None = None) -> ExtractorConfig:
    source = env if env is not None else os.environ
    kind = (source.get("CLAIM_EXTRACTOR") or ExtractorKind.NULL.value).lower()
    if kind not in {k.value for k in ExtractorKind}:
        kind = ExtractorKind.NULL.value
    mode = (source.get("CLAIM_EXTRACTOR_MODE") or ExtractorMode.SHADOW.value).lower()
    if mode not in {m.value for m in ExtractorMode}:
        mode = ExtractorMode.SHADOW.value
    try:
        timeout = int(source.get("OLLAMA_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
    except ValueError:
        timeout = DEFAULT_TIMEOUT_SECONDS
    return ExtractorConfig(
        kind=kind,
        mode=mode,
        base_url=source.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL,
        model=source.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL,
        timeout_seconds=timeout,
    )


def build_extractor(config: ExtractorConfig | None = None):
    """Construct the configured extractor. The knowledge worker never
    imports a provider directly — it receives one built here."""
    from .extractor import NullExtractor

    config = config or from_env()
    if config.kind == ExtractorKind.OLLAMA.value:
        from .ollama import OllamaClaimExtractor

        return OllamaClaimExtractor(config)
    return NullExtractor()

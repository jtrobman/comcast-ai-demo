from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import List

from .models import SourceCitation
from .tracing import traceable


ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = ROOT / "data" / "corpus"
EMBED_CACHE_FILE = ROOT / "data" / "cache" / "voyage_embeddings.json"

VOYAGE_EMBED_URL = "https://api.voyageai.com/v1/embeddings"
DEFAULT_VOYAGE_MODEL = "voyage-3.5-lite"
_DOCUMENT_CACHE: dict[tuple[str, str], list[float]] = {}
_EMBEDDING_CACHE: dict[str, list[float]] | None = None


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    _, raw_meta, body = text.split("---", 2)
    meta: dict[str, str] = {}
    for line in raw_meta.strip().splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    return meta, body.strip()


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) > 2 and token not in {"the", "and", "for", "that", "with"}
    }


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0
    return dot / (left_norm * right_norm)


def _excerpt(body: str, max_chars: int = 520) -> str:
    normalized = " ".join(body.split())
    if len(normalized) <= max_chars:
        return normalized
    candidate = normalized[:max_chars].rsplit(" ", 1)[0]
    sentence_end = max(candidate.rfind("."), candidate.rfind("?"), candidate.rfind("!"))
    if sentence_end > 180:
        candidate = candidate[: sentence_end + 1]
    return f"{candidate}..."


def _voyage_model() -> str:
    return os.getenv("VOYAGE_EMBED_MODEL", DEFAULT_VOYAGE_MODEL)


def _embedding_cache_key(*, model: str, input_type: str, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{model}:{input_type}:{digest}"


def _load_embedding_cache() -> dict[str, list[float]]:
    global _EMBEDDING_CACHE
    if _EMBEDDING_CACHE is not None:
        return _EMBEDDING_CACHE
    if not EMBED_CACHE_FILE.exists():
        _EMBEDDING_CACHE = {}
        return _EMBEDDING_CACHE
    try:
        raw = json.loads(EMBED_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    _EMBEDDING_CACHE = {key: value for key, value in raw.items() if isinstance(value, list)}
    return _EMBEDDING_CACHE


def _save_embedding_cache(cache: dict[str, list[float]]) -> None:
    EMBED_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    EMBED_CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")


def _voyage_embeddings(texts: list[str], *, input_type: str, model: str) -> list[list[float]]:
    api_key = os.getenv("VOYAGE_API_KEY")
    if not api_key:
        raise RuntimeError("VOYAGE_API_KEY is not configured.")

    cache = _load_embedding_cache()
    embeddings: list[list[float] | None] = []
    missing_texts: list[str] = []
    missing_indexes: list[int] = []
    for index, text in enumerate(texts):
        cache_key = _embedding_cache_key(model=model, input_type=input_type, text=text)
        if cache_key in cache:
            embeddings.append(cache[cache_key])
        else:
            embeddings.append(None)
            missing_texts.append(text)
            missing_indexes.append(index)

    if not missing_texts:
        return [embedding for embedding in embeddings if embedding is not None]

    payload = json.dumps(
        {
            "input": missing_texts,
            "model": model,
            "input_type": input_type,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        VOYAGE_EMBED_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Voyage embedding request failed: {exc}") from exc
    fetched_embeddings = [item["embedding"] for item in data["data"]]
    for index, text, embedding in zip(missing_indexes, missing_texts, fetched_embeddings):
        embeddings[index] = embedding
        cache[_embedding_cache_key(model=model, input_type=input_type, text=text)] = embedding
    _save_embedding_cache(cache)
    return [embedding for embedding in embeddings if embedding is not None]


def _document_embeddings(documents: list[tuple[dict[str, str], str, Path]], *, model: str) -> dict[str, list[float]]:
    missing: list[tuple[str, str]] = []
    for meta, body, path in documents:
        source_id = meta.get("source_id", path.stem)
        if (model, source_id) not in _DOCUMENT_CACHE:
            missing.append((source_id, body))

    if missing:
        embeddings = _voyage_embeddings([body for _, body in missing], input_type="document", model=model)
        for (source_id, _), embedding in zip(missing, embeddings):
            _DOCUMENT_CACHE[(model, source_id)] = embedding

    return {
        meta.get("source_id", path.stem): _DOCUMENT_CACHE[(model, meta.get("source_id", path.stem))]
        for meta, _, path in documents
    }


def _lexical_score(query: str, body: str) -> float:
    query_tokens = _tokens(query)
    body_tokens = _tokens(body)
    overlap = len(query_tokens & body_tokens)
    score = overlap / max(len(query_tokens), 1)
    return score if score > 0 else 0.05


def _fallback_boost(query: str, source_id: str) -> float:
    tokens = _tokens(query)
    if source_id == "comcast_business_service_interruptions" and tokens & {"outage", "restart", "rebooted", "offline"}:
        return 0.14
    if source_id == "comcast_business_network_performance" and tokens & {"wifi", "signal", "gateway", "dropping", "flaky"}:
        return 0.1
    if source_id == "comcast_business_credits_and_support" and tokens & {"credit", "refund", "billing"}:
        return 0.2
    if source_id == "comcast_business_my_account" and tokens & {"account", "app", "restart", "status"}:
        return 0.05
    return 0


def _load_documents() -> list[tuple[dict[str, str], str, Path]]:
    documents: list[tuple[dict[str, str], str, Path]] = []
    for path in CORPUS_DIR.glob("*.md"):
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        if meta.get("source_type") == "operational_rag":
            documents.append((meta, body, path))
    return documents


@traceable(run_type="retriever", name="rag_retrieve")
def retrieve(query: str, limit: int = 4) -> List[SourceCitation]:
    documents = _load_documents()
    results: list[SourceCitation] = []
    model = _voyage_model()
    use_voyage = bool(os.getenv("VOYAGE_API_KEY"))

    query_embedding: list[float] | None = None
    if use_voyage:
        try:
            query_embedding = _voyage_embeddings([query], input_type="query", model=model)[0]
            document_embeddings = _document_embeddings(documents, model=model)
        except RuntimeError:
            query_embedding = None
            document_embeddings = {}
    else:
        document_embeddings = {}

    for meta, body, path in documents:
        source_id = meta.get("source_id", path.stem)
        domain_boost = _fallback_boost(query, source_id)
        if query_embedding is not None:
            score = min(_cosine(query_embedding, document_embeddings[source_id]) + domain_boost, 1)
            score_kind = f"voyage_cosine+domain_hint:{model}" if domain_boost else f"voyage_cosine:{model}"
        else:
            score = _lexical_score(query, body) + domain_boost
            score_kind = "lexical_overlap"

        results.append(
            SourceCitation(
                source_id=source_id,
                title=meta.get("title", path.stem.replace("_", " ").title()),
                url=meta.get("url", ""),
                excerpt=_excerpt(body),
                score=round(score, 3),
                score_kind=score_kind,
            )
        )

    return sorted(results, key=lambda item: item.score, reverse=True)[:limit]

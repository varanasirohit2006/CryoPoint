"""Derived semantic indexes; workflow versions remain authoritative in storage.

The Chroma adapter imports its optional dependencies only on first use. Callers
must run these blocking operations in a worker thread, not an async event loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
from math import sqrt
from pathlib import Path
import re
from threading import RLock
from typing import Any, Callable, Protocol, Sequence


class IndexUnavailable(RuntimeError):
    """The configured local embedding/index backend cannot be used."""


@dataclass(frozen=True)
class VectorMatch:
    workflow_id: str
    similarity: float
    metadata: dict[str, Any]


class WorkflowIndex(Protocol):
    def upsert(
        self, workflow_id: str, signature: str, metadata: dict[str, Any]
    ) -> None: ...

    def search(
        self, signature: str, metadata_filter: dict[str, Any], limit: int = 5
    ) -> list[VectorMatch]: ...

    def delete(self, workflow_id: str) -> None: ...


class ChromaWorkflowIndex:
    """Persistent cosine index with one reusable, local ONNX MiniLM model."""

    def __init__(
        self,
        directory: Path | str,
        *,
        collection_name: str = "jit_workflows",
        embedding_function: Any = None,
        embedding_directory: Path | str | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.collection_name = collection_name
        self._embedding_function = embedding_function
        self.embedding_directory = Path(embedding_directory) if embedding_directory else self.directory.parent / "cache" / "embeddings"
        self._collection: Any = None
        self._client: Any = None
        self._lock = RLock()

    def _get_collection(self) -> Any:
        if self._collection is not None:
            return self._collection
        if self._embedding_function is None:
            self._check_local_model()
        try:
            import chromadb
            from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

            if self._embedding_function is None:
                owner = self

                class ProvisionedEmbedding(ONNXMiniLM_L6_V2):
                    def _download_model_if_not_exists(self) -> None:
                        owner._check_local_model()

                self._embedding_function = ProvisionedEmbedding(preferred_providers=["CPUExecutionProvider"])
                self._embedding_function.DOWNLOAD_PATH = self.embedding_directory
            self._client = chromadb.PersistentClient(path=str(self.directory))
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
                embedding_function=self._embedding_function,
            )
        except Exception as exc:
            raise IndexUnavailable(
                "Local semantic indexing is unavailable. Install the embeddings "
                "extra and provision the ONNX MiniLM model."
            ) from exc
        return self._collection

    def _check_local_model(self) -> None:
        required = ("config.json", "model.onnx", "special_tokens_map.json", "tokenizer_config.json", "tokenizer.json", "vocab.txt")
        if any(not (self.embedding_directory / "onnx" / name).is_file() for name in required):
            raise IndexUnavailable("Local MiniLM files are not provisioned. Run scripts/setup.ps1 -Semantic or explicitly select lexical indexing. Model downloads never occur during a run.")

    def upsert(
        self, workflow_id: str, signature: str, metadata: dict[str, Any]
    ) -> None:
        with self._lock:
            try:
                self._get_collection().upsert(
                    ids=[workflow_id], documents=[signature], metadatas=[metadata]
                )
            except IndexUnavailable:
                raise
            except Exception as exc:
                raise IndexUnavailable("Unable to update the local semantic index.") from exc

    def search(
        self, signature: str, metadata_filter: dict[str, Any], limit: int = 5
    ) -> list[VectorMatch]:
        if limit < 1:
            return []
        with self._lock:
            try:
                collection = self._get_collection()
                count = collection.count()
                if not count:
                    return []
                clauses = [{key: {"$eq": value}} for key, value in metadata_filter.items()]
                where = clauses[0] if len(clauses) == 1 else {"$and": clauses}
                options: dict[str, Any] = {
                    "query_texts": [signature],
                    "n_results": min(limit, count),
                    "include": ["distances", "metadatas"],
                }
                if clauses:
                    options["where"] = where
                result = collection.query(**options)
                identifiers = (result.get("ids") or [[]])[0]
                distances = (result.get("distances") or [[]])[0]
                metadatas = (result.get("metadatas") or [[]])[0]
                return [
                    VectorMatch(
                        workflow_id=identifier,
                        similarity=max(-1.0, min(1.0, 1.0 - float(distance))),
                        metadata=metadata or {},
                    )
                    for identifier, distance, metadata in zip(
                        identifiers, distances, metadatas, strict=True
                    )
                ]
            except IndexUnavailable:
                raise
            except Exception as exc:
                raise IndexUnavailable("Unable to query the local semantic index.") from exc

    def delete(self, workflow_id: str) -> None:
        with self._lock:
            self._get_collection().delete(ids=[workflow_id])


class MemoryWorkflowIndex:
    """Explicitly injected embeddings for deterministic tests, never a fallback."""

    def __init__(self, embed: Callable[[Sequence[str]], list[list[float]]]) -> None:
        self._embed = embed
        self._records: dict[str, tuple[list[float], dict[str, Any]]] = {}

    def upsert(
        self, workflow_id: str, signature: str, metadata: dict[str, Any]
    ) -> None:
        vector = self._embed([signature])[0]
        self._records[workflow_id] = (vector, dict(metadata))

    def search(
        self, signature: str, metadata_filter: dict[str, Any], limit: int = 5
    ) -> list[VectorMatch]:
        from math import sqrt

        if limit < 1:
            return []
        query = self._embed([signature])[0]
        matches = []
        for identifier, (vector, metadata) in self._records.items():
            if any(metadata.get(key) != value for key, value in metadata_filter.items()):
                continue
            if len(vector) != len(query):
                raise ValueError("Embedding vectors have incompatible dimensions.")
            denominator = sqrt(sum(value * value for value in vector)) * sqrt(
                sum(value * value for value in query)
            )
            similarity = (
                sum(a * b for a, b in zip(query, vector, strict=True)) / denominator
                if denominator
                else 0.0
            )
            matches.append(VectorMatch(identifier, similarity, dict(metadata)))
        return sorted(matches, key=lambda match: (-match.similarity, match.workflow_id))[:limit]

    def delete(self, workflow_id: str) -> None:
        self._records.pop(workflow_id, None)


class LexicalWorkflowIndex:
    """Explicit local token-cosine retrieval, rebuilt from authoritative versions.

    This lightweight backend has no model or download and does not claim learned
    semantic embeddings. The dashboard exposes the chosen backend.
    """

    def __init__(self) -> None:
        self._records: dict[str, tuple[Counter[str], dict[str, Any]]] = {}
        self._lock = RLock()

    @staticmethod
    def _tokens(signature: str) -> Counter[str]:
        return Counter(re.findall(r"<[^>]+>|[\w]+", signature.casefold()))

    def upsert(self, workflow_id: str, signature: str, metadata: dict[str, Any]) -> None:
        with self._lock:
            self._records[workflow_id] = (self._tokens(signature), dict(metadata))

    def search(self, signature: str, metadata_filter: dict[str, Any], limit: int = 5) -> list[VectorMatch]:
        if limit < 1:
            return []
        query = self._tokens(signature)
        query_norm = sqrt(sum(count * count for count in query.values()))
        matches = []
        with self._lock:
            for identifier, (tokens, metadata) in self._records.items():
                if any(metadata.get(key) != value for key, value in metadata_filter.items()):
                    continue
                denominator = query_norm * sqrt(sum(count * count for count in tokens.values()))
                similarity = sum(count * tokens.get(word, 0) for word, count in query.items()) / denominator if denominator else 0.0
                matches.append(VectorMatch(identifier, similarity, dict(metadata)))
        return sorted(matches, key=lambda match: (-match.similarity, match.workflow_id))[:limit]

    def delete(self, workflow_id: str) -> None:
        with self._lock:
            self._records.pop(workflow_id, None)

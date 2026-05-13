from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RagResult:
    context: str
    sources: list[str]

    @property
    def used(self) -> bool:
        return bool(self.context)

    @property
    def context_chars(self) -> int:
        return len(self.context)


class ChromaRetriever:
    def __init__(
        self,
        knowledge_dir: str,
        chroma_host: str,
        chroma_port: int,
        collection_name: str,
    ) -> None:
        self._knowledge_dir = Path(knowledge_dir)
        self._chroma_host = chroma_host
        self._chroma_port = chroma_port
        self._collection_name = collection_name
        self._model = None
        self._collection = None

    def load(self) -> None:
        try:
            import chromadb
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
        except ImportError as exc:
            raise RuntimeError(f"Missing RAG dependency: {exc}") from exc

        self._model = DefaultEmbeddingFunction()
        client = self._connect_chroma(chromadb)
        self._collection = client.get_or_create_collection(
            name=self._collection_name,
            embedding_function=self._model,
            metadata={"description": "Distributed LLM project knowledge base"},
        )

        documents: list[str] = []
        ids: list[str] = []
        metadatas: list[dict[str, str | int]] = []
        for suffix in ("*.txt", "*.md"):
            for path in sorted(self._knowledge_dir.glob(suffix)):
                text = path.read_text(encoding="utf-8").strip()
                for chunk_index, chunk in enumerate(_chunk_text(text)):
                    doc_id = _document_id(path, chunk_index, chunk)
                    ids.append(doc_id)
                    documents.append(chunk)
                    metadatas.append(
                        {
                            "source": path.name,
                            "chunk_index": chunk_index,
                        }
                    )

        if not documents:
            logger.warning("[rag] No documents found in %s — RAG disabled", self._knowledge_dir)
            return

        self._collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
        )

        logger.info(
            "[rag] Upserted %d knowledge chunk(s) into Chroma collection '%s'",
            len(documents),
            self._collection_name,
        )

    def _connect_chroma(self, chromadb):
        last_error: Exception | None = None
        for attempt in range(1, 11):
            try:
                client = chromadb.HttpClient(host=self._chroma_host, port=self._chroma_port)
                client.heartbeat()
                return client
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "[rag] Chroma unavailable at %s:%s (attempt %d/10): %s",
                    self._chroma_host,
                    self._chroma_port,
                    attempt,
                    exc,
                )
                time.sleep(2)

        raise RuntimeError(
            f"Could not connect to Chroma at {self._chroma_host}:{self._chroma_port}"
        ) from last_error

    def retrieve(self, query: str, top_k: int) -> RagResult:
        if self._collection is None or self._model is None:
            return RagResult(context="", sources=[])

        results = self._collection.query(
            query_texts=[query],
            n_results=max(1, top_k),
            include=["documents", "metadatas"],
        )

        passages = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        sources: list[str] = []
        for metadata in metadatas:
            source = metadata.get("source") if isinstance(metadata, dict) else None
            chunk_index = metadata.get("chunk_index") if isinstance(metadata, dict) else None
            if source is None:
                continue
            label = f"{source}#{chunk_index}" if chunk_index is not None else str(source)
            if label not in sources:
                sources.append(label)

        return RagResult(context="\n\n---\n\n".join(passages), sources=sources)


def _chunk_text(text: str, max_chars: int = 1200) -> Iterable[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    current = ""

    for paragraph in paragraphs:
        if not current:
            current = paragraph
            continue

        if len(current) + len(paragraph) + 2 <= max_chars:
            current = f"{current}\n\n{paragraph}"
        else:
            yield current
            current = paragraph

    if current:
        yield current


def _document_id(path: Path, chunk_index: int, chunk: str) -> str:
    digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()[:16]
    return f"{path.stem}-{chunk_index}-{digest}"

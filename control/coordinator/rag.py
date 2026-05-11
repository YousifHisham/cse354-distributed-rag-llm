from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


class ChromaRetriever:
    def __init__(
        self,
        knowledge_dir: str,
        embedding_model: str,
        chroma_host: str,
        chroma_port: int,
        collection_name: str,
    ) -> None:
        self._knowledge_dir = Path(knowledge_dir)
        self._embedding_model_name = embedding_model
        self._chroma_host = chroma_host
        self._chroma_port = chroma_port
        self._collection_name = collection_name
        self._model = None
        self._collection = None

    def load(self) -> None:
        try:
            import chromadb
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(f"Missing RAG dependency: {exc}") from exc

        self._model = SentenceTransformer(self._embedding_model_name)
        client = self._connect_chroma(chromadb)
        self._collection = client.get_or_create_collection(
            name=self._collection_name,
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

        embeddings = self._model.encode(documents, convert_to_numpy=True).tolist()
        self._collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
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

    def retrieve(self, query: str, top_k: int) -> str:
        if self._collection is None or self._model is None:
            return ""

        query_embedding = self._model.encode([query], convert_to_numpy=True).tolist()[0]
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=max(1, top_k),
            include=["documents", "metadatas"],
        )

        passages = results.get("documents", [[]])[0]
        return "\n\n---\n\n".join(passages)


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

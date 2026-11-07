"""
RAG (Retrieval-Augmented Generation) engine.
Handles document ingestion, chunking, embedding, and retrieval.
"""

import asyncio
import hashlib
import logging
import threading
from pathlib import Path
from typing import Any, Iterable, Iterator

logger = logging.getLogger(__name__)

# Binary formats that carry no extractable text. They are still stored in
# the uploads directory (for vision models, viewers and tools) but are
# skipped by the embedder instead of producing garbage chunks.
NON_TEXT_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".heic",
    ".mp3", ".wav", ".ogg", ".flac", ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".zip", ".gz", ".tar", ".7z", ".rar", ".bin", ".exe", ".dll", ".so",
    ".parquet", ".pkl", ".pickle", ".npy", ".npz", ".h5", ".hdf5", ".pt", ".pth", ".onnx",
}


class RAGEngine:
    """Simple RAG engine using ChromaDB for vector storage."""

    def __init__(self, config: dict):
        self.config = config
        self.chunk_size = config.get("chunk_size", 1000)
        self.chunk_overlap = config.get("chunk_overlap", 200)
        self.top_k = config.get("top_k", 5)
        self.persist_dir = config.get("persist_directory", "./data/vector_store")
        # Batch chunks during embedding so we can update progress without
        # holding the GIL on a single huge upsert. 32 is a sensible default
        # for sentence-transformer / ONNX backends; tune via config.
        self.embed_batch_size = config.get("embed_batch_size", 32)
        # Optional cap on tokenizer max length — shorter sequences mean less
        # padding and faster inference for the embedder.
        self.embed_max_length = config.get("embed_max_length", 256)
        self._collection = None
        self._embedding_fn = None
        # Per-file embedding progress: filename -> {stage, current, total, percent}
        self._progress: dict[str, dict] = {}
        self._progress_lock = threading.Lock()

    def _build_embedding_fn(self):
        """Build an ONNX-backed embedding function with optimized session options.

        Falls back to ChromaDB's default if optimization isn't available.
        Optimizations applied (per AWS / ONNX Runtime guidance):
          - graph optimization level = ORT_ENABLE_ALL (operator fusion)
          - intra-op threads scaled to CPU count
          - bounded tokenizer max_length to reduce padding overhead
        """
        try:
            from chromadb.utils import embedding_functions
            ef = embedding_functions.ONNXMiniLM_L6_V2()
            try:
                import os
                import onnxruntime as ort
                so = ort.SessionOptions()
                so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                so.intra_op_num_threads = max(1, (os.cpu_count() or 2))
                # ChromaDB's ONNXMiniLM caches the session on first use as
                # ``_model``; pre-warm with our optimized session so
                # subsequent calls reuse it.
                if hasattr(ef, "_init_model_and_tokenizer"):
                    ef._init_model_and_tokenizer()
                if hasattr(ef, "model") and ef.model is not None:
                    # Replace the default session with an optimized one.
                    model_path = getattr(ef, "DOWNLOAD_PATH", None) or getattr(
                        ef, "_MODEL_DOWNLOAD_PATH", None
                    )
                    if model_path:
                        ef.model = ort.InferenceSession(
                            str(model_path),
                            sess_options=so,
                            providers=["CPUExecutionProvider"],
                        )
                # Cap tokenizer length — see AWS guidance on padding overhead.
                if hasattr(ef, "tokenizer") and ef.tokenizer is not None:
                    try:
                        ef.tokenizer.model_max_length = self.embed_max_length
                    except Exception:
                        pass
            except Exception as opt_err:
                logger.info("ONNX session optimization skipped: %s", opt_err)
            return ef
        except Exception as e:
            logger.info("Custom embedding function unavailable, using default: %s", e)
            return None

    def _get_collection(self):
        if self._collection is not None:
            return self._collection
        try:
            import chromadb
            # Use PersistentClient so embeddings survive container restarts.
            # chromadb.Client() is ephemeral (in-memory only).
            client = chromadb.PersistentClient(
                path=self.persist_dir,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )
            if self._embedding_fn is None:
                self._embedding_fn = self._build_embedding_fn()

            kwargs = {
                "name": "nexus_docs",
                "metadata": {"hnsw:space": "cosine"},
            }
            if self._embedding_fn is not None:
                kwargs["embedding_function"] = self._embedding_fn

            self._collection = client.get_or_create_collection(**kwargs)
            return self._collection
        except ImportError:
            logger.warning("chromadb not installed - RAG disabled")
            return None

    def chunk_text(self, text: str) -> list[str]:
        """Split text into overlapping chunks (in-memory variant)."""
        chunks = []
        start = 0
        while start < len(text):
            end = start + self.chunk_size
            chunk = text[start:end]
            if chunk.strip():
                chunks.append(chunk)
            start += self.chunk_size - self.chunk_overlap
        return chunks

    # ------------------------------------------------------------------
    # Streaming chunkers: emit chunks without holding the full document.
    # ------------------------------------------------------------------
    def _chunk_from_text_stream(self, text_iter: Iterable[str]) -> Iterator[str]:
        """Yield overlapping chunks from a stream of text fragments.

        ``text_iter`` is anything that yields strings (lines, pages,
        paragraphs, fixed-size reads). The buffer is held to roughly
        ``chunk_size`` bytes so memory stays bounded even for files
        that are gigabytes large.
        """
        size = self.chunk_size
        overlap = max(0, min(self.chunk_overlap, size - 1))
        step = max(1, size - overlap)

        buffer = ""
        for fragment in text_iter:
            if not fragment:
                continue
            buffer += fragment
            while len(buffer) >= size:
                chunk = buffer[:size]
                if chunk.strip():
                    yield chunk
                buffer = buffer[step:]
        if buffer.strip():
            yield buffer

    def _stream_text_file(self, filepath: Path, block_size: int = 256 * 1024) -> Iterator[str]:
        """Read a text file in fixed-size blocks (UTF-8, replacement on errors)."""
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            while True:
                block = f.read(block_size)
                if not block:
                    return
                yield block

    def _stream_pdf_pages(self, filepath: Path) -> Iterator[str]:
        """Yield text page-by-page from a PDF without materializing the whole doc."""
        from PyPDF2 import PdfReader
        reader = PdfReader(str(filepath))
        for page in reader.pages:
            text = page.extract_text() or ""
            if text:
                # Trailing newline keeps page boundaries from gluing words together.
                yield text + "\n"

    def _stream_docx_paragraphs(self, filepath: Path) -> Iterator[str]:
        """Yield text paragraph-by-paragraph from a DOCX file."""
        from docx import Document
        doc = Document(str(filepath))
        for para in doc.paragraphs:
            if para.text:
                yield para.text + "\n"

    def _stream_spreadsheet_rows(self, filepath: Path) -> Iterator[str]:
        """Yield rows from an Excel workbook as tab-separated text."""
        import openpyxl  # optional dependency

        wb = openpyxl.load_workbook(str(filepath), read_only=True, data_only=True)
        for ws in wb.worksheets:
            yield f"# Sheet: {ws.title}\n"
            for row in ws.iter_rows(values_only=True):
                cells = ["" if c is None else str(c) for c in row]
                if any(cells):
                    yield "\t".join(cells) + "\n"

    def _stream_pptx_text(self, filepath: Path) -> Iterator[str]:
        from pptx import Presentation

        prs = Presentation(str(filepath))
        for i, slide in enumerate(prs.slides, 1):
            yield f"# Slide {i}\n"
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False) and shape.text_frame.text:
                    yield shape.text_frame.text + "\n"

    def _document_stream(self, filepath: Path) -> Iterator[str]:
        """Pick the right streamer based on file extension."""
        ext = filepath.suffix.lower()
        if ext == ".pdf":
            return self._stream_pdf_pages(filepath)
        if ext == ".docx":
            return self._stream_docx_paragraphs(filepath)
        if ext in (".xlsx", ".xlsm"):
            return self._stream_spreadsheet_rows(filepath)
        if ext == ".pptx":
            return self._stream_pptx_text(filepath)
        if ext in NON_TEXT_EXTENSIONS:
            return iter(())
        return self._stream_text_file(filepath)

    # ------------------------------------------------------------------
    # Progress tracking
    # ------------------------------------------------------------------
    def _set_progress(self, filename: str, **fields):
        with self._progress_lock:
            current = self._progress.get(filename, {"filename": filename})
            current.update(fields)
            self._progress[filename] = current

    def get_progress(self, filename: str) -> dict | None:
        """Return the current embedding progress for a file, if any."""
        with self._progress_lock:
            entry = self._progress.get(filename)
            return dict(entry) if entry else None

    def clear_progress(self, filename: str):
        with self._progress_lock:
            self._progress.pop(filename, None)

    async def ingest_file(self, filepath: Path, metadata: dict | None = None) -> dict:
        """Stream a file from disk into the vector store.

        The file is never fully loaded into memory — text/PDF/DOCX are
        each read incrementally and chunks are emitted as soon as the
        sliding window fills up. Each batch of ``embed_batch_size``
        chunks is embedded on a worker thread (off the event loop) so
        upserts are pipelined with chunk production. Progress is
        exposed via :meth:`get_progress` keyed by ``filepath.name``.

        For files of unknown chunk-count (every streamed file), progress
        is reported as ``current`` chunks processed; ``total`` is
        approximated from file size when possible.
        """
        filename = filepath.name
        self._set_progress(filename, stage="reading", current=0, total=0, percent=0)

        collection = self._get_collection()
        if collection is None:
            self._set_progress(filename, stage="error", percent=0)
            return {"error": "Vector store not available"}

        # Best-effort total estimate for the progress bar. For text
        # files the byte count gives a decent ceiling; for PDF/DOCX we
        # leave it at 0 and just report ``current`` (the UI handles it).
        try:
            file_size = filepath.stat().st_size
        except OSError:
            file_size = 0
        estimated_total = (
            max(1, file_size // max(1, (self.chunk_size - self.chunk_overlap)))
            if filepath.suffix.lower() not in {".pdf", ".docx"} and file_size
            else 0
        )

        owner_key = (metadata or {}).get("owner", "")
        file_id = hashlib.md5(f"{owner_key}:{filepath}".encode()).hexdigest()[:12]
        meta = metadata or {}
        batch_size = max(1, int(self.embed_batch_size))

        self._set_progress(
            filename, stage="embedding", current=0, total=estimated_total, percent=0
        )

        def _flush(batch: list[str], offset: int) -> None:
            """Embed + upsert a batch on a worker thread (sync API)."""
            ids = [f"{file_id}_chunk_{i}" for i in range(offset, offset + len(batch))]
            metas = [
                {**meta, "source": filename, "chunk_index": i}
                for i in range(offset, offset + len(batch))
            ]
            collection.upsert(documents=batch, ids=ids, metadatas=metas)

        processed = 0
        batch: list[str] = []
        try:
            stream = self._document_stream(filepath)
            for chunk in self._chunk_from_text_stream(stream):
                batch.append(chunk)
                if len(batch) >= batch_size:
                    offset = processed
                    # Run embedding off the event loop so /api/upload/progress
                    # remains responsive on huge files.
                    await asyncio.to_thread(_flush, batch, offset)
                    processed += len(batch)
                    batch = []
                    pct = (
                        min(99, int(processed / estimated_total * 100))
                        if estimated_total
                        else 0
                    )
                    self._set_progress(
                        filename,
                        stage="embedding",
                        current=processed,
                        total=estimated_total or processed,
                        percent=pct,
                    )

            if batch:
                offset = processed
                await asyncio.to_thread(_flush, batch, offset)
                processed += len(batch)
                batch = []
        except Exception as e:
            logger.error("Ingestion failed for %s: %s", filename, e)
            self._set_progress(filename, stage="error", percent=0)
            return {"error": f"Failed to ingest file: {e}"}

        if processed == 0:
            if filepath.suffix.lower() in NON_TEXT_EXTENSIONS:
                # Nothing to embed, but the upload itself succeeded.
                self._set_progress(filename, stage="complete", current=0, total=0, percent=100)
                return {"filename": filename, "chunks": 0, "file_id": file_id, "skipped": "binary"}
            self._set_progress(filename, stage="error", percent=0)
            return {"error": "No content extracted from file"}

        self._set_progress(
            filename, stage="complete", current=processed, total=processed, percent=100
        )

        return {
            "filename": filename,
            "chunks": processed,
            "file_id": file_id,
        }

    async def query(
        self,
        query_text: str,
        top_k: int | None = None,
        filenames: list[str] | None = None,
        where: dict | None = None,
    ) -> list[dict]:
        """Query the vector store and return relevant chunks.

        Args:
            query_text: The search query.
            top_k: Max results to return.
            filenames: If provided, restrict results to these source files.
            where: Extra metadata filter (e.g. ``{"owner": "alice"}``) that is
                AND-ed with the filename filter. Used by the multi-user
                server edition to keep each user's documents private.
        """
        collection = self._get_collection()
        if collection is None:
            return []

        k = top_k or self.top_k
        try:
            query_params = {
                "query_texts": [query_text],
                "n_results": k,
            }
            clauses = []
            if filenames:
                if len(filenames) == 1:
                    clauses.append({"source": filenames[0]})
                else:
                    clauses.append({"source": {"$in": filenames}})
            if where:
                clauses.append(where)
            if len(clauses) == 1:
                query_params["where"] = clauses[0]
            elif clauses:
                query_params["where"] = {"$and": clauses}

            results = collection.query(**query_params)

            documents = results.get("documents", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            return [
                {
                    "content": doc,
                    "metadata": meta,
                    "score": 1 - dist,  # Convert distance to similarity
                }
                for doc, meta, dist in zip(documents, metadatas, distances)
            ]
        except Exception as e:
            logger.error(f"RAG query error: {e}")
            return []

    async def delete_file(self, filename: str, where: dict | None = None) -> dict:
        """Remove all chunks for a given file from the vector store."""
        collection = self._get_collection()
        if collection is None:
            return {"error": "Vector store not available"}

        try:
            cond = {"source": filename}
            if where:
                cond = {"$and": [cond, where]}
            collection.delete(where=cond)
            return {"deleted": filename}
        except Exception as e:
            return {"error": str(e)}

    async def list_files(self, where: dict | None = None) -> list[str]:
        """List all unique source files in the vector store."""
        collection = self._get_collection()
        if collection is None:
            return []
        try:
            kwargs = {"include": ["metadatas"]}
            if where:
                kwargs["where"] = where
            all_data = collection.get(**kwargs)
            sources = set()
            for meta in all_data.get("metadatas", []):
                if meta and "source" in meta:
                    sources.add(meta["source"])
            return sorted(sources)
        except Exception:
            return []

    async def sync_uploads(self, upload_dir: Path, metadata: dict | None = None) -> dict:
        """Re-ingest any uploaded files that are missing from the vector store.

        Called on startup so that files persisted on disk but absent from
        the vector store (e.g. after a container restart with a stale or
        previously-ephemeral DB) are automatically indexed.
        """
        if not upload_dir.is_dir():
            return {"synced": 0}

        indexed = set(await self.list_files(where=metadata or None))
        synced = 0
        for filepath in upload_dir.iterdir():
            if not filepath.is_file() or filepath.name.startswith("."):
                continue
            if filepath.suffix.lower() in NON_TEXT_EXTENSIONS:
                continue
            if filepath.name not in indexed:
                logger.info("Re-ingesting uploaded file: %s", filepath.name)
                await self.ingest_file(filepath, metadata=metadata)
                synced += 1

        if synced:
            logger.info("Synced %d file(s) into vector store", synced)
        return {"synced": synced}

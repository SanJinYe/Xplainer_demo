"""Retrieve external documentation snippets from pydoc or synced workspace docs."""

import hashlib
import pydoc
import re
import time
from collections import deque
from importlib import import_module
from statistics import mean
from typing import Optional

from tailevents.models.docs import (
    AuthorizedDocSnapshot,
    DocsSyncResponse,
    DocsSyncSkippedItem,
    ExternalDocChunk,
    ExternalDocMatch,
    ExternalDocSource,
)
from tailevents.models.protocols import DocRetrieverProtocol
from tailevents.storage.database import SQLiteConnectionManager
from tailevents.storage.version_store import SQLiteVersionStore


class DocMetricsTracker:
    """Track runtime retrieval and sync metrics."""

    def __init__(self, sample_limit: int = 200):
        self._retrieve = _MetricBucket(sample_limit)
        self._sync = _MetricBucket(sample_limit)

    def record_retrieve(
        self,
        *,
        total_ms: float,
        match_count: int,
        error: bool = False,
    ) -> None:
        self._retrieve.record(total_ms=total_ms, quantity=match_count, error=error)

    def record_sync(
        self,
        *,
        total_ms: float,
        accepted: int,
        error: bool = False,
    ) -> None:
        self._sync.record(total_ms=total_ms, quantity=accepted, error=error)

    def snapshot(self) -> dict[str, dict[str, float | int | None]]:
        return {
            "retrieve": self._retrieve.snapshot(),
            "sync": self._sync.snapshot(),
        }

    def reset(self) -> None:
        self._retrieve.reset()
        self._sync.reset()


class DocRetriever(DocRetrieverProtocol):
    """Resolve package documentation from local pydoc and authorized workspace docs."""

    def __init__(
        self,
        database: Optional[SQLiteConnectionManager] = None,
        version_store: Optional[SQLiteVersionStore] = None,
        telemetry: Optional[DocMetricsTracker] = None,
        max_pydoc_chars: int = 1500,
    ):
        self._database = database
        self._version_store = version_store
        self._telemetry = telemetry or DocMetricsTracker()
        self._pydoc_cache: dict[str, list[ExternalDocMatch]] = {}
        self._workspace_cache: dict[str, list[ExternalDocMatch]] = {}
        self._max_pydoc_chars = max_pydoc_chars

    def get_metrics(self) -> dict[str, dict[str, float | int | None]]:
        return self._telemetry.snapshot()

    def reset_metrics(self) -> None:
        self._telemetry.reset()

    def clear_caches(self) -> None:
        self._pydoc_cache.clear()
        self._workspace_cache.clear()

    async def retrieve(self, package: str, symbol: str) -> list[ExternalDocMatch]:
        started_at = time.perf_counter()
        try:
            pydoc_matches = self._retrieve_pydoc(package, symbol)
            workspace_matches = await self._retrieve_workspace_docs(package, symbol)
            matches = pydoc_matches + workspace_matches
            self._telemetry.record_retrieve(
                total_ms=(time.perf_counter() - started_at) * 1000,
                match_count=len(matches),
                error=False,
            )
            return matches
        except Exception:
            self._telemetry.record_retrieve(
                total_ms=(time.perf_counter() - started_at) * 1000,
                match_count=0,
                error=True,
            )
            raise

    async def sync_documents(
        self,
        snapshots: list[AuthorizedDocSnapshot],
    ) -> DocsSyncResponse:
        started_at = time.perf_counter()
        if self._database is None or self._version_store is None:
            return DocsSyncResponse(accepted=0, skipped=[], revision=0)

        skipped: list[DocsSyncSkippedItem] = []
        accepted = 0
        self._workspace_cache.clear()

        try:
            async with self._database.connection() as connection:
                await connection.execute("DELETE FROM authorized_docs")
                await connection.execute("DELETE FROM doc_chunks")
                await connection.execute("DELETE FROM doc_search")

                for snapshot in snapshots:
                    if not _is_supported_doc_file(snapshot.file_path):
                        skipped.append(
                            DocsSyncSkippedItem(
                                file_path=snapshot.file_path,
                                reason="unsupported_file_type",
                            )
                        )
                        continue

                    await connection.execute(
                        """
                        INSERT INTO authorized_docs (file_path, content_hash, content)
                        VALUES (?, ?, ?)
                        """,
                        (snapshot.file_path, snapshot.content_hash, snapshot.content),
                    )
                    chunks = _chunk_document(snapshot.content)
                    for index, chunk_content in enumerate(chunks):
                        chunk_id = hashlib.sha1(
                            f"{snapshot.file_path}:{index}:{snapshot.content_hash}".encode("utf-8")
                        ).hexdigest()
                        await connection.execute(
                            """
                            INSERT INTO doc_chunks (
                                chunk_id,
                                file_path,
                                content_hash,
                                chunk_index,
                                content
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                chunk_id,
                                snapshot.file_path,
                                snapshot.content_hash,
                                index,
                                chunk_content,
                            ),
                        )
                        await connection.execute(
                            """
                            INSERT INTO doc_search (chunk_id, file_path, content)
                            VALUES (?, ?, ?)
                            """,
                            (chunk_id, snapshot.file_path, chunk_content),
                        )
                    accepted += 1

                await connection.commit()

            revision = await self._version_store.bump("docs_version")
            self._telemetry.record_sync(
                total_ms=(time.perf_counter() - started_at) * 1000,
                accepted=accepted,
                error=False,
            )
            return DocsSyncResponse(accepted=accepted, skipped=skipped, revision=revision)
        except Exception:
            self._telemetry.record_sync(
                total_ms=(time.perf_counter() - started_at) * 1000,
                accepted=accepted,
                error=True,
            )
            raise

    def _retrieve_pydoc(self, package: str, symbol: str) -> list[ExternalDocMatch]:
        cache_key = f"{package}:{symbol}"
        if cache_key in self._pydoc_cache:
            return self._pydoc_cache[cache_key]

        rendered = None
        resolved_symbol = symbol
        for candidate in self._build_candidates(package, symbol):
            try:
                target = pydoc.locate(candidate)
                if target is None and candidate == package:
                    import_module(package)
                    target = package
                if target is None:
                    continue
                rendered = pydoc.plain(pydoc.render_doc(target)).strip()
                resolved_symbol = candidate.rsplit(".", 1)[-1]
                if rendered:
                    break
            except Exception:
                continue

        if not rendered:
            self._pydoc_cache[cache_key] = []
            return []

        match = ExternalDocMatch(
            source=ExternalDocSource(
                kind="pydoc",
                package=package,
                symbol=resolved_symbol,
            ),
            chunk=ExternalDocChunk(
                chunk_id=f"pydoc:{package}:{resolved_symbol}",
                content=_truncate(rendered, self._max_pydoc_chars),
            ),
            usage_pattern="direct_call",
            score=100.0,
        )
        self._pydoc_cache[cache_key] = [match]
        return [match]

    async def _retrieve_workspace_docs(self, package: str, symbol: str) -> list[ExternalDocMatch]:
        if self._database is None:
            return []

        cache_key = f"{package}:{symbol}"
        if cache_key in self._workspace_cache:
            return self._workspace_cache[cache_key]

        query = _build_fts_query(package, symbol)
        if not query:
            self._workspace_cache[cache_key] = []
            return []

        async with self._database.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT s.chunk_id, s.file_path, s.content, c.content_hash, bm25(doc_search) AS score
                FROM doc_search s
                JOIN doc_chunks c ON c.chunk_id = s.chunk_id
                WHERE doc_search MATCH ?
                ORDER BY bm25(doc_search), s.file_path ASC
                LIMIT 4
                """,
                (query,),
            )
            rows = await cursor.fetchall()
            await cursor.close()

        matches = [
            ExternalDocMatch(
                source=ExternalDocSource(
                    kind="workspace_doc",
                    package=package,
                    symbol=symbol,
                    file_path=str(row["file_path"]),
                ),
                chunk=ExternalDocChunk(
                    chunk_id=str(row["chunk_id"]),
                    content=str(row["content"]),
                    content_hash=str(row["content_hash"]),
                ),
                usage_pattern="direct_call",
                score=float(-row["score"]) if row["score"] is not None else 0.0,
            )
            for row in rows
        ]
        self._workspace_cache[cache_key] = matches
        return matches

    def _build_candidates(self, package: str, symbol: str) -> list[str]:
        candidates = []
        if symbol:
            candidates.append(symbol)
        if symbol and not symbol.startswith(f"{package}."):
            candidates.append(f"{package}.{symbol}")
        candidates.append(package)
        return list(dict.fromkeys(candidate for candidate in candidates if candidate))


class _MetricBucket:
    def __init__(self, sample_limit: int):
        self._total_ms: deque[float] = deque(maxlen=sample_limit)
        self._quantities: deque[int] = deque(maxlen=sample_limit)
        self._requests = 0
        self._errors = 0

    def record(self, *, total_ms: float, quantity: int, error: bool) -> None:
        self._requests += 1
        if error:
            self._errors += 1
        self._total_ms.append(total_ms)
        self._quantities.append(quantity)

    def snapshot(self) -> dict[str, float | int | None]:
        ordered = sorted(self._total_ms)
        p95 = None
        if ordered:
            index = int(round((len(ordered) - 1) * 0.95))
            p95 = round(float(ordered[index]), 2)
        return {
            "requests": self._requests,
            "errors": self._errors,
            "avg_ms": round(mean(self._total_ms), 2) if self._total_ms else 0.0,
            "avg_matches": round(mean(self._quantities), 2) if self._quantities else 0.0,
            "p95_ms": p95,
        }

    def reset(self) -> None:
        self._total_ms.clear()
        self._quantities.clear()
        self._requests = 0
        self._errors = 0


def _build_fts_query(package: str, symbol: str) -> str:
    tokens = [_sanitize_token(package), _sanitize_token(symbol)]
    filtered = [token for token in tokens if token]
    return " ".join(filtered)


def _sanitize_token(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]+", " ", value or "").strip()
    if not normalized:
        return ""
    return " ".join(dict.fromkeys(normalized.split()))


def _is_supported_doc_file(file_path: str) -> bool:
    lowered = file_path.lower()
    return lowered.endswith(".md") or lowered.endswith(".txt")


def _chunk_document(content: str) -> list[str]:
    blocks = _split_blocks(content)
    if not blocks:
        return []

    merged: list[str] = []
    current = ""
    for block in blocks:
        if len(block) < 80 and current:
            candidate = f"{current}\n\n{block}".strip()
            if len(candidate) <= 800:
                current = candidate
                continue
        candidate = f"{current}\n\n{block}".strip() if current else block
        if len(candidate) <= 800:
            current = candidate
            continue
        if current:
            merged.append(current)
        if len(block) <= 800:
            current = block
            continue
        for piece in _split_large_block(block):
            if len(piece) < 80 and merged:
                merged[-1] = f"{merged[-1]}\n\n{piece}".strip()
            else:
                merged.append(piece)
        current = ""

    if current:
        merged.append(current)
    return merged


def _split_blocks(content: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    pending_heading = ""

    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        if line.lstrip().startswith("#"):
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            pending_heading = line.strip()
            continue
        if pending_heading:
            current.append(pending_heading)
            pending_heading = ""
        current.append(line)

    if pending_heading:
        blocks.append(pending_heading)
    if current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


def _split_large_block(block: str) -> list[str]:
    pieces: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?])\s+", block):
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= 800:
            current = candidate
            continue
        if current:
            pieces.append(current)
        current = sentence[:800]
    if current:
        pieces.append(current)
    return pieces


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 3].rstrip()}..."


__all__ = ["DocMetricsTracker", "DocRetriever"]

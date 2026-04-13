"""Retrieve documentation snippets for external dependencies."""

import pydoc
from importlib import import_module
from typing import Optional

from tailevents.models.protocols import DocRetrieverProtocol


class DocRetriever(DocRetrieverProtocol):
    """Resolve package documentation from a local cache or pydoc."""

    def __init__(self, max_chars: int = 1500):
        self._cache: dict[str, Optional[str]] = {}
        self._max_chars = max_chars

    async def retrieve(self, package: str, symbol: str) -> Optional[str]:
        cache_key = f"{package}:{symbol}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        snippet = self._render_doc(package, symbol)
        self._cache[cache_key] = snippet
        return snippet

    def _render_doc(self, package: str, symbol: str) -> Optional[str]:
        for candidate in self._build_candidates(package, symbol):
            try:
                target = pydoc.locate(candidate)
                if target is None and candidate == package:
                    import_module(package)
                    target = package
                if target is None:
                    continue
                rendered = pydoc.plain(pydoc.render_doc(target)).strip()
            except Exception:
                continue

            if rendered:
                return self._truncate(rendered)
        return None

    def _build_candidates(self, package: str, symbol: str) -> list[str]:
        candidates = []
        if symbol:
            candidates.append(symbol)
        if symbol and not symbol.startswith(f"{package}."):
            candidates.append(f"{package}.{symbol}")
        candidates.append(package)
        return list(dict.fromkeys(candidate for candidate in candidates if candidate))

    def _truncate(self, text: str) -> str:
        if len(text) <= self._max_chars:
            return text
        return f"{text[: self._max_chars].rstrip()}..."


__all__ = ["DocRetriever"]

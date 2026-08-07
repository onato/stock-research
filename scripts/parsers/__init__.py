"""Per-exchange report parsers, registered by listing suffix.

Adding an exchange is a three-file affair, none of them shared:
  scripts/parsers/{country}.py          — BaseParser subclass, SUFFIXES set
  tests/parsers/test_{country}.py       — starts as a failing test
  tests/fixtures/extracted/{country}/   — trimmed real filing excerpts

Discovery is automatic: any module in this package exposing a BaseParser
subclass with a non-empty SUFFIXES registers itself. Unregistered suffixes
(and bare US symbols, which normally take the XBRL path) fall back to the
generic BaseParser.
"""

import importlib
import pkgutil

from .base import BaseParser

_REGISTRY: dict[str, type[BaseParser]] = {}


def _discover() -> None:
    for m in pkgutil.iter_modules(__path__):
        mod = importlib.import_module(f"{__name__}.{m.name}")
        for obj in vars(mod).values():
            if (isinstance(obj, type) and issubclass(obj, BaseParser)
                    and obj is not BaseParser and obj.SUFFIXES):
                for s in obj.SUFFIXES:
                    _REGISTRY[s.upper()] = obj


_discover()


def get_parser(ticker: str) -> BaseParser:
    """Parser instance for a ticker's listing suffix ('AGL.NZ' -> NZX)."""
    suffix = ticker.rsplit(".", 1)[1].upper() if "." in ticker else ""
    return _REGISTRY.get(suffix, BaseParser)()

"""Registry routing and the build_facts facade API that exchange_eval.py uses."""

from pathlib import Path

import build_facts as bf
from parsers import BaseParser, get_parser


class TestRouting:
    def test_unregistered_suffix_gets_generic_fallback(self):
        assert type(get_parser("PNG.V")) is BaseParser
        assert type(get_parser("FIH.U")) is BaseParser

    def test_bare_us_symbol_gets_generic_fallback(self):
        assert type(get_parser("AAPL")) is BaseParser

    def test_registered_parsers_are_subclasses(self):
        # Every registered parser must be a BaseParser subclass; the set
        # grows one module at a time (open/closed), so no fixed list here.
        from parsers import _REGISTRY
        for cls in _REGISTRY.values():
            assert issubclass(cls, BaseParser)
            assert cls is not BaseParser
            assert cls.SUFFIXES


class TestFacadeApi:
    def test_scan_file_and_patterns_survive(self, tmp_path):
        # exchange_eval.py imports build_facts and uses exactly these two.
        assert isinstance(bf.PATTERNS, dict)
        assert "Revenue" in bf.PATTERNS
        f = tmp_path / "X_Annual_FY2024.txt"
        f.write_text("Revenue      263,527      267,805\n")
        facts = list(bf.scan_file(f))
        assert [x["value_raw"] for x in facts] == [263527.0, 267805.0]

    def test_ticker_routing_from_research_layout(self, tmp_path):
        # research/{TICKER}/Extracted/x.txt routes by the directory name...
        p = tmp_path / "research" / "AGL.NZ" / "Extracted" / "AGL.NZ_Annual_FY2020.txt"
        assert bf.ticker_for(p) == "AGL.NZ"
        # ...anything else falls back to the filename's {TICKER}_ prefix.
        assert bf.ticker_for(Path("/tmp/0285.HK_Annual_FY2024.txt")) == "0285.HK"

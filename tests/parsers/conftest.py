"""Fixture loading for parser tests.

Fixtures under tests/fixtures/extracted/{country}/ are committed trimmed
excerpts of real filings (tens of lines), never live research/ reads. Each
reproduces one layout or units/currency behavior for its exchange.
"""

from pathlib import Path

import pytest

EXTRACTED = Path(__file__).parent.parent / "fixtures" / "extracted"


@pytest.fixture
def fixture_path():
    def _path(country, name):
        return EXTRACTED / country / name

    return _path


@pytest.fixture
def fixture_text(fixture_path):
    def _text(country, name):
        return fixture_path(country, name).read_text()

    return _text

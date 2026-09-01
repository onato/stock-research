"""Re-embed a corrected DCF JSON into an already-built dashboard.

Dashboards inline the whole DCF as `const dcfData = {...}` so they open from
file://. When only the DCF JSON changes -- as with the 2026-09-01 entry-price
correction -- the page can be refreshed by swapping that literal, without
re-running the dashboard-generator agent (41 of the 55 affected tickers have no
committed DashboardSpec.json and cannot be rebuilt any other way).
"""
import json

import pytest
import reembed_dcf as R

HTML = (
    "<html><script>\n"
    "const csvData = `Period,Revenue\\nFY2025,100`;\n"
    'const dcfData = {\n  "entry_price": {"base": {"entry_price": 86.18}}\n};\n'
    "function render(){return dcfData;}\n"
    "</script></html>"
)


def test_replaces_the_embedded_json():
    new = {"entry_price": {"base": {"entry_price": 55.87}}}
    out = R.reembed(HTML, new)
    assert json.loads(R.extract(out)) == new


def test_preserves_everything_around_it():
    out = R.reembed(HTML, {"x": 1})
    assert "const csvData" in out
    assert "function render(){return dcfData;}" in out
    assert out.startswith("<html><script>")


def test_keeps_the_trailing_semicolon_and_is_valid_js():
    out = R.reembed(HTML, {"x": 1})
    assert "};\nfunction render" in out


def test_raises_when_no_embedded_dcf():
    with pytest.raises(R.NoEmbeddedDCFError):
        R.reembed("<html>nothing here</html>", {"x": 1})


def test_is_idempotent():
    new = {"entry_price": {"base": {"entry_price": 55.87}}}
    once = R.reembed(HTML, new)
    assert R.reembed(once, new) == once


def test_handles_nested_braces_in_strings():
    """A brace inside a JSON string must not end the literal early."""
    html = HTML.replace('"entry_price": 86.18',
                        '"note": "a } brace", "entry_price": 86.18')
    out = R.reembed(html, {"ok": True})
    assert json.loads(R.extract(out)) == {"ok": True}


def test_non_json_object_literal_is_reported_not_crashed():
    """Some pages embed a JS object literal with unquoted keys. We cannot
    safely compare or rewrite that, so it must be skipped, never crash."""
    html = HTML.replace('"entry_price": {"base": {"entry_price": 86.18}}',
                        'entry_price: {base: {entry_price: 86.18}}')
    with pytest.raises(R.NotStrictJSONError):
        R.parse_embedded(html)

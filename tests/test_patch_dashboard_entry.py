"""Patch the rendered entry-price element in an already-built dashboard.

The 2026-09-01 entry-price correction changed a number the page also renders
outside the embedded `dcfData` literal, in `<div id="dcfEntry">`. Re-embedding
the JSON alone leaves that display stale.

This patches ONLY that element. Prose elsewhere on the page (warning banners,
KPI subtitles) can carry reasoning that the corrected number invalidates -- for
example KMD.NZ's "Entry price sits ABOVE base intrinsic value because WACC >
hurdle", which stops being true once the entry price falls. Rewriting that is a
judgement call, so those pages are reported for review instead.
"""
import patch_dashboard_entry as P
import pytest

PAGE = (
    '<div class="dcf-card">\n'
    '  <div class="dcf-label">Entry Price (15% CAGR)</div>\n'
    '  <div class="dcf-value" id="dcfEntry">$2.19</div>\n'
    "</div>\n"
)


def test_patches_the_rendered_element():
    out, n = P.patch(PAGE, 1.54, "$")
    assert '<div class="dcf-value" id="dcfEntry">$1.54</div>' in out
    assert n == 1


def test_preserves_the_currency_prefix():
    out, _ = P.patch(PAGE.replace("$2.19", "NZ$2.19"), 1.54, "NZ$")
    assert ">NZ$1.54<" in out


def test_reports_zero_when_absent():
    _, n = P.patch("<div>nothing</div>", 1.54, "$")
    assert n == 0


def test_is_idempotent():
    once, _ = P.patch(PAGE, 1.54, "$")
    twice, n = P.patch(once, 1.54, "$")
    assert once == twice
    assert n == 1


def test_formats_sub_dollar_values_to_matching_precision():
    """A $0.58 entry must not render as $0.6566."""
    page = PAGE.replace("$2.19", "$0.58")
    out, _ = P.patch(page, 0.6566, "$")
    assert ">$0.66<" in out


@pytest.mark.parametrize(("value", "expected"),
                         [(-0.0117, "-$0.01"), (2673.52, "$2,673.52")])
def test_negative_and_large_values(value, expected):
    out, _ = P.patch(PAGE, value, "$")
    assert f">{expected}<" in out

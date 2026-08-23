"""Period label parsing -- the keystone for every cross-ticker derivation.

The corpus holds 2,274 distinct labels in 13 format families. Nothing can
compute a TTM or a CAGR without knowing that `H1 FY2026` and `H1-2026` name
the same six months, or that `FY2017-15mo` is not a comparable year.
"""

import pathlib

import export_csv
import periods
import pytest

CORPUS = pathlib.Path(__file__).parent / "fixtures" / "screener" / "periods_corpus.txt"


def corpus_labels() -> list[str]:
    return [
        line.strip()
        for line in CORPUS.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


class TestParseFamilies:
    @pytest.mark.parametrize(
        ("label", "year", "ptype", "months"),
        [
            ("FY2006", 2006, "FY", 12),
            ("FY2024", 2024, "FY", 12),
            ("Q1 2009", 2009, "Q1", 3),
            ("Q3 2025", 2025, "Q3", 3),
            ("Q1-2017", 2017, "Q1", 3),
            ("Q3-2025", 2025, "Q3", 3),
            ("Q1 FY2020", 2020, "Q1", 3),
            ("Q4 FY2026", 2026, "Q4", 3),
            ("H1 2015", 2015, "H1", 6),
            ("H1-2008", 2008, "H1", 6),
            ("H1 FY2017", 2017, "H1", 6),
            ("H2 FY2019", 2019, "H2", 6),
            ("H1-FY2022", 2022, "H1", 6),
            ("9M 2021", 2021, "9M", 9),
        ],
    )
    def test_family(self, label, year, ptype, months):
        p = periods.parse(label)
        assert (p.fiscal_year, p.ptype, p.months) == (year, ptype, months)
        assert p.raw == label

    def test_every_corpus_label_parses(self):
        for label in corpus_labels():
            p = periods.parse(label)
            assert p.raw == label
            # Only the deliberately-irregular labels may be OTHER.
            if p.ptype == "OTHER":
                assert label in periods.IRREGULAR_LABELS


class TestIrregulars:
    """The 4 bespoke labels are table-driven -- never inferred from a regex."""

    @pytest.mark.parametrize(
        ("label", "year", "ptype", "months"),
        [
            ("FY2016-Jun", 2016, "FY", 12),        # a normal year, month-qualified
            ("FY2017-15mo", 2017, "OTHER", 15),
            ("FY2018-6moStub", 2018, "OTHER", 6),
            ("FY2021 (10mo)", 2021, "OTHER", 10),
        ],
    )
    def test_irregular(self, label, year, ptype, months):
        p = periods.parse(label)
        assert (p.fiscal_year, p.ptype, p.months) == (year, ptype, months)

    def test_non_twelve_month_years_are_not_annual(self):
        """A 15-month year summed into a TTM is a plausible-wrong number."""
        assert not periods.is_annual(periods.parse("FY2017-15mo"))
        assert not periods.is_annual(periods.parse("FY2018-6moStub"))
        assert not periods.is_annual(periods.parse("FY2021 (10mo)"))
        assert periods.is_annual(periods.parse("FY2016-Jun"))
        assert periods.is_annual(periods.parse("FY2024"))


class TestEquivalence:
    """Separator and FY-prefix spellings name the same period.

    The year in `H1 2026` is the FISCAL year, verified against SPK.NZ:
    `H1 2026` revenue 1917 is half of `FY2026`, not of `FY2025`.
    """

    def test_half_year_spellings_agree(self):
        parsed = [periods.parse(s) for s in
                  ("H1 2026", "H1-2026", "H1 FY2026", "H1-FY2026")]
        assert {(p.fiscal_year, p.ptype, p.months) for p in parsed} == {(2026, "H1", 6)}

    def test_quarter_hyphen_and_space_agree(self):
        """The PYPL case: the CSV wrote `Q1-2020`, the DB wrote `Q1 2020`."""
        a, b = periods.parse("Q1-2020"), periods.parse("Q1 2020")
        assert (a.fiscal_year, a.ptype) == (b.fiscal_year, b.ptype) == (2020, "Q1")
        assert a.sort_key[:2] == b.sort_key[:2]


class TestSortKeyMatchesExportCsv:
    """export_csv.sort_key now delegates here, so the two cannot diverge.

    Before that, the incumbent looped over tokens and let a trailing `FY2020`
    overwrite the sub-rank already set by a leading `Q1`, collapsing every
    such interim onto its own full year. WISE.L's CSV showed the damage --
    `FY2023, Q1 FY2023, Q2 FY2023, ...` -- and 21 committed CSVs were written
    that way before the fix.
    """

    def test_identical_for_every_corpus_label(self):
        for label in corpus_labels():
            assert periods.sort_key(label) == export_csv.sort_key(label), label

    @pytest.mark.parametrize(
        ("label", "sub"), [("Q1 FY2020", 1), ("Q4 FY2026", 4), ("H1 FY2017", 2)])
    def test_interim_fy_labels_sort_before_their_full_year(self, label, sub):
        assert periods.sort_key(label)[1] == sub
        year = periods.parse(label).fiscal_year
        assert periods.sort_key(label) < periods.sort_key(f"FY{year}")

    def test_quarters_of_a_fiscal_year_order_correctly(self):
        labels = ["FY2020", "Q3 FY2020", "Q1 FY2020", "Q4 FY2020", "Q2 FY2020"]
        assert sorted(labels, key=periods.sort_key) == [
            "Q1 FY2020", "Q2 FY2020", "Q3 FY2020", "Q4 FY2020", "FY2020"]

    def test_orders_parts_before_their_full_year(self):
        # Within a year: Q1 < H1 < Q3 < H2 < FY (sub-ranks 1,2,3,4,9), so the
        # full year always trails the interims it contains.
        labels = ["FY2025", "H1-2025", "Q3 2025", "FY2024"]
        assert sorted(labels, key=periods.sort_key) == [
            "FY2024", "H1-2025", "Q3 2025", "FY2025"]


class TestGarbage:
    """A missing period is obvious; a plausible wrong one is not."""

    @pytest.mark.parametrize("label", [None, "", "   ", "banana", "FY", "Q9 2020"])
    def test_unparseable_is_other_and_never_raises(self, label):
        p = periods.parse(label)
        assert p.ptype == "OTHER"
        assert p.months is None

    def test_unparseable_has_no_fiscal_year(self):
        assert periods.parse("banana").fiscal_year is None


class TestPriorYear:
    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("FY2026", "FY2025"),
            ("H1 FY2026", "H1 FY2025"),
            ("H1-2026", "H1 FY2025"),
            ("Q3 2025", "Q3 FY2024"),
        ],
    )
    def test_prior_year_is_canonical(self, label, expected):
        assert periods.prior_year(periods.parse(label)) == expected

    def test_prior_year_of_unparseable_is_none(self):
        assert periods.prior_year(periods.parse("banana")) is None


class TestStubYears:
    """`FY####-#mo` names a fiscal year shortened by a balance-date change.

    NZK.NZ changed its balance date twice -- to 31 Jan (a 7-month stub) and
    then to 30 Sep (an 8-month stub) -- so `FY2021-7mo` and `FY2025-8mo`
    both appear in its metrics. Before this rule only the four hardcoded
    IRREGULAR labels parsed, and a single-digit stub fell through every
    regex to `fiscal_year=None`, which sorts it to the top of the CSV as
    year 0. The month count is inferred, but `is_annual` stays False: a
    7-month year summed into a TTM is exactly the plausible-wrong number
    the irregular table exists to prevent.
    """

    @pytest.mark.parametrize(
        ("label", "year", "months"),
        [
            ("FY2021-7mo", 2021, 7),
            ("FY2025-8mo", 2025, 8),
            ("FY2020-10mo", 2020, 10),
            ("FY2019-15mo", 2019, 15),
        ],
    )
    def test_stub_parses_with_year_and_length(self, label, year, months):
        p = periods.parse(label)
        assert (p.fiscal_year, p.ptype, p.months) == (year, "OTHER", months)

    def test_stub_is_never_annual(self):
        assert not periods.is_annual(periods.parse("FY2021-7mo"))
        assert not periods.is_annual(periods.parse("FY2025-8mo"))

    def test_stub_sorts_chronologically(self):
        """The bug this fixes: a stub sorted to year 0, ahead of FY2016."""
        labels = ["FY2024", "FY2021-7mo", "FY2016", "FY2025-8mo"]
        assert sorted(labels, key=periods.sort_key) == [
            "FY2016", "FY2021-7mo", "FY2024", "FY2025-8mo",
        ]

    def test_hardcoded_irregulars_still_win(self):
        """`FY2018-6moStub` has a trailing word the general rule won't match."""
        p = periods.parse("FY2018-6moStub")
        assert (p.fiscal_year, p.months) == (2018, 6)


class TestMonthQualifiedLabels:
    """`FY2025-Jan` names a fiscal year by the month it ended in.

    A balance-date change makes the bare year ambiguous: NZK.NZ has both a
    12-month year to 31 Jan 2025 and an 8-month stub to 30 Sep 2025, and
    both would otherwise be `FY2025`. Qualifying with the end month
    disambiguates them. `FY2016-Jun` was already in the corpus as a
    hardcoded irregular; this generalises it so a new one does not need a
    code change to sort correctly.

    The month suffix says nothing about length, so a qualified FY keeps
    FY's 12 months and a qualified H1 keeps H1's 6 -- the suffix only
    disambiguates WHICH period, never how long it was.
    """

    @pytest.mark.parametrize(
        ("label", "year", "ptype", "months"),
        [
            ("FY2025-Jan", 2025, "FY", 12),
            ("FY2016-Jun", 2016, "FY", 12),      # the incumbent hardcoded one
            ("H1 FY2026-Jul", 2026, "H1", 6),
            ("H1 FY2026-Mar", 2026, "H1", 6),
        ],
    )
    def test_month_qualified(self, label, year, ptype, months):
        p = periods.parse(label)
        assert (p.fiscal_year, p.ptype, p.months) == (year, ptype, months)

    def test_qualified_year_is_annual(self):
        """A month-qualified full year is still a comparable 12 months."""
        assert periods.is_annual(periods.parse("FY2025-Jan"))

    def test_qualified_sorts_with_its_year(self):
        labels = ["FY2024", "H1 FY2026-Jul", "FY2025-Jan", "FY2016"]
        assert sorted(labels, key=periods.sort_key) == [
            "FY2016", "FY2024", "FY2025-Jan", "H1 FY2026-Jul",
        ]

    def test_a_bare_month_word_is_not_a_year(self):
        """Guard the regex: only a 3-letter month follows the year."""
        assert periods.parse("FY2025-Restated").fiscal_year is None

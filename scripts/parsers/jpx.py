"""Tokyo Stock Exchange (.T) filings — 決算短信 (tanshin).

The tanshin is the statement-bearing filing for a Tokyo listing: a compact
PDF carrying the full income statement, balance sheet and cash flow
statement, filed quarterly. Companies publish them in Japanese even when
their glossy annual report is bilingual, so a `.T` ticker whose facts come
only from English annual reports has three or four periods of balance-sheet
history where twenty are available.

Four structural quirks, none of them one company's formatting:

1. **CJK labels.** ``common.LABEL_RE`` anchors on ``[A-Za-z(]`` and rejects
   every line of a Japanese statement, so the generic parser yields nothing.

2. **``△`` is the minus sign** (``▲`` in older filings). Read as a bare
   label character, ``△397,547`` parses as +397,547 — a sign error on
   every cost, outflow and loss in the file.

3. **Units are declared as ``（単位：百万円）``** — millions of yen. The
   generic UNITS_RE looks for the English word and finds nothing, and the
   front page's ``(百万円未満切捨て)`` ("truncated below one million yen")
   says the same thing in a different phrasing.

4. **Column order is reversed.** A Japanese statement prints 前連結会計年度
   (prior year) in the FIRST numeric column and 当連結会計年度 (current
   year) in the SECOND — the opposite of the Western layout BaseParser
   assumes. Taking column 0 as the reporting period would file every figure
   one fiscal year late, which is the period-shift error class the project
   already fought once on filenames.

   The front summary page is the exception: it tabulates one fiscal year
   per ROW, each row labelled ``2026年３月期``. That label is authoritative,
   so summary rows are emitted against their own stated year and the
   reversed-column rule is confined to the two-column statements below.

The vocabulary additions beyond the core metrics are ``MinorityInterest``
and ``LeasePrincipalPaid``: both are candidate-only metrics (the facts
table takes any metric string), and both are required deductions for an
owner-FCF valuation of a Japanese group — NCI routinely takes a quarter of
group profit, and IFRS-16 lease principal sits in financing.
"""

import re
from collections.abc import Iterator
from typing import Any, ClassVar

import periods

from . import common
from .base import BaseParser

# Metric -> Japanese label regexes, matched against the line's label cell.
# Anchored with ^ where the label is a whole statement line; the few
# unanchored entries are labels that appear indented under a heading.
JP_PATTERNS: dict[str, list[str]] = {
    # 売上収益 (IFRS) / 売上高 (JGAAP) / 営業収益 (service filers).
    "Revenue": [r"^売上収益$", r"^売上高$", r"^営業収益$"],
    "CostOfRevenue": [r"^売上原価$"],
    "GrossProfit": [r"^売上総利益", r"^売上総損失"],
    # 営業利益 alone. "営業活動によるキャッシュ・フロー" also starts with 営業
    # but is excluded by the anchor + terminator.
    "OperatingIncome": [r"^営業利益", r"^営業損失"],
    "ProfitBeforeTax": [r"^税引前利益", r"^税引前(当期|中間|四半期)?純?利益",
                        r"^税金等調整前当期純利益"],
    # Both the group figure (当期利益) and the attributable figure
    # (親会社の所有者に帰属する当期利益) are candidates; with NCI often ~25% of
    # group profit the agent must be able to see and choose between them.
    "NetIncome": [r"^親会社の所有者に帰属する(当期|中間|四半期)利益",
                  r"^親会社株主に帰属する(当期|中間|四半期)純利益",
                  r"^(当期|中間|四半期)利益$", r"^(当期|中間|四半期)純利益$"],
    "MinorityInterest": [r"^非支配持分に帰属する(当期|中間|四半期)(純)?利益",
                         r"^非支配持分$"],
    "EPS": [r"^基本的１株当たり(当期|中間|四半期)利益",
            r"^１株当たり(当期|中間|四半期)純利益",
            r"^基本的1株当たり", r"^1株当たり(当期|中間|四半期)純利益"],
    "OperatingCashFlow": [r"^営業活動によるキャッシュ・フロー$"],
    "CapEx": [r"^有形固定資産の取得による支出", r"^固定資産の取得による支出",
              r"^有形及び無形固定資産の取得による支出"],
    "Depreciation": [r"^減価償却費", r"^減価償却費及び償却費", r"^減価償却及び償却費"],
    # 資本合計 is group equity (includes NCI); 親会社の所有者に帰属する持分合計
    # is what an equity holder owns. Both emitted, agent chooses.
    "ShareholdersEquity": [r"^親会社の所有者に帰属する持分合計", r"^資本合計$",
                           r"^株主資本合計$", r"^純資産合計$"],
    "TotalAssets": [r"^資産合計$"],
    "TotalLiabilities": [r"^負債合計$"],
    "TotalDebt": [r"^(短期|長期)借入金$", r"^社債及び借入金", r"^借入金$"],
    "CashAndEquivalents": [r"^現金及び現金同等物$",
                           r"^現金及び現金同等物の(期末|中間期末|四半期末)残高"],
    "InterestIncome": [r"^利息の受取額", r"^受取利息"],
    "InterestExpense": [r"^利息の支払額", r"^支払利息"],
    "DividendsPaid": [r"^配当金の支払額"],
    "ShareRepurchases": [r"^自己株式の取得による支出"],
    "CashTaxesPaid": [r"^法人所得税等の支払額", r"^法人税等の支払額"],
    # IFRS-16 principal, in financing. A required owner-FCF deduction.
    "LeasePrincipalPaid": [r"^リース債務の返済による支出",
                           r"^リース負債の返済による支出"],
    "SharesOutstanding": [r"^期末発行済株式数", r"^発行済株式数",
                          r"^期中平均株式数"],
}
JP_COMPILED: dict[str, list[re.Pattern[str]]] = {
    m: [re.compile(p) for p in pats] for m, pats in JP_PATTERNS.items()}


class JPXParser(BaseParser):
    SUFFIXES = ("T",)

    # "（単位：百万円）", "(単位:百万円)", "(百万円未満切捨て)", "百万円".
    UNITS_JP_RE = re.compile(r"[（(]?\s*単位\s*[：:]\s*(百万円|千円|億円|円)"
                             r"|(百万円|千円|億円)未満")
    UNITS_WORD: ClassVar[dict[str, str | None]] = {
        "百万円": "millions", "千円": "thousands",
        "億円": "hundred_millions", "円": None}

    # △ and ▲ are the Japanese minus signs; ─/－ are dashes for nil.
    NEG_CHARS = "△▲"

    # A fiscal-year row label on the summary page: 2026年３月期 (year ended
    # March 2026), 2026年3月期 第２四半期, etc. Full-width digits included.
    ROW_PERIOD_RE = re.compile(
        r"^(\d{4}|[０-９]{4})年\s*([0-9０-９]{1,2}|[一二三四五六七八九十]{1,2})月期"
        r"\s*(?:第([0-9０-９一二三四五六七八九])四半期|中間期)?")

    # The filing's own period, from the tanshin title line.
    TITLE_PERIOD_RE = re.compile(
        r"(\d{4}|[０-９]{4})年\s*([0-9０-９]{1,2})月期\s*"
        r"(?:第([0-9０-９])四半期(?:（中間期）|\(中間期\))?|中間期)?")

    ZEN2HAN = str.maketrans("０１２３４５６７８９", "0123456789")

    # Column headers naming which fiscal year a numeric column belongs to.
    PRIOR_COL_RE = re.compile(r"前(連結会計年度|事業年度|第[0-9０-９]四半期|中間)")
    CURRENT_COL_RE = re.compile(r"当(連結会計年度|事業年度|第[0-9０-９]四半期|中間)")

    # ------------------------------------------------------------------

    def scan(self, text: str, filename: str,
             fy_end_month: int | None = None) -> Iterator[dict[str, Any]]:
        lines = common.split_lines(text)
        period = self._filing_period(lines) or common.period_from_filename(filename)
        units = self.units_hint(lines)
        prior = (periods.prior_year(periods.parse(period)) if period else None)

        for i, line in enumerate(lines):
            label, nums = self._split_jp(line)
            if not label or not nums:
                continue

            # A summary-page row states its own fiscal year: "2026年３月期
            # 442,316 ...". That label wins over any column convention.
            row_period = self._row_period(label)
            if row_period is not None:
                yield from self._emit_summary_row(
                    lines, i, row_period, nums, units, filename)
                continue

            for metric, regexes in JP_COMPILED.items():
                if not any(r.search(label) for r in regexes):
                    continue
                ctx = "\n".join(lines[max(0, i - 2):i + 3])
                # Reversed order: prior year first, current year second.
                # A single-column line is the current period.
                cols = ([(prior, nums[0]), (period, nums[1])] if len(nums) >= 2
                        else [(period, nums[0])])
                for col_period, val in cols:
                    yield {
                        "metric": metric,
                        "period": col_period,
                        "value_raw": val,
                        "units_hint": units,
                        "source_file": filename,
                        "line_no": i + 1,
                        "context": ctx[:600],
                        "confidence": ("statement_line" if col_period == period
                                       else "prior_year_column"),
                        "currency": "JPY",
                    }
                break

    # ------------------------------------------------------------------
    # Summary page: one fiscal year per row, metrics named by the column
    # headers printed above. Emit the row's figures against the row's own
    # stated year, matching each value to the header stack above it.
    # ------------------------------------------------------------------

    # Section headings on the tanshin front page, each followed by a header
    # block naming the columns of the rows beneath it.
    SUMMARY_SECTIONS: ClassVar[list[tuple[re.Pattern[str], list[str | None]]]] = [
        (re.compile(r"連結経営成績"),
         ["Revenue", "OperatingIncome", "ProfitBeforeTax", "NetIncome"]),
        (re.compile(r"連結財政状態"),
         ["TotalAssets", "ShareholdersEquity"]),
        (re.compile(r"連結キャッシュ・フローの状況"),
         ["OperatingCashFlow", None, None, "CashAndEquivalents"]),
    ]

    def _emit_summary_row(self, lines: list[str], i: int, row_period: str,
                          nums: list[float], units: str | None, filename: str
                          ) -> Iterator[dict[str, Any]]:
        """Map a summary row's values onto the section's column metrics.

        The 経営成績 row interleaves values with percent-change figures
        (442,316 △4.0 10,325 △37.2 ...), so that section takes every
        second number. The others are plain value columns.
        """
        section = self._section_for(lines, i)
        if section is None:
            return
        metrics, interleaved = section
        vals = nums[::2] if interleaved else nums
        for metric, val in zip(metrics, vals, strict=False):
            if metric is None:
                continue
            yield {
                "metric": metric,
                "period": row_period,
                "value_raw": val,
                "units_hint": units,
                "source_file": filename,
                "line_no": i + 1,
                "context": "\n".join(lines[max(0, i - 6):i + 2])[:600],
                "confidence": "summary_page",
                "currency": "JPY",
            }

    def _section_for(self, lines: list[str], i: int
                     ) -> tuple[list[str | None], bool] | None:
        """Nearest summary section heading above line i, if any."""
        for j in range(i, max(-1, i - 14), -1):
            for pat, metrics in self.SUMMARY_SECTIONS:
                if pat.search(lines[j]):
                    return metrics, "経営成績" in lines[j]
        return None

    # ------------------------------------------------------------------

    def _split_jp(self, line: str) -> tuple[str, list[float]]:
        """Split a statement line into its label and numeric values.

        Japanese statements are laid out with runs of spaces like every
        other pdftotext output; what differs is that the label is CJK and
        the numbers may carry a leading △.
        """
        cells = [c for c in common.CELL_SPLIT.split(line.strip()) if c]
        if len(cells) < 2:
            return "", []
        label = cells[0].strip()
        nums: list[float] = []
        for cell in cells[1:]:
            for tok in cell.split():
                v = self._num(tok)
                if v is not None:
                    nums.append(v)
        return label, nums

    def _num(self, tok: str) -> float | None:
        tok = tok.strip().translate(self.ZEN2HAN)
        neg = False
        while tok and tok[0] in self.NEG_CHARS:
            neg, tok = True, tok[1:]
        if tok.startswith("(") and tok.endswith(")"):
            neg, tok = True, tok[1:-1]
        tok = tok.replace(",", "").replace("，", "")
        if not tok or not re.fullmatch(r"-?\d+(?:\.\d+)?", tok):
            return None
        v = float(tok)
        return -v if neg or tok.startswith("-") else v

    def _row_period(self, label: str) -> str | None:
        m = self.ROW_PERIOD_RE.match(label)
        if not m:
            return None
        return self._period_from_match(m)

    def _filing_period(self, lines: list[str]) -> str | None:
        """The period the tanshin covers, from its title line.

        "2026年３月期 第２四半期（中間期）決算短信" -> H1 FY2026: the fiscal
        year is named by the year it ENDS, which is what the label already
        states, so no offset arithmetic is needed here.
        """
        for line in lines[:self.HEAD_LINES]:
            if "決算短信" not in line and "四半期" not in line:
                continue
            m = self.TITLE_PERIOD_RE.search(line)
            if m:
                return self._period_from_match(m)
        return None

    def _period_from_match(self, m: re.Match[str]) -> str | None:
        year = m.group(1).translate(self.ZEN2HAN)
        q = m.group(3) if m.lastindex and m.lastindex >= 3 else None
        if q:
            qn = q.translate(self.ZEN2HAN)
            qn = {"一": "1", "二": "2", "三": "3", "四": "4"}.get(qn, qn)
            # Q2 is the half-year; Japanese filers label it 第２四半期 even
            # when the document is the 中間期 (interim) report.
            return f"H1 FY{year}" if qn == "2" else f"Q{qn} FY{year}"
        if "中間" in m.group(0):
            return f"H1 FY{year}"
        return f"FY{year}"

    # ------------------------------------------------------------------

    def units_hint(self, lines: list[str]) -> str | None:
        for line in lines:
            m = self.UNITS_JP_RE.search(line)
            if m:
                word = m.group(1) or m.group(2)
                u = self.UNITS_WORD.get(word)
                if u:
                    return u
        return super().units_hint(lines)

    def currency(self, lines: list[str]) -> str | None:
        if any("円" in line for line in lines[:self.HEAD_LINES]):
            return "JPY"
        return super().currency(lines)

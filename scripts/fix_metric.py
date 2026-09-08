#!/usr/bin/env python3
"""Apply a hand correction to a ticker's DuckDB, with provenance, DB-first.

DCBO's FY2022 row was corrected by hand in the CSV on 2026-08-19 (commit
197bbd9a) and silently clobbered on 2026-08-28 when the next export
regenerated the CSV from a DB that still held the old values. The
DB-first rule in CLAUDE.md was right; what was missing was a tool that made
the DB the natural place for a fix to go. This is that tool.

Every cell it changes is recorded in the `corrections` table (old value,
new value, source, who, which operation) and mirrored, one JSON object per
line, into Reports/{T}_Corrections.jsonl -- the DB is gitignored, so the
JSONL is the durable record, and load_existing.py replays it when it
rebuilds a DB from a legacy CSV. After writing, the CSV is re-exported from
the DB and the tier-1 eval runs, so a fix is never half-applied.

Dry run is the default; nothing is written without --apply.

Usage:
  fix_metric.py T --period FY2022 --set revenue=142.912 net_income=7.018 --source "DCBO_40F_FY2022.txt:1234"
  fix_metric.py T --periods all --scale shares_outstanding 1e-6 --source "..."
  fix_metric.py T --periods all --scale eps 0.01 --source "EPS printed in cents"
  fix_metric.py T --periods all --derive eps=net_income/shares_outstanding --source "restated share basis"
  fix_metric.py T --period "Q1 2021" --move cash_and_equivalents->kpis:SharesAsPrinted --unit shares --source "..."
  fix_metric.py T --period FY2025 --kpi AISC=1450 --unit "AUD/oz" --source "PFS p.12"
  fix_metric.py T --kpi-unit-default "USD millions" --source "units never tagged"
  fix_metric.py T --periods all --null net_margin --source "..."
  ... [--apply] [--no-export] [--actor NAME]

Expressions for --derive are plain arithmetic over core column names and
numeric literals (+ - * / with the usual precedence); a row with a NULL
operand is skipped, never written as 0.
"""

import ast
import datetime as dt
import json
import math
import os
import pathlib
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import schema

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = pathlib.Path(__file__).resolve().parent

Record = dict[str, Any]


class FixError(Exception):
    """A correction that cannot be applied as asked; nothing was written."""


@dataclass
class Set:
    period: str
    values: dict[str, float]


@dataclass
class Scale:
    cols: list[str]
    factor: float
    periods: list[str] | None      # None = every period


@dataclass
class Derive:
    target: str
    expr: str
    periods: list[str] | None


@dataclass
class Null:
    cols: list[str]
    periods: list[str] | None


@dataclass
class Move:
    period: str
    col: str
    kpi_name: str
    unit: str | None


@dataclass
class Kpi:
    period: str
    name: str
    value: float
    unit: str | None


@dataclass
class KpiUnitDefault:
    unit: str


Op = Set | Scale | Derive | Null | Move | Kpi | KpiUnitDefault

NUMERIC = [n for n, t, _ in schema.CORE_COLUMNS if t == "DOUBLE"]


# ---------------------------------------------------------------------------
# --derive expressions: a tiny arithmetic evaluator over an AST whitelist
# ---------------------------------------------------------------------------

_BINOPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
           ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b}


def _parse_expr(expr: str) -> tuple[ast.expr, list[str]]:
    """Validate an expression and return (tree, column names it reads)."""
    try:
        tree = ast.parse(expr.strip(), mode="eval").body
    except SyntaxError as e:
        raise FixError(f"cannot parse expression {expr!r}: {e.msg}") from None
    names: list[str] = []

    def walk(node: ast.AST) -> None:
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            walk(node.left)
            walk(node.right)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            walk(node.operand)
        elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return
        elif isinstance(node, ast.Name):
            if node.id not in NUMERIC:
                raise FixError(f"unknown column {node.id!r} in expression {expr!r}")
            names.append(node.id)
        else:
            raise FixError(f"only + - * / over core columns and numbers are allowed: {expr!r}")

    walk(tree)
    return tree, names


def _eval(node: ast.AST, row: dict[str, float]) -> float:
    if isinstance(node, ast.BinOp):
        return _BINOPS[type(node.op)](_eval(node.left, row), _eval(node.right, row))
    if isinstance(node, ast.UnaryOp):
        return -_eval(node.operand, row)
    if isinstance(node, ast.Constant):
        assert isinstance(node.value, (int, float))
        return float(node.value)
    assert isinstance(node, ast.Name)
    return row[node.id]


# ---------------------------------------------------------------------------
# Applying operations
# ---------------------------------------------------------------------------

def _same(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)


def _check_col(col: str) -> None:
    if col not in NUMERIC:
        raise FixError(f"unknown core column {col!r} (see schema.CORE_COLUMNS)")


def _periods(con: "DuckDBPyConnection", wanted: list[str] | None) -> list[str]:
    have = [r[0] for r in con.execute(
        "SELECT period FROM core_metrics").fetchall()]
    if wanted is None:
        return have
    missing = [p for p in wanted if p not in have]
    if missing:
        raise FixError(f"no such period in core_metrics: {missing}")
    return wanted


def _cell(con: "DuckDBPyConnection", period: str, col: str) -> float | None:
    row = con.execute(f"SELECT {col} FROM core_metrics WHERE period = ?", [period]).fetchone()
    return None if row is None else row[0]


def _write_core(con: "DuckDBPyConnection", period: str, col: str,
                value: float | None) -> None:
    con.execute(f"UPDATE core_metrics SET {col} = ? WHERE period = ?", [value, period])


def _record(con: "DuckDBPyConnection", rec: Record) -> Record:
    con.execute(
        "INSERT INTO corrections (ts, target, period, col, old_value, new_value,"
        " unit, source, actor, op) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [rec["ts"], rec["target"], rec["period"], rec["col"], rec["old_value"],
         rec["new_value"], rec.get("unit"), rec["source"], rec["actor"], rec["op"]])
    return rec


def apply_ops(con: "DuckDBPyConnection", ops: list[Op], *, source: str,
              actor: str, ts: str | None = None) -> list[Record]:
    """Apply the operations in order; return the corrections recorded.

    Raises FixError before writing anything if an op names a period or
    column that does not exist. Cells whose value would not change are
    neither written nor recorded.
    """
    ts = ts or dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    recs: list[Record] = []

    def rec(**kw: Any) -> Record:
        base: Record = {"ts": ts, "target": "core_metrics", "period": None,
                        "col": None, "old_value": None, "new_value": None,
                        "unit": None, "source": source, "actor": actor}
        base.update(kw)
        recs.append(_record(con, base))
        return base

    def core_update(period: str, col: str, new: float | None, op: str) -> None:
        old = _cell(con, period, col)
        if _same(old, new):
            return
        _write_core(con, period, col, new)
        rec(period=period, col=col, old_value=old, new_value=new, op=op)

    # Validate everything first so a bad op cannot leave a partial write.
    for op in ops:
        if isinstance(op, Set):
            _periods(con, [op.period])
            for c in op.values:
                _check_col(c)
        elif isinstance(op, (Scale, Null)):
            _periods(con, op.periods)
            for c in op.cols:
                _check_col(c)
        elif isinstance(op, Derive):
            _periods(con, op.periods)
            _check_col(op.target)
            _parse_expr(op.expr)
        elif isinstance(op, Move):
            _periods(con, [op.period])
            _check_col(op.col)
        # A Kpi may name a period with no core row (a feasibility-study
        # year, a metric disclosed only quarterly), so it is not checked.

    for op in ops:
        if isinstance(op, Set):
            for col, val in op.values.items():
                core_update(op.period, col, float(val), "set")
        elif isinstance(op, Scale):
            for period in _periods(con, op.periods):
                for col in op.cols:
                    old = _cell(con, period, col)
                    if old is not None:
                        core_update(period, col, old * op.factor, "scale")
        elif isinstance(op, Derive):
            tree, names = _parse_expr(op.expr)
            for period in _periods(con, op.periods):
                row = {n: _cell(con, period, n) for n in names}
                if any(v is None for v in row.values()):
                    continue
                try:
                    new = _eval(tree, row)  # type: ignore[arg-type]
                except ZeroDivisionError:
                    continue
                core_update(period, op.target, new, "derive")
        elif isinstance(op, Null):
            for period in _periods(con, op.periods):
                for col in op.cols:
                    core_update(period, col, None, "null")
        elif isinstance(op, Move):
            old = _cell(con, op.period, op.col)
            if old is None:
                continue
            _write_core(con, op.period, op.col, None)
            rec(period=op.period, col=op.col, old_value=old, new_value=None, op="move")
            _upsert_kpi(con, op.period, op.kpi_name, old, op.unit, rec)
        elif isinstance(op, Kpi):
            _upsert_kpi(con, op.period, op.name, float(op.value), op.unit, rec)
        elif isinstance(op, KpiUnitDefault):
            rows = con.execute(
                "SELECT period, name FROM kpis WHERE unit IS NULL ORDER BY period, name"
            ).fetchall()
            for period, name in rows:
                con.execute("UPDATE kpis SET unit = ? WHERE period = ? AND name = ?"
                            " AND unit IS NULL", [op.unit, period, name])
                rec(target=f"kpis:{name}", period=period, col=f"unit:{op.unit}",
                    unit=op.unit, op="kpi_unit")
    return recs


def _upsert_kpi(con: "DuckDBPyConnection", period: str, name: str, value: float,
                unit: str | None, rec: Any) -> None:
    prior = con.execute("SELECT value FROM kpis WHERE period = ? AND name = ?",
                        [period, name]).fetchone()
    old = None if prior is None else prior[0]
    con.execute("DELETE FROM kpis WHERE period = ? AND name = ?", [period, name])
    con.execute("INSERT INTO kpis (period, name, value, unit) VALUES (?, ?, ?, ?)",
                [period, name, value, unit])
    rec(target=f"kpis:{name}", period=period, col="value", old_value=old,
        new_value=value, unit=unit, op="kpi")


def replay(con: "DuckDBPyConnection", recs: list[Record]) -> int:
    """Re-apply recorded corrections to a rebuilt DB. Returns rows replayed.

    Each record already carries the value it produced, so replay is a set
    of writes, not a re-run of the arithmetic -- a rebuilt DB lands on the
    same numbers the original fix produced.
    """
    n = 0
    for r in recs:
        target, op = r["target"], r["op"]
        if target == "core_metrics":
            _write_core(con, r["period"], r["col"], r["new_value"])
        elif op == "kpi":
            name = target.split(":", 1)[1]
            con.execute("DELETE FROM kpis WHERE period = ? AND name = ?", [r["period"], name])
            con.execute("INSERT INTO kpis (period, name, value, unit) VALUES (?, ?, ?, ?)",
                        [r["period"], name, r["new_value"], r.get("unit")])
        elif op == "kpi_unit":
            name = target.split(":", 1)[1]
            con.execute("UPDATE kpis SET unit = ? WHERE period = ? AND name = ?"
                        " AND unit IS NULL", [r.get("unit"), r["period"], name])
        else:
            continue
        _record(con, r)
        n += 1
    return n


def load_jsonl(path: pathlib.Path) -> list[Record]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _num(text: str, what: str) -> float:
    try:
        return float(text.replace(",", ""))
    except ValueError:
        raise FixError(f"{what}: not a number: {text!r}") from None


def parse_ops(argv: list[str]) -> list[Op]:
    """Turn the op flags into operations, in the order given.

    --period / --periods set the scope for the ops that follow; --unit
    applies to the Move/Kpi it follows and to any later ones.
    """
    ops: list[Op] = []
    scope: list[str] | None = None
    scoped = False
    unit: str | None = None
    i = 0

    def values_after(j: int) -> tuple[list[str], int]:
        vals: list[str] = []
        while j < len(argv) and not argv[j].startswith("--"):
            vals.append(argv[j])
            j += 1
        return vals, j

    def one_period(flag: str) -> str:
        if not scope or len(scope) != 1:
            raise FixError(f"{flag} needs exactly one --period")
        return scope[0]

    while i < len(argv):
        a = argv[i]
        if a == "--period":
            vals, i = values_after(i + 1)
            scope = (scope or []) + vals if scoped else vals
            scoped = True
            continue
        if a == "--periods":
            vals, i = values_after(i + 1)
            scope = None if vals == ["all"] else vals
            scoped = True
            continue
        if a == "--unit":
            vals, i = values_after(i + 1)
            unit = " ".join(vals)
            for prev in reversed(ops):
                if isinstance(prev, (Move, Kpi)) and prev.unit is None:
                    prev.unit = unit
                break
            continue
        if a == "--set":
            vals, i = values_after(i + 1)
            period = one_period("--set")
            pairs: dict[str, float] = {}
            for v in vals:
                col, _, num = v.partition("=")
                pairs[col.strip()] = _num(num, col)
            ops.append(Set(period, pairs))
            continue
        if a == "--scale":
            vals, i = values_after(i + 1)
            if len(vals) < 2:
                raise FixError("--scale COL [COL...] FACTOR")
            ops.append(Scale(vals[:-1], _num(vals[-1], "--scale factor"), scope))
            continue
        if a == "--derive":
            vals, i = values_after(i + 1)
            target, _, expr = " ".join(vals).partition("=")
            ops.append(Derive(target.strip(), expr, scope))
            continue
        if a == "--null":
            vals, i = values_after(i + 1)
            ops.append(Null(vals, scope))
            continue
        if a == "--move":
            vals, i = values_after(i + 1)
            spec = " ".join(vals)
            col, _, dest = spec.partition("->")
            if not dest.startswith("kpis:"):
                raise FixError("--move COL->kpis:Name")
            ops.append(Move(one_period("--move"), col.strip(), dest[5:].strip(), unit))
            continue
        if a == "--kpi":
            vals, i = values_after(i + 1)
            name, _, num = " ".join(vals).partition("=")
            ops.append(Kpi(one_period("--kpi"), name.strip(), _num(num, name), unit))
            continue
        if a == "--kpi-unit-default":
            vals, i = values_after(i + 1)
            ops.append(KpiUnitDefault(" ".join(vals)))
            continue
        # Flags handled by main() (--source, --apply, ...) and their values.
        if a.startswith("--"):
            _, i = values_after(i + 1)
            continue
        i += 1
    return ops


def _fmt(v: float | None) -> str:
    return "NULL" if v is None else f"{v:g}"


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0].startswith("--"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    ticker = argv[0]
    apply = "--apply" in argv
    export = "--no-export" not in argv

    def value_of(flag: str) -> str | None:
        if flag not in argv:
            return None
        j = argv.index(flag) + 1
        vals = []
        while j < len(argv) and not argv[j].startswith("--"):
            vals.append(argv[j])
            j += 1
        return " ".join(vals) or None

    source = value_of("--source")
    if not source:
        print("--source is required: cite the filing (file:line) or state the basis",
              file=sys.stderr)
        return 2
    actor = value_of("--actor") or os.environ.get("USER") or "unknown"

    try:
        ops = parse_ops(argv[1:])
    except FixError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not ops:
        print("nothing to do: give at least one of --set/--scale/--derive/--null/"
              "--move/--kpi/--kpi-unit-default", file=sys.stderr)
        return 2

    reports = REPO / "research" / ticker / "Reports"
    db = reports / f"{ticker}.duckdb"
    if not db.exists():
        print(f"no database at {db}", file=sys.stderr)
        return 1

    import duckdb
    con = duckdb.connect(str(db))
    schema.ensure_schema(con)
    con.execute("BEGIN")
    try:
        recs = apply_ops(con, ops, source=source, actor=actor)
    except FixError as e:
        con.execute("ROLLBACK")
        con.close()
        print(f"error: {e}", file=sys.stderr)
        return 1

    if not recs:
        con.execute("ROLLBACK")
        con.close()
        print(f"{ticker}: nothing would change")
        return 0

    mode = "APPLIED" if apply else "DRY RUN (add --apply to write)"
    print(f"{ticker}: {len(recs)} cell(s) -- {mode}")
    for r in recs:
        where = f"{r['target']} {r['period'] or ''} {r['col']}"
        print(f"  {r['op']:8s} {where:45s} {_fmt(r['old_value']):>16s} -> {_fmt(r['new_value'])}")
    print(f"  source: {source}")

    if not apply:
        con.execute("ROLLBACK")
        con.close()
        return 0

    schema.backfill_period_columns(con)
    con.execute("COMMIT")
    con.close()

    mirror = reports / f"{ticker}_Corrections.jsonl"
    with open(mirror, "a") as fh:
        for r in recs:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    print(f"  recorded in {mirror.name}")

    if export:
        for script in ("export_csv.py", "run_evals.py"):
            proc = subprocess.run([sys.executable, str(SCRIPTS / script), ticker],
                                  capture_output=True, text=True, check=False)
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
            print(f"  {script}: exit {proc.returncode}" + "".join(f"\n    {ln}" for ln in tail))
    return 0


if __name__ == "__main__":
    sys.exit(main())

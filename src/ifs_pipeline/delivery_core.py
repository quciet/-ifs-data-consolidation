"""Exact-value delivery merge primitives."""
from pathlib import Path
import json
import math
import re
import shutil
import sqlite3
from contextlib import contextmanager
from .model import KEYS, META, Series
from .storage import PipelineError, check_idle_database, columns, inside, new_id, now, q, read_json, readonly, records, sha256, tables, write_json

DATABASES = ("IFsHistSeries.db", "DataDict.db")
REPORT = "Consolidation Report"
PARENT = None

def resolve_folder(value, parent=PARENT):
    from .paths import path_settings
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    if parent is None:
        parent = path_settings().get('delivery_root', Path.cwd())
    return (Path(parent) / path).resolve()

def fingerprints(folder):
    result = {}
    for name in DATABASES:
        check_idle_database(folder / name)
        result[name] = sha256(folder / name)
    return result

def copy_pair(source, destination, expected):
    destination.mkdir(parents=True, exist_ok=True)
    for name in DATABASES:
        target = destination / name
        if target.exists():
            raise PipelineError(f"Refusing to overwrite {target}")
        shutil.copy2(source / name, target)
        if sha256(target) != expected[name]:
            raise PipelineError(f"Database changed while copying: {name}")
    if fingerprints(source) != expected:
        raise PipelineError("Source database pair changed while copying")

@contextmanager
def locked(delivery):
    path = delivery / REPORT / ".lock"
    try:
        handle = path.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise PipelineError(f"Delivery is running or was interrupted. Verify no process is using it before removing {path}") from exc
    try:
        handle.write(now())
        handle.close()
        yield
    finally:
        path.unlink(missing_ok=True)

def exact_series(conn, table, canonical=None, missing=None, batch=""):
    """No float coercion, numeric-text conversion, sentinel guessing or scaling."""
    names = [c["name"] for c in columns(conn, table)]
    keys = next((keys for keys in KEYS.values() if set(keys).issubset(names)), None)
    if keys is None:
        raise PipelineError(f"{table}: missing country identity columns")
    years = sorted(c for c in names if re.fullmatch(r"[12][0-9]{3}", c))
    if not years or set(names) - set(keys) - set(years) - set(META):
        raise PipelineError(f"{table}: unsupported columns or missing years")
    output, seen_names, seen_codes = {}, set(), set()
    for row in records(conn, table):
        key = tuple(row[k] for k in keys)
        if any(not isinstance(v, str) or not v.strip() or v != v.strip() for v in key):
            raise PipelineError(f"{table}: identities must be nonempty trimmed text")
        if key[::2] in seen_names or key[1::2] in seen_codes:
            raise PipelineError(f"{table}: duplicate country/pair identity")
        seen_names.add(key[::2])
        seen_codes.add(key[1::2])
        if canonical is not None and any(canonical.get(c) != n for n, c in zip(key[::2], key[1::2])):
            raise PipelineError(f"{table}: inconsistent country {key}")
        cells = {}
        for year in years:
            value = row[year]
            category = None
            if value is None:
                category = "sql_null"
            elif isinstance(value, str) and not value.strip():
                category = "empty_string" if value == "" else "whitespace"
            elif type(value) not in (int, float) or not math.isfinite(value):
                raise PipelineError(f"{table}/{key}/{year}: expected a finite stored number; correct formatted import ({value!r})")
            if category:
                if missing is not None:
                    missing.append({"batch": batch, "table": table, "key": json.dumps(key),
                                    "year": year, "representation": category, "raw_value": repr(value)})
                cells[year] = None
            else:
                cells[year] = value
        output[key] = cells
    if not output:
        raise PipelineError(f"{table}: no country rows")
    return Series(table, keys, years, output)

def merge_exact(old, incoming):
    if old and old.keys != incoming.keys:
        raise PipelineError("Incompatible country key structures")
    years = sorted(set(incoming.years) | set(old.years if old else []))
    result = {}
    for key in (set(old.rows) if old else set()) | set(incoming.rows):
        result[key] = {}
        for year in years:
            value = incoming.rows.get(key, {}).get(year)
            result[key][year] = value if value is not None else (old.rows.get(key, {}).get(year) if old else None)
    keys = list(result)
    if len({k[::2] for k in keys}) != len(keys) or len({k[1::2] for k in keys}) != len(keys):
        raise PipelineError("Old and incoming country identities conflict")
    return Series(incoming.name, incoming.keys, years, result)

def metadata_exact(conn, table, incoming):
    if conn.execute("SELECT name FROM dd.sqlite_master WHERE type='trigger' AND tbl_name='DataDict'").fetchone():
        raise PipelineError("DataDict triggers require a dedicated adapter; untracked changes are not allowed")
    cols = [dict(r)["name"] for r in conn.execute('PRAGMA dd.table_info("DataDict")')]
    required = {"Table", "Variable", "Definition", "Units", "Source"}
    if not required.issubset(cols):
        raise PipelineError("Baseline DataDict is missing required fields")
    prior = [dict(r) for r in conn.execute('SELECT * FROM dd.DataDict WHERE "Table"=?', (table,))]
    selected = [r for r in incoming if r.get("Table") == table]
    if not selected:
        raise PipelineError(f"{table}: no source DataDict entries")
    if len({r.get("Units") for r in selected}) != 1:
        raise PipelineError("Source metadata assigns incompatible units to one physical table")
    old = {r["Variable"]: r for r in prior}
    if len(old) != len(prior):
        raise PipelineError("Duplicate baseline metadata identities")
    seen, output, changes = set(), [], []
    for row in selected:
        if set(row) - set(cols) or any(not isinstance(row.get(k), str) or not row[k].strip() for k in required):
            raise PipelineError("Source metadata has unknown columns or missing required fields")
        variable = row["Variable"]
        if variable in seen:
            raise PipelineError("Duplicate source metadata identities")
        seen.add(variable)
        units = {r["Units"].strip() for r in prior if isinstance(r.get("Units"), str)}
        if units and units != {row["Units"].strip()}:
            raise PipelineError(f"Units changed from {sorted(units)} to {row['Units']!r}; correct formatted import before blending")
        base = old.get(variable, {})
        merged = {c: base.get(c) for c in cols}
        merged.update(row)
        output.append(merged)
        for col in cols:
            if base.get(col) != merged[col]:
                changes.append({"table": table, "variable": variable, "field": col,
                                "before": base.get(col), "after": merged[col]})
    if set(old) - seen:
        raise PipelineError("Incoming metadata would remove existing Variable entries")
    return output, cols, changes

def write_exact(conn, data):
    if data.name in tables(conn):
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (data.name,)).fetchone()[0]
        deps = conn.execute("SELECT name FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger')", (data.name,)).fetchall()
        if deps or re.search(r"\b(CHECK|REFERENCES|GENERATED|UNIQUE|PRIMARY|DEFAULT)\b|\bNOT\s+NULL\b", ddl, re.I):
            raise PipelineError("Custom constraints/indexes/triggers require a dedicated adapter")
    for cells in data.rows.values():
        if any(type(v) is int and float(v) != v for v in cells.values() if v is not None):
            raise PipelineError("A numeric value cannot be stored exactly in IFs DOUBLE columns")
    names = [*data.keys, *data.years, *META]
    conn.execute(f"DROP TABLE IF EXISTS {q(data.name)}")
    conn.execute(f"CREATE TABLE {q(data.name)} (" + ",".join(
        f"{q(c)} " + ("VARCHAR(255)" if c in data.keys else "DOUBLE(53)") for c in names) + ")")
    for key, cells in sorted(data.rows.items()):
        values = [cells[y] for y in data.years]
        valid = [v for v in values if v is not None]
        row = [*key, *values, valid[0] if valid else None, valid[-1] if valid else None]
        conn.execute(f"INSERT INTO {q(data.name)} VALUES ({','.join('?' for _ in row)})", row)
    actual = exact_series(conn, data.name)
    if actual.rows != data.rows or actual.years != data.years:
        raise PipelineError("Saved values differ from exact source values")
    for row in records(conn, data.name):
        valid = [row[y] for y in data.years if row[y] is not None]
        if [row[k] for k in META] != ([valid[0], valid[-1]] if valid else [None, None]):
            raise PipelineError("Derived endpoints do not match existing observations")

def open_ledger(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE events (batch TEXT, file TEXT, series TEXT, key TEXT, year TEXT,
                             before_value, imported_value, changed INTEGER);
        CREATE TABLE origins (series TEXT, key TEXT, year TEXT, batch TEXT, PRIMARY KEY(series,key,year));
        CREATE TABLE metadata_events (batch TEXT, series TEXT, variable TEXT, field TEXT,
                                      before_json TEXT, after_json TEXT);
        CREATE TABLE metadata_result (series TEXT PRIMARY KEY, rows_json TEXT);
    """)
    return conn

def find_source(origins, key, year):
    return origins.get((json.dumps(key), year), "base")

def canonical_base_names(data, canonical):
    """Explicit name-only baseline view, preserving codes and every cell verbatim."""
    if data.keys != KEYS["monadic"]:
        raise PipelineError("Baseline name normalization requires monadic data")
    reverse = {name: code for code, name in canonical.items()}
    if len(reverse) != len(canonical):
        raise PipelineError("Duplicate canonical names")
    rows = {}
    for (name, code), cells in data.rows.items():
        if code not in canonical or (name in reverse and reverse[name] != code):
            raise PipelineError("Unresolved baseline country identity")
        key = (canonical[code], code)
        if key in rows:
            raise PipelineError("Duplicate baseline country code")
        rows[key] = dict(cells)
    return Series(data.name, data.keys, list(data.years), rows)


def baseline_name_view(data, baseline, run):
    policy = run / "baseline_names.json"
    if data is None or not policy.exists():
        return data
    policy = read_json(policy)
    if data.name not in policy["tables"]:
        return data
    roster = exact_series(baseline, policy["country_table"])
    canonical = {key[1]: key[0] for key in roster.rows}
    result = canonical_base_names(data, canonical)
    changes = [{"code": code, "before": name, "after": canonical[code]}
               for name, code in data.rows if name != canonical[code]]
    if changes != policy["tables"][data.name]:
        raise PipelineError("Baseline name evidence does not match original base")
    return result


def verify_origins(work, baseline, run, batches, delivery):
    """Independently reread immutable sources for every affected output observation."""
    by_id = {b["id"]: b for b in batches}
    with readonly(run / "provenance.db") as ledger, readonly(work / DATABASES[0]) as output, readonly(baseline / DATABASES[0]) as base:
        updated = [r[0] for r in ledger.execute("SELECT series FROM metadata_result")]
        for table in updated:
            actual = exact_series(output, table)
            old = exact_series(base, table) if table in tables(base) else None
            old = baseline_name_view(old, base, run)
            origins = {(r["key"], r["year"]): r["batch"] for r in ledger.execute("SELECT * FROM origins WHERE series=?", (table,))}
            sources = {}
            for batch in batches:
                if table in batch.get("applied_tables", []):
                    with readonly(inside(delivery / REPORT, batch["archive"])) as src:
                        sources[batch["id"]] = exact_series(src, table)
            # Independently reconstruct precedence, including equal imported values.
            expected_origins = {}
            expected_values = {k: dict(v) for k, v in old.rows.items()} if old else {}
            for bid, source in sources.items():
                for key, cells in source.rows.items():
                    target = expected_values.setdefault(key, {})
                    for year, value in cells.items():
                        if value is not None:
                            target[year] = value
                            expected_origins[(json.dumps(key), year)] = bid
            if origins != expected_origins:
                raise PipelineError(f"Missing or incorrect observation provenance: {table}")
            for key, cells in actual.rows.items():
                for year, value in cells.items():
                    if value != expected_values.get(key, {}).get(year):
                        raise PipelineError(f"Untraceable output value: {table}/{key}/{year}")
            expected_keys = set(old.rows if old else {})
            expected_years = set(old.years if old else [])
            for src in sources.values():
                expected_keys.update(src.rows)
                expected_years.update(src.years)
            if set(actual.rows) != expected_keys or set(actual.years) != expected_years:
                raise PipelineError(f"Output structure differs from base/import union: {table}")
        with readonly(work / DATABASES[1]) as md:
            for row in ledger.execute("SELECT * FROM metadata_result"):
                saved = [dict(r) for r in md.execute('SELECT * FROM DataDict WHERE "Table"=?', (row["series"],))]
                with readonly(baseline / DATABASES[1]) as old_md:
                    cols = [c["name"] for c in columns(old_md, "DataDict")]
                    expected = {r["Variable"]: dict(r) for r in old_md.execute(
                        'SELECT * FROM DataDict WHERE "Table"=?', (row["series"],))}
                for batch in batches:
                    if row["series"] not in batch.get("applied_tables", []):
                        continue
                    with readonly(inside(delivery / REPORT, batch["archive"])) as src:
                        incoming = [dict(r) for r in src.execute(
                            'SELECT * FROM DataDict WHERE "Table"=?', (row["series"],))]
                    copied = {}
                    for item in incoming:
                        prior = expected.get(item["Variable"], {})
                        copied[item["Variable"]] = {c: item[c] if c in item else prior.get(c) for c in cols}
                    expected = copied
                expected_rows = sorted(expected.values(), key=lambda r: r["Variable"])
                if sorted(saved, key=lambda r: r["Variable"]) != expected_rows or sorted(json.loads(row["rows_json"]), key=lambda r: r["Variable"]) != expected_rows:
                    raise PipelineError("Saved metadata differs from baseline/import sources")
    for name in DATABASES:
        with readonly(work / name) as conn:
            if [r[0] for r in conn.execute("PRAGMA integrity_check")] != ["ok"]:
                raise PipelineError(f"Output SQLite integrity failed: {name}")

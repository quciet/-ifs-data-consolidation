"""IFs table normalization, validation, blending and change accounting."""
from dataclasses import dataclass
import math
import re
from .storage import PipelineError, columns, q, records

KEYS = {
    "monadic": ("Country", "FIPS_CODE"),
    "dyadic": ("Actor", "Actor_FIPS", "Partner", "Partner_FIPS"),
}
META = ("Earliest", "MostRecent")


def numeric(value, where, missing=("",), missing_numeric=()):
    if value is None or (isinstance(value, str) and (not value.strip() or value.strip() in missing)):
        return None
    if not isinstance(value, (str, int, float)):
        raise PipelineError(f"Unsupported numeric storage at {where}: {type(value).__name__}")
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise PipelineError(f"Non-numeric value at {where}: {value!r}") from exc
    if not math.isfinite(result):
        raise PipelineError(f"Non-finite value at {where}: {value!r}")
    return None if result in missing_numeric else result


@dataclass
class Series:
    name: str
    keys: tuple
    years: list
    rows: dict


def make_series(name, keys, year_columns, rows, country_map=None, missing=("",), missing_numeric=()):
    if not name.startswith("Series"):
        raise PipelineError(f"Expected an IFs Series table: {name}")
    years = sorted(set(year_columns), key=int)
    if not years or any(not re.fullmatch(r"[12][0-9]{3}", y) for y in years):
        raise PipelineError(f"{name}: expected year columns from 1000 through 2999")
    output, names_seen, codes_seen = {}, set(), set()
    for row in rows:
        key = tuple(row.get(k) for k in keys)
        if any(not isinstance(v, str) or not v.strip() or v != v.strip() for v in key):
            raise PipelineError(f"{name}: country keys must be non-empty, trimmed text: {key}")
        names, codes = key[::2], key[1::2]
        if key in output or names in names_seen or codes in codes_seen:
            raise PipelineError(f"{name}: duplicate country/pair key: {key}")
        if country_map is not None:
            for name_part, code_part in zip(names, codes):
                if country_map.get(code_part) != name_part:
                    raise PipelineError(f"{name}: unmapped or inconsistent country: {name_part}/{code_part}")
        names_seen.add(names)
        codes_seen.add(codes)
        output[key] = {y: numeric(row.get(y), f"{name}/{key}/{y}", missing, missing_numeric) for y in years}
    if not output:
        raise PipelineError(f"{name}: no country rows")
    return Series(name, keys, years, output)


def read_series(conn, name, kind, country_map=None, missing=("",), missing_numeric=()):
    keys = KEYS[kind]
    cols = [c["name"] for c in columns(conn, name)]
    if not set(keys).issubset(cols):
        raise PipelineError(f"{name}: expected {kind} keys {keys}; found {cols[:6]}")
    extra = set(cols) - set(keys) - set(META)
    if any(not re.fullmatch(r"[12][0-9]{3}", c) for c in extra):
        raise PipelineError(f"{name}: unsupported non-year columns: {sorted(extra - {c for c in extra if c.isdigit()})}")
    return make_series(name, keys, extra, records(conn, name), country_map, missing, missing_numeric)


def blend(old, new, policy):
    if old is None or policy == "replace":
        base = new
    else:
        if old.keys != new.keys:
            raise PipelineError(f"{new.name}: incompatible key structures")
        years = sorted(set(old.years + new.years), key=int)
        rows = {}
        for key in old.rows.keys() | new.rows.keys():
            rows[key] = {}
            for year in years:
                recent = new.rows.get(key, {}).get(year)
                rows[key][year] = recent if recent is not None else old.rows.get(key, {}).get(year)
        base = Series(new.name, new.keys, years, rows)
    years = [str(y) for y in range(int(base.years[0]), int(base.years[-1]) + 1)]
    return Series(base.name, base.keys, years, base.rows)


def write_series(conn, series):
    # Existing IFs tables are plain tables. Stop if rebuilding could erase constraints.
    from .storage import tables
    if series.name in tables(conn):
        schema = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (series.name,)).fetchone()[0]
        dependent = conn.execute("SELECT name FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger')", (series.name,)).fetchall()
        constrained = any(re.search(pattern, schema, re.I) for pattern in (r"\bCHECK\s*\(", r"\bREFERENCES\b", r"\bGENERATED\b", r"\bUNIQUE\b", r"\bPRIMARY\s+KEY\b", r"\bNOT\s+NULL\b", r"\bDEFAULT\b"))
        if dependent or constrained:
            raise PipelineError(f"{series.name}: custom constraints/indexes/triggers require a dedicated adapter")
    conn.execute(f"DROP TABLE IF EXISTS {q(series.name)}")
    cols = list(series.keys) + series.years + list(META)
    ddl = [f"{q(c)} VARCHAR(255)" if c in series.keys else f"{q(c)} DOUBLE(53)" for c in cols]
    conn.execute(f"CREATE TABLE {q(series.name)} ({', '.join(ddl)})")
    data = []
    for key, values in sorted(series.rows.items()):
        valid = [values.get(y) for y in series.years if values.get(y) is not None]
        data.append(list(key) + [values.get(y) for y in series.years] + [valid[0] if valid else None, valid[-1] if valid else None])
    conn.executemany(f"INSERT INTO {q(series.name)} VALUES ({','.join('?' for _ in cols)})", data)


def compare(old, result, new, relative_threshold):
    counts = {k: 0 for k in ("added", "revised", "removed", "unchanged", "backfilled", "large_revisions")}
    changes = []
    old_rows = old.rows if old else {}
    years = sorted(set((old.years if old else []) + result.years), key=int)
    for key in sorted(old_rows.keys() | result.rows.keys()):
        for year in years:
            before = old_rows.get(key, {}).get(year)
            after = result.rows.get(key, {}).get(year)
            if before is None and after is None:
                continue
            if before == after:
                counts["unchanged"] += 1
            else:
                change = "added" if before is None else "removed" if after is None else "revised"
                counts[change] += 1
                large = change == "revised" and (before == 0 or abs(after - before) / abs(before) > relative_threshold)
                counts["large_revisions"] += int(large)
                changes.append({"table": result.name, "key": " | ".join(key), "year": year, "before": before, "after": after, "change": change, "large_revision": large})
            if after is not None and before is not None and new.rows.get(key, {}).get(year) is None:
                counts["backfilled"] += 1
    coverage = []
    for year in years:
        coverage.append({"table": result.name, "year": year,
                         "before": sum(r.get(year) is not None for r in old_rows.values()),
                         "after": sum(r.get(year) is not None for r in result.rows.values())})
    return counts, changes, coverage

"""Explicit source adapters; no guessed countries, units, or indicator matches."""
import csv
import math
from pathlib import Path
from .model import KEYS, make_series, numeric, read_series
from .storage import PipelineError, read_json, readonly, records, resource_path


def load_recipe(path):
    recipe = read_json(path)
    if recipe.get("schema_version") != 1:
        raise PipelineError("Recipe schema_version must be 1")
    if recipe.get("status") != "active":
        raise PipelineError("Recipe is not active; finish and review its mappings first")
    if not isinstance(recipe.get("id"), str) or not recipe["id"].strip():
        raise PipelineError("Recipe needs an id")
    if recipe.get("adapter") not in ("ifs_sqlite", "csv_long"):
        raise PipelineError("Supported adapters: ifs_sqlite, csv_long")
    if recipe.get("kind", "monadic") not in KEYS:
        raise PipelineError("Recipe kind must be monadic or dyadic")
    if recipe.get("merge_policy") not in ("prefer_new_non_null", "replace"):
        raise PipelineError("Declare merge_policy: prefer_new_non_null or replace")
    threshold = recipe.get("large_revision_fraction", 0.5)
    if not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or threshold < 0:
        raise PipelineError("large_revision_fraction must be a finite nonnegative number")
    count = recipe.get("expected_country_count")
    if count is not None and (type(count) is not int or count < 1):
        raise PipelineError("expected_country_count must be a positive integer or null")
    if recipe.get("kind", "monadic") == "dyadic" and count is not None:
        raise PipelineError("expected_country_count is for monadic data; use null for dyadic data")
    for flag in ("allow_unit_change", "allow_coverage_loss"):
        if flag in recipe and type(recipe[flag]) is not bool:
            raise PipelineError(f"{flag} must be true or false")
    tokens = recipe.get("missing_values", [""])
    sentinels = recipe.get("missing_numeric_values", [])
    if not isinstance(tokens, list) or any(not isinstance(v, str) for v in tokens):
        raise PipelineError("missing_values must be a list of text tokens")
    for token in tokens:
        try:
            is_zero = float(token) == 0
        except ValueError:
            is_zero = False
        if is_zero:
            raise PipelineError("Zero must not be declared a missing-value token")
    if not isinstance(sentinels, list) or any(type(v) not in (int, float) or not math.isfinite(v) or v == 0 for v in sentinels):
        raise PipelineError("missing_numeric_values must contain finite nonzero numeric sentinels")
    return recipe


def country_reference(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    canonical, aliases, names = {}, {}, {}
    for row in rows:
        name, code = row.get("Country", "").strip(), row.get("FIPS_CODE", "").strip()
        alias = row.get("source_code", code).strip()
        if not name or not code or not alias:
            raise PipelineError("Country reference needs Country, FIPS_CODE and non-empty source_code")
        if code in canonical and canonical[code] != name:
            raise PipelineError(f"Conflicting country mapping: {code}")
        if name in names and names[name] != code:
            raise PipelineError(f"Conflicting country mapping: {name}")
        if alias in aliases and aliases[alias] != (name, code):
            raise PipelineError(f"Conflicting source country code: {alias}")
        canonical[code], names[name], aliases[alias] = name, code, (name, code)
    if not canonical:
        raise PipelineError("Country reference is empty")
    return canonical, aliases


def load_source(path, recipe, canonical, aliases):
    if recipe["adapter"] == "ifs_sqlite":
        return load_sqlite(path, recipe, canonical)
    return load_csv(path, recipe, canonical, aliases)


def load_sqlite(path, recipe, canonical):
    with readonly(path) as conn:
        metadata = records(conn, "DataDict")
        names = {r.get("Table") for r in metadata}
        if not names or any(not isinstance(t, str) or not t.startswith("Series") for t in names):
            raise PipelineError("Import DataDict has missing or invalid Series table names")
        selection = recipe.get("tables")
        if selection is not None:
            if not isinstance(selection, list) or not selection or len(selection) != len(set(selection)):
                raise PipelineError("Recipe tables must be null or a non-empty unique list")
            if set(selection) - names:
                raise PipelineError(f"Selected tables absent from import DataDict: {set(selection) - names}")
            names = set(selection)
        series = {t: read_series(conn, t, recipe.get("kind", "monadic"), canonical, tuple(recipe.get("missing_values", [""])), tuple(recipe.get("missing_numeric_values", []))) for t in sorted(names)}
        return series, [r for r in metadata if r["Table"] in names]


def load_csv(path, recipe, canonical, aliases):
    if recipe.get("kind", "monadic") != "monadic":
        raise PipelineError("csv_long currently supports monadic data only")
    fields = recipe.get("columns", {})
    if not all(fields.get(k) for k in ("country", "indicator", "year", "value")):
        raise PipelineError("CSV recipe needs country, indicator, year and value column mappings")
    indicators = recipe.get("indicators", {})
    if not indicators:
        raise PipelineError("CSV recipe needs explicit indicator mappings")
    target_names = [spec.get("table") for spec in indicators.values()]
    if len(set(target_names)) != len(target_names) or any(not isinstance(t, str) or not t.startswith("Series") for t in target_names):
        raise PipelineError("Each CSV indicator must map to a distinct Series table")
    buffers, years, seen, observed = {}, {}, set(), set()
    missing = tuple(recipe.get("missing_values", [""]))
    with Path(path).open(encoding=recipe.get("encoding", "utf-8-sig"), newline="") as handle:
        reader = csv.DictReader(handle, delimiter=recipe.get("delimiter", ","))
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise PipelineError("CSV headers are missing or duplicated")
        required = {fields[k] for k in ("country", "indicator", "year", "value")}
        if fields.get("unit"):
            required.add(fields["unit"])
        if not required.issubset(reader.fieldnames):
            raise PipelineError(f"CSV is missing columns: {required - set(reader.fieldnames)}")
        for line, row in enumerate(reader, 2):
            if None in row or any(v is None for v in row.values()):
                raise PipelineError(f"CSV row {line}: inconsistent column count")
            indicator = row[fields["indicator"]].strip()
            if indicator not in indicators:
                if indicator in recipe.get("ignored_indicators", []):
                    continue
                raise PipelineError(f"CSV row {line}: unmapped indicator {indicator!r}")
            code = row[fields["country"]].strip()
            if code not in aliases:
                if code in recipe.get("ignored_country_codes", []):
                    continue
                raise PipelineError(f"CSV row {line}: unmapped country code {code!r}")
            spec = indicators[indicator]
            if not spec.get("input_unit") or not spec.get("metadata", {}).get("Units"):
                raise PipelineError(f"{indicator}: declare input_unit and output metadata Units")
            if fields.get("unit") and row[fields["unit"]].strip() != spec["input_unit"]:
                raise PipelineError(f"CSV row {line}: unexpected unit {row[fields['unit']]!r}")
            year = row[fields["year"]].strip()
            if len(year) != 4 or not year.isascii() or not year.isdigit() or not 1000 <= int(year) <= 2999:
                raise PipelineError(f"CSV row {line}: invalid year {year!r}")
            key = aliases[code]
            table = spec["table"]
            identity = (table, key, year)
            if identity in seen:
                raise PipelineError(f"CSV row {line}: duplicate country/indicator/year {identity}")
            seen.add(identity)
            observed.add(indicator)
            value = numeric(row[fields["value"]], f"CSV row {line}", missing, tuple(recipe.get("missing_numeric_values", [])))
            multiplier = numeric(spec.get("multiplier", 1), f"{indicator} multiplier")
            if multiplier is None:
                raise PipelineError(f"{indicator}: multiplier cannot be null")
            if value is not None:
                value = numeric(value * multiplier, f"CSV row {line} converted value")
                if spec.get("min_value") is not None and value < spec["min_value"]:
                    raise PipelineError(f"CSV row {line}: value below min_value")
                if spec.get("max_value") is not None and value > spec["max_value"]:
                    raise PipelineError(f"CSV row {line}: value above max_value")
            buffers.setdefault(table, {}).setdefault(key, {"Country": key[0], "FIPS_CODE": key[1]})[year] = value
            years.setdefault(table, set()).add(year)
    if set(indicators) - observed:
        raise PipelineError(f"Mapped indicators absent from usable CSV rows: {set(indicators) - observed}")
    series = {t: make_series(t, KEYS["monadic"], years[t], list(rows.values()), canonical) for t, rows in buffers.items()}
    metadata = []
    for spec in indicators.values():
        row = dict(spec["metadata"])
        row["Table"] = spec["table"]
        for col in ("Variable", "Definition", "Units", "Source"):
            if not isinstance(row.get(col), str) or not row[col].strip():
                raise PipelineError(f"{spec['table']}: metadata needs {col}")
        metadata.append(row)
    return series, metadata

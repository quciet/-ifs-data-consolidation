"""Pre-merge diagnostics for prepared IFs databases. Findings never repair source data."""
from collections import Counter, defaultdict
import hashlib
import json
import math

from .model import KEYS, make_series, numeric
from .storage import PipelineError, columns, q, records, tables

DEFAULTS = {"frequency_years": 1, "jump_fraction": 0.5, "revision_fraction": 0.5,
            "absolute_change_floor": 0.0, "near_zero_floor": 1e-9,
            "flat_run_length": 6, "min_value": None, "max_value": None}


def rules_for(recipe, table):
    config = recipe.get("validation", {})
    if not isinstance(config, dict) or set(config) - {"allow_partial_countries", "defaults", "tables", "sum_rules"}:
        raise PipelineError("Unknown or invalid validation configuration")
    if type(config.get("allow_partial_countries", False)) is not bool:
        raise PipelineError("allow_partial_countries must be true or false")
    default = config.get("defaults", {})
    overrides = config.get("tables", {})
    if not isinstance(default, dict) or not isinstance(overrides, dict):
        raise PipelineError("validation defaults and tables must be objects")
    selected = overrides.get(table, {})
    if not isinstance(selected, dict) or (set(default) | set(selected)) - set(DEFAULTS):
        raise PipelineError(f"{table}: unknown validation rule")
    result = {**DEFAULTS, **default, **selected}
    for key in ("frequency_years", "flat_run_length"):
        if type(result[key]) is not int or result[key] < (2 if key == "flat_run_length" else 1):
            raise PipelineError(f"{key} must be a positive integer (flat_run_length at least 2)")
    for key in set(DEFAULTS) - {"frequency_years", "flat_run_length"}:
        v = result[key]
        if key in ("min_value", "max_value") and v is None:
            continue
        if type(v) not in (int, float) or not math.isfinite(v):
            raise PipelineError(f"{key} must be finite")
        if key not in ("min_value", "max_value") and v < 0:
            raise PipelineError(f"{key} must be nonnegative")
    if result["near_zero_floor"] <= 0:
        raise PipelineError("near_zero_floor must be positive")
    if result["min_value"] is not None and result["max_value"] is not None and result["min_value"] > result["max_value"]:
        raise PipelineError("min_value exceeds max_value")
    return result


def issue(findings, severity, code, table, message, key="", year=""):
    item = {"severity": severity, "code": code, "table": table, "key": str(key),
            "year": str(year), "message": message}
    # Stable within a request; the review wrapper adds request identity.
    item["id"] = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()[:16]
    findings.append(item)


def profile(conn, table, recipe, canonical, findings, missing_counts, normalization, source_label):
    keys = KEYS[recipe.get("kind", "monadic")]
    info = columns(conn, table)
    names = [r["name"] for r in info]
    if not set(keys).issubset(names):
        issue(findings, "error", "key_columns", table, f"Required keys absent: {set(keys)-set(names)}")
        return None
    year_cols = [c for c in names if c.isascii() and c.isdigit() and len(c) == 4 and 1000 <= int(c) <= 2999]
    extra = set(names) - set(keys) - set(year_cols) - {"Earliest", "MostRecent"}
    if extra or not year_cols:
        issue(findings, "error", "series_schema", table, f"Unsupported columns {sorted(extra)} or no valid years")
        return None
    raw = records(conn, table)
    counts = Counter()
    prepared = []
    tokens = tuple(recipe.get("missing_values", [""]))
    numeric_tokens = tuple(recipe.get("missing_numeric_values", []))
    for row_number, row in enumerate(raw, 1):
        converted = dict(row)
        key = " | ".join(str(row.get(k)) for k in keys)
        for y in year_cols:
            value = row[y]
            category = ("sql_null" if value is None else "empty_string" if value == "" else
                        "whitespace_string" if isinstance(value, str) and not value.strip() else
                        "declared_string_token" if isinstance(value, str) and value.strip() in tokens else
                        "numeric_text" if isinstance(value, str) else "numeric")
            try:
                parsed = numeric(value, f"{table}/{key}/{y}", tokens, numeric_tokens)
                if parsed is None and category in ("numeric", "numeric_text"):
                    category = "declared_numeric_token"
                elif parsed == 0 and category == "numeric":
                    category = "numeric_zero"
                converted[y] = parsed
                action = "to_sql_null" if parsed is None else "to_number"
            except PipelineError as exc:
                category, action = "invalid_value", "blocked"
                converted[y] = None
                issue(findings, "error", "invalid_value", table, str(exc), key, y)
            counts[category] += 1
            if category in ("empty_string", "whitespace_string", "declared_string_token",
                            "declared_numeric_token", "numeric_text", "invalid_value"):
                normalization.append({"dataset": source_label, "table": table, "row": row_number,
                                      "key": key, "year": y, "storage_type": type(value).__name__,
                                      "raw_value": repr(value), "category": category, "action": action})
        prepared.append(converted)
    missing_counts.extend({"dataset": source_label, "table": table, "representation": k, "cells": v}
                          for k, v in sorted(counts.items()))
    changed = sum(counts[k] for k in ("empty_string", "whitespace_string", "declared_string_token", "declared_numeric_token"))
    if changed:
        issue(findings, "review", "missing_normalization", table,
              f"{changed} cells normalize to NULL; inspect original representations and their meaning")
    if counts["numeric_text"]:
        issue(findings, "info", "numeric_text", table, f"{counts['numeric_text']} numeric strings convert to numbers")
    try:
        series = make_series(table, keys, year_cols, prepared, canonical)
    except (ValueError, TypeError) as exc:
        issue(findings, "error", "country_identity", table, str(exc))
        return None
    if counts["invalid_value"]:
        return None
    if source_label == "incoming" and len(keys) == 2:
        actual = {key[1] for key in series.rows}
        absent = sorted(set(canonical) - actual)
        severity = "review" if recipe.get("validation", {}).get("allow_partial_countries", False) else "error"
        if absent:
            issue(findings, severity, "missing_countries", table,
                  f"Incoming table is missing {len(absent)} IFs country rows: {', '.join(absent)}")
        expected = recipe.get("expected_country_count")
        if expected is not None and len(actual) != expected and not absent:
            issue(findings, "error", "country_count", table, f"Incoming table has {len(actual)} countries; expected {expected}")
    for col in info:
        normalized_type = col["type"].replace(" ", "").upper()
        expected_type = "VARCHAR(255)" if col["name"] in keys else "DOUBLE(53)"
        if normalized_type != expected_type:
            issue(findings, "info", "declared_type", table,
                  f"{col['name']}: {col['type']} will be written as {expected_type}")
    # Derived columns are inspected before being recomputed, not trusted as coverage evidence.
    for source_row, row in zip(raw, prepared):
        key = tuple(row[k] for k in keys)
        values = [row[y] for y in sorted(year_cols, key=int) if row[y] is not None]
        expected = [values[0], values[-1]] if values else [None, None]
        for field, value in zip(("Earliest", "MostRecent"), expected):
            try:
                stored = numeric(source_row.get(field), field)
            except PipelineError:
                stored = "invalid"
            if field not in names or stored != value:
                issue(findings, "review", "derived_value_mismatch", table,
                      f"{field} is missing/inconsistent and will be recomputed", " | ".join(key))
    return series


def is_large(old, new, fraction, rules):
    delta = abs(new - old)
    if delta <= rules["absolute_change_floor"]:
        return False
    return abs(old) < rules["near_zero_floor"] or delta / abs(old) > fraction


def inspect_new(series, rules, findings):
    step = rules["frequency_years"]
    for key, cells in series.rows.items():
        label = " | ".join(key)
        valid = [(int(y), cells[y]) for y in series.years if cells[y] is not None]
        if not valid:
            issue(findings, "review", "all_null_country", series.name, "Country row has no usable observations", label)
            continue
        for y, value in valid:
            if rules["min_value"] is not None and value < rules["min_value"]:
                issue(findings, "review", "value_below_bound", series.name, f"{value} is below declared minimum {rules['min_value']}", label, y)
            if rules["max_value"] is not None and value > rules["max_value"]:
                issue(findings, "review", "value_above_bound", series.name, f"{value} exceeds declared maximum {rules['max_value']}", label, y)
        for (y0, v0), (y1, v1) in zip(valid, valid[1:]):
            if y1 - y0 > step:
                issue(findings, "review", "internal_gap", series.name,
                      f"No usable observation between {y0} and {y1}; configured interval is {step} year(s)", label, y1)
            elif y1 - y0 == step and is_large(v0, v1, rules["jump_fraction"], rules):
                issue(findings, "review", "temporal_jump", series.name,
                      f"{y0}: {v0} -> {y1}: {v1}; screening flag, not proof of error", label, y1)
        run_length = 1
        for (y0, v0), (y1, v1) in zip(valid, valid[1:]):
            run_length = run_length + 1 if y1-y0 == step and v0 == v1 else 1
            if run_length == rules["flat_run_length"]:
                issue(findings, "review", "flat_run", series.name,
                      f"At least {run_length} successive observations equal {v1}", label, y1)
    for year in series.years:
        if not any(c.get(year) is not None for c in series.rows.values()):
            issue(findings, "info", "all_null_year", series.name, "Year column has no usable observations", year=year)


def compare_incoming(old, new, rules, findings, differences, coverage):
    previous = old.rows if old else {}
    years = sorted(set((old.years if old else []) + new.years), key=int)
    ratios = []
    for year in years:
        coverage.append({"table": new.name, "year": year,
                         "old_rows": len(previous), "incoming_rows": len(new.rows),
                         "old_non_null": sum(v.get(year) is not None for v in previous.values()),
                         "incoming_non_null": sum(v.get(year) is not None for v in new.rows.values())})
    for key in sorted(set(previous) | set(new.rows)):
        for year in years:
            before, after = previous.get(key, {}).get(year), new.rows.get(key, {}).get(year)
            if before == after:
                continue
            change = ("added" if before is None else
                      "missing_country" if key not in new.rows else
                      "old_only_year" if year not in new.years else
                      "incoming_null" if after is None else "revised")
            differences.append({"table": new.name, "key": " | ".join(key), "year": year,
                                "old": before, "incoming": after, "change": change})
            if change == "incoming_null":
                issue(findings, "review", "lost_observation", new.name,
                      f"Baseline value {before} is missing in the incoming data; decide whether it is a gap or withdrawal",
                      " | ".join(key), year)
            if before is not None and after is not None:
                if is_large(before, after, rules["revision_fraction"], rules):
                    issue(findings, "review", "large_revision", new.name,
                          f"Baseline {before} -> incoming {after}", " | ".join(key), year)
    if old is None:
        issue(findings, "review", "new_table", new.name, "No baseline table; confirm definition, units and scope")
        return
    old_only = sorted(set(old.years) - set(new.years), key=int)
    if old_only:
        issue(findings, "review", "historical_years_absent", new.name,
              f"{len(old_only)} baseline year columns are absent from import ({old_only[0]}-{old_only[-1]})")
    shared_years = sorted(set(old.years) & set(new.years), key=int)
    for key in set(old.rows) & set(new.rows):
        for y in shared_years:
            a, b = old.rows[key].get(y), new.rows[key].get(y)
            if a is not None and b is not None and abs(a) >= rules["near_zero_floor"]:
                ratios.append(b/a)
    if len(ratios) >= 10:
        for scale in (0.000001, 0.001, 0.01, 100, 1000, 1000000):
            fraction = sum(abs(r/scale-1) <= 0.02 for r in ratios) / len(ratios)
            if fraction >= 0.8:
                issue(findings, "review", "possible_scale_change", new.name,
                      f"{fraction:.0%} of {len(ratios)} shared values are approximately baseline x {scale}")
    # Exact trajectory reuse is evidence for investigation, never an automatic reassignment.
    signatures = defaultdict(list)
    for key, row in old.rows.items():
        values = tuple(row.get(y) for y in shared_years)
        usable = [v for v in values if v is not None]
        if len(usable) >= 5 and len(set(usable)) >= 3:
            signatures[values].append(key)
    for key, row in new.rows.items():
        signature = tuple(row.get(y) for y in shared_years)
        matches = signatures.get(signature, [])
        if len(matches) == 1 and matches[0] != key:
            issue(findings, "review", "possible_country_misplacement", new.name,
                  f"Incoming trajectory exactly matches baseline country {' | '.join(matches[0])}; confirm against source",
                  " | ".join(key))
    for offset in (-rules["frequency_years"], rules["frequency_years"]):
        compared, shifted, same = 0, 0, 0
        for key in set(old.rows) & set(new.rows):
            values = old.rows[key]
            if len({v for v in values.values() if v is not None}) < 3:
                continue
            for year in new.years:
                v = new.rows[key].get(year)
                a, b = values.get(str(int(year)-offset)), values.get(year)
                if v is not None and a is not None and b is not None:
                    compared += 1
                    shifted += math.isclose(v, a, rel_tol=1e-9, abs_tol=1e-12)
                    same += math.isclose(v, b, rel_tol=1e-9, abs_tol=1e-12)
        if compared >= 5 and shifted/compared >= 0.8 and (shifted-same)/compared >= 0.3:
            issue(findings, "review", "possible_year_shift", new.name,
                  f"{shifted}/{compared} values match baseline year (incoming year minus {offset}); check alignment")


def check_sum_rules(series, recipe, findings):
    rules = recipe.get("validation", {}).get("sum_rules", [])
    if not isinstance(rules, list):
        raise PipelineError("validation.sum_rules must be a list")
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - {"total", "parts", "absolute_tolerance", "relative_tolerance"}:
            raise PipelineError("Invalid declared sum rule")
        total, parts = rule.get("total"), rule.get("parts")
        if not isinstance(total, str) or not isinstance(parts, list) or len(parts) < 2 or len(set(parts)) != len(parts) or total in parts:
            raise PipelineError("Sum rule requires a total and distinct component tables")
        abs_tol, rel_tol = rule.get("absolute_tolerance", 1e-6), rule.get("relative_tolerance", 1e-6)
        if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in (abs_tol, rel_tol)):
            raise PipelineError("Sum tolerances must be finite and nonnegative")
        if total not in series or any(p not in series for p in parts):
            issue(findings, "review", "sum_rule_unchecked", total, "Declared sum rule cannot run: a selected source table is absent/invalid")
            continue
        checked, skipped = 0, 0
        for key, row in series[total].rows.items():
            for year, value in row.items():
                components = [series[p].rows.get(key, {}).get(year) for p in parts]
                if value is None or any(v is None for v in components):
                    skipped += 1
                    continue
                checked += 1
                if not math.isclose(value, sum(components), abs_tol=abs_tol, rel_tol=rel_tol):
                    issue(findings, "review", "sum_inconsistency", total,
                          f"Total {value} differs from component sum {sum(components)}", " | ".join(key), year)
        issue(findings, "info" if checked else "review", "sum_rule_coverage", total,
              f"Declared sum check evaluated {checked} cells; {skipped} skipped for missing values")

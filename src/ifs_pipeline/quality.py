"""Versioned, read-only statistical evidence. No inference or numerical repairs."""
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path
import shutil
import statistics

from .delivery_core import exact_series
from .storage import (PipelineError, check_idle_database, read_json, readonly,
                      sha256, tables, write_json)

VERSION = 1
DEFAULTS = dict(frequency_years=1, aggregation="unknown", jump_fraction=0.5,
                absolute_floor=0.0, near_zero=1e-9, min_history=5,
                robust_z=6.0, scale_factors=[0.000001, 0.001, 0.01, 100, 1000, 1000000],
                scale_tolerance=0.02, scale_share=0.8, min_pairs=10,
                swap_min_years=3, swap_tolerance=0.02, swap_improvement=0.8,
                reversal_tolerance=0.1, min_value=None, max_value=None)


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def identity(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def rules_for(settings, table):
    if not isinstance(settings, dict) or set(settings) - {"defaults", "tables"}:
        raise PipelineError("Unknown quality settings section")
    overrides = settings.get("tables", {})
    if not isinstance(overrides, dict):
        raise PipelineError("tables settings must be an object")
    parts = [settings.get("defaults", {}), overrides.get(table, {})]
    if any(not isinstance(p, dict) or set(p) - set(DEFAULTS) for p in parts):
        raise PipelineError("Unknown quality rule")
    r = {**DEFAULTS, **parts[0], **parts[1]}
    if r["aggregation"] not in ("unknown", "sum", "country_mean"):
        raise PipelineError("aggregation must be unknown, sum or country_mean")
    for k in ("frequency_years", "min_history", "min_pairs", "swap_min_years"):
        if type(r[k]) is not int or r[k] < 1:
            raise PipelineError(f"{k} must be a positive integer")
    for k in set(DEFAULTS) - {"aggregation", "scale_factors", "frequency_years", "min_history", "min_pairs", "swap_min_years"}:
        if k in ("min_value", "max_value") and r[k] is None:
            continue
        if type(r[k]) not in (int, float) or not math.isfinite(r[k]):
            raise PipelineError(f"{k} must be finite")
        if k not in ("min_value", "max_value") and r[k] < 0:
            raise PipelineError(f"{k} must be nonnegative")
    if r["near_zero"] <= 0 or any(r[k] > 1 for k in ("scale_tolerance", "scale_share", "swap_tolerance", "swap_improvement", "reversal_tolerance")):
        raise PipelineError("Invalid near-zero floor or fractional tolerance")
    if r["min_value"] is not None and r["max_value"] is not None and r["min_value"] > r["max_value"]:
        raise PipelineError("Minimum exceeds maximum")
    if not isinstance(r["scale_factors"], list) or any(type(v) not in (float, int) or not math.isfinite(v) or v <= 0 or v == 1 for v in r["scale_factors"]):
        raise PipelineError("Scale factors must be finite, positive and different from one")
    return r


def change(a, b, r):
    if a is None or b is None:
        return dict(delta=None, relative_change=None, ratio=None, reason="missing_value")
    d = b - a
    if not math.isfinite(d):
        return dict(delta=None, relative_change=None, ratio=None, reason="arithmetic_overflow")
    if abs(a) < r["near_zero"]:
        return dict(delta=d, relative_change=None, ratio=None, reason="near_zero_denominator")
    rel, ratio = d / abs(a), b / a
    return dict(delta=d, relative_change=rel if math.isfinite(rel) else None,
                ratio=ratio if math.isfinite(ratio) else None,
                reason="" if math.isfinite(rel) and math.isfinite(ratio) else "arithmetic_overflow")


def large(row, r):
    return row["delta"] is not None and abs(row["delta"]) > r["absolute_floor"] and (
        row["relative_change"] is None or abs(row["relative_change"]) > r["jump_fraction"])


class Evidence:
    def __init__(self):
        self.rows = {k: [] for k in ("summary", "coverage", "aggregates", "comparisons",
                    "scales", "swaps", "checks", "findings", "observations", "missing", "metadata")}

    def add(self, collection, **row):
        row = {"id": identity([collection, row])[:24], **row}
        self.rows[collection].append(row)
        return row

    def flag(self, rule, evidence, **details):
        context = {k:evidence[k] for k in ("key", "year", "year_before", "before", "after", "delta", "relative_change", "country_a", "country_b", "factor", "share") if k in evidence}
        return self.add("findings", rule=rule, table=evidence["table"],
                        dataset=evidence["dataset"], evidence_id=evidence["id"],
                        severity="review", **context, **details)

    def check(self, table, dataset, rule, status, reason="", evaluated=0):
        self.add("checks", table=table, dataset=dataset, rule=rule, status=status,
                 reason=reason, evaluated=evaluated)


def stats(values, r):
    if not values:
        return dict(count=0, total=None, mean=None, median=None, reason="no_observations")
    try:
        total = math.fsum(values) if r["aggregation"] == "sum" else None
        mean = statistics.fmean(values)
        median = statistics.median(values)
        if any(v is not None and not math.isfinite(v) for v in (total, mean, median)):
            raise OverflowError
    except (OverflowError, ValueError):
        return dict(count=len(values), total=None, mean=None, median=None, reason="arithmetic_overflow")
    return dict(count=len(values), total=total, mean=mean, median=median,
                reason="" if total is not None else "sum_not_declared")


def scale_evidence(e, table, dataset, label, rows, r):
    ratios = [x["ratio"] for x in rows if x["ratio"] is not None and x["before"] != 0 and x["after"] != 0]
    status = "calculated" if len(ratios) >= r["min_pairs"] else "insufficient_evidence"
    e.check(table, dataset, "scale:" + label, status, evaluated=len(ratios))
    for factor in r["scale_factors"]:
        support = [x["id"] for x in rows if x["ratio"] is not None and abs(x["ratio"] / factor - 1) <= r["scale_tolerance"]]
        row = e.add("scales", table=table, dataset=dataset, comparison=label,
                    factor=factor, eligible=len(ratios), supporting=len(support),
                    share=len(support)/len(ratios) if ratios else None, evidence_ids=support, status=status)
        if status == "calculated" and row["share"] >= r["scale_share"]:
            e.flag("possible_scale_change", row)


def inspect(e, table, dataset, series, r):
    years = series.years
    e.add("summary", table=table, dataset=dataset, country_rows=len(series.rows),
          countries_with_data=sum(any(v is not None for v in cells.values()) for cells in series.rows.values()),
          populated_cells=sum(v is not None for cells in series.rows.values() for v in cells.values()),
          year_columns=years, observed_years=[y for y in years if any(c.get(y) is not None for c in series.rows.values())])
    for y in years:
        members = sorted(k for k, c in series.rows.items() if c.get(y) is not None)
        e.add("coverage", table=table, dataset=dataset, year=y, country_rows=len(series.rows),
              populated=len(members), missing=len(series.rows)-len(members), members=members)
        e.add("aggregates", table=table, dataset=dataset, year=y, year_before="", cohort="available",
              members=members, **stats([series.rows[k][y] for k in members], r))
    e.check(table, dataset, "world_total", "calculated" if r["aggregation"] == "sum" else "not_applicable",
            "" if r["aggregation"] == "sum" else "Additivity not declared; means are unweighted country screening statistics")
    transitions = []
    for key, cells in sorted(series.rows.items()):
        valid = [(y, cells[y]) for y in years if cells.get(y) is not None]
        country_changes = []
        for (y0, a), (y1, b) in zip(valid, valid[1:]):
            interval = int(y1) - int(y0)
            row = e.add("comparisons", table=table, dataset=dataset, kind="temporal", key=key,
                        year_before=y0, year=y1, before=a, after=b, interval=interval,
                        expected_interval=interval == r["frequency_years"], **change(a, b, r))
            if interval != r["frequency_years"]:
                e.flag("interval_gap", row)
                continue
            country_changes.append(row)
            transitions.append(row)
            if large(row, r):
                e.flag("temporal_jump", row)
            if a * b < 0:
                e.flag("sign_change", row)
        for y, value in valid:
            if (r["min_value"] is not None and value < r["min_value"]) or (r["max_value"] is not None and value > r["max_value"]):
                row = e.add("comparisons", table=table, dataset=dataset, kind="bounds", key=key, year=y, value=value)
                e.flag("outside_declared_bounds", row)
        for first, second in zip(country_changes, country_changes[1:]):
            if first["year"] == second["year_before"] and large(first, r) and first["delta"] * second["delta"] < 0:
                residual = abs(second["after"] - first["before"]) / max(abs(first["delta"]), r["near_zero"])
                if residual <= r["reversal_tolerance"]:
                    e.flag("spike_reversal", first, next_evidence_id=second["id"], residual=residual)
        evaluated = 0
        for i, row in enumerate(country_changes):
            history = [v["relative_change"] for v in country_changes[:i] if v["relative_change"] is not None]
            if len(history) < r["min_history"] or row["relative_change"] is None:
                continue
            center = statistics.median(history)
            mad = statistics.median(abs(v-center) for v in history)
            distance = abs(row["relative_change"]-center)
            score = distance/(1.4826*mad) if mad > r["near_zero"] else None
            if score is not None and not math.isfinite(score):
                score = None
            evaluated += 1
            robust = e.add("comparisons", table=table, dataset=dataset, kind="historical_variability", key=key,
                           year=row["year"], temporal_id=row["id"], history_count=len(history), median_change=center,
                           mad=mad, score=score, reason="constant_history" if score is None else "")
            if large(row, r) and (score is not None and score > r["robust_z"] or score is None and distance > r["jump_fraction"]):
                e.flag("unusual_country_movement", robust)
        e.check(table, dataset, "historical_variability:" + encoded(key), "calculated" if evaluated else "insufficient_evidence", evaluated=evaluated)
    for y0, y1 in zip(years, years[1:]):
        if int(y1)-int(y0) != r["frequency_years"]:
            continue
        members = sorted(k for k, c in series.rows.items() if c.get(y0) is not None and c.get(y1) is not None)
        previous_members = {k for k,c in series.rows.items() if c.get(y0) is not None}
        current_members = {k for k,c in series.rows.items() if c.get(y1) is not None}
        e.add("coverage", table=table, dataset=dataset, year_before=y0, year=y1,
              matched=len(members), entering=sorted(current_members-previous_members),
              leaving=sorted(previous_members-current_members))
        available_before, available_after = [stats([c[y] for c in series.rows.values() if c.get(y) is not None], r) for y in (y0,y1)]
        for metric in ("total", "mean", "median"):
            e.add("aggregates", table=table, dataset=dataset, year_before=y0, year=y1,
                  cohort="available_change", metric=metric,
                  before=available_before[metric], after=available_after[metric],
                  **change(available_before[metric], available_after[metric], r))
        before, after = [stats([series.rows[k][y] for k in members], r) for y in (y0, y1)]
        for metric in ("total", "mean", "median"):
            row = e.add("aggregates", table=table, dataset=dataset, year_before=y0, year=y1,
                        cohort="matched", metric=metric, members=members, count=len(members),
                        before=before[metric], after=after[metric], **change(before[metric], after[metric], r))
            if large(row, r):
                e.flag("aggregate_jump", row)
        scale_evidence(e, table, dataset, y0+":"+y1, [x for x in transitions if x["year_before"] == y0 and x["year"] == y1], r)
    e.check(table, dataset, "temporal_changes", "calculated" if transitions else "insufficient_evidence", evaluated=len(transitions))
    e.check(table, dataset, "bounds", "calculated" if r["min_value"] is not None or r["max_value"] is not None else "not_applicable", "No bounds inferred")
    return transitions


def compare(e, table, dataset, old, new, r):
    rows = []
    for key in sorted(old.rows.keys() | new.rows.keys()):
        for y in sorted(set(old.years) | set(new.years)):
            a, b = old.rows.get(key, {}).get(y), new.rows.get(key, {}).get(y)
            kind = "both_missing" if a is None and b is None else "unchanged" if a == b else "added" if a is None else "incoming_missing" if b is None else "revised"
            row = e.add("comparisons", table=table, dataset=dataset, kind="baseline", key=key,
                        year=y, before=a, after=b, outcome=kind, **change(a, b, r))
            rows.append(row)
            if a is not None and b is not None and large(row, r):
                e.flag("large_revision", row)
            if kind == "incoming_missing":
                e.flag("incoming_missing", row)
    scale_evidence(e, table, dataset, "baseline", rows, r)
    swaps(e, table, dataset, old, new, r)


def swaps(e, table, dataset, old, new, r, temporal=False):
    evaluated = 0
    for a, b in itertools.combinations(sorted(old.rows.keys() & new.rows.keys()), 2):
        years = sorted(set(old.years) & set(new.years))
        values = [(y, old.rows[a].get(y), old.rows[b].get(y), new.rows[a].get(y), new.rows[b].get(y)) for y in years]
        values = [v for v in values if all(x is not None for x in v[1:])]
        if len(values) < (1 if temporal else r["swap_min_years"]):
            continue
        same, crossed = [], []
        for _, x, y, u, v in values:
            scale = max(abs(x), abs(y), abs(u), abs(v), r["near_zero"])
            same.append((abs(x/scale-u/scale)+abs(y/scale-v/scale))/2)
            crossed.append((abs(x/scale-v/scale)+abs(y/scale-u/scale))/2)
        direct, cross = statistics.fmean(same), statistics.fmean(crossed)
        gain = (direct-cross)/direct if direct > 0 else 0
        evaluated += 1
        candidate = cross <= r["swap_tolerance"] and direct > r["swap_tolerance"] and gain >= r["swap_improvement"]
        # Pairwise search is quadratic: retain candidates, while recording every evaluation count.
        # All underlying observations are exported, so rejected pairs remain reproducible.
        if not candidate:
            continue
        row = e.add("swaps", table=table, dataset=dataset, kind="temporal" if temporal else "baseline",
                    country_a=a, country_b=b, years=[v[0] for v in values], values=values,
                    year_before=str(int(values[0][0])-r["frequency_years"]) if temporal else "",
                    direct_error=direct, crossed_error=cross, improvement=gain)
        e.flag("possible_temporal_swap" if temporal else "possible_country_swap", row)
    e.check(table, dataset, "temporal_swaps:"+old.years[0] if temporal else "baseline_swaps",
            "calculated" if evaluated else "insufficient_evidence", evaluated=evaluated)


def save_csv(path, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row)) or ["id"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: encoded(v) if isinstance(v, (list, tuple, dict)) else v for k, v in row.items()})


def run_quality(spec_path, output):
    """Explicit dataset scope; final origins are calculated only from declared applied inputs."""
    spec_path, output = Path(spec_path).resolve(), Path(output).resolve()
    spec = read_json(spec_path)
    if not isinstance(spec, dict) or set(spec) - {"version", "datasets", "tables", "settings", "baseline_names"} or spec.get("version") != VERSION:
        raise PipelineError("Expected quality specification version 1")
    selected = spec.get("tables")
    datasets = spec.get("datasets")
    if not isinstance(selected, list) or not selected or any(not isinstance(t, str) or not t.startswith("Series") for t in selected) or len(set(selected)) != len(selected):
        raise PipelineError("Specify unique Series table names")
    if not isinstance(datasets, list) or not datasets:
        raise PipelineError("No datasets")
    settings = spec.get("settings", {})
    rules = {t: rules_for(settings, t) for t in selected}
    ids = set()
    for d in datasets:
        if not isinstance(d, dict) or set(d) - {"id", "role", "path", "metadata", "applied_tables"} or d.get("role") not in ("base", "incoming", "final"):
            raise PipelineError("Invalid dataset fields/role")
        if not isinstance(d.get("id"), str) or not d["id"] or d["id"] in ids:
            raise PipelineError("Dataset IDs must be nonempty and unique")
        ids.add(d["id"])
        for field in ("path", "metadata"):
            if field in d:
                p = Path(d[field])
                d[field] = str((spec_path.parent / p).resolve() if not p.is_absolute() else p.resolve())
        if "path" not in d:
            raise PipelineError("Dataset path required")
        if "applied_tables" in d and (not isinstance(d["applied_tables"], list) or any(t not in selected for t in d["applied_tables"])):
            raise PipelineError("applied_tables must be within selected scope")
    if sum(d["role"] == "base" for d in datasets) != 1 or sum(d["role"] == "final" for d in datasets) > 1:
        raise PipelineError("Exactly one base and at most one final required")
    if [d["role"] for d in datasets] != sorted([d["role"] for d in datasets], key={"base":0,"incoming":1,"final":2}.get):
        raise PipelineError("Declare base, incoming batches in precedence order, then final")
    inputs = sorted({d[k] for d in datasets for k in ("path", "metadata") if k in d})
    for p in inputs:
        check_idle_database(Path(p))
    hashes = {p: sha256(p) for p in inputs}
    output.mkdir(parents=True, exist_ok=False)
    try:
        frozen = output / "inputs"
        frozen.mkdir()
        copies = {}
        for index, p in enumerate(inputs):
            target = frozen / f"{index:03d}.db"
            shutil.copyfile(p, target)
            if sha256(target) != hashes[p]:
                raise PipelineError("Input changed during freezing")
            copies[p] = target
        write_json(output / "spec.json", spec)
        write_json(output / "settings.json", rules)
        e = Evidence()
        for table in sorted(selected):
            loaded, origins, expected, findings_start = {}, {}, {}, len(e.rows["findings"])
            for d in datasets:
                label, role = d["id"], d["role"]
                with readonly(copies[d["path"]]) as conn:
                    if table not in tables(conn):
                        e.check(table, label, "table", "not_applicable", "table_absent")
                        continue
                    try:
                        missing = []
                        series = exact_series(conn, table, missing=missing, batch=label)
                    except PipelineError as exc:
                        e.check(table, label, "table", "insufficient_evidence", str(exc))
                        continue
                for row in missing:
                    e.add("missing", dataset=label, **row)
                if len(series.keys) != 2:
                    e.check(table, label, "monadic_checks", "not_applicable", "dyadic_checks_not_implemented")
                    continue
                if role == "base" and table in spec.get("baseline_names", {}):
                    mapping = spec["baseline_names"][table]
                    for item in mapping:
                        old_key, new_key = (item["before"], item["code"]), (item["after"], item["code"])
                        if old_key not in series.rows or new_key in series.rows or not all(isinstance(v,str) and v.strip()==v and v for v in new_key):
                            raise PipelineError("Invalid explicit baseline name view")
                        series.rows[new_key] = series.rows.pop(old_key)
                    if len({k[0] for k in series.rows}) != len(series.rows):
                        raise PipelineError("Baseline name view creates duplicate names")
                loaded[label] = series
                e.check(table, label, "table", "calculated")
                metadata_path = d.get("metadata", d["path"])
                with readonly(copies[metadata_path]) as conn:
                    if "DataDict" in tables(conn):
                        for row in conn.execute('SELECT * FROM DataDict WHERE "Table"=?', (table,)):
                            e.add("metadata", table=table, dataset=label, fields=dict(row))
                r = rules[table]
                transitions = inspect(e, table, label, series, r)
                for key, cells in sorted(series.rows.items()):
                    for year, value in sorted(cells.items()):
                        e.add("observations", table=table, dataset=label, key=key, year=year, value=value)
                if role != "base" and datasets[0]["id"] in loaded:
                    compare(e, table, label, loaded[datasets[0]["id"]], series, r)
                elif role != "base":
                    e.check(table, label, "baseline_comparison", "insufficient_evidence", "base_table_unavailable")
                # Single-transition crossed assignments, with original years preserved in the dataset label.
                from .model import Series
                for y0, y1 in zip(series.years, series.years[1:]):
                    if int(y1)-int(y0) == r["frequency_years"]:
                        before = Series(table, series.keys, [y1], {k:{y1:c.get(y0)} for k,c in series.rows.items()})
                        after = Series(table, series.keys, [y1], {k:{y1:c.get(y1)} for k,c in series.rows.items()})
                        swaps(e, table, label, before, after, r, temporal=True)
                if role in ("base", "incoming") and (role == "base" or table in d.get("applied_tables", [])):
                    for key, cells in series.rows.items():
                        for year, value in cells.items():
                            if value is not None:
                                expected[key, year] = value
                                origins[key, year] = label
                if role == "final":
                    declared = all("applied_tables" in x and (table not in x["applied_tables"] or x["id"] in loaded) for x in datasets if x["role"] == "incoming")
                    actual = {(k,y):v for k,c in series.rows.items() for y,v in c.items() if v is not None}
                    matched = declared and expected == actual
                    e.check(table, label, "declared_exact_origins", "calculated" if matched else "insufficient_evidence",
                            "Matches explicit non-null precedence; not native delivery certification" if matched else "Missing applied scope or values differ from declared replay")
                    if matched:
                        for row in e.rows["observations"]:
                            if row["table"] == table and row["dataset"] == label:
                                row["origin"] = origins.get((row["key"], row["year"]))
                        for row in transitions:
                            source_pair = [origins.get((row["key"], y)) for y in (row["year_before"], row["year"])]
                            if len(set(source_pair)) > 1 and large(row, r):
                                e.flag("blending_boundary_jump", row, origins=source_pair)
            # Classify only exact same numerical evidence; never infer from flag counts.
            lookup = {x["id"]:x for kind in e.rows for x in e.rows[kind]}
            groups = {}
            for f in e.rows["findings"][findings_start:]:
                target = lookup[f["evidence_id"]]
                signature = {k:v for k,v in target.items() if k not in ("id", "dataset")}
                groups.setdefault(identity([f["rule"],signature]),[]).append(f)
            for f in e.rows["findings"][findings_start:]:
                target = lookup[f["evidence_id"]]
                signature = {k:v for k,v in target.items() if k not in ("id", "dataset")}
                related = [x for x in groups[identity([f["rule"],signature])] if x["dataset"] != f["dataset"]]
                f["matching_findings"] = [x["id"] for x in related]
                incoming_ids = {d["id"] for d in datasets if d["role"] == "incoming"}
                f["classification"] = ("also_in_base" if any(x["dataset"] == datasets[0]["id"] for x in related) else
                    "also_in_incoming" if any(x["dataset"] in incoming_ids for x in related) else
                    "blending_boundary" if f["rule"] == "blending_boundary_jump" else "dataset_specific_or_unresolved")
        from collections import Counter
        outcomes = Counter((r["table"],r["dataset"],r.get("outcome")) for r in e.rows["comparisons"] if r["kind"] == "baseline")
        finding_counts = Counter((r["table"],r["dataset"]) for r in e.rows["findings"])
        for row in e.rows["summary"]:
            for outcome in ("added", "revised", "incoming_missing", "unchanged", "both_missing"):
                row[outcome] = outcomes[row["table"],row["dataset"],outcome]
            row["findings"] = finding_counts[row["table"],row["dataset"]]
            row["checks"] = dict(Counter(r["status"] for r in e.rows["checks"] if r["table"]==row["table"] and r["dataset"]==row["dataset"]))
        for p in inputs:
            check_idle_database(Path(p))
            if sha256(p) != hashes[p]:
                raise PipelineError("Input changed during analysis")
        for kind, rows in e.rows.items():
            save_csv(output / (kind + ".csv"), rows)
        write_json(output / "evidence.json", e.rows)
        save_csv(output / "issues.csv", [dict(issue_id=f["id"], stage="comparison", owner_stage="comparison",
            source_file=next(d["path"] for d in datasets if d["id"] == f["dataset"]), batch_id=f["dataset"],
            table=f["table"], variable="", country_key=f.get("key", ""), year=f.get("year", ""),
            category=f["rule"], severity="review", status="open", message=f["rule"],
            evidence_path="evidence.json", evidence_id=f["evidence_id"],
            next_action="Inspect numerical evidence and metadata; route confirmed source problems to preprocessing/validation") for f in e.rows["findings"]])
        write_json(output / "interpretation-input.json", {
            "instructions":"Optional interpretation only. Cite finding IDs and evidence IDs. Distinguish hypotheses from supported explanations. Do not change observations, clear flags or claim release approval. Use quality-review --author-type ai for suggestions; human reviews remain distinct.",
            "findings":e.rows["findings"], "checks":e.rows["checks"],
            "evidence_file":"evidence.json", "metadata_file":"metadata.csv"})
        from .quality_report import render
        render(output, e.rows)
        write_json(output / "review-template.json", {"version":1, "records":[], "allowed_decisions":["explained", "requires_investigation", "confirmed_problem"]})
        unavailable = sum(x["status"] == "insufficient_evidence" for x in e.rows["checks"])
        manifest = {"version":VERSION, "status":"completed_with_limitations" if unavailable else "completed_with_findings" if e.rows["findings"] else "completed",
                    "inputs":hashes, "frozen_inputs":{p:str(copies[p].relative_to(output)) for p in inputs},
                    "code":{p.name:sha256(p) for p in Path(__file__).parent.glob("*.py")},
                    "tables":selected, "findings":len(e.rows["findings"]), "unavailable_checks":unavailable,
                    "limitations":["Statistical evidence is not semantic approval or native delivery certification", "Dyadic statistical checks unsupported", "No weighted world rates"]}
        manifest["artifacts"] = {str(p.relative_to(output)):sha256(p) for p in sorted(output.rglob("*")) if p.is_file()}
        manifest["evidence_id"] = identity(manifest)
        write_json(output / "manifest.json", manifest)
        return {"status":manifest["status"], "output":str(output), "findings":manifest["findings"]}
    except Exception as exc:
        write_json(output / "failure.json", {"status":"failed", "error":str(exc)})
        raise


def verify_quality(folder):
    folder = Path(folder).resolve()
    if (folder / "failure.json").exists():
        raise PipelineError("Quality analysis failed; inspect failure.json")
    m = read_json(folder / "manifest.json")
    if identity({k:v for k,v in m.items() if k != "evidence_id"}) != m["evidence_id"]:
        raise PipelineError("Quality manifest changed")
    for relative, digest in m["artifacts"].items():
        p = (folder / relative).resolve()
        if not p.is_relative_to(folder) or sha256(p) != digest:
            raise PipelineError(f"Quality evidence changed: {relative}")
    return {"status":"verified", "evidence_id":m["evidence_id"]}


def record_review(folder, finding, reviewer, decision, rationale, author_type="human"):
    folder = Path(folder).resolve()
    verified = verify_quality(folder)
    evidence = read_json(folder / "evidence.json")
    if finding not in {r["id"] for r in evidence["findings"]}:
        raise PipelineError("Unknown finding ID")
    if decision not in ("explained", "requires_investigation", "confirmed_problem") or author_type not in ("human", "ai") or not reviewer.strip() or not rationale.strip():
        raise PipelineError("Review requires valid decision, author type, reviewer and rationale")
    from .storage import new_id, now
    row = dict(evidence_id=verified["evidence_id"], finding_id=finding, reviewer=reviewer,
               author_type=author_type, decision=decision, rationale=rationale, recorded_at=now())
    path = folder / "reviews" / (new_id() + ".json")
    write_json(path, row)
    return {"status":"recorded", "path":str(path)}


def delivery_quality(delivery, output, settings_path=None):
    """Use verified native scope/order. Never remerge or refresh native reports."""
    import tempfile
    from .delivery import verify_recorded_delivery
    from .delivery_core import REPORT, resolve_folder, locked
    from .storage import inside
    delivery, output = resolve_folder(delivery), Path(output).resolve()
    report = delivery / REPORT
    # The native verifier can recover publication. This entry point must remain read-only.
    if (report / "pending.json").exists():
        raise PipelineError("Pending publication must be recovered through the native delivery workflow first")
    if output.is_relative_to(report / "runs") or output.is_relative_to(report / "imports"):
        raise PipelineError("Quality output must be separate from native run evidence and frozen imports")
    with locked(delivery):
        if (report / "pending.json").exists():
            raise PipelineError("Pending publication; use native recovery first")
        state, run = verify_recorded_delivery(delivery)
        active = [b for b in state["batches"] if not b["excluded"]]
        selected = sorted({t for b in active for t in b.get("applied_tables", [])})
        if not selected:
            raise PipelineError("No applied tables to analyze")
        datasets = [{"id":"base", "role":"base", "path":str(Path(state["base"])/"IFsHistSeries.db"),
                     "metadata":str(Path(state["base"])/"DataDict.db")}]
        for b in active:
            datasets.append({"id":b["id"], "role":"incoming", "path":str(inside(report,b["archive"])),
                             "applied_tables":b.get("applied_tables", [])})
        datasets.append({"id":"final", "role":"final", "path":str(delivery/"IFsHistSeries.db"), "metadata":str(delivery/"DataDict.db")})
        spec = {"version":1, "tables":selected, "datasets":datasets,
                "settings":read_json(settings_path) if settings_path else {}}
        if (run/"baseline_names.json").exists():
            spec["baseline_names"] = read_json(run/"baseline_names.json")["tables"]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/"spec.json"
            write_json(p,spec)
            result = run_quality(p,output)
        # Keep native scope and exact origins alongside this supplemental analysis.
        native = output / "native-context"
        native.mkdir()
        for name in ("processed_tables.csv", "baseline_names.json", "run.json", "metadata_changes.csv", "incoming_missing.csv"):
            if (run/name).is_file():
                shutil.copyfile(run/name,native/name)
        shutil.copyfile(report/"delivery.json",native/"delivery.json")
        # Verify again before sealing native evidence; no old manifests are rewritten.
        try:
            verify_recorded_delivery(delivery)
        except Exception as exc:
            write_json(output / "failure.json", {"status":"failed", "error":str(exc)})
            raise
        m=read_json(output/"manifest.json")
        m["native_delivery"]={"path":str(delivery), "run":run.name, "verification":"passed",
                              "note":"Recorded same-code baseline name views are applied for comparison only; frozen databases are unchanged"}
        m["artifacts"].update({str(p.relative_to(output)):sha256(p) for p in native.iterdir()})
        m["evidence_id"]=identity({k:v for k,v in m.items() if k != "evidence_id"})
        write_json(output/"manifest.json",m)
        return result

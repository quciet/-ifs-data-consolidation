"""Validation reports and explicit merge decisions for prepared IFs imports."""
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path

from .adapters import country_reference, load_recipe
from .diagnostics import (check_sum_rules, compare_incoming, inspect_new, issue, profile, rules_for)
from .storage import (PipelineError, check_idle_database, columns, inside, new_id, now,
                      read_json, readonly, records, resource_path, sha256, tables, write_json)


def code_hashes():
    return {p.name: sha256(p) for p in Path(__file__).parent.glob("*.py")}


def dump_csv(path, rows, fields):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def request_context(root, request_id):
    folder = inside(root / "inbox", request_id)
    request = read_json(folder / "request.json")
    raw = inside(root, request["raw_path"])
    hashes = {"request.json": sha256(folder / "request.json")}
    for name, expected, path in (
            ("source", request["sha256"], raw),
            ("recipe.json", request["recipe_sha256"], folder / "recipe.json"),
            ("countries.csv", request["country_reference_sha256"], folder / "countries.csv")):
        if sha256(path) != expected:
            raise PipelineError(f"Archived request artifact changed: {name}")
        hashes[name] = expected
    for name, expected in request.get("concordance_evidence_sha256", {}).items():
        if sha256(inside(folder / "concordance", name)) != expected:
            raise PipelineError(f"Archived concordance changed: {name}")
    recipe = load_recipe(folder / "recipe.json")
    if recipe["adapter"] != "ifs_sqlite":
        raise PipelineError("validate currently targets prepared IFs SQLite imports; CSV retains its existing pipeline")
    return request, raw, recipe, hashes


def selected_tables(conn, recipe):
    metadata = records(conn, "DataDict")
    all_names = {r.get("Table") for r in metadata}
    if not all_names or any(not isinstance(t, str) or not t.startswith("Series") for t in all_names):
        raise PipelineError("Import DataDict has missing or invalid Series table names")
    names = recipe.get("tables")
    if names is None:
        names = sorted(all_names)
    elif not isinstance(names, list) or not names or len(set(names)) != len(names) or set(names)-all_names:
        raise PipelineError("Invalid selected tables or tables absent from import DataDict")
    return names, [r for r in metadata if r["Table"] in names]


def country_stats(series, request_id, dataset):
    if series is None:
        return []
    output = []
    for key, values in sorted(series.rows.items()):
        years = [y for y in series.years if values.get(y) is not None]
        output.append({"request_id": request_id, "dataset": dataset, "table": series.name,
                       "key": " | ".join(key), "populated_years": len(years),
                       "first_populated_year": years[0] if years else "",
                       "last_populated_year": years[-1] if years else ""})
    return output


def validate_requests(root, request_ids):
    root = Path(root).resolve()
    if not request_ids or len(set(request_ids)) != len(request_ids):
        raise PipelineError("Provide one or more distinct request IDs")
    review_id = new_id()
    folder = root / "reviews" / review_id
    folder.mkdir(parents=True)
    findings, normalization, missing_counts, differences, coverage, countries = [], [], [], [], [], []
    review = {"schema_version": 1, "id": review_id, "created_at": now(),
              "status": "blocked", "requests": {}, "code_sha256": code_hashes()}
    choices = {}
    table_sources = defaultdict(list)
    try:
        config = read_json(root / "config.local.json")
        baseline = resource_path(root, config["baseline"])
        master = resource_path(root, config["country_reference"])
        canonical, _ = country_reference(master)
        review["baseline"] = str(baseline)
        review["master_sha256"] = sha256(master)
        review["baseline_sha256"] = {}
        for name in ("IFsHistSeries.db", "DataDict.db"):
            check_idle_database(baseline / name)
            review["baseline_sha256"][name] = sha256(baseline / name)
        with readonly(baseline / "IFsHistSeries.db") as old_conn, readonly(baseline / "DataDict.db") as md_conn:
            old_tables = tables(old_conn)
            for request_id in request_ids:
                first_missing, first_norm = len(missing_counts), len(normalization)
                local, new_series = [], {}
                try:
                    request, raw, recipe, hashes = request_context(root, request_id)
                    check_idle_database(raw)
                    request_canonical, _ = country_reference(inside(root / "inbox", request_id) / "countries.csv")
                    if request_canonical != canonical:
                        raise PipelineError("Archived country reference differs from the configured master")
                    review["requests"][request_id] = {"hashes": hashes, "source_url": request["source_url"],
                                                     "source_release": request["source_release"], "tables": {}}
                    with readonly(raw) as new_conn:
                        check = [r[0] for r in new_conn.execute("PRAGMA quick_check")]
                        if check != ["ok"]:
                            raise PipelineError(f"Import quick_check failed: {check}")
                        names, metadata = selected_tables(new_conn, recipe)
                        choices[request_id] = {}
                        for table in names:
                            table_sources[table].append(request_id)
                            choices[request_id][table] = {"action": None, "reason": ""}
                            rules = rules_for(recipe, table)
                            findings_start = len(local)
                            try:
                                incoming = profile(new_conn, table, recipe, canonical, local,
                                                   missing_counts, normalization, "incoming")
                                for r in local[findings_start:]:
                                    r["dataset"] = "incoming"
                                if incoming is None:
                                    continue
                                new_series[table] = incoming
                                old_findings, old_missing, old_norm = [], [], []
                                old = profile(old_conn, table, {"kind": recipe.get("kind", "monadic")},
                                              canonical, old_findings, old_missing, old_norm, "baseline") if table in old_tables else None
                                # Baseline issues are distinct from defects introduced by this import.
                                for r in old_findings:
                                    r["dataset"] = "baseline"
                                    r["code"] = "baseline_" + r["code"]
                                local.extend(old_findings)
                                missing_counts.extend(old_missing)
                                normalization.extend(old_norm)
                                if table in old_tables and old is None:
                                    continue
                                inspect_new(incoming, rules, local)
                                local_differences, local_coverage = [], []
                                compare_incoming(old, incoming, rules, local, local_differences, local_coverage)
                                differences.extend({**r, "request_id": request_id} for r in local_differences)
                                coverage.extend({**r, "request_id": request_id} for r in local_coverage)
                                countries.extend(country_stats(incoming, request_id, "incoming"))
                                countries.extend(country_stats(old, request_id, "baseline"))
                                # Reuse the actual writer's metadata compatibility rules without writing.
                                from .workflow import metadata_for_candidate
                                _, changed, _ = metadata_for_candidate(md_conn, metadata, recipe, {table: incoming})
                                for change in changed:
                                    field = change["field"]
                                    if field in ("Last IFs Update", "Years"):
                                        continue
                                    if change["before"] != change["after"]:
                                        issue(local, "review", "metadata_changed", table,
                                              f"{change['variable']}/{field}: {change['before']!r} -> {change['after']!r}")
                                populated_years = [y for y in incoming.years if any(r.get(y) is not None for r in incoming.rows.values())]
                                actual_span = f"{populated_years[0]}-{populated_years[-1]}" if populated_years else ""
                                for row in [r for r in metadata if r["Table"] == table]:
                                    if row.get("Years") not in (None, "", actual_span):
                                        issue(local, "review", "metadata_year_span", table,
                                              f"DataDict Years={row.get('Years')!r}; observed non-null span={actual_span!r}")
                                review["requests"][request_id]["tables"][table] = {
                                    "incoming_rows": len(incoming.rows),
                                    "incoming_countries_with_data": sum(any(v is not None for v in row.values()) for row in incoming.rows.values()),
                                    "incoming_year_columns": len(incoming.years),
                                    "baseline_rows": len(old.rows) if old else 0,
                                    "rules": rules}
                            except (PipelineError, ValueError, TypeError) as exc:
                                issue(local, "error", "table_validation", table, str(exc))
                        extras = {n for n in tables(new_conn) if n.startswith("Series")} - {r["Table"] for r in records(new_conn, "DataDict")}
                        if extras:
                            issue(local, "review", "unlisted_source_tables", "", f"Source Series tables absent from DataDict: {sorted(extras)}")
                    check_sum_rules(new_series, recipe, local)
                    if not new_series:
                        issue(local, "error", "no_valid_series", "", "No structurally usable incoming series")
                except Exception as exc:
                    issue(local, "error", "request_validation", "", f"{type(exc).__name__}: {exc}")
                for rows in (missing_counts[first_missing:], normalization[first_norm:]):
                    for row in rows:
                        row["request_id"] = request_id
                for item in local:
                    item["request_id"] = request_id
                    content = {k: v for k, v in item.items() if k != "id"}
                    item["id"] = request_id + ":" + hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:16]
                findings.extend(local)
            for table, ids in table_sources.items():
                if len(ids) > 1:
                    issue(findings, "review", "overlapping_imports", table,
                          f"Multiple requests update this table: {ids}. Choose source precedence before cumulative processing.")
                    findings[-1]["request_id"] = "batch"
                    findings[-1]["id"] = "batch:" + findings[-1]["id"]
        # Reject a review assembled from changing evidence.
        for request_id, entry in review["requests"].items():
            if request_context(root, request_id)[3] != entry["hashes"]:
                raise PipelineError("Archived input changed during review")
        for name, before in review["baseline_sha256"].items():
            check_idle_database(baseline / name)
            if sha256(baseline / name) != before:
                raise PipelineError("Baseline changed during review; use a stable release")
        if sha256(master) != review["master_sha256"] or code_hashes() != review["code_sha256"]:
            raise PipelineError("Master reference or processing code changed during review")
    except Exception as exc:
        issue(findings, "error", "review_validation", "", f"{type(exc).__name__}: {exc}")
        findings[-1]["request_id"] = "batch"
    # Some checks naturally find the same metadata issue twice; stable IDs deduplicate it.
    findings = list({r["id"]: r for r in findings}.values())
    counts = Counter(r["severity"] for r in findings)
    review["counts"] = {k: counts[k] for k in ("error", "review", "info")}
    review["status"] = "blocked" if counts["error"] else "ready_for_decision"
    review["finished_at"] = now()
    dump_csv(folder / "issues.csv", findings, ["id", "request_id", "dataset", "severity", "code", "table", "key", "year", "message"])
    dump_csv(folder / "missing_values.csv", missing_counts, ["request_id", "dataset", "table", "representation", "cells"])
    dump_csv(folder / "normalization.csv", normalization, ["request_id", "dataset", "table", "row", "key", "year", "storage_type", "raw_value", "category", "action"])
    dump_csv(folder / "incoming_vs_old.csv", differences, ["request_id", "table", "key", "year", "old", "incoming", "change"])
    dump_csv(folder / "coverage.csv", coverage, ["request_id", "table", "year", "old_rows", "incoming_rows", "old_non_null", "incoming_non_null"])
    dump_csv(folder / "country_coverage.csv", countries, ["request_id", "dataset", "table", "key", "populated_years", "first_populated_year", "last_populated_year"])
    lines = ["# Prepared IFs import validation", "", f"Status: **{review['status']}**", "",
             f"Blocking errors: {counts['error']}; review findings: {counts['review']}; informational findings: {counts['info']}.", "",
             "Incoming data was inspected before any blending. No candidate databases were created.",
             "Missing-value normalization is proposed in memory; original files are unchanged.", "",
             "## Findings by check", "", "| Severity | Check | Count |", "|---|---|---:|"]
    for (severity, code), count in sorted(Counter((r["severity"], r["code"]) for r in findings).items()):
        lines.append(f"| {severity} | {code} | {count} |")
    lines += ["", "## First findings requiring attention", ""]
    for r in [r for r in findings if r["severity"] != "info"][:40]:
        lines.append(f"- [{r['severity']}] {r['table']} {r['key']} {r['year']}: {r['message']}")
    lines += ["", "Full details: issues.csv, normalization.csv, missing_values.csv, incoming_vs_old.csv, coverage.csv and country_coverage.csv.",
              "", "## Merge decision", "",
              "Resolve errors first. Copy decisions.template.json to a separate file, choose each table's action",
              "(prefer_new_non_null, replace, keep_old, or hold), explain the decision, and list accepted review issue IDs.",
              "Use run REQUEST_ID --decisions FILE only after those decisions are settled.",
              "A missing incoming observation is not evidence that the old value should be restored or deleted.",
              "", "Screening defaults are annual frequency, 50% jumps/revisions, a 1e-9 near-zero floor, and six-value flat runs.",
              "Use source/table rules for meaningful frequency, absolute-change floors and bounds. Heuristic flags are not corrections.",
              "Country misplacement checks find exact trajectory reuse; they cannot prove or detect every incorrect assignment.",
              "Sum consistency checks run only when explicitly declared; missing components are not treated as zero.",
              "For multiple imports, settle overlaps and process cumulatively. Changing the baseline requires a fresh review."]
    (folder / "report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    review["files"] = {p.name: sha256(p) for p in folder.iterdir() if p.is_file()}
    write_json(folder / "review.json", review)
    write_json(folder / "decisions.template.json", {"schema_version": 1, "review_id": review_id,
               "review_sha256": sha256(folder / "review.json"), "accepted_issue_ids": [], "requests": choices})
    return {**review, "folder": str(folder), "report": str(folder / "report.md")}


def decision_context(root, request_id, decisions_path):
    root = Path(root).resolve()
    decisions_path = Path(decisions_path).resolve()
    decisions = read_json(decisions_path)
    if decisions.get("schema_version") != 1:
        raise PipelineError("Decisions schema_version must be 1")
    folder = inside(root / "reviews", decisions["review_id"])
    if sha256(folder / "review.json") != decisions.get("review_sha256"):
        raise PipelineError("Review changed after the decision template was created")
    review = read_json(folder / "review.json")
    if review["status"] != "ready_for_decision":
        raise PipelineError("Blocking validation errors must be resolved before merging")
    if request_id not in review["requests"]:
        raise PipelineError("This request is absent from the reviewed batch")
    for reviewed_id, entry in review["requests"].items():
        if request_context(root, reviewed_id)[3] != entry["hashes"]:
            raise PipelineError("Request differs from reviewed input")
    config = read_json(root / "config.local.json")
    baseline = resource_path(root, config["baseline"])
    if str(baseline) != review["baseline"]:
        raise PipelineError("Baseline changed; validate this request again")
    for name, expected in review["baseline_sha256"].items():
        check_idle_database(baseline / name)
        if sha256(baseline / name) != expected:
            raise PipelineError("Baseline changed; validate this request again")
    if sha256(resource_path(root, config["country_reference"])) != review["master_sha256"]:
        raise PipelineError("Country master changed; validate again")
    if code_hashes() != review["code_sha256"]:
        raise PipelineError("Processing code changed; validate again")
    for name, expected in review["files"].items():
        if sha256(inside(folder, name)) != expected:
            raise PipelineError(f"Review evidence changed: {name}")
    with (folder / "issues.csv").open(encoding="utf-8-sig", newline="") as handle:
        issues = list(csv.DictReader(handle))
    review_ids = {r["id"] for r in issues if r["severity"] == "review"}
    required = {r["id"] for r in issues if r["severity"] == "review" and r["request_id"] in (request_id, "batch")}
    accepted = decisions.get("accepted_issue_ids", [])
    if not isinstance(accepted, list) or len(accepted) != len(set(accepted)) or set(accepted)-review_ids:
        raise PipelineError("accepted_issue_ids must contain distinct review finding IDs from this report")
    if required-set(accepted):
        raise PipelineError(f"Unresolved review findings: {sorted(required-set(accepted))}")
    selected = decisions.get("requests", {}).get(request_id, {})
    expected_tables = set(review["requests"][request_id]["tables"])
    if set(selected) != expected_tables:
        raise PipelineError("Record a merge decision for every reviewed table in this request")
    for table, choice in selected.items():
        if not isinstance(choice, dict) or choice.get("action") not in ("prefer_new_non_null", "replace", "keep_old", "hold"):
            raise PipelineError(f"{table}: choose a valid merge action")
        if not isinstance(choice.get("reason"), str) or not choice["reason"].strip():
            raise PipelineError(f"{table}: record the decision reason")
        if choice["action"] == "hold":
            raise PipelineError(f"{table}: merge is on hold; resolve it or select an explicit keep_old action")
    return folder, decisions, review

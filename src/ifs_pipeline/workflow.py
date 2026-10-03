"""Versioned intake, candidate creation, review artifacts, and local release publication."""
import csv
import json
from pathlib import Path
import re
import shutil
import sqlite3
from . import __version__
from .adapters import country_reference, load_recipe, load_source
from .model import META, blend, compare, read_series, write_series
from .storage import (PipelineError, check_idle_database, columns, inside, new_id, now,
                      q, read_json, readonly, records, resource_path, sha256, snapshot,
                      tables, write_json)

DATABASES = ("IFsHistSeries.db", "DataDict.db")


def csv_write(path, rows, fields):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def initialize(root):
    root = Path(root).resolve()
    for name in ("inbox", "raw", "reviews", "runs", "releases", "reference/local"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def configure(root, baseline, country_table="SeriesPopulation"):
    root = initialize(root)
    baseline = Path(baseline).resolve()
    for name in DATABASES:
        if not (baseline / name).is_file():
            raise PipelineError(f"Baseline needs {baseline / name}")
        check_idle_database(baseline / name)
    with readonly(baseline / "IFsHistSeries.db") as conn:
        names = {c["name"] for c in columns(conn, country_table)}
        if not {"Country", "FIPS_CODE"}.issubset(names):
            raise PipelineError("Country reference table must contain Country and FIPS_CODE")
        rows = [dict(r) for r in conn.execute(f"SELECT Country, FIPS_CODE FROM {q(country_table)} ORDER BY Country")]
    if not rows or len({r["Country"] for r in rows}) != len(rows) or len({r["FIPS_CODE"] for r in rows}) != len(rows):
        raise PipelineError("Country reference table is empty or has duplicate countries/codes")
    if any(not isinstance(v, str) or not v.strip() or v != v.strip() for r in rows for v in r.values()):
        raise PipelineError("Country reference keys must be non-empty, trimmed text")
    with readonly(baseline / "DataDict.db") as conn:
        catalog = records(conn, "DataDict")
        schema = columns(conn, "DataDict")
    # Version reference exports so old source-specific alias mappings are never overwritten.
    folder = root / "reference/local" / new_id()
    folder.mkdir()
    for row in rows:
        row["source_code"] = row["FIPS_CODE"]
    csv_write(folder / "countries.csv", rows, ["source_code", "Country", "FIPS_CODE"])
    country_reference(folder / "countries.csv")
    csv_write(folder / "indicator_catalog.csv", catalog, [c["name"] for c in schema])
    write_json(folder / "datadict_schema.json", schema)
    config = {"schema_version": 1, "baseline": str(baseline),
              "country_reference": str((folder / "countries.csv").relative_to(root)),
              "country_table": country_table, "country_count": len(rows), "configured_at": now()}
    write_json(folder / "provenance.json", config)
    write_json(root / "config.local.json", config)
    return config


def ingest(root, input_path, recipe_path, source_url, source_release, notes=""):
    root = initialize(root)
    source = Path(input_path).resolve()
    recipe_path = Path(recipe_path).resolve()
    recipe = load_recipe(recipe_path)
    if not source.is_file():
        raise PipelineError(f"Input does not exist: {source}")
    concordance = None
    if recipe.get("concordance_manifest"):
        from .concordance import validate_bundle
        concordance = validate_bundle(root, recipe["concordance_manifest"], source, recipe)
    if not source_url.strip() or not source_release.strip():
        raise PipelineError("Record source_url and source_release for provenance")
    if recipe["adapter"] == "ifs_sqlite":
        check_idle_database(source)
        with readonly(source) as conn:
            columns(conn, "DataDict")
    digest = sha256(source)
    folder = root / "raw" / digest
    folder.mkdir(exist_ok=True)
    destination = folder / source.name
    if destination.exists():
        if sha256(destination) != digest:
            raise PipelineError(f"Archived source was modified: {destination}")
    else:
        shutil.copy2(source, destination)
        if sha256(destination) != digest or sha256(source) != digest:
            raise PipelineError("Input changed while being archived; close the source and retry")
    if recipe["adapter"] == "ifs_sqlite":
        check_idle_database(source)
    request_id = new_id()
    request_dir = root / "inbox" / request_id
    request_dir.mkdir()
    write_json(request_dir / "recipe.json", recipe)
    request = {"schema_version": 1, "id": request_id, "created_at": now(),
               "raw_path": str(destination.relative_to(root)), "sha256": digest,
               "original_path": str(source), "source_url": source_url,
               "source_release": source_release, "notes": notes,
               "recipe_sha256": sha256(request_dir / "recipe.json")}
    if recipe.get("country_reference"):
        mapping = resource_path(root, recipe["country_reference"])
    elif (root / "config.local.json").exists():
        mapping = resource_path(root, read_json(root / "config.local.json")["country_reference"])
    else:
        raise PipelineError("Configure a baseline or specify a recipe country_reference before intake")
    country_reference(mapping)
    shutil.copy2(mapping, request_dir / "countries.csv")
    request["country_reference_sha256"] = sha256(request_dir / "countries.csv")
    if concordance:
        manifest_path, bundle = concordance
        evidence = {"concordance.json": sha256(manifest_path), **bundle["files"]}
        archive = request_dir / "concordance"
        archive.mkdir()
        for name, expected in evidence.items():
            original = manifest_path if name == "concordance.json" else manifest_path.parent / name
            shutil.copy2(original, archive / name)
            if sha256(archive / name) != expected:
                raise PipelineError(f"Concordance changed during intake: {name}")
        request["concordance_evidence_sha256"] = evidence
    write_json(request_dir / "request.json", request)
    return request_id


def metadata_for_candidate(conn, incoming, recipe, series):
    target_columns = [c["name"] for c in columns(conn, "DataDict")]
    required = {"Table", "Variable", "Definition", "Units", "Source"}
    if not required.issubset(target_columns):
        raise PipelineError(f"Baseline DataDict lacks columns: {required - set(target_columns)}")
    result, changes, seen = [], [], set()
    for table, data in series.items():
        previous = [dict(r) for r in conn.execute('SELECT * FROM DataDict WHERE "Table"=?', (table,))]
        selected = [r for r in incoming if r["Table"] == table]
        if not selected:
            raise PipelineError(f"{table}: missing source metadata")
        if recipe["adapter"] == "csv_long" and len(previous) > 1:
            raise PipelineError(f"{table}: multiple baseline DataDict entries need a dedicated metadata recipe")
        for source_row in selected:
            unknown = set(source_row) - set(target_columns)
            if unknown:
                raise PipelineError(f"{table}: source DataDict columns absent from baseline: {sorted(unknown)}")
            if any(not isinstance(source_row.get(c), str) or not source_row[c].strip() for c in required):
                raise PipelineError(f"{table}: non-empty metadata required: {sorted(required)}")
            identity = (table, source_row["Variable"])
            if identity in seen:
                raise PipelineError(f"Duplicate source DataDict Table/Variable: {identity}")
            seen.add(identity)
            matches = [r for r in previous if r["Variable"] == source_row["Variable"]]
            if len(matches) > 1:
                raise PipelineError(f"Duplicate baseline DataDict Table/Variable: {identity}")
            prior = matches[0] if matches else {}
            # Check all metadata definitions for this table, including a renamed Variable.
            previous_units = {str(r["Units"]).strip() for r in previous if r.get("Units") is not None}
            if previous_units and previous_units != {source_row["Units"].strip()} and not recipe.get("allow_unit_change", False):
                raise PipelineError(f"{table}: units changed from {previous_units} to {source_row['Units']!r}; resolve conversion before consolidation")
            if previous_units and previous_units != {source_row["Units"].strip()} and recipe["merge_policy"] != "replace":
                raise PipelineError(f"{table}: blending would mix old and new units; convert to baseline units or use a reviewed full replacement")
            merged = {c: prior.get(c) for c in target_columns}
            merged.update(source_row)
            if "Last IFs Update" in target_columns:
                merged["Last IFs Update"] = now()[:10].replace("-", "/")
            if "Years" in target_columns:
                populated = [y for y in data.years if any(r.get(y) is not None for r in data.rows.values())]
                merged["Years"] = f"{populated[0]}-{populated[-1]}" if populated else ""
            for col, default in (("UsedInHistAnalog", 0), ("UsedInFunctions", 0), ("Decimal Places", 5)):
                if col in merged and merged[col] is None:
                    merged[col] = default
            for col in target_columns:
                if prior.get(col) != merged[col]:
                    changes.append({"table": table, "variable": source_row["Variable"], "field": col,
                                    "before": prior.get(col), "after": merged[col]})
            result.append(merged)
        removed_variables = {r["Variable"] for r in previous} - {r["Variable"] for r in selected}
        if removed_variables:
            raise PipelineError(f"{table}: incoming metadata would remove Variable entries: {removed_variables}")
    return result, changes, target_columns


def write_report(folder, manifest):
    lines = ["# IFs consolidation run", "", f"Status: **{manifest['status']}**", "",
             f"Run: `{manifest['id']}`", f"Request: `{manifest.get('request_id', '')}`", "",
             "Candidate outputs are local review artifacts. Source and baseline files are not modified.", ""]
    for kind in ("errors", "warnings"):
        if manifest.get(kind):
            lines += [kind.capitalize() + ":", ""] + [f"- {v}" for v in manifest[kind]] + [""]
    if manifest.get("review_id"):
        lines += [f"Pre-merge review: {manifest['review_id']}", manifest.get("review_report", "Frozen evidence: validation/ and decisions.json"), ""]
    for table, choice in manifest.get("table_decisions", {}).items():
        lines.append(f"- {table}: {choice['action']} — {choice['reason']}")
    if manifest.get("tables"):
        lines += ["| Table | Added | Revised | Removed | Backfilled |", "|---|---:|---:|---:|---:|"]
        for table, stats in manifest["tables"].items():
            lines.append(f"| {table.replace('|', '/')} | {stats['added']} | {stats['revised']} | {stats['removed']} | {stats['backfilled']} |")
        lines += ["", "See changes.csv, coverage.csv, metadata_changes.csv and manifest.json for details.", "",
                  "Validation covers the updated tables and SQLite integrity. Untouched baseline tables are preserved, not fully revalidated."]
    (folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(root, request_id, decisions_path=None):
    root = initialize(root)
    request_dir = inside(root / "inbox", request_id)
    request = read_json(request_dir / "request.json")
    run_id = new_id()
    folder = root / "runs" / run_id
    folder.mkdir()
    manifest = {"id": run_id, "request_id": request_id, "started_at": now(), "status": "running",
                "pipeline_version": __version__, "errors": [], "warnings": [], "tables": {}}
    write_json(folder / "manifest.json", manifest)
    try:
        config = read_json(root / "config.local.json")
        baseline = resource_path(root, config["baseline"])
        raw = inside(root, request["raw_path"])
        for path, expected in ((raw, request["sha256"]), (request_dir / "recipe.json", request["recipe_sha256"]),
                               (request_dir / "countries.csv", request["country_reference_sha256"])):
            if sha256(path) != expected:
                raise PipelineError(f"Archived input or recipe changed: {path}")
        for name in ("request.json", "recipe.json", "countries.csv"):
            shutil.copy2(request_dir / name, folder / name)
        if request.get("concordance_evidence_sha256"):
            archive = folder / "concordance"
            archive.mkdir()
            for name, expected in request["concordance_evidence_sha256"].items():
                evidence_path = inside(request_dir / "concordance", name)
                if sha256(evidence_path) != expected:
                    raise PipelineError(f"Archived concordance changed: {name}")
                copied = inside(archive, name)
                shutil.copy2(evidence_path, copied)
                if sha256(copied) != expected:
                    raise PipelineError(f"Archived concordance changed during copy: {name}")
        write_json(folder / "config.json", config)
        recipe = load_recipe(folder / "recipe.json")
        review = None
        choices = {}
        if recipe["adapter"] == "ifs_sqlite":
            from .review import decision_context, validate_requests
            if decisions_path is None:
                review_result = validate_requests(root, [request_id])
                manifest["review_id"] = review_result["id"]
                manifest["review_report"] = review_result["report"]
                manifest["status"] = "failed" if review_result["status"] == "blocked" else "needs_review"
                message = "Inspect the pre-merge review and record table decisions before creating a candidate."
                manifest["errors" if manifest["status"] == "failed" else "warnings"].append(message)
                manifest["finished_at"] = now()
                write_json(folder / "manifest.json", manifest)
                write_report(folder, manifest)
                return manifest
            review_folder, decisions, review = decision_context(root, request_id, decisions_path)
            choices = decisions["requests"][request_id]
            manifest["review_id"] = review["id"]
            manifest["table_decisions"] = choices
            evidence = {"review.json": decisions["review_sha256"], **review["files"]}
            archive = folder / "validation"
            archive.mkdir()
            for name, expected in evidence.items():
                copied = inside(archive, name)
                shutil.copy2(inside(review_folder, name), copied)
                if sha256(copied) != expected:
                    raise PipelineError(f"Review evidence changed during copy: {name}")
            write_json(folder / "decisions.json", decisions)
            manifest["validation_evidence_sha256"] = {
                **{"validation/" + name: expected for name, expected in evidence.items()},
                "decisions.json": sha256(folder / "decisions.json")}
        elif decisions_path is not None:
            raise PipelineError("--decisions currently applies to prepared IFs SQLite imports")
        canonical, aliases = country_reference(folder / "countries.csv")
        source, metadata = load_source(raw, recipe, canonical, aliases)
        manifest["source"] = request
        manifest["baseline"] = str(baseline)
        manifest["baseline_sha256"] = {}
        manifest["code_sha256"] = {p.name: sha256(p) for p in Path(__file__).parent.glob("*.py")}
        candidate = folder / "candidate"
        for name in DATABASES:
            check_idle_database(baseline / name)
            manifest["baseline_sha256"][name] = sha256(baseline / name)
            if review and manifest["baseline_sha256"][name] != review["baseline_sha256"][name]:
                raise PipelineError("Baseline changed since review; validate again")
            snapshot(baseline / name, candidate / name)
        for name in DATABASES:
            check_idle_database(baseline / name)
            if sha256(baseline / name) != manifest["baseline_sha256"][name]:
                raise PipelineError("Baseline changed while being copied; use a stable release folder")
        changes, coverage, outputs = [], [], {}
        with readonly(candidate / "IFsHistSeries.db") as conn:
            available = tables(conn)
            for table, recent in source.items():
                policy = choices.get(table, {}).get("action", recipe["merge_policy"])
                if policy == "keep_old":
                    continue
                prior = read_series(conn, table, recipe.get("kind", "monadic"), canonical) if table in available else None
                result = blend(prior, recent, policy)
                # Validate union keys: independently valid old/new names can still conflict.
                from .model import make_series
                make_series(table, result.keys, result.years,
                            [dict(zip(result.keys, k), **v) for k, v in result.rows.items()], canonical)
                expected = recipe.get("expected_country_count")
                if expected is not None and len(result.rows) != expected:
                    raise PipelineError(f"{table}: expected {expected} countries, found {len(result.rows)}")
                stats, delta, cov = compare(prior, result, recent, recipe.get("large_revision_fraction", 0.5))
                manifest["tables"][table] = stats
                changes.extend(delta)
                for row in cov:
                    row["incoming"] = sum(v.get(str(row["year"])) is not None for v in recent.rows.values())
                coverage.extend(cov)
                if stats["removed"] and not recipe.get("allow_coverage_loss", False):
                    raise PipelineError(f"{table}: would remove {stats['removed']} populated cells; resolve the replacement policy")
                if stats["removed"]:
                    manifest["warnings"].append(f"{table}: {stats['removed']} populated cells removed under explicit recipe policy")
                if stats["large_revisions"]:
                    manifest["warnings"].append(f"{table}: {stats['large_revisions']} large revisions; inspect changes.csv")
                if prior is None:
                    manifest["warnings"].append(f"{table}: new table; confirm definition, units and mappings")
                if recipe.get("allow_unit_change", False):
                    manifest["warnings"].append(f"{table}: recipe permits unit changes; confirm old and new values are compatible")
                outputs[table] = result
        source_metadata = metadata
        metadata, md_changes = [], []
        with readonly(candidate / "DataDict.db") as conn:
            md_columns = [c["name"] for c in columns(conn, "DataDict")]
            for table, result in outputs.items():
                policy = choices.get(table, {}).get("action", recipe["merge_policy"])
                rows, delta, _ = metadata_for_candidate(
                    conn, source_metadata, {**recipe, "merge_policy": policy}, {table: result})
                metadata.extend(rows)
                md_changes.extend(delta)
        csv_write(folder / "changes.csv", changes, ["table", "key", "year", "before", "after", "change", "large_revision"])
        csv_write(folder / "coverage.csv", coverage, ["table", "year", "before", "incoming", "after"])
        csv_write(folder / "metadata_changes.csv", md_changes, ["table", "variable", "field", "before", "after"])
        for item in md_changes:
            if item["field"] in ("Definition", "Units", "Source", "Formula", "Aggregation", "Disaggregation") and item["before"] not in (None, ""):
                manifest["warnings"].append(f"{item['table']}: metadata {item['field']} changed; inspect metadata_changes.csv")
        # The two files are private to this run. Publication is gated on both passing.
        conn = sqlite3.connect(candidate / "IFsHistSeries.db")
        try:
            with conn:
                for result in outputs.values():
                    write_series(conn, result)
        finally:
            conn.close()
        conn = sqlite3.connect(candidate / "DataDict.db")
        try:
            with conn:
                for table in outputs:
                    conn.execute('DELETE FROM DataDict WHERE "Table"=?', (table,))
                conn.executemany(f"INSERT INTO DataDict ({','.join(q(c) for c in md_columns)}) VALUES ({','.join('?' for _ in md_columns)})",
                                 [[row[c] for c in md_columns] for row in metadata])
        finally:
            conn.close()
        for name in DATABASES:
            with readonly(candidate / name) as conn:
                integrity = [r[0] for r in conn.execute("PRAGMA integrity_check")]
                if integrity != ["ok"]:
                    raise PipelineError(f"{name}: integrity_check failed: {integrity}")
        with readonly(candidate / "IFsHistSeries.db") as conn:
            for table, expected in outputs.items():
                actual = read_series(conn, table, recipe.get("kind", "monadic"), canonical)
                normalized = {k: {y: v.get(y) for y in expected.years} for k, v in expected.rows.items()}
                if actual.rows != normalized or actual.years != expected.years:
                    raise PipelineError(f"{table}: saved values differ from computed result")
                for col in columns(conn, table):
                    target_type = "VARCHAR(255)" if col["name"] in expected.keys else "DOUBLE(53)"
                    if col["type"].upper().replace(" ", "") != target_type:
                        raise PipelineError(f"{table}: incorrect output type for {col['name']}")
                for row in records(conn, table):
                    valid = [row[y] for y in expected.years if row[y] is not None]
                    if [row[m] for m in META] != ([valid[0], valid[-1]] if valid else [None, None]):
                        raise PipelineError(f"{table}: Earliest/MostRecent values are inconsistent")
        with readonly(candidate / "DataDict.db") as conn:
            for table in outputs:
                saved = [dict(r) for r in conn.execute('SELECT * FROM DataDict WHERE "Table"=?', (table,))]
                intended = [r for r in metadata if r["Table"] == table]
                if sorted(saved, key=lambda r: r["Variable"]) != sorted(intended, key=lambda r: r["Variable"]):
                    raise PipelineError(f"{table}: saved metadata differs from computed result")
        if review:
            from .diagnostics import check_sum_rules, inspect_new, rules_for
            from .review import dump_csv
            postmerge_findings, checked_series = [], {}
            with readonly(candidate / "IFsHistSeries.db") as conn:
                for table in outputs:
                    actual = read_series(conn, table, recipe.get("kind", "monadic"), canonical)
                    checked_series[table] = actual
                    inspect_new(actual, rules_for(recipe, table), postmerge_findings)
                available = tables(conn)
                for rule in recipe.get("validation", {}).get("sum_rules", []):
                    for table in [rule["total"], *rule["parts"]]:
                        if table not in checked_series and table in available:
                            checked_series[table] = read_series(conn, table, recipe.get("kind", "monadic"), canonical)
            check_sum_rules(checked_series, recipe, postmerge_findings)
            dump_csv(folder / "postmerge_issues.csv", postmerge_findings,
                     ["id", "severity", "code", "table", "key", "year", "message"])
            review_count = sum(r["severity"] == "review" for r in postmerge_findings)
            if review_count:
                manifest["warnings"].append(f"{review_count} post-merge screening findings; inspect postmerge_issues.csv")
            for name in ("postmerge_issues.csv", "changes.csv", "coverage.csv", "metadata_changes.csv"):
                manifest["validation_evidence_sha256"][name] = sha256(folder / name)
            # Bind completion to the same input, baseline, code and decisions used above.
            _, current_decisions, _ = decision_context(root, request_id, folder / "decisions.json")
            if current_decisions != decisions:
                raise PipelineError("Merge decisions changed during processing")
            for name, expected_hash in manifest["validation_evidence_sha256"].items():
                if sha256(inside(folder, name)) != expected_hash:
                    raise PipelineError(f"Run validation evidence changed: {name}")
        manifest["candidate_sha256"] = {name: sha256(candidate / name) for name in DATABASES}
        manifest["status"] = "validated_with_warnings" if manifest["warnings"] else "validated"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["errors"].append(f"{type(exc).__name__}: {exc}")
    manifest["finished_at"] = now()
    manifest["warnings"] = sorted(set(manifest["warnings"]))
    write_json(folder / "manifest.json", manifest)
    write_report(folder, manifest)
    return manifest


def promote(root, run_id, label, accept_warnings=False):
    root = initialize(root)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", label):
        raise PipelineError("Release label must be 1-80 letters/digits/dots/hyphens/underscores")
    folder = inside(root / "runs", run_id)
    manifest = read_json(folder / "manifest.json")
    if manifest["status"] not in ("validated", "validated_with_warnings"):
        raise PipelineError("Only a successfully validated run can be promoted")
    if manifest["warnings"] and not accept_warnings:
        raise PipelineError("Review report.md, then pass --accept-warnings to record acceptance")
    destination = root / "releases" / label
    if destination.exists():
        raise PipelineError(f"Release already exists: {label}")
    for name in DATABASES:
        if sha256(folder / "candidate" / name) != manifest["candidate_sha256"][name]:
            raise PipelineError(f"Candidate was modified after validation: {name}")
    concordance_hashes = manifest.get("source", {}).get("concordance_evidence_sha256", {})
    for name, expected in concordance_hashes.items():
        if sha256(inside(folder / "concordance", name)) != expected:
            raise PipelineError(f"Run concordance was modified after validation: {name}")
    validation_hashes = manifest.get("validation_evidence_sha256", {})
    for name, expected in validation_hashes.items():
        if sha256(inside(folder, name)) != expected:
            raise PipelineError(f"Run validation evidence was modified after validation: {name}")
    stage = root / "releases" / (".pending-" + new_id())
    stage.mkdir()
    for name in DATABASES:
        shutil.copy2(folder / "candidate" / name, stage / name)
        if sha256(stage / name) != manifest["candidate_sha256"][name]:
            raise PipelineError(f"Candidate changed during promotion: {name}")
    for path in folder.iterdir():
        if path.is_file():
            shutil.copy2(path, stage / path.name)
    if (folder / "concordance").is_dir():
        shutil.copytree(folder / "concordance", stage / "concordance")
        for name, expected in concordance_hashes.items():
            if sha256(inside(stage / "concordance", name)) != expected:
                raise PipelineError(f"Run concordance changed during promotion: {name}")
    if (folder / "validation").is_dir():
        shutil.copytree(folder / "validation", stage / "validation")
    for name, expected in validation_hashes.items():
        if sha256(inside(stage, name)) != expected:
            raise PipelineError(f"Run validation evidence changed during promotion: {name}")
    write_json(stage / "release.json", {"label": label, "run_id": run_id, "promoted_at": now(),
                                        "accepted_warnings": bool(accept_warnings),
                                        "sha256": manifest["candidate_sha256"]})
    stage.rename(destination)
    return destination

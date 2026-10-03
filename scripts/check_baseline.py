"""Verify candidate creation against one real baseline table without introducing updates.

Creates an isolated workspace, imports an unchanged SeriesPopulation snapshot, and
checks that no observations change and original database hashes remain unchanged.
This is a compatibility check, not validation of a new source's methodology.
"""
import argparse
import csv
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ifs_pipeline.model import read_series, write_series
from ifs_pipeline.storage import PipelineError, q, read_json, readonly, sha256, write_json
from ifs_pipeline.review import validate_requests
from ifs_pipeline.workflow import DATABASES, configure, ingest, run


def check(baseline, directory, table):
    baseline, directory = Path(baseline).resolve(), Path(directory).resolve()
    if directory.exists():
        raise PipelineError(f"Choose a new check directory: {directory}")
    before = {name: sha256(baseline / name) for name in DATABASES}
    configure(directory, baseline, table)
    with readonly(baseline / "IFsHistSeries.db") as conn:
        series = read_series(conn, table, "monadic")
    with readonly(baseline / "DataDict.db") as conn:
        metadata = [dict(r) for r in conn.execute('SELECT * FROM DataDict WHERE "Table"=?', (table,))]
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='DataDict'").fetchone()[0]
    if not metadata:
        raise PipelineError(f"No DataDict metadata for {table}")
    import_path = directory / "baseline-roundtrip.db"
    conn = sqlite3.connect(import_path)
    try:
        with conn:
            write_series(conn, series)
            conn.execute(ddl)
            cols = list(metadata[0])
            conn.executemany(f"INSERT INTO DataDict ({','.join(q(c) for c in cols)}) VALUES ({','.join('?' for _ in cols)})",
                             [[r[c] for c in cols] for r in metadata])
    finally:
        conn.close()
    recipe_path = directory / "recipe.json"
    write_json(recipe_path, {"schema_version": 1, "id": "baseline-compatibility-check", "status": "active",
                             "adapter": "ifs_sqlite", "kind": "monadic", "merge_policy": "prefer_new_non_null",
                             "expected_country_count": len(series.rows), "tables": [table]})
    request_id = ingest(directory, import_path, recipe_path, "local-baseline://compatibility-check",
                        baseline.name, "Unchanged baseline observations; schema compatibility check only")
    review = validate_requests(directory, [request_id])
    review_folder = Path(review["folder"])
    with (review_folder / "incoming_vs_old.csv").open(encoding="utf-8-sig", newline="") as handle:
        differences = list(csv.DictReader(handle))
    if review["status"] != "ready_for_decision" or differences:
        raise PipelineError(f"Unchanged-baseline fixture did not validate: {review_folder / 'report.md'}")
    # This tool constructs an unchanged baseline fixture. Acceptance applies only to
    # existing patterns in that fixture, after proving zero incoming value differences.
    # Do not reuse this acceptance code for an external source update.
    decisions = read_json(review_folder / "decisions.template.json")
    with (review_folder / "issues.csv").open(encoding="utf-8-sig", newline="") as handle:
        decisions["accepted_issue_ids"] = [r["id"] for r in csv.DictReader(handle) if r["severity"] == "review"]
    decisions["requests"][request_id][table] = {
        "action": "prefer_new_non_null",
        "reason": "Unchanged baseline compatibility fixture; incoming_vs_old contains zero differences. Existing patterns retained."}
    decisions_path = directory / "compatibility-decisions.json"
    write_json(decisions_path, decisions)
    result = run(directory, request_id, decisions_path)
    after = {name: sha256(baseline / name) for name in DATABASES}
    checks = {"original_databases_unchanged": before == after,
              "candidate_validated": result["status"].startswith("validated"),
              "no_observation_changes": all(result.get("tables", {}).get(table, {}).get(k) == 0 for k in ("added", "revised", "removed"))}
    summary = {"checks": checks, "run_id": result["id"], "status": result["status"],
               "table": table, "country_count": len(series.rows), "year_columns": len(series.years),
               "baseline_sha256": before, "errors": result["errors"],
               "report": str(directory / "runs" / result["id"] / "report.md")}
    write_json(directory / "compatibility.json", summary)
    return summary


if __name__ == "__main__":
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--table", default="SeriesPopulation")
    args = parser.parse_args()
    result = check(args.baseline, args.directory, args.table)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if all(result["checks"].values()) else 1)

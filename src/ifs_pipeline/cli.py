"""CLI for both the user and an agent working in this repository."""
from .retention import verify_archive, restore_delivery
from .janitor import clean_delivery
import argparse
import csv
import json
from pathlib import Path
import sqlite3
import sys
from .demo import create_demo
from .concordance import prepare
from .storage import PipelineError, columns, read_json, readonly, tables
from .workflow import configure, ingest, promote, run
from .review import validate_requests
from .delivery import prepare_delivery, consolidate_delivery, compare_delivery, refresh_delivery_logs
from .repair import repair_imports, verify_preparation
from .stages import STAGES, workflow_catalog, init_workflow


def inspect_file(path):
    path = Path(path).resolve()
    if path.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
        with readonly(path) as conn:
            names = sorted(tables(conn))
            result = {"path": str(path), "format": "sqlite", "table_count": len(names), "tables": names}
            if "DataDict" in names:
                result["datadict_columns"] = [c["name"] for c in columns(conn, "DataDict")]
                result["datadict_rows"] = conn.execute("SELECT COUNT(*) FROM DataDict").fetchone()[0]
            return result
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = []
            for _, row in zip(range(5), reader):
                rows.append(row)
            return {"path": str(path), "format": "csv", "columns": reader.fieldnames, "sample": rows}
    raise PipelineError("Inspect supports CSV and SQLite. Document other formats before writing a source adapter.")


def status(root):
    config_path = root / "config.local.json"
    config = read_json(config_path) if config_path.exists() else None
    requests, runs = [], []
    for path in sorted((root / "inbox").glob("*/request.json")):
        row = read_json(path)
        requests.append({k: row[k] for k in ("id", "source_release", "created_at")})
    for path in sorted((root / "runs").glob("*/manifest.json")):
        row = read_json(path)
        runs.append({k: row[k] for k in ("id", "request_id", "status")})
    completed = {r["request_id"] for r in runs if r["status"].startswith("validated")}
    return {"root": str(root), "configured": bool(config), "config": config,
            "requests": requests, "pending_requests": [r["id"] for r in requests if r["id"] not in completed],
            "runs": runs, "releases": sorted(p.parent.name for p in (root / "releases").glob("*/release.json"))}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local IFs data intake and candidate consolidation")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository/data workspace (default: current directory)")
    parser.add_argument('--paths-config', type=Path, help='Optional JSON file of path defaults')
    parser.add_argument('--delivery-root', type=Path, help='Parent for relative delivery/base names')
    parser.add_argument('--source-library', type=Path, help='Location of reusable source guides')
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser('paths', help='Show resolved path defaults and read-only resources')
    p = commands.add_parser("quality-evidence", help="Create a new read-only local statistical evidence package")
    p.add_argument("--spec", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p = commands.add_parser("quality-verify", help="Verify a frozen quality evidence package")
    p.add_argument("--evidence", required=True, type=Path)
    p = commands.add_parser("delivery-quality", help="Create supplemental quality evidence from verified native delivery scope")
    p.add_argument("--delivery", required=True)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--settings", type=Path)
    p = commands.add_parser("quality-review", help="Append a separate human or AI review record")
    p.add_argument("--evidence", required=True, type=Path)
    p.add_argument("--finding", required=True)
    p.add_argument("--reviewer", required=True)
    p.add_argument("--decision", required=True, choices=("explained", "requires_investigation", "confirmed_problem"))
    p.add_argument("--rationale", required=True)
    p.add_argument("--author-type", choices=("human", "ai"), default="human")
    commands.add_parser("status", help="Show baseline, requests, and runs")
    p = commands.add_parser('workflows', help='List the four workflows or show one runbook and implementation map')
    p.add_argument('stage', nargs='?', choices=STAGES)
    p = commands.add_parser('workflow-init', help='Create an empty workflow workspace; does not process data')
    p.add_argument('--stage', required=True, choices=STAGES)
    p.add_argument('--output', required=True, type=Path)
    p = commands.add_parser('repair-imports', help='Prepare audited formatting repairs as new staged imports')
    p.add_argument('--base', required=True, type=Path)
    p.add_argument('--input', required=True, nargs='+', type=Path, help='Formatted import files, or one IFsDataImport directory (direct files only)')
    p.add_argument('--output', required=True, type=Path, help='New preparation evidence folder outside IFsDataImport')
    p.add_argument('--country-table', default='SeriesPopulation')
    p.add_argument('--canonical-only', action='store_true', help='Use baseline identities only, without DataGator aliases')
    p.add_argument('--mapping', type=Path, help='Source-specific reviewed DataGator original_name,matched_name export')
    p.add_argument('--reviewed', action='store_true')
    p = commands.add_parser('repair-verify', help='Verify frozen preparation artifacts before importing a correction')
    p.add_argument('--preparation', required=True, type=Path)
    p = commands.add_parser("delivery-prepare", help="Copy a base pair into a new delivery folder")
    p.add_argument("--base", required=True)
    p.add_argument("--delivery", required=True)
    p.add_argument("--country-table", default="SeriesPopulation")
    p = commands.add_parser("delivery-merge", help="Merge formatted imports, log their origins and compare with the base")
    p.add_argument("--delivery", required=True)
    p.add_argument("--order", nargs="+", help="All active import filenames in first-to-last precedence order")
    p.add_argument("--validation", type=Path, help="Optional JSON diagnostic rules; no value transformations")
    p.add_argument("--normalize-base-names", nargs="+", help="Explicit table scope for same-code canonical baseline names; retry prior identity conflicts")
    p = commands.add_parser("delivery-compare", help="Reverify database origins and open the recorded comparison")
    p.add_argument("--delivery", required=True)
    p = commands.add_parser("delivery-logs", help="Reverify an existing delivery and refresh brief/detailed logs without remerging")
    p.add_argument("--delivery", required=True)
    p = commands.add_parser("delivery-clean", help="Janitor: remove verified redundant report work files")
    p.add_argument("--delivery", required=True)
    p.add_argument("--execute", action="store_true", help="Delete candidates; otherwise preview only")
    for command in ("delivery-archive-verify", "delivery-restore"):
        p = commands.add_parser(command, help="Recovery compatibility for old archives")
        p.add_argument("--delivery", required=True)
        if command == "delivery-restore":
            p.add_argument("--archive")
    p = commands.add_parser("delivery-discard", help="Exclude a batch and rebuild using remaining imports")
    p.add_argument("--delivery", required=True)
    p.add_argument("--batch", required=True, help="Registered filename or batch ID")
    p.add_argument("--reason", required=True)
    p = commands.add_parser("configure", help="Register an existing baseline and export its country/indicator references")
    p.add_argument("--baseline", required=True, type=Path)
    p.add_argument("--country-table", default="SeriesPopulation")
    p = commands.add_parser("inspect", help="Inspect a download without importing it")
    p.add_argument("input", type=Path)
    p = commands.add_parser("ingest", help="Archive a source file, recipe, country mapping and request")
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--recipe", required=True, type=Path)
    p.add_argument("--source-url", required=True)
    p.add_argument("--source-release", required=True)
    p.add_argument("--notes", default="")
    p = commands.add_parser("validate", help="Inspect prepared IFs imports before blending and prepare merge decisions")
    p.add_argument("request_ids", nargs="+")
    p = commands.add_parser("run", help="Create and validate candidate databases for one request")
    p.add_argument("request_id")
    p.add_argument("--decisions", type=Path, help="Completed decisions tied to a prepared-import validation report")
    p = commands.add_parser("promote", help="Copy a validated candidate into a named local release")
    p.add_argument("run_id")
    p.add_argument("--label", required=True)
    p.add_argument("--accept-warnings", action="store_true")
    p = commands.add_parser("concordance", help="Reuse Datagator aliases or a reviewed mapping export for an original CSV")
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--country-column", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--mapping", type=Path, help="Datagator original_name,matched_name CSV")
    p.add_argument("--reviewed", action="store_true", help="Confirm export was reviewed as identity-only mappings, not geographic transformations")
    p.add_argument("--exclude", action="append", default=[], help="Explicit source entity to exclude; repeat as needed")
    p = commands.add_parser("demo", help="Build and process an isolated synthetic example")
    p.add_argument("--directory", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        from .paths import path_settings
        from .resources import resource_root
        from .delivery_core import resolve_folder
        settings = path_settings(args.paths_config)
        delivery_root = args.delivery_root or settings.get('delivery_root') or Path.cwd()
        if hasattr(args, 'delivery'):
            args.delivery = resolve_folder(args.delivery, delivery_root)
        if args.command in ('delivery-prepare', 'repair-imports'):
            args.base = resolve_folder(args.base, delivery_root)
        if args.command == 'paths':
            result = {'delivery_root': str(Path(delivery_root).resolve()),
                      'source_library': str(args.source_library.resolve()) if args.source_library else settings.get('source_library'),
                      'resources': str(resource_root(root)), 'workspace_required': False}
        elif args.command == 'quality-evidence':
            from .quality import run_quality
            result = run_quality(args.spec, args.output)
        elif args.command == 'delivery-quality':
            from .quality import delivery_quality
            result = delivery_quality(args.delivery, args.output, args.settings)
        elif args.command == 'quality-verify':
            from .quality import verify_quality
            result = verify_quality(args.evidence)
        elif args.command == 'quality-review':
            from .quality import record_review
            result = record_review(args.evidence, args.finding, args.reviewer, args.decision, args.rationale, args.author_type)
        elif args.command == 'workflows':
            result = workflow_catalog(root, args.stage)
        elif args.command == 'workflow-init':
            result = init_workflow(root, args.stage, args.output)
        elif args.command == 'repair-imports':
            inputs = args.input
            if len(inputs) == 1 and inputs[0].is_dir():
                inputs = sorted(p for p in inputs[0].iterdir() if p.is_file() and p.name.lower().startswith('ifsdataimport') and p.suffix.lower() == '.db')
            result = repair_imports(root, args.base, inputs, args.output, args.country_table,
                                    not args.canonical_only, args.mapping, args.reviewed)
        elif args.command == 'repair-verify':
            result = verify_preparation(args.preparation)
        elif args.command == "delivery-prepare":
            result = prepare_delivery(args.base, args.delivery, args.country_table)
        elif args.command == "delivery-merge":
            result = consolidate_delivery(args.delivery, args.order, args.validation,
                                          normalize_base_names=args.normalize_base_names)
        elif args.command == "delivery-compare":
            result = compare_delivery(args.delivery)
        elif args.command == "delivery-logs":
            result = refresh_delivery_logs(args.delivery)
        elif args.command == "delivery-clean":
            result = clean_delivery(args.delivery, execute=args.execute)
        elif args.command == "delivery-archive-verify":
            result = verify_archive(args.delivery)
        elif args.command == "delivery-restore":
            result = restore_delivery(args.delivery, archive=args.archive)
        elif args.command == "delivery-discard":
            result = consolidate_delivery(args.delivery, discard=args.batch, reason=args.reason)
        elif args.command == "status":
            result = status(root)
        elif args.command == "configure":
            result = configure(root, args.baseline, args.country_table)
        elif args.command == "inspect":
            result = inspect_file(args.input)
        elif args.command == "ingest":
            result = {"request_id": ingest(root, args.input, args.recipe, args.source_url, args.source_release, args.notes)}
        elif args.command == "validate":
            result = validate_requests(root, args.request_ids)
        elif args.command == "run":
            result = run(root, args.request_id, args.decisions)
        elif args.command == "concordance":
            result = prepare(root, args.input, args.country_column, args.source, args.mapping, args.reviewed, args.exclude)
        elif args.command == "promote":
            result = {"release": str(promote(root, args.run_id, args.label, args.accept_warnings))}
        else:
            result = create_demo(args.directory or root / "examples/demo-workspace")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if isinstance(result, dict) and result.get("status") in ("failed", "blocked", "needs_processing_review", "needs_review", "completed_with_skips", "prepared_with_unresolved") else 0
    except (PipelineError, OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

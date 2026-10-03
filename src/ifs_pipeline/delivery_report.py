"""Post-consolidation reports; calculations never write into IFs databases."""
from collections import Counter
import csv
from html import escape
import json
from pathlib import Path
from .delivery_core import DATABASES, REPORT, exact_series, find_source, baseline_name_view
from .diagnostics import compare_incoming, inspect_new, rules_for, check_sum_rules
from .storage import readonly, tables, now

def csv_save(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def mediawiki_text(value):
    """Keep source labels literal without allowing table/template/link markup."""
    text = escape(" ".join(value.split()), quote=False)
    return text.translate({ord(c): f"&#{ord(c)};" for c in "|{}[]!'"})


def build_release_log(delivery, state, evidence, work, output):
    """Count distinct final changes by DataDict Source, relative to the original base."""
    def read_csv(name):
        with (evidence / name).open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    processed = read_csv("processed_tables.csv")
    applied = {r["table"] for r in processed if r["status"] == "applied"}
    changed = set()
    for name in ("changes.csv", "metadata_changes.csv"):
        with (evidence / name).open(encoding="utf-8-sig", newline="") as handle:
            changed.update(r["table"] for r in csv.DictReader(handle))
    entries = []
    with readonly(Path(state["base"]) / DATABASES[0]) as base, readonly(work / DATABASES[1]) as metadata:
        existing = set(tables(base))
        for table in sorted(applied):
            if table in existing and table not in changed:
                continue
            sources = sorted({r[0].strip() for r in metadata.execute(
                'SELECT Source FROM DataDict WHERE "Table"=?', (table,)) if isinstance(r[0], str) and r[0].strip()})
            entries.append({"source": "; ".join(sources), "table": table,
                            "action": "created" if table not in existing else "updated",
                            "batches": ", ".join(dict.fromkeys(r["batch"] for r in processed
                                                             if r["table"] == table and r["status"] == "applied"))})
    counts = Counter((r["source"], r["action"]) for r in entries)
    created = sum(r["action"] == "created" for r in entries)
    updated = len(entries) - created
    lines = [f"Change Log - {delivery.name}", f"Base case: {Path(state['base']).name}", "",
             f"{created} tables created; {updated} tables updated.", "",
             '{| class="wikitable"', "! Data source !! Tables created !! Tables updated"]
    for source in sorted({r["source"] for r in entries}, key=str.casefold):
        lines += ["|-", f"| {mediawiki_text(source)} || {counts[source, 'created']} || {counts[source, 'updated']}"]
    lines += ["|}", "", "Counts are distinct tables changed from the base case, including metadata-only updates."]
    if any(r["status"] == "skipped" for r in processed):
        lines.append("Some imports were partially applied; skipped tables are excluded from these counts.")
    lines.append("Detailed processing log and review findings: Consolidation Report.")
    (output / "release_log.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    csv_save(output / "release_tables.csv", entries, ["source", "table", "action", "batches"])

def build_report(delivery, state, run, work, batches, findings, missing, processed):
    changes, coverage, metadata_changes, summaries = [], [], [], []
    updated = sorted({r["table"] for r in processed if r["status"] == "applied"})
    with readonly(run / "provenance.db") as ledger, readonly(work / DATABASES[0]) as current, readonly(Path(state["base"]) / DATABASES[0]) as base:
        final_series = {}
        for table in updated:
            data = exact_series(current, table)
            final_series[table] = data
            old = exact_series(base, table) if table in tables(base) else None
            old = baseline_name_view(old, base, run)
            local, diff, cov = [], [], []
            rules = rules_for({"validation": state["validation"]}, table)
            inspect_new(data, rules, local)
            compare_incoming(old, data, rules, local, diff, cov)
            origins = {(r["key"], r["year"]): r["batch"] for r in ledger.execute("SELECT * FROM origins WHERE series=?", (table,))}
            labels = {" | ".join(key): key for key in data.rows}
            table_batches = [b["id"] for b in batches if table in b.get("applied_tables", [])]
            for row in local:
                key = labels.get(row["key"])
                bid = find_source(origins, key, row["year"]) if key is not None and row["year"] else ",".join(table_batches)
                findings.append({**row, "batch": bid, "phase": "final"})
            counts = Counter()
            for row in diff:
                key = labels.get(row["key"])
                bid = find_source(origins, key, row["year"]) if key else "base"
                changes.append({**row, "after": row["incoming"], "batch": bid})
                counts[row["change"]] += 1
            coverage.extend({**r, "after_non_null": r["incoming_non_null"]} for r in cov)
            summaries.append({"table": table, "added": counts["added"], "revised": counts["revised"],
                              "country_rows": len(data.rows), "countries_with_data": sum(any(v is not None for v in r.values()) for r in data.rows.values())})
        sum_tables = {n for rule in state["validation"].get("sum_rules", []) for n in [rule["total"], *rule["parts"]]}
        for table in sum_tables - set(final_series):
            if table in tables(current):
                final_series[table] = exact_series(current, table)
        sum_findings = []
        check_sum_rules(final_series, {"validation": state["validation"]}, sum_findings)
        findings.extend({**r, "batch": "", "phase": "final"} for r in sum_findings)
        with readonly(Path(state["base"]) / DATABASES[1]) as old_md, readonly(work / DATABASES[1]) as new_md:
            for table in updated:
                before = {r["Variable"]: dict(r) for r in old_md.execute('SELECT * FROM DataDict WHERE "Table"=?', (table,))}
                for row in new_md.execute('SELECT * FROM DataDict WHERE "Table"=?', (table,)):
                    for field in row.keys():
                        old_value = before.get(row["Variable"], {}).get(field)
                        if old_value != row[field]:
                            origin = ledger.execute("SELECT batch FROM metadata_events WHERE series=? AND variable=? AND field=? ORDER BY rowid DESC LIMIT 1", (table, row["Variable"], field)).fetchone()
                            metadata_changes.append({"table": table, "variable": row["Variable"], "field": field,
                                                     "before": old_value, "after": row[field], "batch": origin[0] if origin else "base"})
    csv_save(run / "changes.csv", changes, ["batch", "table", "key", "year", "old", "after", "change"])
    csv_save(run / "coverage.csv", coverage, ["table", "year", "old_non_null", "after_non_null"])
    csv_save(run / "metadata_changes.csv", metadata_changes, ["batch", "table", "variable", "field", "before", "after"])
    csv_save(run / "incoming_missing.csv", missing, ["batch", "table", "key", "year", "representation", "raw_value"])
    csv_save(run / "findings.csv", findings, ["batch", "phase", "severity", "code", "table", "key", "year", "message"])
    csv_save(run / "processed_tables.csv", processed, ["batch", "file", "table", "status", "reason", "action", "supplied_observations", "changed_observations", "added_observations", "revised_observations", "metadata_changes"])
    csv_save(run / "table_summary.csv", summaries, ["table", "added", "revised", "country_rows", "countries_with_data"])
    skipped = sum(r["status"] == "skipped" for r in processed)
    status = "completed_with_skips" if skipped else "completed"
    lines = ["# IFs delivery comparison", "", f"Status: **{status}**", f"Base case: {state['base']}",
             f"Delivery: {delivery}", "", f"{len(updated)} distinct tables processed; {skipped} table/import failures; {len(changes)} observations changed from the base case.",
             "", "## Import batches", "", "| Batch ID | File | Applied tables |", "|---|---|---:|"]
    for b in batches:
        lines.append(f"| {b['id']} | {b['file']} | {len(b.get('applied_tables', []))} |")
    lines += ["", "## Findings", "", "| Phase | Severity | Check | Count |", "|---|---|---|---:|"]
    for (phase, severity, code), count in sorted(Counter((r["phase"], r["severity"], r["code"]) for r in findings).items()):
        lines.append(f"| {phase} | {severity} | {code} | {count} |")
    lines += ["", "## First findings", ""]
    for r in [r for r in findings if r["severity"] != "info"][:40]:
        lines.append(f"- {r['phase']} / {r['batch']} / {r['table']} / {r['key']} / {r['year']}: {r['message']}")
    lines += ["", "Complete findings and batch IDs: findings.csv. Values: changes.csv. Metadata: metadata_changes.csv.",
              "incoming_missing.csv preserves SQL NULL/blank/whitespace evidence even when old values were retained.",
              "provenance.db records every supplied non-null observation (including unchanged values), its previous value and winning batch.",
              "", "Values were copied from the base or frozen formatted imports. No correction, interpolation, scaling or sentinel conversion was performed.",
              "Earliest/MostRecent copy first/last existing observations. Metadata is copied without numeric defaults or generated dates/year ranges.",
              "Flags are screening evidence, not proof of an error. Defaults: 50% jumps/revisions, annual intervals, six-value flat runs. Rules are saved in run.json.",
              "Only processed series are statistically screened. Unrelated baseline tables are preserved; SQLite integrity is checked for both output files.",
              "Discarding a batch rebuilds from the base and remaining accepted imports; no source files are deleted."]
    (run / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    logs = [f"{len(updated)} distinct tables processed in IFsHistSeries and DataDict", ""]
    for b in batches:
        rows = [r for r in processed if r["batch"] == b["id"]]
        logs.append(f"- {b['file']} [{b['id']}]: {sum(r['status']=='applied' for r in rows)} applied, {sum(r['status']=='skipped' for r in rows)} skipped")
        for row in rows:
            logs.append(f"  - {row['table']}: {row['status']}" + (f" — {row['reason']}" if row.get("reason") else f"; {row['action']}, {row['added_observations']} observations added, {row['revised_observations']} revised, {row['metadata_changes']} metadata fields changed"))
    logs += ["", "Notes:", f'- Changes made are based on "{Path(state["base"]).name}".',
             f"- Processing time: {now()}", f"- Comparison report: {REPORT}/runs/{run.name}/report.md"]
    for b in state["batches"]:
        if b["excluded"]:
            logs.append(f"- Excluded batch: {b['file']} [{b['id']}]; {b.get('exclusion_reason', '')}")
    (run / "change_log.txt").write_text("\n".join(logs) + "\n", encoding="utf-8")
    build_release_log(delivery, state, run, work, run)
    return status

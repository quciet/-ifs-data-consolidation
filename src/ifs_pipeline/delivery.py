"""Delivery preparation, cumulative consolidation and batch exclusion."""
import json
from pathlib import Path
import re
import shutil
import sqlite3
from .delivery_core import (DATABASES, REPORT, PARENT, resolve_folder, fingerprints, copy_pair,
    locked, exact_series, merge_exact, metadata_exact, write_exact, open_ledger, verify_origins,
    canonical_base_names)
from .delivery_report import build_report, build_release_log
from .delivery_packages import verify_visible, stage_packages, check_package_publication, publish_packages
from .diagnostics import compare_incoming, inspect_new, issue, rules_for
from .storage import (PipelineError, check_idle_database, inside, new_id, now, q,
    read_json, readonly, records, sha256, tables, write_json)

def prepare_delivery(base, delivery, country_table="SeriesPopulation", parent=PARENT):
    base, delivery = resolve_folder(base, parent), resolve_folder(delivery, parent)
    if base == delivery or delivery.is_relative_to(base) or base.is_relative_to(delivery):
        raise PipelineError("Base and delivery must be separate, non-nested folders")
    before = fingerprints(base)
    if delivery.exists():
        for name in (*DATABASES, REPORT):
            if (delivery / name).exists():
                raise PipelineError(f"Delivery already contains {name}; existing outputs are not overwritten")
    with readonly(base / DATABASES[0]) as conn:
        canonical = {}
        for row in records(conn, country_table):
            code, name = row.get("FIPS_CODE"), row.get("Country")
            if not isinstance(code, str) or not isinstance(name, str) or not code.strip() or not name.strip():
                raise PipelineError("Country reference needs nonempty text Country/FIPS_CODE")
            if code in canonical or name in canonical.values():
                raise PipelineError("Country reference has duplicate identities")
            canonical[code] = name
        if not canonical:
            raise PipelineError("Country reference is empty")
    delivery.mkdir(parents=True, exist_ok=True)
    (delivery / REPORT).mkdir()
    with locked(delivery):
        for name in ("IFsDataImport", "Working Files"):
            (delivery / name).mkdir(exist_ok=True)
        copy_pair(base, delivery, before)
        state = {"schema_version": 1, "base": str(base), "delivery": str(delivery),
                 "base_sha256": before, "output_sha256": before, "created_at": now(),
                 "country_table": country_table, "countries": canonical, "batches": [],
                 "status": "prepared", "last_run": None, "validation": {}}
        write_json(delivery / REPORT / "delivery.json", state)
    return {"status": "prepared", "delivery": str(delivery), "base": str(base), "country_count": len(canonical)}

def load_state(delivery):
    state = read_json(delivery / REPORT / "delivery.json")
    if state.get("schema_version") != 1 or Path(state["delivery"]).resolve() != delivery:
        raise PipelineError("Invalid delivery state or moved delivery folder")
    if fingerprints(Path(state["base"])) != state["base_sha256"]:
        raise PipelineError("Base case changed; restore it before continuing")
    return state

def assert_output(delivery, state):
    if fingerprints(delivery) != state["output_sha256"]:
        raise PipelineError("Delivery databases changed outside this workflow; refusing to overwrite or certify them")

def archive_inventory(delivery, state):
    verify_visible(delivery, state)
    batches = json.loads(json.dumps(state["batches"]))
    known = {b["file"]: b for b in batches}
    import_dir = delivery / "IFsDataImport"
    for path in sorted(import_dir.iterdir(), key=lambda p: p.name.casefold()):
        if not path.is_file() or path.suffix.lower() != ".db" or not path.name.lower().startswith("ifsdataimport"):
            continue
        if path.resolve().parent != import_dir.resolve():
            raise PipelineError("Import files must be inside IFsDataImport")
        check_idle_database(path)
        digest = sha256(path)
        if path.name in known:
            if digest != state.get("import_packages", {}).get(path.name, known[path.name]["sha256"]):
                raise PipelineError(f"Registered import changed: {path.name}. Restore it and supply corrections under a new filename.")
            continue
        if any(b["sha256"] == digest for b in batches):
            raise PipelineError(f"Duplicate batch contents: {path.name}; identical imports must not be applied twice")
        archive = delivery / REPORT / "imports" / digest / path.name
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.exists():
            shutil.copy2(path, archive)
        if sha256(archive) != digest or sha256(path) != digest:
            raise PipelineError(f"Import changed during archiving: {path.name}")
        batches.append({"id": new_id(), "file": path.name, "sha256": digest,
                        "archive": str(archive.relative_to(delivery / REPORT)), "excluded": False,
                        "registered_at": now()})
    return batches

def import_catalog(path):
    with readonly(path) as conn:
        if [r[0] for r in conn.execute("PRAGMA quick_check")] != ["ok"]:
            raise PipelineError("Import SQLite integrity check failed")
        metadata = records(conn, "DataDict")
        names = {r.get("Table") for r in metadata}
        if not names or any(not isinstance(t, str) or not t.startswith("Series") for t in names):
            raise PipelineError("DataDict must identify Series tables")
        names |= {t for t in tables(conn) if t.startswith("Series")}
        return sorted(names), metadata

def choose_order(batches, catalogs, previous, order):
    active = [b for b in batches if not b["excluded"]]
    if order is not None:
        if len(order) != len(set(order)) or set(order) != {b["file"] for b in active}:
            raise PipelineError("--order must list every active import filename exactly once, from first to last")
        by_name = {b["file"]: b for b in active}
        return [by_name[name] for name in order]
    established = {b["id"] for b in previous}
    owners, conflicts = {}, []
    for batch in active:
        for table in catalogs.get(batch["id"], ([], []))[0]:
            if table in owners and (batch["id"] not in established or owners[table]["id"] not in established):
                conflicts.append(f"{table}: {owners[table]['file']} and {batch['file']}")
            owners[table] = batch
    if conflicts:
        raise PipelineError("Overlapping tables need explicit precedence via --order: " + "; ".join(conflicts))
    return active

def recover_publish(delivery):
    journal = delivery / REPORT / "pending.json"
    if not journal.exists():
        return
    pending = read_json(journal)
    state = pending["state"]
    if fingerprints(Path(state["base"])) != state["base_sha256"]:
        raise PipelineError("Base changed during interrupted publication")
    run = inside(delivery / REPORT / "runs", state["last_run"])
    for filename, digest in state["report_sha256"].items():
        if sha256(inside(run, filename)) != digest:
            raise PipelineError("Interrupted publication evidence changed")
    for name in DATABASES:
        expected = state["output_sha256"][name]
        if sha256(run / "work" / name) != expected:
            raise PipelineError("Interrupted publication database evidence changed")
        target = delivery / name
        check_idle_database(target)
        if target.exists() and sha256(target) not in (expected, pending["previous_sha256"][name]):
            raise PipelineError("Delivery modified during interrupted publication")
    package_entries = check_package_publication(delivery, run)
    for name in DATABASES:
        temp = delivery / (name + ".delivery-tmp")
        shutil.copy2(run / "work" / name, temp)
        if sha256(temp) != state["output_sha256"][name]:
            raise PipelineError("Publication copy verification failed")
        temp.replace(delivery / name)
    publish_packages(delivery, run, package_entries)
    shutil.copy2(run / "report.md", delivery / REPORT / "report.md")
    shutil.copy2(run / "change_log.txt", delivery / REPORT / Path(state["log_file"]).name)
    # Legacy pending publications predate the brief release-log artifact.
    release_log = "release_log.txt" if "release_log.txt" in state["report_sha256"] else "change_log.txt"
    shutil.copy2(run / release_log, inside(delivery, state["log_file"]))
    write_json(delivery / REPORT / "delivery.json", state)
    journal.unlink()

def record_progress(run, outcome):
    with (run / "processing.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"time": now(), **outcome}, ensure_ascii=False) + "\n")

def consolidate_delivery(delivery, order=None, validation_path=None, discard=None, reason="", parent=PARENT,
                         normalize_base_names=None):
    delivery = resolve_folder(delivery, parent)
    from .retention import assert_mutable
    with locked(delivery):
        assert_mutable(delivery)
        recover_publish(delivery)
        state = load_state(delivery)
        assert_output(delivery, state)
        verify_visible(delivery, state)
        previous_hashes = state["output_sha256"]
        previous_batches = json.loads(json.dumps(state["batches"]))
        batches = archive_inventory(delivery, state) if discard is None else json.loads(json.dumps(state["batches"]))
        if discard is not None:
            matches = [b for b in batches if discard in (b["id"], b["file"])]
            if len(matches) != 1:
                raise PipelineError("Identify one registered batch by filename or batch ID")
            if matches[0]["excluded"]:
                raise PipelineError("Batch is already excluded")
            if not reason.strip():
                raise PipelineError("Record why the batch is being discarded")
            matches[0].update(excluded=True, exclusion_reason=reason, excluded_at=now())
        if validation_path:
            state["validation"] = read_json(validation_path)
        rules_for({"validation": state["validation"]}, "")
        catalogs, failures = {}, {}
        for batch in batches:
            archived = inside(delivery / REPORT, batch["archive"])
            if sha256(archived) != batch["sha256"]:
                raise PipelineError(f"Archived import changed: {batch['file']}")
            if batch["excluded"]:
                continue
            try:
                catalogs[batch["id"]] = import_catalog(archived)
            except (PipelineError, sqlite3.Error) as exc:
                failures[batch["id"]] = str(exc)
            for table in catalogs.get(batch["id"], ([], []))[0]:
                rules_for({"validation": state["validation"]}, table)
        active = choose_order(batches, catalogs, previous_batches, order)
        requested = set(normalize_base_names or [])
        active_tables = {t for b in active for t in catalogs.get(b["id"], ([], []))[0]}
        if requested - active_tables:
            raise PipelineError("Requested baseline-name tables are not in active imports")
        selected = set(state.get("baseline_name_tables", [])) | requested
        policy = {"country_table": state["country_table"], "tables": {}}
        with readonly(Path(state["base"]) / DATABASES[0]) as base:
            roster = exact_series(base, state["country_table"])
            canonical = {key[1]: key[0] for key in roster.rows}
            if canonical != state["countries"]:
                raise PipelineError("Canonical roster differs from pinned base")
            for table in sorted(selected):
                if table not in {t for b in active for t in catalogs.get(b["id"], ([], []))[0]}:
                    continue
                old = exact_series(base, table)
                canonical_base_names(old, canonical)
                policy["tables"][table] = [
                    {"code": code, "before": name, "after": canonical[code]}
                    for name, code in old.rows if name != canonical[code]]
        state["baseline_name_tables"] = sorted(selected)
        if not active and discard is None:
            raise PipelineError("No active IFsDataImport*.db files found")
        state["batches"] = [*active, *(b for b in batches if b["excluded"])]
        run = delivery / REPORT / "runs" / new_id()
        run.mkdir(parents=True)
        if policy["tables"]:
            write_json(run / "baseline_names.json", policy)
        work = run / "work"
        copy_pair(Path(state["base"]), work, state["base_sha256"])
        ledger = open_ledger(run / "provenance.db")
        conn = sqlite3.connect(work / DATABASES[0])
        conn.row_factory = sqlite3.Row
        conn.execute("ATTACH DATABASE ? AS dd", (str(work / DATABASES[1]),))
        findings, missing, processed = [], [], []
        try:
            for batch in active:
                prior_outcomes = batch.get("table_outcomes", {})
                batch["applied_tables"] = []
                batch["table_outcomes"] = {}
                if batch["id"] in failures:
                    message = failures[batch["id"]]
                    processed.append({"batch": batch["id"], "file": batch["file"], "table": "", "status": "skipped", "reason": message})
                    record_progress(run, processed[-1])
                    local = []
                    issue(local, "error", "invalid_import", "", message)
                    findings.extend({**r, "batch": batch["id"], "phase": "incoming"} for r in local)
                    continue
                names, metadata = catalogs[batch["id"]]
                with readonly(inside(delivery / REPORT, batch["archive"])) as source:
                    for table in names:
                        local = []
                        outcome = {"batch": batch["id"], "file": batch["file"], "table": table, "status": "skipped", "reason": ""}
                        try:
                            retry_names = (table in requested and prior_outcomes.get(table, {}).get("reason")
                                           == "Old and incoming country identities conflict")
                            if table in prior_outcomes and prior_outcomes[table]["status"] == "skipped" and not retry_names:
                                raise PipelineError(prior_outcomes[table]["reason"])
                            incoming = exact_series(source, table, state["countries"], missing, batch["id"])
                            old = exact_series(conn, table) if table in tables(conn) else None
                            if old is not None and table in policy["tables"]:
                                old = canonical_base_names(old, canonical)
                            rules = rules_for({"validation": state["validation"]}, table)
                            inspect_new(incoming, rules, local)
                            compare_incoming(old, incoming, rules, local, [], [])
                            if len(incoming.keys) == 2:
                                absent = sorted(set(state["countries"]) - {k[1] for k in incoming.rows})
                                if absent:
                                    issue(local, "review", "missing_country_rows", table, f"{len(absent)} master country rows absent: {', '.join(absent)}")
                            result = merge_exact(old, incoming)
                            md_rows, md_columns, md_changes = metadata_exact(conn, table, metadata)
                            for change in md_changes:
                                issue(local, "review", "metadata_changed", table,
                                      f"{change['variable']}/{change['field']}: {change['before']!r} -> {change['after']!r}")
                            conn.execute("SAVEPOINT imported_table")
                            try:
                                write_exact(conn, result)
                                conn.execute('DELETE FROM dd.DataDict WHERE "Table"=?', (table,))
                                conn.executemany(f"INSERT INTO dd.DataDict ({','.join(q(c) for c in md_columns)}) VALUES ({','.join('?' for _ in md_columns)})",
                                                 [[r[c] for c in md_columns] for r in md_rows])
                                saved = [dict(r) for r in conn.execute('SELECT * FROM dd.DataDict WHERE "Table"=?', (table,))]
                                if sorted(saved, key=lambda r: r["Variable"]) != sorted(md_rows, key=lambda r: r["Variable"]):
                                    raise PipelineError("Metadata storage changed copied values")
                            except Exception:
                                conn.execute("ROLLBACK TO imported_table")
                                conn.execute("RELEASE imported_table")
                                raise
                            conn.execute("RELEASE imported_table")
                        except (PipelineError, sqlite3.Error, ValueError) as exc:
                            outcome["reason"] = str(exc)
                            issue(local, "error", "table_skipped", table, str(exc))
                        else:
                            # Ledger failures abort the whole build, never leave an unrecorded applied table.
                            supplied = changed = added = revised = 0
                            for key, cells in incoming.rows.items():
                                for year, value in cells.items():
                                    if value is None:
                                        continue
                                    before = old.rows.get(key, {}).get(year) if old else None
                                    supplied += 1
                                    changed += before != value
                                    added += before is None
                                    revised += before is not None and before != value
                                    identity = json.dumps(key)
                                    ledger.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)", (batch["id"], batch["file"], table, identity, year, before, value, before != value))
                                    ledger.execute("INSERT OR REPLACE INTO origins VALUES (?,?,?,?)", (table, identity, year, batch["id"]))
                            for c in md_changes:
                                ledger.execute("INSERT INTO metadata_events VALUES (?,?,?,?,?,?)",
                                               (batch["id"], table, c["variable"], c["field"], json.dumps(c["before"]), json.dumps(c["after"])))
                            ledger.execute("INSERT OR REPLACE INTO metadata_result VALUES (?,?)", (table, json.dumps(md_rows)))
                            ledger.commit()
                            batch["applied_tables"].append(table)
                            outcome.update(status="applied", supplied_observations=supplied, changed_observations=changed,
                                           added_observations=added, revised_observations=revised, metadata_changes=len(md_changes),
                                           action="new_table" if old is None else "values_updated" if changed else "metadata_only" if md_changes else "unchanged")
                        findings.extend({**r, "batch": batch["id"], "phase": "incoming"} for r in local)
                        processed.append(outcome)
                        batch["table_outcomes"][table] = {"status": outcome["status"], "reason": outcome["reason"]}
                        record_progress(run, outcome)
            conn.close()
            conn = None
            ledger.close()
            ledger = None
            verify_origins(work, Path(state["base"]), run, active, delivery)
            status = build_report(delivery, state, run, work, active, findings, missing, processed)
            if fingerprints(Path(state["base"])) != state["base_sha256"]:
                raise PipelineError("Base changed during consolidation")
            for b in active:
                if sha256(inside(delivery / REPORT, b["archive"])) != b["sha256"]:
                    raise PipelineError("Archived input changed during consolidation")
                original = delivery / "IFsDataImport" / b["file"]
                if original.exists() and sha256(original) != state.get("import_packages", {}).get(b["file"], b["sha256"]):
                    raise PipelineError("Original input changed during consolidation")
            assert_output(delivery, {**state, "output_sha256": previous_hashes})
            state.update(status=status, last_run=run.name, output_sha256=fingerprints(work), completed_at=now())
            if "log_file" not in state:
                date_match = re.search(r"(\d{8})$", delivery.name)
                state["log_file"] = "Change Log " + (date_match[1] if date_match else now()[:10].replace("-", "")) + ".txt"
                if (delivery / state["log_file"]).exists():
                    raise PipelineError("An existing change log would be overwritten; preserve/rename it before first consolidation")
            verify_visible(delivery, state)
            stage_packages(delivery, state, run)
            write_json(run / "run.json", state)
            state["report_sha256"] = {p.name: sha256(p) for p in run.iterdir() if p.is_file()}
            write_json(delivery / REPORT / "pending.json", {"state": state, "previous_sha256": previous_hashes})
            recover_publish(delivery)
            return {"status": status, "delivery": str(delivery), "report": str(delivery / REPORT / "report.md"),
                    "run": run.name, "batches": [{"id": b["id"], "file": b["file"], "excluded": b["excluded"]} for b in state["batches"]]}
        except Exception as exc:
            write_json(run / "failure.json", {"status": "failed", "error": str(exc), "time": now()})
            raise
        finally:
            if conn is not None:
                conn.close()
            if ledger is not None:
                ledger.close()

def verify_recorded_delivery(delivery):
    """Caller holds the delivery lock; verifies without changing run evidence."""
    recover_publish(delivery)
    state = load_state(delivery)
    assert_output(delivery, state)
    if not state["last_run"]:
        raise PipelineError("No consolidation has run yet")
    run = inside(delivery / REPORT / "runs", state["last_run"])
    for name, digest in state["report_sha256"].items():
        if sha256(inside(run, name)) != digest:
            raise PipelineError(f"Processing evidence changed: {name}")
    for b in state["batches"]:
        if sha256(inside(delivery / REPORT, b["archive"])) != b["sha256"]:
            raise PipelineError(f"Archived import changed: {b['file']}")
    verify_visible(delivery, state)
    check_package_publication(delivery, run)
    active = [b for b in state["batches"] if not b["excluded"]]
    verify_origins(delivery, Path(state["base"]), run, active, delivery)
    return state, run


def compare_delivery(delivery, parent=PARENT):
    delivery = resolve_folder(delivery, parent)
    from .retention import retained_access
    with locked(delivery), retained_access(delivery):
        state, run = verify_recorded_delivery(delivery)
        shutil.copy2(run / "report.md", delivery / REPORT / "report.md")
        return {"status": state["status"], "verified": True, "report": str(delivery / REPORT / "report.md")}


def refresh_delivery_logs(delivery, parent=PARENT):
    """Refresh the two user-facing logs without remerging or rewriting native manifests."""
    delivery = resolve_folder(delivery, parent)
    from .retention import retained_access
    with locked(delivery), retained_access(delivery):
        state, run = verify_recorded_delivery(delivery)
        bundle = delivery / REPORT / "logs" / new_id()
        bundle.mkdir(parents=True)
        release = inside(delivery, state["log_file"])
        detailed = delivery / REPORT / Path(state["log_file"]).name
        for path, name in ((release, "previous_release_log.txt"), (detailed, "previous_detailed_log.txt")):
            if path.exists():
                shutil.copy2(path, bundle / name)
        shutil.copy2(run / "change_log.txt", bundle / "change_log.txt")
        build_release_log(delivery, state, run, delivery, bundle)
        assert_output(delivery, state)
        write_json(bundle / "manifest.json", {
            "created_at": now(), "status": state["status"], "verified": True,
            "run": str(run), "delivery_state_sha256": sha256(delivery / REPORT / "delivery.json"),
            "output_sha256": state["output_sha256"], "source_evidence_sha256": state["report_sha256"],
            "release_log": str(release), "detailed_log": str(detailed),
            "log_code_sha256": {name: sha256(Path(__file__).with_name(name))
                                for name in ("delivery.py", "delivery_report.py")},
            "artifacts_sha256": {p.name: sha256(p) for p in bundle.iterdir() if p.is_file()},
        })
        for source, target in ((bundle / "change_log.txt", detailed), (bundle / "release_log.txt", release)):
            temp = target.with_name(target.name + ".logs-tmp")
            shutil.copy2(source, temp)
            temp.replace(target)
        return {"status": state["status"], "verified": True, "release_log": str(release),
                "detailed_log": str(detailed), "evidence": str(bundle)}


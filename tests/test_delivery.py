"""Synthetic delivery tests. No real release directories are accessed."""
import csv
import io
import json
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ifs_pipeline.cli import main
from ifs_pipeline.demo import create_demo
from ifs_pipeline.delivery import prepare_delivery, consolidate_delivery, compare_delivery, refresh_delivery_logs
from ifs_pipeline.delivery_core import DATABASES, REPORT, exact_series, fingerprints
from ifs_pipeline.model import KEYS, make_series, write_series
from ifs_pipeline.storage import PipelineError, read_json, readonly, sha256, write_json

TABLE = "SeriesDemoPopulation"

def csv_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        create_demo(self.root / "demo")
        self.base = self.root / "demo/baseline"
        self.before = fingerprints(self.base)
        self.delivery = self.root / "IFsHistSeries 8.68 20260915"
        prepare_delivery(self.base, self.delivery, TABLE)
        self.sources = {}

    def tearDown(self):
        self.assertEqual(self.before, fingerprints(self.base))
        for path, digest in self.sources.items():
            if path.exists() and sha256(path) == digest:
                continue
            moved = self.delivery / "Working Files" / path.name
            if not path.exists() and moved.exists():
                self.assertEqual(sha256(moved), digest)
                continue
            state = read_json(self.delivery / REPORT / "delivery.json")
            batch = next(b for b in state["batches"] if b["file"] == path.name)
            self.assertEqual(batch["sha256"], digest)
            self.assertEqual(sha256(self.delivery / REPORT / batch["archive"]), digest)

    def source(self, name="IFsDataImport_A.db", a=115, b=None, table=TABLE, units="persons", year="2023"):
        path = self.delivery / "IFsDataImport" / name
        shutil.copy2(self.base / "DataDict.db", path)
        conn = sqlite3.connect(path)
        try:
            with conn:
                conn.execute('UPDATE DataDict SET "Table"=?, Variable=?, Units=?', (table, table.removeprefix("Series"), units))
                data = make_series(table, KEYS["monadic"], [year], [
                    {"Country": "Example A", "FIPS_CODE": "AAA", year: a},
                    {"Country": "Example B", "FIPS_CODE": "BBB", year: b}])
                write_series(conn, data)
        finally:
            conn.close()
        self.sources[path] = sha256(path)
        return path

    def edit_source(self, path, query, args=()):
        conn = sqlite3.connect(path)
        with conn:
            conn.execute(query, args)
        conn.close()
        self.sources[path] = sha256(path)

    def output(self, table=TABLE):
        with readonly(self.delivery / DATABASES[0]) as conn:
            return exact_series(conn, table)

    def run_folder(self, result):
        return self.delivery / REPORT / "runs" / result["run"]

    def test_prepare_copies_only_pair_preserves_existing_work(self):
        self.assertEqual(fingerprints(self.delivery), self.before)
        self.assertEqual(list((self.delivery / "IFsDataImport").iterdir()), [])
        self.assertTrue((self.delivery / "Working Files").is_dir())
        other = self.root / "other"
        (other / "Working Files").mkdir(parents=True)
        (other / "Working Files/note.txt").write_text("keep")
        prepare_delivery(self.base, other, TABLE)
        self.assertEqual((other / "Working Files/note.txt").read_text(), "keep")

    def test_existing_or_nested_destination_is_rejected(self):
        for target in (self.delivery, self.base, self.base / "nested", self.base.parent):
            with self.subTest(target=target), self.assertRaises(PipelineError):
                prepare_delivery(self.base, target, TABLE)

    def test_copies_values_preserves_history_and_records_provenance(self):
        self.source(a=115, b=0)
        result = consolidate_delivery(self.delivery)
        self.assertEqual(result["status"], "completed", result)
        actual = self.output()
        self.assertEqual(actual.rows[("Example A", "AAA")], {"2022": 100, "2023": 115})
        self.assertEqual(actual.rows[("Example B", "BBB")], {"2022": 200, "2023": 0})
        run = self.run_folder(result)
        with readonly(run / "provenance.db") as ledger:
            rows = list(ledger.execute("SELECT * FROM events ORDER BY key"))
            self.assertEqual([r["imported_value"] for r in rows], [115, 0])
            self.assertEqual([r["before_value"] for r in rows], [110, 220])
        delta = csv_rows(run / "changes.csv")
        self.assertEqual(len(delta), 2)
        self.assertTrue(all(r["batch"] == result["batches"][0]["id"] for r in delta))
        self.assertTrue(compare_delivery(self.delivery)["verified"])
        self.assertTrue((self.delivery / "Change Log 20260915.txt").is_file())

    def test_blank_and_whitespace_evidence_survive_backfill(self):
        source = self.source()
        self.edit_source(source, f'UPDATE {TABLE} SET "2023"=CASE Country WHEN ? THEN ? ELSE ? END', ("Example A", "", " \t "))
        result = consolidate_delivery(self.delivery)
        self.assertEqual(self.output().rows[("Example A", "AAA")]["2023"], 110)
        missing = csv_rows(self.run_folder(result) / "incoming_missing.csv")
        self.assertEqual({r["representation"] for r in missing}, {"empty_string", "whitespace"})
        findings = csv_rows(self.run_folder(result) / "findings.csv")
        self.assertIn("lost_observation", {r["code"] for r in findings})

    def test_metadata_values_are_copied_without_defaults_or_generated_dates(self):
        source = self.source()
        self.edit_source(source, 'UPDATE DataDict SET [Decimal Places]=NULL, UsedInFunctions=NULL, [Last IFs Update]=?, Years=?', ("2000/01/01", "source supplied"))
        result = consolidate_delivery(self.delivery)
        self.assertEqual(result["status"], "completed")
        with readonly(self.delivery / DATABASES[1]) as conn:
            row = dict(conn.execute("SELECT * FROM DataDict").fetchone())
        self.assertIsNone(row["Decimal Places"])
        self.assertIsNone(row["UsedInFunctions"])
        self.assertEqual(row["Last IFs Update"], "2000/01/01")
        self.assertEqual(row["Years"], "source supplied")

    def test_nonoverlapping_batches_merge_cumulatively(self):
        self.source()
        self.source("IFsDataImport_B.db", a=7, b=8, table="SeriesNew")
        result = consolidate_delivery(self.delivery)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.output().rows[("Example A", "AAA")]["2023"], 115)
        self.assertEqual(self.output("SeriesNew").rows[("Example A", "AAA")]["2023"], 7)
        with readonly(self.delivery / DATABASES[0]) as conn:
            self.assertEqual(conn.execute("SELECT note FROM Unrelated").fetchone()[0], "Preserve this table")

    def test_overlapping_batches_need_explicit_order_then_keep_it(self):
        a = self.source(a=115, b=230)
        b = self.source("IFsDataImport_B.db", a=120, b=None)
        with self.assertRaisesRegex(PipelineError, "precedence"):
            consolidate_delivery(self.delivery)
        self.assertEqual(fingerprints(self.delivery), self.before)
        result = consolidate_delivery(self.delivery, order=[a.name, b.name])
        self.assertEqual(self.output().rows[("Example A", "AAA")]["2023"], 120)
        self.assertEqual(self.output().rows[("Example B", "BBB")]["2023"], 230)
        repeated = consolidate_delivery(self.delivery)
        self.assertEqual([r["id"] for r in result["batches"]], [r["id"] for r in repeated["batches"]])
        with readonly(self.run_folder(repeated) / "provenance.db") as ledger:
            self.assertEqual(ledger.execute("SELECT COUNT(*) FROM events").fetchone()[0], 3)

    def test_discard_last_overlapping_batch_restores_previous_import(self):
        a = self.source(a=115)
        b = self.source("IFsDataImport_B.db", a=120)
        consolidate_delivery(self.delivery, order=[a.name, b.name])
        result = consolidate_delivery(self.delivery, discard=b.name, reason="Wrong source release")
        self.assertEqual(self.output().rows[("Example A", "AAA")]["2023"], 115)
        self.assertTrue(next(r for r in result["batches"] if r["file"] == b.name)["excluded"])
        self.assertFalse(b.exists())
        self.assertTrue(compare_delivery(self.delivery)["verified"])

    def test_discard_earlier_batch_preserves_later_and_removes_new_table(self):
        a = self.source(a=115, b=230)
        b = self.source("IFsDataImport_B.db", a=120)
        consolidate_delivery(self.delivery, order=[a.name, b.name])
        consolidate_delivery(self.delivery, discard=a.name, reason="Bad batch")
        self.assertEqual(self.output().rows[("Example A", "AAA")]["2023"], 120)
        self.assertEqual(self.output().rows[("Example B", "BBB")]["2023"], 220)
        c = self.source("IFsDataImport_C.db", table="SeriesNew", a=8)
        consolidate_delivery(self.delivery)
        consolidate_delivery(self.delivery, discard=c.name, reason="Withdraw addition")
        with readonly(self.delivery / DATABASES[0]) as conn:
            self.assertFalse(conn.execute("SELECT name FROM sqlite_master WHERE name='SeriesNew'").fetchone())
        with readonly(self.delivery / DATABASES[1]) as conn:
            self.assertFalse(conn.execute("SELECT * FROM DataDict WHERE [Table]='SeriesNew'").fetchone())

    def test_discard_only_batch_returns_base_values_and_metadata(self):
        source = self.source()
        consolidate_delivery(self.delivery)
        result = consolidate_delivery(self.delivery, discard=source.name, reason="Discard fixture")
        self.assertEqual(fingerprints(self.delivery), self.before)
        self.assertTrue(compare_delivery(self.delivery)["verified"])
        self.assertIn("Excluded batch", (self.delivery / REPORT / "Change Log 20260915.txt").read_text(encoding="utf-8"))
        self.assertIn("0 tables created; 0 tables updated.", (self.delivery / "Change Log 20260915.txt").read_text(encoding="utf-8"))

    def test_two_logs_group_final_changes_by_metadata_source(self):
        a = self.source()
        b = self.source("IFsDataImport_B.db", table="SeriesNew")
        for path in (a, b):
            self.edit_source(path, "UPDATE DataDict SET Source='Shared source'")
        self.source("IFsDataImport_Skipped.db", table="SeriesBroken", units="")
        result = consolidate_delivery(self.delivery)
        run = self.run_folder(result)
        brief = (self.delivery / "Change Log 20260915.txt").read_text(encoding="utf-8")
        detail = self.delivery / REPORT / "Change Log 20260915.txt"
        self.assertIn("1 tables created; 1 tables updated.", brief)
        self.assertIn('{| class="wikitable"\n! Data source !! Tables created !! Tables updated\n|-\n| Shared source || 1 || 1\n|}', brief)
        self.assertNotIn("--- | ---:", brief)
        self.assertNotIn("```", brief)
        self.assertIn("partially applied", brief)
        self.assertNotIn("SeriesBroken", brief)
        self.assertNotIn(result["batches"][0]["id"], brief)
        self.assertEqual(detail.read_bytes(), (run / "change_log.txt").read_bytes())
        self.assertEqual({r["action"] for r in csv_rows(run / "release_tables.csv")}, {"created", "updated"})

    def test_release_counts_metadata_only_but_excludes_unchanged_and_reverted_updates(self):
        a = self.source(a=110, b=220)
        first = consolidate_delivery(self.delivery)
        self.assertEqual(csv_rows(self.run_folder(first) / "release_tables.csv"), [])
        b = self.source("IFsDataImport_B.db", a=110, b=220)
        self.edit_source(b, "UPDATE DataDict SET Definition='Reviewed definition'")
        result = consolidate_delivery(self.delivery, order=[a.name, b.name])
        self.assertEqual(csv_rows(self.run_folder(result) / "release_tables.csv")[0]["action"], "updated")
        c = self.source("IFsDataImport_C.db", a=115, b=220)
        d = self.source("IFsDataImport_D.db", a=110, b=220)
        self.edit_source(d, "CREATE TABLE FixtureMarker (note TEXT)")
        result = consolidate_delivery(self.delivery, order=[a.name, b.name, c.name, d.name])
        self.assertEqual(csv_rows(self.run_folder(result) / "release_tables.csv"), [])

    def test_mediawiki_source_labels_do_not_break_table_or_expand_markup(self):
        source = self.source()
        label = "A | B & <tag>\n{{template}} [[link]] ''quoted'' !"
        self.edit_source(source, "UPDATE DataDict SET Source=?", (label,))
        result = consolidate_delivery(self.delivery)
        brief = (self.delivery / "Change Log 20260915.txt").read_text(encoding="utf-8")
        self.assertIn("| A &#124; B &amp; &lt;tag&gt; &#123;&#123;template&#125;&#125; &#91;&#91;link&#93;&#93; &#39;&#39;quoted&#39;&#39; &#33; || 0 || 1", brief)
        self.assertEqual(brief.count("\n|-\n"), 1)
        self.assertEqual(brief.count("\n|}\n"), 1)
        self.assertEqual(csv_rows(self.run_folder(result) / "release_tables.csv")[0]["source"], label)

    def test_refresh_logs_preserves_pair_manifests_and_historical_evidence(self):
        self.source()
        result = consolidate_delivery(self.delivery)
        run = self.run_folder(result)
        paths = [*run.iterdir(), self.delivery / REPORT / "delivery.json"]
        hashes = {p: sha256(p) for p in paths if p.is_file()}
        pair = fingerprints(self.delivery)
        public = self.delivery / "Change Log 20260915.txt"
        public.write_text("Prior release text", encoding="utf-8")
        refreshed = refresh_delivery_logs(self.delivery)
        self.assertTrue(refreshed["verified"])
        self.assertEqual(fingerprints(self.delivery), pair)
        self.assertEqual(hashes, {p: sha256(p) for p in hashes})
        self.assertEqual((Path(refreshed["evidence"]) / "previous_release_log.txt").read_text(), "Prior release text")
        self.assertIn("1 tables updated", public.read_text())
        self.assertTrue(compare_delivery(self.delivery)["verified"])

    def test_log_refresh_rejects_tampered_evidence_before_replacing_logs(self):
        self.source()
        result = consolidate_delivery(self.delivery)
        public = self.delivery / "Change Log 20260915.txt"
        digest = sha256(public)
        (self.run_folder(result) / "metadata_changes.csv").write_text("tampered")
        with self.assertRaisesRegex(PipelineError, "evidence changed"):
            refresh_delivery_logs(self.delivery)
        self.assertEqual(sha256(public), digest)

    def test_unit_mismatch_skips_table_and_other_batch_continues(self):
        self.source(units="thousands")
        self.source("IFsDataImport_B.db", table="SeriesNew")
        result = consolidate_delivery(self.delivery)
        self.assertEqual(result["status"], "completed_with_skips")
        self.assertEqual(self.output().rows[("Example A", "AAA")]["2023"], 110)
        self.assertEqual(self.output("SeriesNew").rows[("Example A", "AAA")]["2023"], 115)
        rows = csv_rows(self.run_folder(result) / "processed_tables.csv")
        self.assertIn("Units changed", next(r["reason"] for r in rows if r["table"] == TABLE))
        with readonly(self.delivery / DATABASES[1]) as conn:
            self.assertEqual(conn.execute("SELECT Units FROM DataDict WHERE [Table]=?", (TABLE,)).fetchone()[0], "persons")

    def test_invalid_values_and_country_keys_skip_without_partial_writes(self):
        source = self.source()
        for query in (f'UPDATE {TABLE} SET "2023"=\'NA\'', f"UPDATE {TABLE} SET Country='Wrong'"):
            with self.subTest(query=query):
                self.edit_source(source, query)
                # New delivery per revision, because registered batch files are immutable.
                other = self.root / ("test-" + str(len(list(self.root.iterdir()))))
                prepare_delivery(self.base, other, TABLE)
                shutil.copy2(source, other / "IFsDataImport" / source.name)
                result = consolidate_delivery(other)
                self.assertEqual(result["status"], "completed_with_skips")
                self.assertEqual(fingerprints(other), self.before)

    def test_missing_country_is_reported_and_baseline_row_retained(self):
        source = self.source()
        self.edit_source(source, f"DELETE FROM {TABLE} WHERE Country='Example B'")
        result = consolidate_delivery(self.delivery)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.output().rows), 2)
        flags = csv_rows(self.run_folder(result) / "findings.csv")
        self.assertIn("missing_country_rows", {r["code"] for r in flags})

    def test_anomalies_do_not_require_premerge_approval(self):
        self.source(a=99999)
        result = consolidate_delivery(self.delivery)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.output().rows[("Example A", "AAA")]["2023"], 99999)
        flags = csv_rows(self.run_folder(result) / "findings.csv")
        self.assertTrue(any(r["phase"] == "final" and r["code"] == "large_revision" and r["batch"] == result["batches"][0]["id"] for r in flags))

    def test_working_files_and_unrelated_db_are_not_imports(self):
        source = self.source()
        shutil.move(str(source), str(self.delivery / "Working Files" / source.name))
        with self.assertRaisesRegex(PipelineError, "No active"):
            consolidate_delivery(self.delivery)
        (self.delivery / "IFsDataImport/notes.db").write_text("not an import")
        with self.assertRaisesRegex(PipelineError, "No active"):
            consolidate_delivery(self.delivery)

    def test_identical_duplicate_files_are_not_applied_twice(self):
        source = self.source()
        shutil.copy2(source, source.with_name("IFsDataImport_duplicate.db"))
        with self.assertRaisesRegex(PipelineError, "Duplicate batch contents"):
            consolidate_delivery(self.delivery)

    def test_registered_source_tampering_blocks_rebuild(self):
        source = self.source()
        consolidate_delivery(self.delivery)
        before = fingerprints(self.delivery)
        self.edit_source(source, f'UPDATE {TABLE} SET "2023"=123')
        with self.assertRaisesRegex(PipelineError, "import package changed"):
            consolidate_delivery(self.delivery)
        self.assertEqual(before, fingerprints(self.delivery))

    def test_external_output_edit_is_not_certified_or_overwritten(self):
        self.source()
        consolidate_delivery(self.delivery)
        conn = sqlite3.connect(self.delivery / DATABASES[0])
        with conn:
            conn.execute(f'UPDATE {TABLE} SET "2023"=987')
        conn.close()
        for operation in (compare_delivery, consolidate_delivery):
            with self.assertRaisesRegex(PipelineError, "outside this workflow"):
                operation(self.delivery)

    def test_archived_evidence_tampering_blocks_compare(self):
        self.source()
        result = consolidate_delivery(self.delivery)
        with (self.run_folder(result) / "changes.csv").open("a") as handle:
            handle.write("tamper")
        with self.assertRaisesRegex(PipelineError, "evidence changed"):
            compare_delivery(self.delivery)

    def test_large_integer_is_rejected_instead_of_rounded(self):
        source = self.source()
        self.edit_source(source, f"DROP TABLE {TABLE}")
        self.edit_source(source, f'CREATE TABLE {TABLE} (Country TEXT, FIPS_CODE TEXT, "2023")')
        self.edit_source(source, f'INSERT INTO {TABLE} VALUES (?,?,?)', ("Example A", "AAA", 9007199254740993))
        result = consolidate_delivery(self.delivery)
        self.assertEqual(result["status"], "completed_with_skips")
        self.assertEqual(fingerprints(self.delivery), self.before)
        self.assertIn("exactly", csv_rows(self.run_folder(result) / "processed_tables.csv")[0]["reason"])

    def test_independent_origin_verification_rejects_invented_number(self):
        self.source()
        from ifs_pipeline.delivery_core import write_exact
        def corrupt(conn, data):
            write_exact(conn, data)
            conn.execute(f'UPDATE "{data.name}" SET "2023"=987 WHERE Country=\'Example A\'')
        with patch("ifs_pipeline.delivery.write_exact", side_effect=corrupt):
            with self.assertRaisesRegex(PipelineError, "Untraceable"):
                consolidate_delivery(self.delivery)
        self.assertEqual(fingerprints(self.delivery), self.before)

    def test_interrupted_pair_publication_recovers(self):
        self.source()
        original_replace = Path.replace
        def interrupt(path, target):
            if path.name == "DataDict.db.delivery-tmp":
                raise OSError("synthetic interruption")
            return original_replace(path, target)
        with patch.object(Path, "replace", interrupt):
            with self.assertRaisesRegex(OSError, "interruption"):
                consolidate_delivery(self.delivery)
        self.assertTrue((self.delivery / REPORT / "pending.json").exists())
        self.assertTrue(compare_delivery(self.delivery)["verified"])
        self.assertFalse((self.delivery / REPORT / "pending.json").exists())
        self.assertEqual(self.output().rows[("Example A", "AAA")]["2023"], 115)

    def test_unchanged_incoming_value_still_has_batch_provenance(self):
        self.source(a=110, b=220)
        result = consolidate_delivery(self.delivery)
        with readonly(self.run_folder(result) / "provenance.db") as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM events WHERE changed=0").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM origins").fetchone()[0], 2)
        self.assertEqual(csv_rows(self.run_folder(result) / "changes.csv"), [])

    def test_independent_metadata_verification_rejects_generated_default(self):
        self.source()
        from ifs_pipeline.delivery_core import metadata_exact
        def invented(conn, table, incoming):
            rows, cols, changes = metadata_exact(conn, table, incoming)
            rows[0]["Decimal Places"] = 99
            return rows, cols, changes
        with patch("ifs_pipeline.delivery.metadata_exact", side_effect=invented):
            with self.assertRaisesRegex(PipelineError, "baseline/import sources"):
                consolidate_delivery(self.delivery)
        self.assertEqual(fingerprints(self.delivery), self.before)

    def test_discard_does_not_activate_previously_skipped_table(self):
        a = self.source(table="SeriesNew", units="persons")
        b = self.source("IFsDataImport_B.db", table="SeriesNew", units="thousands")
        first = consolidate_delivery(self.delivery, order=[a.name, b.name])
        self.assertEqual(first["status"], "completed_with_skips")
        result = consolidate_delivery(self.delivery, discard=a.name, reason="Withdraw")
        self.assertEqual(result["status"], "completed_with_skips")
        with readonly(self.delivery / DATABASES[0]) as conn:
            self.assertFalse(conn.execute("SELECT name FROM sqlite_master WHERE name='SeriesNew'").fetchone())

    def test_dyadic_values_use_pair_identity(self):
        source = self.source(table="SeriesTrade")
        self.edit_source(source, "DROP TABLE SeriesTrade")
        conn = sqlite3.connect(source)
        with conn:
            write_series(conn, make_series("SeriesTrade", KEYS["dyadic"], ["2024"], [
                {"Actor": "Example A", "Actor_FIPS": "AAA", "Partner": "Example B", "Partner_FIPS": "BBB", "2024": 7},
                {"Actor": "Example B", "Actor_FIPS": "BBB", "Partner": "Example A", "Partner_FIPS": "AAA", "2024": 9}]))
        conn.close()
        self.sources[source] = sha256(source)
        result = consolidate_delivery(self.delivery)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.output("SeriesTrade").rows[("Example A", "AAA", "Example B", "BBB")]["2024"], 7)
        self.assertTrue(compare_delivery(self.delivery)["verified"])

    def test_progress_is_written_as_tables_complete(self):
        self.source()
        result = consolidate_delivery(self.delivery)
        events = [json.loads(line) for line in (self.run_folder(result) / "processing.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[0]["table"], TABLE)
        self.assertEqual(events[0]["status"], "applied")

    def test_cli_merge_compare_and_discard(self):
        source = self.source()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["delivery-merge", "--delivery", str(self.delivery)]), 0)
            self.assertEqual(main(["delivery-compare", "--delivery", str(self.delivery)]), 0)
            self.assertEqual(main(["delivery-discard", "--delivery", str(self.delivery), "--batch", source.name, "--reason", "fixture"]), 0)
        self.assertEqual(fingerprints(self.delivery), self.before)

if __name__ == "__main__":
    unittest.main()

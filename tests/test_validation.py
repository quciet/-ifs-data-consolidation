"""Prepared-import validation must expose source defects before any backfill."""
import csv
import io
from contextlib import redirect_stdout
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ifs_pipeline.adapters import load_recipe
from ifs_pipeline.cli import main
from ifs_pipeline.demo import create_demo
from ifs_pipeline.diagnostics import check_sum_rules, compare_incoming, inspect_new, rules_for
from ifs_pipeline.model import KEYS, make_series, read_series, write_series
from ifs_pipeline.review import decision_context, validate_requests
from ifs_pipeline.storage import PipelineError, q, read_json, readonly, sha256, write_json
from ifs_pipeline.workflow import ingest, promote, run

TABLE = "SeriesDemoPopulation"


def csv_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def synthetic_decisions(root, report, actions=None):
    """Only for controlled test fixtures: explicit acceptance never used on real imports."""
    folder = Path(report["folder"])
    decisions = read_json(folder / "decisions.template.json")
    decisions["accepted_issue_ids"] = [r["id"] for r in csv_rows(folder / "issues.csv") if r["severity"] == "review"]
    for request, tables in decisions["requests"].items():
        for table in tables:
            tables[table] = {"action": (actions or {}).get(table, "prefer_new_non_null"),
                             "reason": "Synthetic fixture: known gaps and changes are intentional for this test."}
    path = root / ("decisions-" + report["id"] + ".json")
    write_json(path, decisions)
    return path


class PreparedValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspace"
        create_demo(self.root)
        self.before = {p.name: sha256(p) for p in (self.root / "baseline").glob("*.db")}
        self.source = self.root / "prepared.db"
        shutil.copy2(self.root / "baseline/DataDict.db", self.source)
        with readonly(self.root / "baseline/IFsHistSeries.db") as conn:
            series = read_series(conn, TABLE, "monadic")
        conn = sqlite3.connect(self.source)
        with conn:
            write_series(conn, series)
        conn.close()
        self.recipe = {"schema_version": 1, "id": "prepared-test", "status": "active", "adapter": "ifs_sqlite",
                       "kind": "monadic", "merge_policy": "prefer_new_non_null", "expected_country_count": 2}

    def tearDown(self):
        self.assertEqual(self.before, {p.name: sha256(p) for p in (self.root / "baseline").glob("*.db")})

    def sql(self, query, params=()):
        conn = sqlite3.connect(self.source)
        with conn:
            conn.execute(query, params)
        conn.close()

    def submit(self):
        write_json(self.root / "prepared-recipe.json", self.recipe)
        return ingest(self.root, self.source, self.root / "prepared-recipe.json", "synthetic://validation", "fixture")

    def review(self):
        request = self.submit()
        source_hash = sha256(self.source)
        result = validate_requests(self.root, [request])
        self.assertEqual(source_hash, sha256(self.source))
        return request, result

    def codes(self, report):
        return {r["code"] for r in csv_rows(Path(report["folder"]) / "issues.csv")}

    def test_run_without_decisions_stops_before_candidate(self):
        request = self.submit()
        result = run(self.root, request)
        self.assertEqual(result["status"], "needs_review", result)
        self.assertFalse((self.root / "runs" / result["id"] / "candidate").exists())
        self.assertTrue(Path(result["review_report"]).is_file())
        with self.assertRaises(PipelineError):
            promote(self.root, result["id"], "unreviewed")

    def test_missing_country_cannot_hide_in_baseline(self):
        self.sql(f"DELETE FROM {TABLE} WHERE Country='Example B'")
        request, report = self.review()
        self.assertEqual(report["status"], "blocked", report)
        self.assertIn("missing_countries", self.codes(report))
        result = run(self.root, request, synthetic_decisions(self.root, report))
        self.assertEqual(result["status"], "failed")
        self.assertFalse((self.root / "runs" / result["id"] / "candidate").exists())

    def test_declared_partial_import_reports_then_preserves_country(self):
        self.sql(f"DELETE FROM {TABLE} WHERE Country='Example B'")
        self.recipe["validation"] = {"allow_partial_countries": True}
        request, report = self.review()
        self.assertEqual(report["status"], "ready_for_decision")
        self.assertIn("missing_countries", self.codes(report))
        result = run(self.root, request, synthetic_decisions(self.root, report))
        self.assertTrue(result["status"].startswith("validated"), result)
        with readonly(self.root / "runs" / result["id"] / "candidate/IFsHistSeries.db") as conn:
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0], 2)

    def test_null_representations_and_zero_are_distinct(self):
        self.sql(f'UPDATE {TABLE} SET "2022"=?, "2023"=? WHERE Country=?', ("", " \t ", "Example A"))
        self.sql(f'UPDATE {TABLE} SET "2022"=NULL, "2023"=0 WHERE Country=?', ("Example B",))
        request, report = self.review()
        counts = {r["representation"]: int(r["cells"]) for r in csv_rows(Path(report["folder"]) / "missing_values.csv") if r["dataset"] == "incoming"}
        self.assertEqual(counts, {"empty_string": 1, "whitespace_string": 1, "sql_null": 1, "numeric_zero": 1})
        stats = report["requests"][request]["tables"][TABLE]
        self.assertEqual((stats["incoming_rows"], stats["incoming_countries_with_data"]), (2, 1))
        self.assertIn("all_null_country", self.codes(report))
        self.assertIn("lost_observation", self.codes(report))
        normalization = csv_rows(Path(report["folder"]) / "normalization.csv")
        self.assertEqual(len(normalization), 2)
        self.assertTrue(all(r["action"] == "to_sql_null" for r in normalization))
        result = run(self.root, request, synthetic_decisions(self.root, report))
        with readonly(self.root / "runs" / result["id"] / "candidate/IFsHistSeries.db") as conn:
            self.assertEqual(conn.execute(f'SELECT "2023" FROM {TABLE} WHERE Country=?', ("Example B",)).fetchone()[0], 0)
        coverage = csv_rows(self.root / "runs" / result["id"] / "coverage.csv")
        y2022 = next(r for r in coverage if r["year"] == "2022")
        self.assertEqual((y2022["before"], y2022["incoming"], y2022["after"]), ("2", "0", "2"))

    def test_declared_tokens_apply_to_validation_and_writer(self):
        self.recipe.update(missing_values=["", ".."], missing_numeric_values=[-999])
        self.sql(f'UPDATE {TABLE} SET "2022"=?, "2023"=? WHERE Country=?', ("..", -999, "Example A"))
        request, report = self.review()
        self.assertEqual(report["status"], "ready_for_decision", report)
        normalization = csv_rows(Path(report["folder"]) / "normalization.csv")
        self.assertEqual({r["category"] for r in normalization}, {"declared_string_token", "declared_numeric_token"})
        result = run(self.root, request, synthetic_decisions(self.root, report))
        self.assertTrue(result["status"].startswith("validated"), result)
        self.assertEqual(result["tables"][TABLE]["backfilled"], 2)

    def test_unknown_tokens_and_blob_are_blocked(self):
        for value in ("NULL", "NaN", b"123"):
            with self.subTest(value=value):
                self.sql(f'UPDATE {TABLE} SET "2022"=? WHERE Country=?', (value, "Example A"))
                _, report = self.review()
                self.assertEqual(report["status"], "blocked")
                self.assertIn("invalid_value", self.codes(report))

    def test_zero_cannot_be_missing_token(self):
        for config in ({"missing_values": ["0"]}, {"missing_values": ["0.0"]}, {"missing_numeric_values": [0]}):
            with self.subTest(config=config):
                write_json(self.root / "bad-recipe.json", {**self.recipe, **config})
                with self.assertRaises(PipelineError):
                    load_recipe(self.root / "bad-recipe.json")

    def test_country_spelling_and_duplicate_identities_are_blocked(self):
        self.sql(f"UPDATE {TABLE} SET Country='Exampel A' WHERE Country='Example A'")
        _, report = self.review()
        self.assertIn("country_identity", self.codes(report))
        self.sql(f"UPDATE {TABLE} SET Country='Example A', FIPS_CODE='AAA'")
        _, report = self.review()
        self.assertEqual(report["status"], "blocked")

    def test_metadata_unit_error_is_not_waivable(self):
        self.sql("UPDATE DataDict SET Units='thousands'")
        request, report = self.review()
        self.assertEqual(report["status"], "blocked")
        result = run(self.root, request, synthetic_decisions(self.root, report))
        self.assertIn("Blocking validation errors", result["errors"][0])

    def test_all_table_errors_are_reported(self):
        self.sql("INSERT INTO DataDict SELECT 'SeriesMissing', 'Missing', Definition, Units, Source, Years, [Last IFs Update], UsedInHistAnalog, UsedInFunctions, [Decimal Places] FROM DataDict")
        self.sql(f"UPDATE {TABLE} SET Country='Wrong' WHERE Country='Example A'")
        _, report = self.review()
        errors = [r for r in csv_rows(Path(report["folder"]) / "issues.csv") if r["severity"] == "error"]
        self.assertTrue({TABLE, "SeriesMissing"}.issubset({r["table"] for r in errors}))

    def test_decisions_need_review_acceptance_and_reason(self):
        self.sql(f'UPDATE {TABLE} SET "2023"=NULL WHERE Country=?', ("Example B",))
        request, report = self.review()
        path = synthetic_decisions(self.root, report)
        decisions = read_json(path)
        decisions["accepted_issue_ids"] = []
        write_json(path, decisions)
        with self.assertRaisesRegex(PipelineError, "Unresolved"):
            decision_context(self.root, request, path)
        path = synthetic_decisions(self.root, report)
        decisions = read_json(path)
        decisions["requests"][request][TABLE]["reason"] = ""
        write_json(path, decisions)
        with self.assertRaisesRegex(PipelineError, "reason"):
            decision_context(self.root, request, path)

    def test_hold_blocks_and_keep_old_preserves_values_and_metadata(self):
        self.sql(f'UPDATE {TABLE} SET "2023"=999 WHERE Country=?', ("Example A",))
        request, report = self.review()
        held = run(self.root, request, synthetic_decisions(self.root, report, {TABLE: "hold"}))
        self.assertEqual(held["status"], "failed")
        self.assertFalse((self.root / "runs" / held["id"] / "candidate").exists())
        kept = run(self.root, request, synthetic_decisions(self.root, report, {TABLE: "keep_old"}))
        self.assertEqual(kept["status"], "validated", kept)
        self.assertEqual(kept["tables"], {})
        with readonly(self.root / "runs" / kept["id"] / "candidate/IFsHistSeries.db") as conn:
            self.assertEqual(conn.execute(f'SELECT "2023" FROM {TABLE} WHERE Country=?', ("Example A",)).fetchone()[0], 110)
        with readonly(self.root / "runs" / kept["id"] / "candidate/DataDict.db") as conn:
            self.assertEqual(conn.execute('SELECT [Last IFs Update] FROM DataDict').fetchone()[0], "2025/01/01")

    def test_per_table_merge_actions(self):
        self.sql(f'UPDATE {TABLE} SET "2023"=NULL WHERE Country=?', ("Example B",))
        self.sql(f'CREATE TABLE SeriesOther AS SELECT * FROM {TABLE}')
        self.sql("INSERT INTO DataDict SELECT 'SeriesOther', 'Other', Definition, Units, Source, Years, [Last IFs Update], UsedInHistAnalog, UsedInFunctions, [Decimal Places] FROM DataDict")
        self.recipe["allow_coverage_loss"] = True
        request, report = self.review()
        path = synthetic_decisions(self.root, report, {TABLE: "replace", "SeriesOther": "keep_old"})
        result = run(self.root, request, path)
        self.assertTrue(result["status"].startswith("validated"), result)
        with readonly(self.root / "runs" / result["id"] / "candidate/IFsHistSeries.db") as conn:
            self.assertIsNone(conn.execute(f'SELECT "2023" FROM {TABLE} WHERE Country=?', ("Example B",)).fetchone()[0])
            self.assertFalse(conn.execute("SELECT name FROM sqlite_master WHERE name='SeriesOther'").fetchone())
        self.assertEqual(result["tables"][TABLE]["removed"], 1)

    def test_replacement_still_requires_coverage_loss_policy(self):
        self.sql(f'UPDATE {TABLE} SET "2023"=NULL WHERE Country=?', ("Example B",))
        request, report = self.review()
        result = run(self.root, request, synthetic_decisions(self.root, report, {TABLE: "replace"}))
        self.assertEqual(result["status"], "failed")
        self.assertIn("remove", result["errors"][0])

    def test_stale_review_evidence_and_code_are_rejected(self):
        request, report = self.review()
        path = synthetic_decisions(self.root, report)
        with patch("ifs_pipeline.review.code_hashes", return_value={"changed": "hash"}):
            with self.assertRaisesRegex(PipelineError, "code changed"):
                decision_context(self.root, request, path)
        with (Path(report["folder"]) / "issues.csv").open("a") as handle:
            handle.write("tampered")
        with self.assertRaisesRegex(PipelineError, "evidence changed"):
            decision_context(self.root, request, path)

    def test_changed_baseline_selection_requires_new_review(self):
        request, report = self.review()
        path = synthetic_decisions(self.root, report)
        copied = self.root / "other-baseline"
        shutil.copytree(self.root / "baseline", copied)
        config = read_json(self.root / "config.local.json")
        config["baseline"] = str(copied)
        write_json(self.root / "config.local.json", config)
        with self.assertRaisesRegex(PipelineError, "Baseline changed"):
            decision_context(self.root, request, path)

    def test_changed_source_requires_new_review(self):
        request, report = self.review()
        path = synthetic_decisions(self.root, report)
        raw = self.root / read_json(self.root / "inbox" / request / "request.json")["raw_path"]
        with raw.open("ab") as handle:
            handle.write(b"tamper")
        with self.assertRaisesRegex(PipelineError, "Archived request artifact changed"):
            decision_context(self.root, request, path)

    def test_batch_overlap_does_not_choose_precedence(self):
        first, second = self.submit(), self.submit()
        report = validate_requests(self.root, [first, second])
        self.assertIn("overlapping_imports", self.codes(report))
        decisions = read_json(Path(report["folder"]) / "decisions.template.json")
        self.assertIsNone(decisions["requests"][first][TABLE]["action"])
        self.assertIsNone(decisions["requests"][second][TABLE]["action"])

    def test_promoted_release_keeps_review_and_rejects_tampering(self):
        request, report = self.review()
        result = run(self.root, request, synthetic_decisions(self.root, report))
        self.assertEqual(result["status"], "validated", result)
        release = promote(self.root, result["id"], "reviewed")
        self.assertTrue((release / "validation/normalization.csv").is_file())
        self.assertTrue((release / "decisions.json").is_file())
        evidence = self.root / "runs" / result["id"] / "validation/issues.csv"
        with evidence.open("a") as handle:
            handle.write("tamper")
        with self.assertRaisesRegex(PipelineError, "modified after validation"):
            promote(self.root, result["id"], "tampered")

    def test_merge_can_introduce_a_jump_absent_from_incoming(self):
        self.sql(f'UPDATE {TABLE} SET "2022"=NULL, "2023"=400')
        request, report = self.review()
        self.assertNotIn("temporal_jump", self.codes(report))
        result = run(self.root, request, synthetic_decisions(self.root, report))
        self.assertEqual(result["status"], "validated_with_warnings", result)
        issues = csv_rows(self.root / "runs" / result["id"] / "postmerge_issues.csv")
        self.assertIn("temporal_jump", {r["code"] for r in issues})
        with self.assertRaisesRegex(PipelineError, "accept-warnings"):
            promote(self.root, result["id"], "unchecked-splice")

    def test_changed_baseline_contents_invalidate_review(self):
        copied = self.root / "mutable-baseline"
        shutil.copytree(self.root / "baseline", copied)
        config = read_json(self.root / "config.local.json")
        config["baseline"] = str(copied)
        write_json(self.root / "config.local.json", config)
        request, report = self.review()
        path = synthetic_decisions(self.root, report)
        conn = sqlite3.connect(copied / "IFsHistSeries.db")
        with conn:
            conn.execute(f'UPDATE {TABLE} SET "2023"=111 WHERE Country=?', ("Example A",))
        conn.close()
        with self.assertRaisesRegex(PipelineError, "Baseline changed"):
            decision_context(self.root, request, path)

    def test_unchanged_baseline_compatibility_script(self):
        import runpy
        script = Path(__file__).resolve().parents[1] / "scripts/check_baseline.py"
        check = runpy.run_path(str(script))["check"]
        result = check(self.root / "baseline", self.root / "compatibility", TABLE)
        self.assertTrue(all(result["checks"].values()), result)

    def test_validate_cli_produces_report(self):
        request = self.submit()
        with redirect_stdout(io.StringIO()) as output:
            code = main(["--root", str(self.root), "validate", request])
        self.assertEqual(code, 0)
        self.assertIn("ready_for_decision", output.getvalue())


class DiagnosticTests(unittest.TestCase):
    def series(self, values, name="SeriesTest"):
        years = [str(2000 + i) for i in range(len(values[0]))]
        return make_series(name, KEYS["monadic"], years, [
            {"Country": f"Country {i}", "FIPS_CODE": f"C{i}", **dict(zip(years, row))}
            for i, row in enumerate(values)])

    def checks(self, old, new):
        findings, differences, coverage = [], [], []
        compare_incoming(old, new, rules_for({}, new.name), findings, differences, coverage)
        return {r["code"] for r in findings}

    def test_jumps_gaps_bounds_flat_and_near_zero_floor(self):
        data = self.series([[0, 1e-12, None, 10, 10, 10, 10, 10, 10], [2, 100, 101, 102, 103, 104, 105, 106, 107]])
        findings = []
        rules = rules_for({"validation": {"defaults": {"absolute_change_floor": .01, "max_value": 99}}}, data.name)
        inspect_new(data, rules, findings)
        self.assertTrue({"internal_gap", "temporal_jump", "flat_run", "value_above_bound"}.issubset({r["code"] for r in findings}))
        self.assertFalse(any(r["code"] == "temporal_jump" and r["year"] == "2001" and r["key"] == "Country 0 | C0" for r in findings))

    def test_five_year_data_does_not_raise_annual_gaps(self):
        data = self.series([[1, None, None, None, None, 1.1]])
        findings = []
        inspect_new(data, rules_for({"validation": {"defaults": {"frequency_years": 5}}}, data.name), findings)
        self.assertNotIn("internal_gap", {r["code"] for r in findings})
        findings = []
        inspect_new(data, rules_for({}, data.name), findings)
        self.assertIn("internal_gap", {r["code"] for r in findings})

    def test_possible_country_swap_scale_and_year_shift(self):
        a, b = [1, 3, 5, 7, 9, 11, 13], [2, 4, 6, 8, 10, 12, 14]
        old = self.series([a, b])
        self.assertIn("possible_country_misplacement", self.checks(old, self.series([b, a])))
        self.assertIn("possible_scale_change", self.checks(old, self.series([[v*1000 for v in a], [v*1000 for v in b]])))
        self.assertIn("possible_year_shift", self.checks(old, self.series([[None]+a[:-1], [None]+b[:-1]])))
        self.assertNotIn("possible_country_misplacement", self.checks(old, old))

    def test_declared_sums_report_missing_components_without_zero_fill(self):
        data = {name: self.series([values], name) for name, values in
                (("SeriesTotal", [3, 99, 10]), ("SeriesA", [1, 1, None]), ("SeriesB", [2, 2, 10]))}
        recipe = {"validation": {"sum_rules": [{"total": "SeriesTotal", "parts": ["SeriesA", "SeriesB"]}]}}
        findings = []
        check_sum_rules(data, recipe, findings)
        errors = [r for r in findings if r["code"] == "sum_inconsistency"]
        self.assertEqual([r["year"] for r in errors], ["2001"])
        self.assertIn("1 skipped", findings[-1]["message"])
        empty = []
        check_sum_rules(data, {}, empty)
        self.assertEqual(empty, [])

    def test_invalid_threshold_configuration_is_rejected(self):
        for defaults in ({"frequency_years": 0}, {"flat_run_length": 1}, {"near_zero_floor": 0},
                         {"jump_fraction": -1}, {"unknown": 1}, {"min_value": 5, "max_value": 2}):
            with self.subTest(defaults=defaults):
                with self.assertRaises(PipelineError):
                    rules_for({"validation": {"defaults": defaults}}, "SeriesTest")


if __name__ == "__main__":
    unittest.main()

import csv
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ifs_pipeline.adapters import country_reference
from ifs_pipeline.cli import main, status
from ifs_pipeline.demo import create_demo
from ifs_pipeline.model import KEYS, blend, make_series, read_series, write_series
from ifs_pipeline.storage import PipelineError, read_json, readonly, sha256, write_json
from ifs_pipeline.workflow import csv_write, ingest, promote, run


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspace"
        self.initial = create_demo(self.root)
        self.assertEqual(self.initial["status"], "validated", self.initial)
        self.recipe = read_json(self.root / "recipe.json")
        self.before = {p.name: sha256(p) for p in (self.root / "baseline").glob("*.db")}
        self.assertEqual(self.before, self.initial["baseline_sha256"])

    def tearDown(self):
        self.assertEqual(self.before, {p.name: sha256(p) for p in (self.root / "baseline").glob("*.db")})

    def submit(self, rows=None, recipe=None):
        if rows is not None:
            csv_write(self.root / "download.csv", rows, ["code", "indicator", "year", "value", "unit"])
        write_json(self.root / "recipe.json", recipe or self.recipe)
        return ingest(self.root, self.root / "download.csv", self.root / "recipe.json", "synthetic://test", "test-release")

    def result(self, rows=None, recipe=None):
        return run(self.root, self.submit(rows, recipe))

    def test_end_to_end_values_types_metadata_and_source_immutability(self):
        manifest = self.initial
        candidate = self.root / "runs" / manifest["id"] / "candidate"
        with readonly(candidate / "IFsHistSeries.db") as conn:
            data = {r["Country"]: dict(r) for r in conn.execute("SELECT * FROM SeriesDemoPopulation")}
            self.assertEqual([data["Example A"][y] for y in ("2022", "2023", "2024")], [100, 115, 120])
            self.assertEqual([data["Example B"][y] for y in ("2022", "2023", "2024")], [200, 220, 230])
            self.assertEqual(data["Example A"]["Earliest"], 100)
            self.assertEqual(data["Example B"]["MostRecent"], 230)
            self.assertEqual(conn.execute("SELECT note FROM Unrelated").fetchone()[0], "Preserve this table")
            schema = {r["name"]: r["type"] for r in conn.execute("PRAGMA table_info(SeriesDemoPopulation)")}
            self.assertEqual(schema["Country"], "VARCHAR(255)")
            self.assertEqual(schema["2024"], "DOUBLE(53)")
        with readonly(candidate / "DataDict.db") as conn:
            row = dict(conn.execute("SELECT * FROM DataDict").fetchone())
            self.assertEqual(row["Years"], "2022-2024")
            self.assertEqual(row["Decimal Places"], 5)
        self.assertEqual(manifest["tables"]["SeriesDemoPopulation"]["added"], 2)
        self.assertEqual(manifest["tables"]["SeriesDemoPopulation"]["revised"], 1)
        self.assertEqual(sha256(self.root / "download.csv"), manifest["source"]["sha256"])

    def test_rerun_is_deterministic_without_duplicate_rows(self):
        again = run(self.root, self.initial["request_id"])
        self.assertEqual(again["status"], "validated")
        self.assertEqual(again["tables"], self.initial["tables"])
        self.assertEqual(again["candidate_sha256"], self.initial["candidate_sha256"])
        self.assertNotEqual(again["id"], self.initial["id"])

    def test_bad_csv_values_and_keys_are_blocked(self):
        base = {"code": "AAA", "indicator": "POP", "year": 2024, "value": 1, "unit": "persons"}
        for update, error in (({"code": "ZZZ"}, "unmapped country"),
                              ({"indicator": "OTHER"}, "unmapped indicator"),
                              ({"value": "oops"}, "Non-numeric"),
                              ({"value": "NaN"}, "Non-finite"),
                              ({"value": "inf"}, "Non-finite"),
                              ({"value": -1}, "below min_value"),
                              ({"year": "2024.0"}, "invalid year"),
                              ({"unit": "thousands"}, "unexpected unit")):
            with self.subTest(update=update):
                result = self.result([{**base, **update}])
                self.assertEqual(result["status"], "failed")
                self.assertIn(error, " ".join(result["errors"]))
                self.assertTrue((self.root / "runs" / result["id"] / "report.md").is_file())

    def test_duplicate_observations_blocked(self):
        row = {"code": "AAA", "indicator": "POP", "year": 2024, "value": 120, "unit": "persons"}
        result = self.result([row, row])
        self.assertEqual(result["status"], "failed")
        self.assertIn("duplicate", result["errors"][0])

    def test_missing_tokens_preserve_history_and_zero_is_data(self):
        self.recipe["missing_values"] = ["", ".."]
        result = self.result([
            {"code": "AAA", "indicator": "POP", "year": 2023, "value": "..", "unit": "persons"},
            {"code": "BBB", "indicator": "POP", "year": 2023, "value": 0, "unit": "persons"},
        ])
        self.assertEqual(result["status"], "validated_with_warnings")
        with readonly(self.root / "runs" / result["id"] / "candidate/IFsHistSeries.db") as conn:
            rows = list(conn.execute('SELECT Country, "2023" FROM SeriesDemoPopulation ORDER BY Country'))
            self.assertEqual([tuple(r) for r in rows], [("Example A", 110), ("Example B", 0)])

    def test_explicit_unit_conversion(self):
        spec = self.recipe["indicators"]["POP"]
        spec["input_unit"] = "thousand persons"
        spec["multiplier"] = 1000
        result = self.result([{"code": "AAA", "indicator": "POP", "year": 2024, "value": 0.120, "unit": "thousand persons"}])
        self.assertEqual(result["status"], "validated")
        with readonly(self.root / "runs" / result["id"] / "candidate/IFsHistSeries.db") as conn:
            self.assertEqual(conn.execute('SELECT "2024" FROM SeriesDemoPopulation WHERE Country=?', ("Example A",)).fetchone()[0], 120)

    def test_unit_mismatch_and_mixed_unit_blending_blocked(self):
        self.recipe["indicators"]["POP"]["metadata"]["Units"] = "thousands"
        result = self.result()
        self.assertIn("units changed", result["errors"][0])
        self.recipe["allow_unit_change"] = True
        result = self.result()
        self.assertIn("mix old and new units", result["errors"][0])

    def test_country_count_failure(self):
        self.recipe["expected_country_count"] = 188
        self.assertIn("expected 188", self.result()["errors"][0])

    def test_replacement_coverage_loss_needs_explicit_policy(self):
        self.recipe["merge_policy"] = "replace"
        result = self.result()
        self.assertEqual(result["status"], "failed")
        self.assertIn("remove", result["errors"][0])
        self.recipe["allow_coverage_loss"] = True
        result = self.result()
        self.assertEqual(result["status"], "validated_with_warnings")
        self.assertEqual(result["tables"]["SeriesDemoPopulation"]["removed"], 3)

    def test_changed_archived_recipe_input_or_reference_is_blocked(self):
        for name in ("recipe.json", "countries.csv", "raw"):
            with self.subTest(file=name):
                request_id = self.submit()
                folder = self.root / "inbox" / request_id
                path = folder / name if name != "raw" else self.root / read_json(folder / "request.json")["raw_path"]
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                try:
                    result = run(self.root, request_id)
                    self.assertIn("Archived input or recipe changed", result["errors"][0])
                finally:
                    path.write_bytes(original)

    def test_future_recipe_edits_do_not_change_archived_request(self):
        request_id = self.submit()
        self.recipe["merge_policy"] = "replace"
        write_json(self.root / "recipe.json", self.recipe)
        self.assertEqual(run(self.root, request_id)["status"], "validated")

    def test_promote_and_no_overwrite(self):
        release = promote(self.root, self.initial["id"], "demo-release")
        self.assertEqual(sha256(release / "IFsHistSeries.db"), self.initial["candidate_sha256"]["IFsHistSeries.db"])
        self.assertEqual(status(self.root)["releases"], ["demo-release"])
        with self.assertRaises(PipelineError):
            promote(self.root, self.initial["id"], "demo-release")
        with self.assertRaises(PipelineError):
            promote(self.root, self.initial["id"], "../escape")

    def test_modified_candidate_cannot_be_promoted(self):
        candidate = self.root / "runs" / self.initial["id"] / "candidate/IFsHistSeries.db"
        with candidate.open("ab") as handle:
            handle.write(b"modified")
        with self.assertRaisesRegex(PipelineError, "modified after validation"):
            promote(self.root, self.initial["id"], "bad")

    def test_failed_run_cannot_be_promoted(self):
        self.recipe["expected_country_count"] = 999
        result = self.result()
        with self.assertRaisesRegex(PipelineError, "successfully validated"):
            promote(self.root, result["id"], "bad")

    def test_warnings_require_recorded_acceptance(self):
        self.recipe["large_revision_fraction"] = 0
        result = self.result()
        self.assertEqual(result["status"], "validated_with_warnings")
        with self.assertRaisesRegex(PipelineError, "accept-warnings"):
            promote(self.root, result["id"], "reviewed")
        release = promote(self.root, result["id"], "reviewed", True)
        self.assertTrue(read_json(release / "release.json")["accepted_warnings"])

    def test_prepared_sqlite_import_path(self):
        source = self.root / "prepared-import.db"
        shutil.copy2(self.root / "baseline/DataDict.db", source)
        conn = sqlite3.connect(source)
        try:
            with conn:
                series = make_series("SeriesDemoPopulation", KEYS["monadic"], ["2023", "2024"], [
                    {"Country": "Example A", "FIPS_CODE": "AAA", "2023": 115, "2024": 120},
                    {"Country": "Example B", "FIPS_CODE": "BBB", "2023": None, "2024": 230}])
                write_series(conn, series)
        finally:
            conn.close()
        source_hash = sha256(source)
        self.recipe["adapter"] = "ifs_sqlite"
        write_json(self.root / "recipe.json", self.recipe)
        request_id = ingest(self.root, source, self.root / "recipe.json", "synthetic://sqlite", "test")
        from ifs_pipeline.review import validate_requests
        from test_validation import synthetic_decisions
        review = validate_requests(self.root, [request_id])
        result = run(self.root, request_id, synthetic_decisions(self.root, review))
        self.assertEqual(result["status"], "validated", result)
        self.assertEqual(result["tables"], self.initial["tables"])
        self.assertEqual(sha256(source), source_hash)

    def test_csv_draft_recipe_rejected(self):
        self.recipe["status"] = "draft"
        with self.assertRaisesRegex(PipelineError, "not active"):
            self.submit()

    def test_live_wal_database_intake_is_blocked(self):
        source = self.root / "live-source.db"
        shutil.copy2(self.root / "baseline/DataDict.db", source)
        conn = sqlite3.connect(source)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            with conn:
                conn.execute("UPDATE DataDict SET Source=?", ("Live source",))
            self.recipe["adapter"] = "ifs_sqlite"
            write_json(self.root / "recipe.json", self.recipe)
            with self.assertRaisesRegex(PipelineError, "Close/checkpoint"):
                ingest(self.root, source, self.root / "recipe.json", "synthetic://live", "test")
        finally:
            conn.close()

    def test_csv_reference_aliases_and_conflicts(self):
        path = self.root / "aliases.csv"
        csv_write(path, [{"source_code": "A", "Country": "Example A", "FIPS_CODE": "AAA"},
                         {"source_code": "AAA", "Country": "Example A", "FIPS_CODE": "AAA"}],
                  ["source_code", "Country", "FIPS_CODE"])
        canonical, aliases = country_reference(path)
        self.assertEqual(aliases["A"], aliases["AAA"])
        self.assertEqual(canonical, {"AAA": "Example A"})
        with path.open("a", encoding="utf-8") as handle:
            handle.write("A,Example B,BBB\n")
        with self.assertRaisesRegex(PipelineError, "Conflicting"):
            country_reference(path)

    def test_indexes_are_not_silently_discarded(self):
        # Separate copy: the authoritative baseline stays unchanged.
        other = self.root / "indexed"
        shutil.copytree(self.root / "baseline", other)
        conn = sqlite3.connect(other / "IFsHistSeries.db")
        with conn:
            conn.execute("CREATE INDEX country_idx ON SeriesDemoPopulation(Country)")
        conn.close()
        config = read_json(self.root / "config.local.json")
        config["baseline"] = str(other)
        write_json(self.root / "config.local.json", config)
        result = self.result()
        self.assertIn("constraints/indexes/triggers", result["errors"][0])
        with readonly(other / "IFsHistSeries.db") as conn:
            self.assertTrue(conn.execute("SELECT name FROM sqlite_master WHERE name='country_idx'").fetchone())


class ModelTests(unittest.TestCase):
    def test_dyadic_pairs_and_quoted_table_names(self):
        series = make_series('SeriesTrade%"test', KEYS["dyadic"], ["2022", "2024"], [
            {"Actor": "A", "Actor_FIPS": "AA", "Partner": "B", "Partner_FIPS": "BB", "2022": 10, "2024": 20},
            {"Actor": "B", "Actor_FIPS": "BB", "Partner": "A", "Partner_FIPS": "AA", "2022": 30, "2024": 40}])
        series = blend(None, series, "prefer_new_non_null")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.db"
            conn = sqlite3.connect(path)
            with conn:
                write_series(conn, series)
            conn.close()
            with readonly(path) as conn:
                actual = read_series(conn, series.name, "dyadic", {"AA": "A", "BB": "B"})
            self.assertEqual(actual.years, ["2022", "2023", "2024"])
            self.assertIsNone(next(iter(actual.rows.values()))["2023"])

    def test_duplicate_countries_and_null_keys_rejected(self):
        for rows in ([{"Country": "A", "FIPS_CODE": "AA", "2024": 1}, {"Country": "A", "FIPS_CODE": "BB", "2024": 2}],
                     [{"Country": None, "FIPS_CODE": "AA", "2024": 1}]):
            with self.assertRaises(PipelineError):
                make_series("SeriesTest", KEYS["monadic"], ["2024"], rows)


if __name__ == "__main__":
    unittest.main()

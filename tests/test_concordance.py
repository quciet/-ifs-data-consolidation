import csv
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ifs_pipeline.concordance import lookup_index, prepare, validate_bundle
from ifs_pipeline.demo import create_demo
from ifs_pipeline.storage import PipelineError, read_json, readonly, sha256, write_json
from ifs_pipeline.workflow import csv_write, ingest, promote, run

ROOT = Path(__file__).resolve().parents[1]


class ConcordanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspace"
        self.demo = create_demo(self.root)
        self.lookup = [
            {"ifs_name": "Example A", "ifs_fipscode": "AAA",
             "alternative_names": ["Example Republic A", "A-land"]},
            {"ifs_name": "Example B", "ifs_fipscode": "BBB",
             "alternative_names": ["Example Republic B"]},
        ]
        self.lookup_path = self.root / "reference/datagator/country_data.json"
        write_json(self.lookup_path, self.lookup)
        self.input = self.root / "download.csv"
        self.recipe = read_json(self.root / "recipe.json")
        self.baseline_hashes = {n: sha256(self.root / "baseline" / n)
                                for n in ("IFsHistSeries.db", "DataDict.db")}

    def tearDown(self):
        self.assertEqual(self.baseline_hashes, {n: sha256(self.root / "baseline" / n)
                                               for n in self.baseline_hashes})

    def source(self, names):
        rows = [{"code": name, "indicator": "POP", "year": 2024,
                 "value": 120 if i == 0 else 230, "unit": "persons"}
                for i, name in enumerate(names)]
        csv_write(self.input, rows, ["code", "indicator", "year", "value", "unit"])

    def prepare(self, **kwargs):
        return prepare(self.root, self.input, "code", "synthetic source", **kwargs)

    def mapping(self, rows):
        p = self.root / "datagator_mapping.csv"
        csv_write(p, [{"original_name": a, "matched_name": b} for a, b in rows],
                  ["original_name", "matched_name"])
        return p

    def intake(self, bundle):
        self.recipe.update(bundle["recipe_fields"])
        write_json(self.root / "recipe.json", self.recipe)
        return ingest(self.root, self.input, self.root / "recipe.json", "synthetic://source", "test")

    def test_exact_alias_normalization_and_frozen_run_evidence(self):
        self.source(["  EXAMPLE   REPUBLIC A ", "Example B"])
        original = self.input.read_bytes()
        bundle = self.prepare()
        self.assertEqual(bundle["status"], "ready")
        self.assertEqual(bundle["mapped_names"], 2)
        request_id = self.intake(bundle)
        # The source concordance can be removed after intake; archived request remains runnable.
        shutil.rmtree(Path(bundle["folder"]))
        result = run(self.root, request_id)
        self.assertEqual(result["status"], "validated", result)
        folder = self.root / "runs" / result["id"]
        self.assertTrue((folder / "concordance/datagator.json").is_file())
        self.assertTrue((folder / "concordance/matches.csv").is_file())
        with readonly(folder / "candidate/IFsHistSeries.db") as conn:
            rows = list(conn.execute('SELECT Country, "2024" FROM SeriesDemoPopulation ORDER BY Country'))
            self.assertEqual([tuple(r) for r in rows], [("Example A", 120), ("Example B", 230)])
        self.assertEqual(self.input.read_bytes(), original)
        release = promote(self.root, result["id"], "mapped-demo")
        self.assertTrue((release / "concordance/concordance.json").is_file())

    def test_unknown_and_near_matches_require_review(self):
        self.source(["Exampel A", "Example B"])
        bundle = self.prepare()
        self.assertEqual(bundle["status"], "blocked")
        self.assertEqual(bundle["unresolved_names"], 1)
        self.assertFalse((Path(bundle["folder"]) / "countries.csv").exists())
        mapping = self.mapping([("Exampel A", "Example A")])
        with self.assertRaisesRegex(PipelineError, "reviewed"):
            self.prepare(mapping_path=mapping)
        reviewed = self.prepare(mapping_path=mapping, reviewed=True)
        self.assertEqual(reviewed["status"], "ready")
        self.assertTrue((Path(reviewed["folder"]) / "reviewed_mapping.csv").is_file())

    def test_reviewed_blank_is_unresolved_even_if_alias_exists(self):
        self.source(["Example A", "Example B"])
        mapping = self.mapping([("Example A", "")])
        self.assertEqual(self.prepare(mapping_path=mapping, reviewed=True)["status"], "blocked")

    def test_two_source_names_to_one_target_require_processing_review(self):
        self.source(["Example A", "A-land"])
        before = self.input.read_bytes()
        bundle = self.prepare()
        self.assertEqual(bundle["status"], "needs_processing_review")
        self.assertEqual(bundle["collisions"][0]["FIPS_CODE"], "AAA")
        self.assertFalse((Path(bundle["folder"]) / "countries.csv").exists())
        self.assertEqual(self.input.read_bytes(), before)

    def test_explicit_exclusion_has_evidence_and_preserves_master(self):
        self.source(["Example A", "World"])
        bundle = self.prepare(exclude=["World"])
        self.assertEqual(bundle["status"], "ready")
        self.assertEqual(bundle["absent_ifs_countries"], ["BBB"])
        self.assertEqual(bundle["excluded_names"], ["World"])
        with (Path(bundle["folder"]) / "countries.csv").open(encoding="utf-8-sig") as h:
            rows = list(csv.DictReader(h))
        self.assertIn("BBB", [r["FIPS_CODE"] for r in rows])
        result = run(self.root, self.intake(bundle))
        self.assertEqual(result["status"], "validated", result)

    def test_nonexistent_exclusion_and_all_excluded_fail(self):
        self.source(["Example A"])
        with self.assertRaisesRegex(PipelineError, "Exclusions absent"):
            self.prepare(exclude=["Other"])
        self.assertEqual(self.prepare(exclude=["Example A"])["status"], "blocked")

    def test_alias_conflicts_are_reported_not_last_write_wins(self):
        self.lookup[0]["alternative_names"].append("Shared")
        self.lookup[1]["alternative_names"].append("Shared")
        write_json(self.lookup_path, self.lookup)
        self.source(["Shared"])
        bundle = self.prepare()
        self.assertEqual(bundle["status"], "blocked")
        with (Path(bundle["folder"]) / "matches.csv").open(encoding="utf-8-sig") as h:
            row = next(csv.DictReader(h))
        self.assertEqual(row["status"], "ambiguous")
        self.assertEqual(row["candidates"], "Example A | Example B")

    def test_mismatched_master_blocks(self):
        self.lookup[0]["ifs_fipscode"] = "OTHER"
        write_json(self.lookup_path, self.lookup)
        with self.assertRaisesRegex(PipelineError, "differs from configured"):
            self.prepare()

    def test_changed_source_requires_new_concordance(self):
        bundle = self.prepare()
        self.input.write_text(self.input.read_text(encoding="utf-8-sig") + "\n", encoding="utf8")
        with self.assertRaisesRegex(PipelineError, "different input"):
            self.intake(bundle)

    def test_wrong_country_column_or_exclusions_are_blocked(self):
        bundle = self.prepare()
        recipe = {**self.recipe, **bundle["recipe_fields"]}
        recipe["columns"] = {**recipe["columns"], "country": "other"}
        with self.assertRaisesRegex(PipelineError, "country column"):
            validate_bundle(self.root, recipe["concordance_manifest"], self.input, recipe)
        recipe["columns"]["country"] = "code"
        recipe["ignored_country_codes"] = ["AAA"]
        with self.assertRaisesRegex(PipelineError, "exclusions"):
            validate_bundle(self.root, recipe["concordance_manifest"], self.input, recipe)

    def test_tampered_concordance_blocks_intake_and_run(self):
        bundle = self.prepare()
        matches = Path(bundle["folder"]) / "matches.csv"
        original = matches.read_bytes()
        matches.write_bytes(original + b"changed")
        with self.assertRaisesRegex(PipelineError, "evidence changed"):
            self.intake(bundle)
        matches.write_bytes(original)
        request_id = self.intake(bundle)
        saved = self.root / "inbox" / request_id / "concordance/matches.csv"
        saved.write_bytes(saved.read_bytes() + b"changed")
        result = run(self.root, request_id)
        self.assertEqual(result["status"], "failed")
        self.assertIn("Archived concordance changed", result["errors"][0])

    def test_one_source_to_two_targets_cannot_be_name_mapping(self):
        self.source(["Historic territory"])
        mapping = self.mapping([("Historic territory", "Example A"),
                                ("Historic territory", "Example B")])
        with self.assertRaisesRegex(PipelineError, "Duplicate original_name"):
            self.prepare(mapping_path=mapping, reviewed=True)

    def test_mapping_from_wrong_dataset_is_rejected(self):
        mapping = self.mapping([("Not in selected column", "Example A")])
        with self.assertRaisesRegex(PipelineError, "absent from"):
            self.prepare(mapping_path=mapping, reviewed=True)

    def test_invalid_or_conflicting_reviewed_target_rejected(self):
        mapping = self.mapping([("AAA", "Unknown target")])
        with self.assertRaisesRegex(PipelineError, "Invalid or ambiguous"):
            self.prepare(mapping_path=mapping, reviewed=True)
        mapping = self.mapping([("AAA", "Example B")])
        with self.assertRaisesRegex(PipelineError, "conflicts with canonical"):
            self.prepare(mapping_path=mapping, reviewed=True)

    def test_modified_run_concordance_cannot_be_promoted(self):
        bundle = self.prepare()
        result = run(self.root, self.intake(bundle))
        path = self.root / "runs" / result["id"] / "concordance/matches.csv"
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaisesRegex(PipelineError, "concordance was modified"):
            promote(self.root, result["id"], "bad-evidence")

    def test_lookup_provenance_mismatch_is_blocked(self):
        write_json(self.lookup_path.with_name("provenance.json"),
                   {"repository": "synthetic://lookup", "commit": "fixture", "local_sha256": "incorrect"})
        with self.assertRaisesRegex(PipelineError, "recorded provenance"):
            self.prepare()

    def test_repeated_country_year_values_still_fail_after_mapping(self):
        # Only one distinct label: concordance succeeds, observation validation catches the duplicate.
        self.source(["Example A", "Example A"])
        bundle = self.prepare()
        self.assertEqual(bundle["status"], "ready")
        result = run(self.root, self.intake(bundle))
        self.assertEqual(result["status"], "failed")
        self.assertIn("duplicate country/indicator/year", result["errors"][0])


class PinnedLookupTests(unittest.TestCase):
    def test_pinned_datagator_lookup_has_unique_exact_targets(self):
        data = read_json(ROOT / "reference/datagator/country_data.json")
        canonical = {r["ifs_fipscode"]: r["ifs_name"] for r in data}
        self.assertEqual(len(canonical), 188)
        self.assertEqual(sum(len(r["alternative_names"]) for r in data), 567)
        index, targets = lookup_index(data, canonical)
        self.assertEqual(len(index), 754)
        self.assertTrue(all(len(v) == 1 for v in index.values()))
        self.assertEqual(targets["aut"], {"AUT"})
        self.assertEqual(index["republic of austria"], {"AUT"})


if __name__ == "__main__":
    unittest.main()

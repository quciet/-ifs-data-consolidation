"""Small synthetic example with no dependency on the user's real databases."""
from pathlib import Path
import sqlite3
from .model import KEYS, make_series, write_series
from .storage import PipelineError, write_json
from .workflow import configure, csv_write, ingest, run


def create_demo(root):
    root = Path(root).resolve()
    if root.exists():
        raise PipelineError(f"Demo directory already exists; choose a new --directory: {root}")
    baseline = root / "baseline"
    baseline.mkdir(parents=True)
    conn = sqlite3.connect(baseline / "IFsHistSeries.db")
    try:
        with conn:
            series = make_series("SeriesDemoPopulation", KEYS["monadic"], ["2022", "2023"], [
                {"Country": "Example A", "FIPS_CODE": "AAA", "2022": 100, "2023": 110},
                {"Country": "Example B", "FIPS_CODE": "BBB", "2022": 200, "2023": 220},
            ])
            write_series(conn, series)
            conn.execute('CREATE TABLE Unrelated (note TEXT)')
            conn.execute('INSERT INTO Unrelated VALUES (?)', ("Preserve this table",))
    finally:
        conn.close()
    conn = sqlite3.connect(baseline / "DataDict.db")
    try:
        with conn:
            conn.execute('CREATE TABLE DataDict ("Table" TEXT, Variable TEXT, Definition TEXT, Units TEXT, Source TEXT, Years TEXT, "Last IFs Update" TEXT, UsedInHistAnalog INTEGER, UsedInFunctions INTEGER, "Decimal Places" INTEGER)')
            conn.execute('INSERT INTO DataDict VALUES (?,?,?,?,?,?,?,?,?,?)',
                         ("SeriesDemoPopulation", "DemoPopulation", "Synthetic population for testing", "persons", "Synthetic demo", "2022-2023", "2025/01/01", 0, 0, 5))
    finally:
        conn.close()
    configure(root, baseline, "SeriesDemoPopulation")
    csv_write(root / "download.csv", [
        {"code": "AAA", "indicator": "POP", "year": 2023, "value": 115, "unit": "persons"},
        {"code": "AAA", "indicator": "POP", "year": 2024, "value": 120, "unit": "persons"},
        {"code": "BBB", "indicator": "POP", "year": 2023, "value": "", "unit": "persons"},
        {"code": "BBB", "indicator": "POP", "year": 2024, "value": 230, "unit": "persons"},
    ], ["code", "indicator", "year", "value", "unit"])
    recipe = {"schema_version": 1, "id": "synthetic-population", "status": "active", "adapter": "csv_long",
              "kind": "monadic", "merge_policy": "prefer_new_non_null", "expected_country_count": 2,
              "large_revision_fraction": 0.5,
              "columns": {"country": "code", "indicator": "indicator", "year": "year", "value": "value", "unit": "unit"},
              "indicators": {"POP": {"table": "SeriesDemoPopulation", "input_unit": "persons", "multiplier": 1,
                                      "min_value": 0, "metadata": {"Variable": "DemoPopulation", "Definition": "Synthetic population for testing",
                                                                    "Units": "persons", "Source": "Synthetic demo"}}}}
    write_json(root / "recipe.json", recipe)
    request_id = ingest(root, root / "download.csv", root / "recipe.json", "synthetic://demo", "demo-2026")
    return run(root, request_id)

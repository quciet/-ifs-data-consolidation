"""Prepare the supplied wide CSVs as an IFs import; no numeric transformations.

Run with --repo PATH --source PATH [--output PATH]. Default output is a new
versioned subfolder of source. Explicit output must not exist.
The repository's pinned DataGator concordance is used for country identities.
"""
import argparse
import csv
import hashlib
import json
import math
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

def q(name):
    return '"' + name.replace('"', '""') + '"'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def csv_write(path, fields, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, help='Explicit output override; default is inside source')
    args = p.parse_args()
    metadata_profile_path = args.repo / 'recipes/sdebt.metadata.json'
    metadata_profile = json.loads(metadata_profile_path.read_text(encoding='utf-8'))
    sys.path.insert(0, str(args.repo / 'src'))
    from ifs_pipeline.concordance import prepare
    from ifs_pipeline.adapters import country_reference
    from ifs_pipeline.preparation_paths import create_preparation_workspace

    args.output, _ = create_preparation_workspace(args.source, args.output)
    evidence = args.output / 'Working Files'
    evidence.mkdir()
    shutil.copy2(metadata_profile_path, evidence / 'sdebt.metadata.json')
    raw = evidence / 'inputs'
    raw.mkdir()
    for path in args.source.iterdir():
        if path.suffix.lower() in ('.csv', '.txt'):
            shutil.copy2(path, raw / path.name)
            assert digest(path) == digest(raw / path.name)
    config = json.loads((args.repo / 'config.local.json').read_text())
    canonical, _ = country_reference(args.repo / config['country_reference'])
    schema_path = Path(config['baseline']) / 'DataDict.db'
    with sqlite3.connect(schema_path.as_uri() + '?mode=ro', uri=True) as base:
        schema = base.execute("SELECT sql FROM sqlite_master WHERE name='DataDict'").fetchone()[0]
    (evidence / 'datadict_schema.sql').write_text(schema + ';\n', encoding='utf-8')
    common_exclusions = ['Antigua and Barbuda', 'Dominica', 'Marshall Islands']
    specs = [
        ('SeriesSDRAllocation', 'SDR_Allocation_Wide4IFs.csv',
         'Special Drawing Rights Allocation', 'Millions of SDRs',
         'IMF Financial Data Query Tool', 'https://www.imf.org/external/np/fin/tad/query.aspx', None,
         common_exclusions + ['Andorra', 'Liechtenstein', 'Nauru', 'Palau', 'San Marino, Republic of', 'St. Kitts and Nevis', 'Tuvalu']),
        ('SeriesInterest%GDPPFMH', 'Interest_paid_on_public_debt_FPP_Regions4IFs.csv',
         'Interest paid on public debt as a percentage of GDP.', 'Percent of GDP',
         'IMF Public Finance in Modern History', 'https://www.imf.org/external/datamapper/ie@FPP/USA/FRA/JPN/GBR/SWE/ESP/ITA/ZAF/IND', 'ie',
         common_exclusions + ['Aruba', 'Saint Kitts and Nevis']),
        ('SeriesDebt%GDPPFMH', 'Gross_public_debt_FPP_Regions4IFs.csv',
         'Gross public debt as percentage of GDP', 'Percent of GDP',
         'IMF Public Finance in Modern History', 'https://www.imf.org/external/datamapper/d@FPP/USA/FRA/JPN/GBR/SWE/ESP/ITA/ZAF/IND', 'd',
         common_exclusions + ['Aruba', 'Saint Kitts and Nevis']),
    ]
    db = args.output / 'IFsDataImport_SDebt.db'
    summary, missing, excluded, recipes, expected = [], [], [], [], {}
    conn = sqlite3.connect(db)
    conn.execute(schema)
    metadata_fields = [r[1] for r in conn.execute('PRAGMA table_info(DataDict)')]
    for table, filename, definition, units, source, url, code, exclusions in specs:
        source_path = raw / filename
        bundle = prepare(args.repo, source_path, 'Country', source, exclude=exclusions)
        assert bundle['status'] == 'ready', bundle
        recipe = {'id': table, 'adapter': 'source_specific_wide_csv', 'columns': {'country': 'Country'},
                  'table': table, 'source_file': filename, 'source_sha256': digest(source_path),
                  'missing_values': ['empty or whitespace-only cells'], 'numeric_transformation': 'none',
                  'exclusion_reason': 'Not in the configured 188-country IFs roster; no territorial allocation or aggregation.',
                  **bundle['recipe_fields']}
        # Generic intake validates csv_long only; this wide adapter verifies the
        # same frozen source and bundle files directly before consuming them.
        assert bundle['source_sha256'] == digest(source_path)
        for bundle_file, expected_hash in bundle['files'].items():
            assert digest(Path(bundle['folder']) / bundle_file) == expected_hash
        shutil.copytree(bundle['folder'], evidence / ('concordance_' + table))
        with (Path(bundle['folder']) / 'matches.csv').open(encoding='utf-8-sig', newline='') as f:
            mappings = {r['original_name']: r for r in csv.DictReader(f)}
        with source_path.open(encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            years = reader.fieldnames[1:]
            assert reader.fieldnames[0] == 'Country' and all(y.isdigit() and len(y) == 4 for y in years)
            assert years == [str(y) for y in range(int(years[0]), int(years[-1]) + 1)]
            rows = list(reader)
        values_by_code, observed_years = {}, set()
        for line, row in enumerate(rows, 2):
            assert None not in row and all(v is not None for v in row.values())
            match = mappings[row['Country'].strip()]
            parsed = []
            for year in years:
                token = row[year]
                if token.strip() == '':
                    value = None
                    missing.append({'table': table, 'source_country': row['Country'], 'year': year,
                                    'csv_line': line, 'token': token, 'kind': 'blank' if not token else 'whitespace'})
                else:
                    value = float(token)
                    assert math.isfinite(value), (filename, line, year, token)
                parsed.append(value)
            if match['status'] == 'excluded':
                excluded.append({'table': table, 'source_country': row['Country'],
                                 'observations': sum(v is not None for v in parsed),
                                 'reason': recipe['exclusion_reason']})
                continue
            country_code = match['FIPS_CODE']
            assert country_code not in values_by_code
            values_by_code[country_code] = parsed
            observed_years.update(y for y, v in zip(years, parsed) if v is not None)
        columns = ['Country', 'FIPS_CODE'] + years + ['Earliest', 'MostRecent']
        conn.execute('CREATE TABLE ' + q(table) + ' (' + ','.join(q(c) + (' VARCHAR(255)' if c in columns[:2] else ' DOUBLE(53)') for c in columns) + ')')
        output_rows = []
        for country_code, country in sorted(canonical.items(), key=lambda x: x[1]):
            vals = values_by_code.get(country_code, [None] * len(years))
            present = [v for v in vals if v is not None]
            output_rows.append([country, country_code] + vals + [present[0] if present else None, present[-1] if present else None])
        conn.executemany('INSERT INTO ' + q(table) + ' VALUES (' + ','.join('?' for _ in columns) + ')', output_rows)
        expected[table] = output_rows
        metadata = {'Variable': table.removeprefix('Series'), 'Table': table, 'Definition': definition,
                    'Extended Source Defn': filename, 'Units': units, 'Years': f'{min(observed_years)}-{max(observed_years)}',
                    'Source': source, 'Original Source': url, 'Name in Source': definition.rstrip('.'),
                    'Code in Source': code, 'Last IFs Update': datetime.now(timezone.utc).strftime('%Y/%m/%d'),
                    'Country Concordance': 'Pinned DataGatorLite / IFs 188-country roster',
                    'Formula': 'None; supplied CSV values copied without numeric transformation.',
                    'Notes': 'Supplied by Ethan. CSV values preserved, including zero; blank cells are SQL NULL. Unspecified IFs flags remain NULL.'}
        if table == 'SeriesSDRAllocation':
            metadata['CURRENCY'] = 'SDR'
            metadata['Notes'] = 'Cumulative SDR allocations, in millions of SDRs; manually pulled by source preparer. Update when IMF announces allocations. Existing flat runs and zeros preserved.'
        overrides = metadata_profile['tables'][table]
        assert set(overrides) <= set(metadata_fields)
        assert not {'Table', 'Variable'} & set(overrides)
        # Explicit nulls are intentional. Display precision never rounds data.
        metadata.update(overrides)
        conn.execute('INSERT INTO DataDict (' + ','.join(q(f) for f in metadata_fields) + ') VALUES (' + ','.join('?' for _ in metadata_fields) + ')', [metadata.get(f) for f in metadata_fields])
        recipe['metadata'] = metadata
        recipes.append(recipe)
        vals = [v for row in output_rows for v in row[2:-2] if v is not None]
        summary.append({'table': table, 'year_columns': f'{years[0]}-{years[-1]}',
                        'observed_years': metadata['Years'], 'country_rows': len(output_rows),
                        'countries_with_observations': sum(any(v is not None for v in r[2:-2]) for r in output_rows),
                        'observations': len(vals), 'zeros': sum(v == 0 for v in vals),
                        'negative_observations': sum(v < 0 for v in vals),
                        'excluded_source_countries': len(exclusions), 'units': metadata['Units']})
    conn.commit()
    conn.close()
    with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as check:
        assert check.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert check.execute('SELECT count(*) FROM DataDict').fetchone()[0] == 3
        for table, rows in expected.items():
            actual = check.execute('SELECT * FROM ' + q(table)).fetchall()
            assert actual == [tuple(r) for r in rows], table
        for recipe in recipes:
            check.row_factory = sqlite3.Row
            actual = dict(check.execute('SELECT * FROM DataDict WHERE [Table]=?', (recipe['table'],)).fetchone())
            assert actual == {f: recipe['metadata'].get(f) for f in metadata_fields}
    for path in raw.iterdir():
        assert digest(path) == digest(args.source / path.name), path
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'database_sha256': digest(db),
                'metadata_profile_sha256': digest(metadata_profile_path),
                'schema_source': str(schema_path), 'schema_source_sha256': digest(schema_path),
                'inputs': {p.name: digest(p) for p in raw.iterdir()}, 'tables': summary,
                'verification': 'All output cells and metadata reread and compared exactly; integrity_check ok; source hashes unchanged.'}
    (evidence / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    (evidence / 'recipes.json').write_text(json.dumps(recipes, indent=2), encoding='utf-8')
    csv_write(evidence / 'missing_cells.csv', ['table', 'source_country', 'year', 'csv_line', 'token', 'kind'], missing)
    csv_write(evidence / 'excluded_countries.csv', ['table', 'source_country', 'observations', 'reason'], excluded)
    csv_write(evidence / 'coverage.csv', list(summary[0]), summary)
    report = ['# Sovereign debt import preparation', '', manifest['verification'], '',
              'All tables contain the configured 188-country roster. Countries without supplied data contain SQL NULL.',
              'Earliest and MostRecent contain first and last existing observation values, not years.',
              'No scaling, interpolation, aggregation, allocation, rounding or zero filling was applied.',
              'Original pre-IFs country labels are not available; identity mapping uses the supplied Country column.',
              'IFs settings follow the user-corrected SDebt metadata profile. Remaining unspecified fields stay NULL.',
              'Aggregation labels and Decimal Places are metadata only; no aggregation or rounding is performed.',
              'The supplied CSVs are the observation authority; this checks formatting and exact copying, not upstream historical territorial processing.', '',
              'SDR unit evidence: Afghanistan 2009 = 155.314267 and Albania 2009 = 46.45026 match published cumulative allocations of 155,314,267 and 46,450,260 SDRs. Therefore the supplied values are millions of SDRs.',
              'https://www.imf.org/external/np/tre/sdr/proposal/2009/pdf/0709.pdf', '',
              '| Table | Year columns | Countries with observations | Observations | Units |', '|---|---|---:|---:|---|']
    for s in summary:
        report.append(f"| {s['table']} | {s['year_columns']} | {s['countries_with_observations']} | {s['observations']} | {s['units']} |")
    report.extend(['', '## Exclusions', '', 'Source countries outside the configured IFs roster are omitted without assigning their values to another country:'])
    for table, *_ in specs:
        report.append('- ' + table + ': ' + ', '.join(r['source_country'] for r in excluded if r['table'] == table))
    report.extend(['', 'Detailed coverage, missing source tokens, exclusions, hashes, recipes and source-bound concordance bundles accompany this report.',
                   'This package has not been merged into a delivery or baseline.'])
    (args.output / 'Preparation Report.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    shutil.copy2(Path(__file__), evidence / Path(__file__).name)
    print(json.dumps(manifest, indent=2))

if __name__ == '__main__':
    main()

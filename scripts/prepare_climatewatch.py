"""Prepare the locally supplied Climate Watch historical-emissions release.

Requires openpyxl for reading the indicator-scope workbook; no network access.
All source-specific decisions are frozen in the fingerprint-bound profile.
"""
import argparse
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import zipfile

_SUPPORT = Path(__file__).resolve().parent / 'support'
REPO = _SUPPORT if (_SUPPORT / 'src').exists() else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))
from ifs_pipeline.concordance import lookup_index, normalize_name
from ifs_pipeline.preparation_paths import create_preparation_workspace


def digest(path):
    with Path(path).open('rb') as f:
        h = hashlib.sha256()
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
        return h.hexdigest()


def q(value):
    return '"' + value.replace('"', '""') + '"'


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')


def write_csv(path, rows, fields=None):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def number(token):
    """Preserve blanks, zero, negative sinks, and exact decimal representation."""
    if not token.strip():
        return None
    if not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', token.strip()):
        raise ValueError(f'Unrecognized observation token: {token!r}')
    try:
        value = float(token)
        exact = Decimal(token)
    except (ValueError, InvalidOperation) as exc:
        raise ValueError(token) from exc
    if not math.isfinite(value) or Decimal(str(value)) != exact:
        raise ValueError(f'Precision loss/nonfinite: {token}')
    if exact == exact.to_integral_value() and Decimal(value) != exact:
        raise ValueError(f'Integer precision loss: {token}')
    return value


def nested_bytes(source, archive, member):
    with zipfile.ZipFile(source / 'ClimateWatch_AllData.zip') as outer:
        with zipfile.ZipFile(io.BytesIO(outer.read(archive))) as inner:
            return inner.read(member)


def identity_labels(source):
    data = nested_bytes(source, 'ClimateWatch_SocioEconomics.zip', 'CW_gdp.csv')
    rows = list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'), newline='')))
    labels = {}
    for row in rows:
        code, name = row['Country Code'], row['Country']
        if code in labels and labels[code][0] != name:
            raise ValueError(f'Conflicting source names: {code}')
        labels[code] = (name, 'ClimateWatch_AllData.zip/ClimateWatch_SocioEconomics.zip/CW_gdp.csv')
    data = nested_bytes(source, 'ClimateWatch_NDC_Tracker.zip', 'CW_NDC_tracker.csv')
    for row in csv.DictReader(io.StringIO(data.decode('utf-8-sig'), newline='')):
        if row['ISO'] == 'VAT':
            labels['VAT'] = (row['Country'], 'ClimateWatch_AllData.zip/ClimateWatch_NDC_Tracker.zip/CW_NDC_tracker.csv')
    return labels


def concordance(codes, labels, index, canonical, profile):
    result, audit, targets = {}, [], {}
    for code in sorted(codes):
        if code not in labels:
            raise ValueError(f'Missing source code/name evidence: {code}')
        name, location = labels[code]
        lookup_name = profile['reviewed_name_spellings'].get(name, name)
        hits = index.get(normalize_name(lookup_name), set())
        if code in profile['excluded_entities']:
            if name != profile['excluded_entities'][code] or hits:
                raise ValueError(f'Exclusion identity changed: {code}')
            target = None
            method = 'reviewed aggregate/non-IFs entity; no territorial arithmetic'
        else:
            if len(hits) != 1:
                raise ValueError(f'Unresolved source identity: {code} {name}')
            target = next(iter(hits))
            if target in targets:
                raise ValueError(f'Converging source identities: {targets[target]} and {code}')
            targets[target] = code
            method = 'supplied code-to-name evidence + pinned DataGator name alias'
            if lookup_name != name:
                method += ' + reviewed encoding/spelling variant'
        result[code] = target
        audit.append({'original_name': code, 'source_country_name': name,
                      'matched_name': canonical.get(target, ''), 'FIPS_CODE': target or '',
                      'name_evidence': location, 'lookup_name': lookup_name, 'method': method})
    return result, audit


def index_rows(rows):
    indexed = {}
    for line, row in enumerate(rows, 2):
        if row['Source'] != 'Climate Watch':
            raise ValueError(f'Unexpected dataset at row {line}')
        key = (row['Country'], row['Sector'], row['Gas'])
        if key in indexed:
            raise ValueError(f'Duplicate source key: {key}')
        indexed[key] = (line, row)
    return indexed


def main():
    from openpyxl import load_workbook
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ['source', 'base', 'profile', 'guide']:
        ap.add_argument('--' + name, type=Path, required=True)
    ap.add_argument('--output', type=Path)
    a = ap.parse_args()
    a.source, a.base = a.source.resolve(), a.base.resolve()
    profile = json.loads(a.profile.read_text(encoding='utf-8'))
    for name, expected in profile['source_sha256'].items():
        if digest(a.source / name) != expected:
            raise ValueError(f'Source changed: {name}')
    base_hashes = {str(a.base / n): digest(a.base / n) for n in ['DataDict.db', 'IFsHistSeries.db']}
    if base_hashes != profile['base_sha256']:
        raise ValueError('Base changed; repeat source-specific metadata review')
    if digest(REPO / 'reference/datagator/country_data.json') != profile['lookup_sha256']:
        raise ValueError('Pinned country lookup changed')
    hist = sqlite3.connect((a.base / 'IFsHistSeries.db').as_uri() + '?mode=ro', uri=True)
    bc = sqlite3.connect((a.base / 'DataDict.db').as_uri() + '?mode=ro', uri=True)
    bc.row_factory = sqlite3.Row
    canonical = dict(hist.execute('SELECT FIPS_CODE, Country FROM SeriesPopulation'))
    if len(canonical) != 188:
        raise ValueError('Unexpected selected base country roster')
    index, _ = lookup_index(json.loads((REPO / 'reference/datagator/country_data.json').read_text()), canonical)
    w = load_workbook(a.source / profile['scope_workbook'], read_only=True, data_only=True)
    rr = list(w.active.values)
    old = [dict(zip(rr[0], r)) for r in rr[1:] if any(v is not None for v in r)]
    w.close()
    specs = profile['tables']
    if {(s['table'], s['variable']) for s in specs} != {(r['Table'], r['Variable']) for r in old} or len(specs) != len(old):
        raise ValueError('Profile does not cover the exact requested scope')
    raw_csv = a.source / 'CW_HistoricalEmissions_ClimateWatch.csv'
    with raw_csv.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        years = [k for k in reader.fieldnames if re.fullmatch(r'\d{4}', k)]
        rows = list(reader)
    if years != [str(y) for y in range(1990, 2024)]:
        raise ValueError('Release layout changed')
    # Verify the two redundant supplied archive copies agree with the loose files.
    with zipfile.ZipFile(a.source / 'ClimateWatch_HistoricalEmissions.zip') as z:
        for name in ['CW_HistoricalEmissions_ClimateWatch.csv', 'metadata.csv']:
            if z.read(name) != (a.source / name).read_bytes():
                raise ValueError(f'Conflicting loose/archive source: {name}')
    with zipfile.ZipFile(a.source / 'ClimateWatch_AllData.zip') as z:
        if z.read('ClimateWatch_HistoricalEmissions.zip') != (a.source / 'ClimateWatch_HistoricalEmissions.zip').read_bytes():
            raise ValueError('Conflicting nested historical archive')
    indexed = index_rows(rows)
    mapping, identities = concordance({r['Country'] for r in rows}, identity_labels(a.source), index, canonical, profile)
    if {(r['Sector'], r['Gas']) for r in rows} != {(s['sector'], s['gas']) for s in specs}:
        raise ValueError('Source gas/sector scope changed')
    # Check all tokens before creating a deliverable.
    for row in rows:
        for year in years:
            number(row[year])
    out, inputs = create_preparation_workspace(a.source, a.output)
    evidence = out / 'Working Files'
    evidence.mkdir()
    frozen = evidence / 'Original download'
    input_hashes = {str(f): digest(f) for f in inputs}
    for f in inputs:
        dest = frozen / f.relative_to(a.source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest)
        assert digest(dest) == input_hashes[str(f)]
    shutil.copy2(a.profile, evidence / 'climatewatch.profile.json')
    shutil.copy2(__file__, evidence / 'prepare_climatewatch.py')
    for module in (REPO / 'src/ifs_pipeline').rglob('*.py'):
        dest = evidence / 'support' / 'src' / 'ifs_pipeline' / module.relative_to(REPO / 'src/ifs_pipeline')
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(module, dest)
    shutil.copytree(REPO / 'reference/datagator', evidence / 'support/reference/datagator')
    shutil.copytree(REPO / 'reference/datagator', evidence / 'datagator')
    guides = evidence / 'source-guides'
    guides.mkdir()
    shutil.copy2(a.guide, guides / 'SOURCE_GUIDE.md')
    (evidence / 'REPRODUCE.md').write_text(
        '# Reproduce this preparation\n\nUse Python with openpyxl installed. From this Working Files directory, run:\n\n'
        '```powershell\npython prepare_climatewatch.py --source "Original download" --base "' + str(a.base) +
        '" --profile "climatewatch.profile.json" --guide "source-guides/SOURCE_GUIDE.md" --output "NEW_ABSOLUTE_OUTPUT_PATH"\n```\n\n'
        'Replace NEW_ABSOLUTE_OUTPUT_PATH with a fresh directory outside this frozen evidence package. The frozen support modules and DataGator reference are used. '
        'Changed source/base/lookup fingerprints fail; never overwrite the existing import.\n', encoding='utf-8')
    write_csv(evidence / 'country_concordance.csv', identities)
    write_csv(evidence / 'master.csv', [{'FIPS_CODE': c, 'Country': n} for c, n in canonical.items()])
    write_json(evidence / 'table_mapping.json', specs)
    write_json(evidence / 'old_workbook_metadata.json', old)
    schema = list(bc.execute('PRAGMA table_info(DataDict)'))
    fields = [r['name'] for r in schema]
    schema_sql = 'CREATE TABLE DataDict (' + ','.join(q(r['name']) + ' ' + r['type'] for r in schema) + ')'
    write_json(evidence / 'datadict_schema.json', [dict(r) for r in schema])
    base_metadata = {}
    reviewed_metadata = {}
    coverage, reviews, changes, screens, compare, exclusions = [], [], [], [], [], []
    absent = sorted(set(canonical) - {v for v in mapping.values() if v})
    write_json(evidence / 'absent_countries.json', {k: canonical[k] for k in absent})
    for key, (line, row) in indexed.items():
        if mapping[row['Country']] is None:
            exclusions.append({'csv_row': line, **row, 'populated_cells': sum(number(row[y]) is not None for y in years),
                               'reason': 'Explicitly reviewed source entity outside selected IFs roster'})
    write_csv(evidence / 'excluded_rows.csv', exclusions)
    db = out / 'IFsDataImport_ClimateWatch_2026.db'
    conn = sqlite3.connect(db)
    conn.execute(schema_sql)
    audit_path = evidence / 'observation_audit.csv'
    with audit_path.open('w', encoding='utf-8-sig', newline='') as af:
        audit = csv.DictWriter(af, fieldnames=['table','FIPS_CODE','Country','year','source_country_code','csv_row','sector','gas','raw_token','output_value','operation'])
        audit.writeheader()
        for spec in specs:
            table = spec['table']
            bm = bc.execute('SELECT * FROM DataDict WHERE [Table]=? AND Variable=?', (table, spec['variable'])).fetchall()
            if len(bm) != 1:
                raise ValueError(f'Base metadata identity not unique: {table}')
            base_metadata[table] = dict(bm[0])
            m = dict(bm[0])
            m.update(spec['metadata_updates'])
            reviewed_metadata[table] = m
            cols = ['Country','FIPS_CODE'] + years + ['Earliest','MostRecent']
            conn.execute('CREATE TABLE ' + q(table) + '(' + ','.join(q(c) + (' VARCHAR(255)' if j < 2 else ' DOUBLE(53)') for j,c in enumerate(cols)) + ')')
            selected = {}
            for (source_code, sector, gas), (line, row) in indexed.items():
                code = mapping[source_code]
                if code and (sector, gas) == (spec['sector'], spec['gas']):
                    if code in selected:
                        raise ValueError(f'Duplicate target row: {table}/{code}')
                    selected[code] = (line, row)
            observed, zeros, countries, missing_source = 0, 0, 0, 0
            vals_for_compare = {}
            for code, name in canonical.items():
                record = selected.get(code)
                values = []
                for year in years:
                    line, row = record if record else ('', {})
                    token = row.get(year, '')
                    value = number(token)
                    values.append(value)
                    kind = 'exact numeric text to DOUBLE' if value is not None else ('source blank to SQL NULL' if record else 'absent source country/indicator to SQL NULL')
                    audit.writerow({'table':table,'FIPS_CODE':code,'Country':name,'year':year,'source_country_code':row.get('Country',''),
                                    'csv_row':line,'sector':spec['sector'],'gas':spec['gas'],'raw_token':token,'output_value':value,'operation':kind})
                    if value is None:
                        missing_source += int(bool(record))
                    else:
                        observed += 1
                        zeros += value == 0
                        vals_for_compare[(code, year)] = value
                        if value < 0:
                            screens.append({'table':table,'FIPS_CODE':code,'year':year,'value':value,
                                            'finding':'negative value; land-use removals or net totals can be negative' if 'LULUCF' in spec['sector'] or 'Land Use' in spec['sector'] else 'negative emissions outside land-use/net totals',
                                            'action':'retained source value'})
                nn = [v for v in values if v is not None]
                countries += bool(nn)
                output_row = [name, code] + values + ([nn[0], nn[-1]] if nn else [None, None])
                conn.execute('INSERT INTO ' + q(table) + ' VALUES (' + ','.join('?' for _ in output_row) + ')', output_row)
            conn.execute('INSERT INTO DataDict (' + ','.join(q(f) for f in fields) + ') VALUES (' + ','.join('?' for _ in fields) + ')', [m[f] for f in fields])
            for field in fields:
                value, prev = m[field], bm[0][field]
                blank_reason = profile['blank_field_reasons'].get(field)
                disposition = 'intentionally blank' if value is None and blank_reason else ('supported value' if field in spec['metadata_updates'] else 'preserve existing')
                why = profile['metadata_reasons'].get(field, blank_reason or 'Matching Table/Variable in supplied workbook and selected base; established table-specific IFs setting retained.')
                reviews.append({'Table':table,'Variable':m['Variable'],'field':field,'value_json':json.dumps(value,ensure_ascii=False),'disposition':disposition,'basis':why})
                if value != prev:
                    changes.append({'Table':table,'field':field,'base_json':json.dumps(prev,ensure_ascii=False),'prepared_json':json.dumps(value,ensure_ascii=False)})
            # Read-only overlap evidence; never feeds the incoming observations.
            hist.row_factory = sqlite3.Row
            br = list(hist.execute('SELECT * FROM ' + q(table)))
            overlap = equal = added = 0
            relative_changes = []
            baseline_values = {(r['FIPS_CODE'],y):r[y] for r in br for y in years if y in r.keys()}
            for key, value in vals_for_compare.items():
                bv = baseline_values.get(key)
                if bv is None:
                    added += 1
                else:
                    overlap += 1
                    equal += bv == value
                    if bv != 0:
                        relative_changes.append(abs((value-bv)/bv))
            removed = sum(v is not None and key not in vals_for_compare for key,v in baseline_values.items())
            compare.append({'table':table,'overlapping_observations':overlap,'exact_equal':equal,'revised':overlap-equal,'incoming_only':added,
                            'base_values_without_incoming_value':removed,'median_abs_relative_revision': sorted(relative_changes)[len(relative_changes)//2] if relative_changes else None})
            coverage.append({'table':table,'country_rows':188,'source_country_rows':len(selected),'countries_with_observations':countries,'year_columns':34,
                             'first_year':1990,'last_year':2023,'observations':observed,'zero_values':zeros,'source_blank_cells':missing_source,
                             'padding_cells':(188-len(selected))*34,'null_cells':188*34-observed,'status':'ready_with_findings'})
    conn.commit()
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    conn.close()
    for name, data in [('metadata_review',reviews),('metadata_changes',changes),('coverage',coverage),('baseline_comparison',compare),('screening_findings',screens)]:
        if data:
            write_csv(evidence / (name + '.csv'), data)
    write_json(evidence / 'base_metadata.json', base_metadata)
    write_json(evidence / 'reviewed_metadata.json', reviewed_metadata)
    # Independent reread: resolve saved rows back to raw source keys, checking every cell.
    inverse = {v:k for k,v in mapping.items() if v}
    checked = 0
    with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as check:
        check.row_factory = sqlite3.Row
        assert check.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert len(list(check.execute('SELECT * FROM DataDict'))) == len(specs)
        for spec in specs:
            saved = list(check.execute('SELECT * FROM ' + q(spec['table'])))
            assert {(r['FIPS_CODE'],r['Country']) for r in saved} == set(canonical.items()) and len(saved) == 188
            for r in saved:
                source = indexed.get((inverse.get(r['FIPS_CODE']),spec['sector'],spec['gas']))
                expected = [float(source[1][y]) if source and source[1][y].strip() else None for y in years]
                assert [r[y] for y in years] == expected
                nn = [v for v in expected if v is not None]
                assert (r['Earliest'],r['MostRecent']) == ((nn[0],nn[-1]) if nn else (None,None))
                checked += len(years)
            types = [r['type'] for r in check.execute('PRAGMA table_info(' + q(spec['table']) + ')')]
            assert types == ['VARCHAR(255)'] * 2 + ['DOUBLE(53)'] * 36
            assert dict(check.execute('SELECT * FROM DataDict WHERE [Table]=?',(spec['table'],)).fetchone()) == reviewed_metadata[spec['table']]
        assert [(r['name'],r['type']) for r in check.execute('PRAGMA table_info(DataDict)')] == [(r['name'],r['type']) for r in schema]
    for path, h in {**input_hashes, **base_hashes}.items():
        assert digest(Path(path)) == h
    summary = {'tables':len(specs),'country_rows_per_table':188,'mapped_source_countries':len(inverse),'absent_countries':{k:canonical[k] for k in absent},
               'observations':sum(r['observations'] for r in coverage),'source_blank_cells':sum(r['source_blank_cells'] for r in coverage),
               'padding_cells':sum(r['padding_cells'] for r in coverage),'negative_values_retained':len(screens),
               'excluded_source_entities':sum(v is None for v in mapping.values()),'excluded_rows':len(exclusions),
               'excluded_populated_cells':sum(r['populated_cells'] for r in exclusions),'checked_grid_cells':checked}
    issues = profile['issues']
    write_json(evidence / 'issues.json', issues)
    guide_snapshot = {'path':str(guides/'SOURCE_GUIDE.md'),'sha256':digest(guides/'SOURCE_GUIDE.md'),'role':'used', 'maintained_path':str(a.guide)}
    manifest = {'stage':'preprocessing','status':'completed_with_findings','data_source_policy':'provided_files_only',
                'source_files':input_hashes,'base_files':base_hashes,'output':{'path':str(db),'sha256':digest(db)},'summary':summary,
                'verified':{'source_base_unchanged':True,'saved_values_nulls_endpoints_metadata_schema':True,'integrity_check':'ok'},
                'source_guide_snapshot':guide_snapshot,'next_stage':'validation','merge_performed':False}
    write_json(evidence/'preparation_manifest.json',manifest)
    report = ['# Climate Watch preprocessing','',f"Prepared {len(specs)} tables with {summary['observations']:,} numeric observations for 1990–2023.",
              '', 'All tables contain 188 canonical IFs country rows. The source supplies 183 matched countries; observed coverage differs by indicator. No merge was performed. Original downloads and the IFs 8.73 base remain unchanged.',
              '', '## Selection and country concordance',
              'The old DataDict workbook defines the 57-table scope. Only the Climate Watch historical-emissions dataset supplies observations. PRIMAP, UNFCCC, GCP, US state, pathway and pledge datasets were not substituted. Both supplied ZIP copies agree byte-for-byte with the selected CSV and metadata.',
              'Country codes were linked to country names in the supplied nested GDP file, with VAT identified by the supplied NDC tracker. These names were resolved with the pinned DataGator lookup. MNG maps to Mongolia (IFs MON); MNE maps to Montenegro (IFs MNG). PRK uses a reviewed apostrophe variant. There are no converging country identities or duplicate observation keys.',
              f"Excluded {len(exclusions):,} source rows ({summary['excluded_populated_cells']:,} populated cells) for 16 reviewed entities outside the IFs roster. Every excluded row is preserved. No sums, allocations, splits or territorial repairs were made.",
              'Absent IFs countries: ' + ', '.join(canonical[k] for k in absent) + '. Their rows contain SQL NULL throughout.',
              '', '## Units, metadata and limitations',
              'Values are copied without scaling or rounding. Blank source cells become SQL NULL; zero and negative source values are retained. Earliest/MostRecent copy the first/last existing values, not years.',
              'Stored units retain the matching base wording for million tonnes of CO2 equivalent. The raw CSV has no explicit unit column: scale continuity is an inference from the same-source supplied DataDict and matching base, supported by overlap comparisons. The supplied metadata explicitly confirms CO2-equivalent treatment and the AR5 100-year GWP basis, but does not independently spell out the million-tonne scale.',
              'Source metadata identifies the January 2026 release and 1990–2023 coverage. It reports an AR5 basis change effective September 16, 2025. The older base does not identify its GWP basis, so historical revisions cannot be attributed solely to this change. No GWP conversion was performed.',
              'All 31 DataDict fields were reviewed per table. Existing classification, aggregation/disaggregation and IFs usage settings are retained. Source descriptions, years, attribution, source names and update date are refreshed. Generic stale three-year-lag wording and NS notes are replaced by supported release information and source cautions.',
              'The supplied license description says CC BY-NC 4.0 while its terms link points to CC BY 4.0; this conflict is recorded and not resolved by fetching. The base Proprietary flag is retained as IFs configuration, not a legal determination.',
              'Historical territorial coverage remains publisher-defined. Source identity concordance does not establish time-varying boundaries or supply missing IFs territories.',
              '', '## Verification and coverage',
              f"Checked all {checked:,} saved country/year cells against original source keys, including NULLs, plus endpoints, metadata, schema and SQLite integrity. Preparation checks passed; separate validation is the next stage.",
              '', '| Table | Countries with data | Observations | Source blanks | Padding cells |','|---|---:|---:|---:|---:|']
    report += [f"| {r['table']} | {r['countries_with_observations']} | {r['observations']:,} | {r['source_blank_cells']:,} | {r['padding_cells']:,} |" for r in coverage]
    report += ['', 'Evidence: Working Files/observation_audit.csv, country_concordance.csv, excluded_rows.csv, metadata_review.csv, baseline_comparison.csv, screening_findings.csv and preparation_manifest.json.',
               'Source guide created: ' + str(a.guide) + '; the used version is frozen under Working Files/source-guides.', '']
    (out/'Preparation Report.md').write_text('\n'.join(report),encoding='utf-8')
    artifacts = [{'path':str(f),'sha256':digest(f)} for f in evidence.rglob('*') if f.is_file()]
    write_json(evidence/'handoff.json', {'schema_version':1,'stage':'preprocessing','status':'completed_with_findings','created_at':date.today().isoformat(),
               'authorized_scope':'57 Table/Variable entries in supplied DataDict; prepare only','data_source_policy':'provided_files_only','online_fetching_authorization':None,
               'source_directory':str(a.source),'preprocessing_output_directory':str(out),'base_directory':str(a.base),'delivery_directory':None,
               'input_artifacts':[{'path':p,'sha256':h} for p,h in input_hashes.items()], 'output_artifacts':[manifest['output']],
               'evidence_artifacts':artifacts,'indicator_scope_artifact':str(evidence/'table_mapping.json'),
               'inference_evidence':[profile['metadata_reasons']['Units']], 'source_guide_review':{'source_id':'climatewatch','status':'created','maintained_path':str(a.guide),'open_issues':issues},
               'source_guide_snapshots':[guide_snapshot],'native_results':{'preparation_manifest':str(evidence/'preparation_manifest.json')},
               'table_outcomes':coverage,'coverage_summary':summary,'issues_file':str(evidence/'issues.json'),'repull_recommendations':[],
               'next_stage':'validation','next_actions':['Validate prepared import and review source/GWP, geographic and metadata caveats before consolidation.']})
    print(json.dumps({'output':str(out),'database':str(db),'summary':summary},indent=2))


if __name__ == '__main__':
    main()

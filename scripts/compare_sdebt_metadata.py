"""Record the user's SDebt metadata corrections without modifying either input."""
import argparse
import csv
import hashlib
import json
import sqlite3
from pathlib import Path


def read(path):
    con = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    return con


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original', type=Path)
    parser.add_argument('corrected', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.original, args.corrected)}
    old, new = read(args.original), read(args.corrected)
    schemas = [dict(c.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")) for c in (old, new)]
    assert schemas[0] == schemas[1], 'Schema changes require separate review'
    metadata = [{(r['Table'], r['Variable']): dict(r) for r in c.execute('SELECT * FROM DataDict')} for c in (old, new)]
    assert metadata[0].keys() == metadata[1].keys()
    changes, tables = [], []
    for table, variable in sorted(metadata[0]):
        quoted = '"' + table.replace('"', '""') + '"'
        data = [[tuple(r) for r in c.execute('SELECT * FROM ' + quoted + ' ORDER BY Country,FIPS_CODE')] for c in (old, new)]
        assert data[0] == data[1], f'Series data changed: {table}'
        columns = [r[1] for r in old.execute('PRAGMA table_info(' + quoted + ')')]
        years = [i for i, name in enumerate(columns) if len(name) == 4 and name.isdigit()]
        tables.append({'table': table, 'rows': len(data[0]), 'observations': sum(row[i] is not None for row in data[0] for i in years), 'all_cells_equal': True})
        for field, before in metadata[0][table, variable].items():
            after = metadata[1][table, variable][field]
            if before != after:
                changes.append({'table': table, 'variable': variable, 'field': field,
                                'before_json': json.dumps(before), 'after_json': json.dumps(after)})
    with (args.output / 'metadata_changes.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['table', 'variable', 'field', 'before_json', 'after_json'])
        writer.writeheader()
        writer.writerows(changes)
    result = {'input_sha256': hashes, 'schema_equal': True, 'tables': tables, 'changed_metadata_cells': len(changes)}
    (args.output / 'comparison.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    (args.output / 'corrected_metadata.json').write_text(json.dumps(list(metadata[1].values()), indent=2), encoding='utf-8')
    profile = {'schema_version': 1, 'purpose': 'Source-specific metadata overrides for raw SDebt preparation; not a consolidation recipe.',
               'reference_file': args.corrected.name, 'reference_sha256': hashes[str(args.corrected)],
               'tables': {table: {row['field']: json.loads(row['after_json']) for row in changes if row['table'] == table}
                          for table, _ in sorted(metadata[0])}}
    (args.output / 'sdebt.metadata.json').write_text(json.dumps(profile, indent=2) + '\n', encoding='utf-8')
    old.close()
    new.close()
    assert hashes == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.original, args.corrected)}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

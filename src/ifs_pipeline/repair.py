"""Audited import normalization; immutable originals and exact post-repair inputs."""
from collections import defaultdict
from contextlib import closing
import json
from pathlib import Path
import re
import shutil
import sqlite3
from collections import Counter

from .concordance import lookup_index, normalize_name, reviewed_mappings
from .delivery_core import DATABASES, exact_series, fingerprints, resolve_folder
from .model import KEYS, META
from . import normalization
from .normalization import (declared_type, evidence_value, numeric_value, simple_table,
                            metadata_reference, plan_metadata, verify_metadata, metadata_issues)
from .storage import (PipelineError, check_idle_database, columns, now, q, read_json,
                      readonly, records, sha256, tables, write_json)


def missing(value):
    return value is None or isinstance(value, str) and not value.strip()


def identity(name, code, canonical, aliases, reviewed=None):
    """A valid code anchors a typo, but never overrides contradictory evidence."""
    if not (missing(name) or isinstance(name, str)) or not (missing(code) or isinstance(code, str)):
        raise PipelineError('Country identities must be text or missing')
    label = name.strip() if isinstance(name, str) else ''
    token = code.strip() if isinstance(code, str) else ''
    codes = {normalize_name(c): c for c in canonical}
    match = codes.get(normalize_name(token))
    choices = aliases.get(normalize_name(label), set()) if label else set()
    canonical_names = {normalize_name(n): c for c, n in canonical.items()}
    approved = (reviewed or {}).get(label)
    if reviewed is not None and label in reviewed and approved is None:
        raise PipelineError(f'Unresolved reviewed mapping: {name!r}')
    if approved:
        if match and approved != match or choices and choices != {approved}:
            raise PipelineError(f'Reviewed mapping conflicts with country identity: {name!r}/{code!r}')
        choices = {approved}
    elif choices and normalize_name(label) not in canonical_names:
        # Some pinned aliases describe old borders or compound territories.
        # A lookup suggestion alone cannot authorize reassignment of observations.
        raise PipelineError(f'DataGator alias requires source-specific identity review: {name!r}')
    if len(choices) > 1 or match and choices and choices != {match}:
        raise PipelineError(f'Conflicting or ambiguous identity: {name!r}/{code!r}')
    if match:
        return (canonical[match], match), 'canonical_code'
    if token:
        raise PipelineError(f'Unknown nonempty country code: {name!r}/{code!r}')
    if len(choices) == 1:
        match = next(iter(choices))
        return (canonical[match], match), 'exact_name_or_datagator_alias'
    raise PipelineError(f'Unresolved identity: {name!r}/{code!r}')


def surplus_rows(source, keys, years, canonical, aliases, reviewed=None):
    """User policy: exclude extras only after a complete, unique source roster.

    Padding generated later cannot establish completeness. Recognizable conflicts
    and duplicate canonical entities are not surplus identities. No dyadic rule is
    inferred from a monadic roster.
    """
    if len(keys) != 2:
        return {}
    present, candidates = Counter(), []
    codes = {normalize_name(c) for c in canonical}
    for index, row in enumerate(source, 1):
        if all(missing(v) for v in row.values()):
            continue
        name, code = (row[k] for k in keys)
        try:
            key, _ = identity(name, code, canonical, aliases, reviewed)
            present[key] += 1
        except PipelineError:
            label = name.strip() if isinstance(name, str) else ''
            token = code.strip() if isinstance(code, str) else ''
            if normalize_name(token) in codes or aliases.get(normalize_name(label)) or label in (reviewed or {}):
                continue  # Main planner reports the conflict; never discard it.
            candidates.append((index, row))
    expected = {(name, code): 1 for code, name in canonical.items()}
    if dict(present) != expected:
        return {}
    return {index: dict(rule='exclude_extra_after_complete_roster', source_row=index,
                        before={field: evidence_value(value) for field, value in row.items()},
                        reason='All canonical IFs entities already present exactly once in source',
                        canonical_entities=len(canonical),
                        populated_year_cells=sum(not missing(row[y]) for y in years))
            for index, row in candidates}


def plan_table(conn, table, canonical, aliases, reviewed=None):
    info = columns(conn, table)
    names = [c['name'] for c in info]
    keys = next((k for k in KEYS.values() if set(k).issubset(names)), None)
    years = sorted(n for n in names if re.fullmatch(r'[12][0-9]{3}', n))
    if keys is None or not years or set(names) - set(keys) - set(years) - set(META):
        raise PipelineError('Unsupported schema: expected country keys, years and optional endpoints')
    simple_table(conn, table)
    source = records(conn, table)
    exclusions = surplus_rows(source, keys, years, canonical, aliases, reviewed)
    planned, events, lineage, seen = [], [], [], set()
    out_names = [*keys, *years, *META]
    desired = [(n, 'VARCHAR(255)' if n in keys else 'DOUBLE(53)') for n in out_names]
    if [(c['name'], declared_type(c['type'])) for c in info] != desired:
        events.append(dict(rule='normalize_series_schema', before=info, after=desired))
    for endpoint in META:
        if endpoint not in names:
            events.append(dict(rule='add_endpoint_column', field=endpoint))
    for index, row in enumerate(source, 1):
        if index in exclusions:
            events.append(exclusions[index])
            continue
        # Only entirely empty records qualify as padding. Unknown entities with
        # empty observations are still identities, not disposable padding.
        if all(missing(v) for v in row.values()):
            events.append(dict(rule='remove_empty_padding', source_row=index, before=row))
            continue
        raw_key = tuple(row[k] for k in keys)
        fixed_key, methods = [], []
        for offset in range(0, len(keys), 2):
            pair, method = identity(raw_key[offset], raw_key[offset + 1], canonical, aliases, reviewed)
            fixed_key.extend(pair)
            methods.append(method)
        fixed_key = tuple(fixed_key)
        if fixed_key in seen:
            raise PipelineError(f'Duplicate/converging identity: {fixed_key!r}; no automatic deduplication')
        seen.add(fixed_key)
        if raw_key != fixed_key:
            events.append(dict(rule='canonical_identity', source_row=index, before=raw_key,
                               after=fixed_key, evidence=methods))
        values = []
        for year in years:
            value = row[year]
            try:
                value, rule = numeric_value(value)
            except PipelineError as exc:
                raise PipelineError(f'Row {index}/{year}: {exc}') from exc
            if rule:
                events.append(dict(rule=rule, source_row=index, field=year,
                                   before=row[year] if rule == 'blank_to_null' else evidence_value(row[year]),
                                   after=value, original_key=raw_key, output_key=fixed_key))
            values.append(value)
        observed = [v for v in values if v is not None]
        endpoints = [observed[0], observed[-1]] if observed else [None, None]
        for field, value in zip(META, endpoints):
            if field in names and row[field] != value:
                events.append(dict(rule='copy_endpoint_value', source_row=index, field=field,
                                   before=repr(row[field]), after=value))
        planned.append([*fixed_key, *values, *endpoints])
        lineage.append(dict(source_row=index, original_key=raw_key, output_key=fixed_key))
    # Do not turn an empty or wholly unidentified source into a seemingly valid table.
    if not planned:
        raise PipelineError('No identified source rows; cannot repair by manufacturing a roster')
    if len(keys) == 2:
        for code, name in sorted(canonical.items()):
            if (name, code) not in seen:
                planned.append([name, code] + [None] * (len(years) + 2))
                events.append(dict(rule='add_missing_country_null_row', after=[name, code]))
    return dict(keys=keys, years=years, names=out_names, rows=planned, events=events,
                lineage=lineage, source_rows=len(source))


def verify_table(source, result, table, plan, canonical, aliases=None, reviewed=None):
    """Verify retained cells and independently prove eligibility of excluded rows."""
    raw = records(source, table)
    if aliases is None:
        aliases = {normalize_name(name): {code} for code, name in canonical.items()}
    exclusions = surplus_rows(raw, plan['keys'], plan['years'], canonical, aliases, reviewed)
    recorded = {e['source_row']: e for e in plan['events'] if e['rule'] == 'exclude_extra_after_complete_roster'}
    if recorded != exclusions:
        raise PipelineError('Excluded-row evidence does not match the complete source roster')
    if [(c['name'], declared_type(c['type'])) for c in columns(result, table)] != [
            (n, 'VARCHAR(255)' if n in plan['keys'] else 'DOUBLE(53)') for n in plan['names']]:
        raise PipelineError('Repaired series schema verification failed')
    actual = exact_series(result, table, canonical)
    links = {r['source_row']: tuple(r['output_key']) for r in plan['lineage']}
    if set(links) & set(exclusions):
        raise PipelineError('Excluded row also appears in retained lineage')
    if len(set(links.values())) != len(links):
        raise PipelineError('Repair lineage is not one-to-one')
    expected_keys = set(links.values())
    if len(plan['keys']) == 2:
        expected_keys = {(name, code) for code, name in canonical.items()}
    if set(actual.rows) != expected_keys or actual.years != plan['years']:
        raise PipelineError('Repaired roster/year verification failed')
    count = 0
    for index, row in enumerate(raw, 1):
        if index not in links:
            if index not in exclusions and not all(missing(v) for v in row.values()):
                raise PipelineError('Repair removed a nonempty row')
            continue
        cells = actual.rows[links[index]]
        for year in plan['years']:
            value = row[year]
            expected, rule = numeric_value(value)
            if cells[year] != expected:
                raise PipelineError(f'Repair differs from declared normalization at row {index}/{year}')
            count += expected is not None and rule is None
    for key in set(actual.rows) - set(links.values()):
        if any(v is not None for v in actual.rows[key].values()):
            raise PipelineError('Added roster row contains observations')
    for row in records(result, table):
        values = [row[y] for y in plan['years'] if row[y] is not None]
        if [row[k] for k in META] != ([values[0], values[-1]] if values else [None, None]):
            raise PipelineError('Endpoint verification failed')
    return count


def repair_imports(root, base, inputs, output, country_table='SeriesPopulation', use_datagator=True,
                   mapping=None, reviewed=False):
    """Stage revised full import copies. Does not register, order or merge them."""
    root, base, output = Path(root).resolve(), resolve_folder(base), Path(output).resolve()
    paths = [Path(p).resolve() for p in inputs]
    if bool(mapping) != bool(reviewed):
        raise PipelineError('A mapping needs explicit --reviewed identity review; --reviewed needs --mapping')
    if not paths or len({p.name.casefold() for p in paths}) != len(paths):
        raise PipelineError('Supply imports with unique filenames')
    if output.exists() or output == base or output.is_relative_to(base) or base.is_relative_to(output):
        raise PipelineError('Use a new output folder separate from the base')
    hashes = fingerprints(base)
    with readonly(base / DATABASES[1]) as conn:
        base_info, base_metadata = metadata_reference(conn)
    canonical = {}
    with readonly(base / DATABASES[0]) as conn:
        for row in records(conn, country_table):
            name, code = row.get('Country'), row.get('FIPS_CODE')
            if not isinstance(name, str) or not isinstance(code, str) or not name.strip() or not code.strip() or name != name.strip() or code != code.strip():
                raise PipelineError('Invalid baseline country roster')
            if code in canonical or name in canonical.values():
                raise PipelineError('Duplicate baseline country identity')
            canonical[code] = name
    if not canonical:
        raise PipelineError('Empty baseline roster')
    if len({normalize_name(c) for c in canonical}) != len(canonical) or len({normalize_name(n) for n in canonical.values()}) != len(canonical):
        raise PipelineError('Ambiguous normalized baseline country identities')
    aliases = defaultdict(set)
    for code, name in canonical.items():
        aliases[normalize_name(name)].add(code)
    from .resources import datagator_lookup
    lookup = datagator_lookup(root)
    provenance = lookup.with_name('provenance.json')
    reference_hashes = {}
    if use_datagator:
        reference_hashes = {lookup: sha256(lookup), provenance: sha256(provenance)}
        if read_json(provenance).get('local_sha256') != sha256(lookup):
            raise PipelineError('Datagator fingerprint differs from recorded provenance')
        aliases, _ = lookup_index(read_json(lookup), canonical)
    source_hashes = {}
    for path in paths:
        if path.suffix.lower() != '.db' or not path.name.lower().startswith('ifsdataimport'):
            raise PipelineError(f'Not a formatted import filename: {path.name}')
        if output == path or path.is_relative_to(output) or output.is_relative_to(path.parent):
            raise PipelineError('Preparation output must be outside the input import folder')
        check_idle_database(path)
        source_hashes[path.name] = sha256(path)
    mappings = None
    if mapping:
        mapping = Path(mapping).resolve()
        reference_hashes[mapping] = sha256(mapping)
        source_names = set()
        for path in paths:
            with readonly(path) as src:
                for table in tables(src):
                    if table.startswith('Series'):
                        names = {c['name'] for c in columns(src, table)}
                        for field in ('Country', 'Actor', 'Partner'):
                            if field in names:
                                source_names.update(r[0].strip() for r in src.execute(f'SELECT DISTINCT {q(field)} FROM {q(table)}') if isinstance(r[0], str) and r[0].strip())
        targets = defaultdict(set)
        for code, name in canonical.items():
            targets[normalize_name(name)].add(code)
            targets[normalize_name(code)].add(code)
        mappings = reviewed_mappings(mapping, targets, source_names)
    output.mkdir(parents=True)
    (output / 'originals').mkdir()
    (output / 'corrected').mkdir()
    (output / 'evidence').mkdir()
    write_json(output / 'master.json', canonical)
    if use_datagator:
        shutil.copy2(lookup, output / 'datagator.json')
        shutil.copy2(provenance, output / 'datagator-provenance.json')
    if mapping:
        shutil.copy2(mapping, output / 'reviewed_mapping.csv')
    for original, frozen_name in [(lookup, 'datagator.json'), (provenance, 'datagator-provenance.json'), (mapping, 'reviewed_mapping.csv')]:
        if original in reference_hashes and (sha256(original) != reference_hashes[original] or sha256(output / frozen_name) != reference_hashes[original]):
            raise PipelineError('Country reference/mapping changed while preparing')
    shutil.copy2(Path(__file__), output / 'repair-code.py')
    shutil.copy2(Path(normalization.__file__), output / 'normalization-code.py')
    write_json(output / 'schema-contract.json', dict(series_keys='VARCHAR(255)', series_values='DOUBLE(53)',
                                                   datadict=base_info, source='selected baseline DataDict.db'))
    manifest = dict(schema_version=2, created_at=now(), base=str(base), base_sha256=hashes,
                    country_table=country_table, country_count=len(canonical), status='preparing', imports=[])
    write_json(output / 'manifest.json', manifest)
    try:
        for path in paths:
            frozen = output / 'originals' / path.name
            shutil.copy2(path, frozen)
            if sha256(frozen) != source_hashes[path.name]:
                raise PipelineError('Input changed while freezing')
            target = output / 'corrected' / (path.stem + '_formatted.db')
            shutil.copy2(frozen, target)
            entry = dict(source=path.name, original_sha256=source_hashes[path.name], tables=[], corrected=None,
                         metadata={}, issues=[])
            manifest['imports'].append(entry)
            evidence_path = output / 'evidence' / (path.stem + '.jsonl')
            with readonly(frozen) as src, closing(sqlite3.connect(target)) as dst, dst, evidence_path.open('w', encoding='utf-8') as audit:
                dst.row_factory = sqlite3.Row
                if [r[0] for r in src.execute('PRAGMA integrity_check')] != ['ok']:
                    raise PipelineError(f'Invalid SQLite database: {path.name}')
                series = sorted(t for t in tables(src) if t.startswith('Series'))
                for table in series:
                    item = dict(table=table)
                    entry['tables'].append(item)
                    try:
                        plan = plan_table(src, table, canonical, aliases, mappings)
                        if not plan['events']:
                            item.update(status='unchanged')
                            continue
                        dst.execute('SAVEPOINT repair_table')
                        try:
                            dst.execute(f'DROP TABLE {q(table)}')
                            ddl = ','.join(q(n) + (' VARCHAR(255)' if n in plan['keys'] else ' DOUBLE(53)') for n in plan['names'])
                            dst.execute(f'CREATE TABLE {q(table)} ({ddl})')
                            dst.executemany(f'INSERT INTO {q(table)} VALUES ({",".join("?" for _ in plan["names"])})', plan['rows'])
                            count = verify_table(src, dst, table, plan, canonical, aliases, mappings)
                        except Exception:
                            dst.execute('ROLLBACK TO repair_table')
                            dst.execute('RELEASE repair_table')
                            raise
                        dst.execute('RELEASE repair_table')
                        item.update(status='repaired', source_rows=plan['source_rows'], output_rows=len(plan['rows']),
                                    observations_preserved=count, repairs=len(plan['events']))
                        excluded = [e for e in plan['events'] if e['rule'] == 'exclude_extra_after_complete_roster']
                        item.update(excluded_rows=len(excluded),
                                    excluded_populated_rows=sum(e['populated_year_cells'] > 0 for e in excluded),
                                    excluded_year_cells=sum(e['populated_year_cells'] for e in excluded))
                        for record in plan['events']:
                            audit.write(json.dumps(dict(table=table, kind='repair', **record), ensure_ascii=False, allow_nan=False) + '\n')
                        for record in plan['lineage']:
                            audit.write(json.dumps(dict(table=table, kind='lineage', **record), ensure_ascii=False) + '\n')
                    except (PipelineError, sqlite3.Error) as exc:
                        item.update(status='unresolved', reason=str(exc), owner_stage='preprocessing')
                        entry['issues'].append(dict(table=table, category='series', owner_stage='preprocessing', reason=str(exc)))
                try:
                    md_plan = plan_metadata(src, base_info, base_metadata)
                    if md_plan['events']:
                        dst.execute('SAVEPOINT repair_metadata')
                        try:
                            dst.execute('DROP TABLE DataDict')
                            ddl = ','.join(q(c['name']) + ' ' + c['type'] for c in base_info)
                            dst.execute(f'CREATE TABLE DataDict ({ddl})')
                            dst.executemany(f'INSERT INTO DataDict VALUES ({",".join("?" for _ in base_info)})', md_plan['rows'])
                            verify_metadata(dst, md_plan)
                        except Exception:
                            dst.execute('ROLLBACK TO repair_metadata')
                            dst.execute('RELEASE repair_metadata')
                            raise
                        dst.execute('RELEASE repair_metadata')
                        entry['metadata'].update(status='repaired', repairs=len(md_plan['events']))
                        for event in md_plan['events']:
                            audit.write(json.dumps(dict(table='DataDict', kind='repair', **event), ensure_ascii=False, allow_nan=False) + '\n')
                    else:
                        verify_metadata(dst, md_plan)
                        entry['metadata'].update(status='unchanged')
                except (PipelineError, sqlite3.Error) as exc:
                    entry['metadata'].update(status='unresolved', reason=str(exc), owner_stage='preprocessing')
                    entry['issues'].append(dict(table='DataDict', category='metadata_schema', owner_stage='preprocessing', reason=str(exc)))
                entry['issues'].extend(metadata_issues(dst, base_info, base_metadata))
                if [r[0] for r in dst.execute('PRAGMA integrity_check')] != ['ok']:
                    raise PipelineError('Repaired database integrity failure')
            if any(t['status'] == 'repaired' for t in entry['tables']) or entry['metadata']['status'] == 'repaired':
                with readonly(frozen) as src, readonly(target) as saved:
                    if tables(src) != tables(saved):
                        raise PipelineError('Preparation changed table inventory')
                    repaired = {t['table'] for t in entry['tables'] if t['status'] == 'repaired'}
                    for table in tables(src):
                        if table == 'DataDict' and entry['metadata']['status'] == 'repaired':
                            verify_metadata(saved, plan_metadata(src, base_info, base_metadata))
                        elif table in repaired:
                            plan = plan_table(src, table, canonical, aliases, mappings)
                            verify_table(src, saved, table, plan, canonical, aliases, mappings)
                        elif records(src, table) != records(saved, table) or columns(src, table) != columns(saved, table):
                            raise PipelineError(f'Preparation changed an unrelated/unresolved table: {table}')
                entry.update(corrected=target.name, corrected_sha256=sha256(target))
            else:
                target.unlink()  # Only our newly created, unchanged staging copy.
        if fingerprints(base) != hashes or any(sha256(p) != source_hashes[p.name] for p in paths):
            raise PipelineError('Base or inputs changed during preparation')
        unresolved = sum(t['status'] == 'unresolved' for b in manifest['imports'] for t in b['tables'])
        issues = [dict(source=b['source'], **issue) for b in manifest['imports'] for issue in b['issues']]
        write_json(output / 'preprocessing-issues.json', issues)
        manifest['status'] = 'prepared_with_unresolved' if issues else 'prepared'
        counts = Counter()
        for evidence in (output / 'evidence').glob('*.jsonl'):
            for line in evidence.read_text(encoding='utf-8').splitlines():
                event = json.loads(line)
                if event['kind'] == 'repair':
                    counts[event['rule']] += 1
        manifest['repair_counts'] = dict(counts)
        manifest['unresolved_issues'] = len(issues)
        exclusions = [dict(source=b['source'], table=t['table'], rows=t['excluded_rows'],
                           populated_rows=t['excluded_populated_rows'], year_cells=t['excluded_year_cells'])
                      for b in manifest['imports'] for t in b['tables'] if t.get('excluded_rows')]
        manifest['exclusions'] = exclusions
        lines = ['# Formatted import preparation', '', f"Status: **{manifest['status']}**", '',
                 'Original files and the base were preserved. Retained finite observations were preserved; '
                 'declared decimal-text conversions, infinity/blank normalization, schemas and metadata changes are audited. '
                 'Corrected files are staged only; unresolved repair units remain unchanged. '
                 'This is formatting verification, not metadata approval or statistical validation. '
                 'Consolidation may still skip tables for metadata or other structural issues.', '',
                 '| Source | Table | Outcome | Detail |', '|---|---|---|---|']
        for b in manifest['imports']:
            for t in b['tables']:
                detail = t.get('reason', f"{t.get('repairs', 0)} formatting repairs")
                lines.append(f'| {b["source"]} | {t["table"]} | {t["status"]} | {detail.replace("|", "/")} |')
            md = b['metadata']
            lines.append(f'| {b["source"]} | DataDict | {md["status"]} | {str(md.get("reason", str(md.get("repairs", 0)) + " repairs")).replace("|", "/")} |')
        lines += ['', '## Normalization counts', '', *[f'- {rule}: {count}' for rule, count in sorted(counts.items())],
                  '', '## Excluded surplus rows', '',
                  'Monadic extras are excluded only when every canonical IFs entity is already present exactly once. '
                  'Frozen originals and per-row evidence retain every excluded identity and value.', '',
                  '| Source | Table | Excluded rows | With populated years | Populated year cells excluded |',
                  '|---|---|---:|---:|---:|',
                  *[f'| {e["source"]} | {e["table"]} | {e["rows"]} | {e["populated_rows"]} | {e["year_cells"]} |' for e in exclusions],
                  '', '## Return to preprocessing', '',
                  'These findings need preparation evidence or source rework, not an automatic repull. See preprocessing-issues.json.', '']
        lines += [f'- {i["source"]} / {i["table"]}: {i["reason"]}' for i in issues]
        lines += ['', 'Resolve the reported blockers before marking an import ready. Corrected files may contain unresolved tables. '
                  'For each source with a corrected file, select that version instead of the original when it is ready for a new delivery. '
                  'For an already registered batch, use the delivery discard/replacement workflow with explicit precedence where needed; '
                  'never overwrite registered files. Retain this complete preparation folder with the delivery.']
        (output / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        manifest['evidence_sha256'] = {str(p.relative_to(output)): sha256(p) for p in output.rglob('*') if p.is_file() and p.name != 'manifest.json'}
        write_json(output / 'manifest.json', manifest)
        return dict(status=manifest['status'], output=str(output), report=str(output / 'report.md'),
                    corrected_imports=sum(bool(b['corrected']) for b in manifest['imports']),
                    repaired_tables=sum(t['status'] == 'repaired' for b in manifest['imports'] for t in b['tables']),
                    unresolved_tables=unresolved, unresolved_issues=len(issues), repair_counts=dict(counts),
                    excluded_rows=sum(e['rows'] for e in exclusions),
                    excluded_year_cells=sum(e['year_cells'] for e in exclusions))
    except Exception as exc:
        manifest.update(status='failed', error=str(exc))
        write_json(output / 'manifest.json', manifest)
        raise


def verify_preparation(output):
    """Check frozen preparation artifacts before selecting corrected imports."""
    from .storage import inside
    output = Path(output).resolve()
    manifest = read_json(output / 'manifest.json')
    if manifest.get('status') not in ('prepared', 'prepared_with_unresolved'):
        raise PipelineError('Preparation did not finish successfully')
    if not manifest.get('evidence_sha256'):
        raise PipelineError('Preparation evidence is missing')
    for name, digest in manifest['evidence_sha256'].items():
        if sha256(inside(output, name)) != digest:
            raise PipelineError(f'Preparation evidence changed: {name}')
    if fingerprints(Path(manifest['base'])) != manifest['base_sha256']:
        raise PipelineError('Preparation baseline changed')
    return dict(status=manifest['status'], verified=True, output=str(output))

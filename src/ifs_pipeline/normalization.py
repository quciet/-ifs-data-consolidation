"""Declared representation conversions for validation, never for consolidation."""
from decimal import Decimal, InvalidOperation
import math
import re

from .storage import PipelineError, columns, records, tables


DECIMAL = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')
INFINITY = {'inf', '+inf', '-inf', 'infinity', '+infinity', '-infinity'}


def declared_type(value):
    return re.sub(r'\s+', '', value).upper()


def schema_signature(info):
    return [(c['name'], declared_type(c['type']), c.get('notnull', 0),
             c.get('dflt_value'), c.get('pk', 0)) for c in info]


def evidence_value(value):
    # repr keeps infinity, BLOBs and the exact original string JSON-safe.
    return dict(raw_repr=repr(value), storage_type=type(value).__name__)


def numeric_value(value):
    """Return (DOUBLE-compatible value, normalization rule or None).

    Decimal text must round-trip through Python's shortest binary64 decimal
    representation without changing its decimal value. Integral values must also
    be exactly representable. No locale, separator or sentinel guessing.
    """
    if value is None:
        return None, None
    if isinstance(value, str):
        token = value.strip()
        if not token:
            return None, 'blank_to_null'
        if token.casefold() in INFINITY:
            return None, 'infinity_to_null'
        if not DECIMAL.fullmatch(token):
            raise PipelineError(f'Unsupported numeric text: {value!r}')
        try:
            decimal = Decimal(token)
            result = float(decimal)
        except (InvalidOperation, OverflowError, ValueError) as exc:
            raise PipelineError(f'Invalid decimal: {value!r}') from exc
        if not math.isfinite(result) or decimal != 0 and result == 0:
            raise PipelineError(f'Decimal overflow/underflow: {value!r}')
        if Decimal(str(result)) != decimal or (decimal == decimal.to_integral_value() and Decimal.from_float(result) != decimal):
            raise PipelineError(f'Decimal loses precision as IFs DOUBLE: {value!r}')
        return result, 'numeric_text_to_double'
    if type(value) not in (int, float):
        raise PipelineError(f'Unsupported numeric storage: {value!r}')
    if type(value) is float and math.isinf(value):
        return None, 'infinity_to_null'
    if not math.isfinite(value):
        raise PipelineError('NaN requires source-specific missing-value evidence')
    if type(value) is int and float(value) != value:
        raise PipelineError('Integer cannot be represented exactly as IFs DOUBLE')
    return value, None


def integer_value(value):
    """Convert metadata integers without a binary64 detour or invented defaults."""
    if value is None:
        return None
    if isinstance(value, str):
        token = value.strip()
        if not token or token.casefold() in INFINITY:
            return None
        if not DECIMAL.fullmatch(token):
            raise PipelineError(f'Expected integer metadata, found {value!r}')
        try:
            number = Decimal(token)
        except InvalidOperation as exc:
            raise PipelineError(f'Invalid integer metadata: {value!r}') from exc
    elif type(value) in (int, float):
        if type(value) is float and math.isinf(value):
            return None
        number = Decimal(str(value))
    else:
        raise PipelineError(f'Unsupported integer metadata storage: {value!r}')
    if not number.is_finite() or number != number.to_integral_value() or not -(2**63) <= number < 2**63:
        raise PipelineError(f'Metadata value is not a signed 64-bit integer: {value!r}')
    return int(number)


def metadata_value(value, declaration):
    kind = declared_type(declaration)
    if kind in ('TEXT', 'CLOB') or re.fullmatch(r'(?:VAR)?CHAR(?:\([0-9]+\))?', kind):
        if value is None or isinstance(value, str):
            return value
        if type(value) in (int, float) and math.isfinite(value):
            return str(value)
        raise PipelineError(f'Unsupported text metadata storage: {value!r}')
    if re.fullmatch(r'INTEGER(?:\([0-9]+\))?', kind) or kind == 'INT':
        return integer_value(value)
    if kind in ('DOUBLE(53)', 'DOUBLE', 'REAL', 'FLOAT'):
        number = numeric_value(value)[0]
        return float(number) if number is not None else None
    raise PipelineError(f'Unsupported DataDict declaration: {declaration!r}')


def simple_table(conn, table, allow_defaults=False):
    ddl = conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (table,)).fetchone()[0]
    deps = conn.execute("SELECT name FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger')", (table,)).fetchall()
    forbidden = r'\b(CHECK|REFERENCES|GENERATED|UNIQUE|PRIMARY)\b|\bNOT\s+NULL\b|\bCOLLATE\b'
    if not allow_defaults:
        forbidden += r'|\bDEFAULT\b'
    if deps or re.search(forbidden, ddl, re.I):
        raise PipelineError('Custom constraints/indexes/triggers require a dedicated preparation adapter')


def metadata_reference(conn):
    """Use the selected base's field-specific schema for either DataDict location."""
    if 'DataDict' not in tables(conn):
        raise PipelineError('Baseline DataDict table is missing')
    simple_table(conn, 'DataDict')
    info = columns(conn, 'DataDict')
    if not {'Table', 'Variable'}.issubset(c['name'] for c in info):
        raise PipelineError('Baseline DataDict lacks Table/Variable')
    for column in info:
        metadata_value(None, column['type'])  # Reject unsupported destination types.
    rows = records(conn, 'DataDict')
    return info, rows


def plan_metadata(conn, base_info, base_rows):
    if 'DataDict' not in tables(conn):
        raise PipelineError('Import DataDict table is missing')
    # Source defaults are declarations, not evidence for missing values. All
    # columns are inserted explicitly, so no default expression is evaluated.
    simple_table(conn, 'DataDict', allow_defaults=True)
    info = columns(conn, 'DataDict')
    source_names = [c['name'] for c in info]
    names = [c['name'] for c in base_info]
    if set(source_names) - set(names):
        raise PipelineError('Unknown DataDict fields: ' + ', '.join(sorted(set(source_names) - set(names))))
    if not {'Table', 'Variable'}.issubset(source_names):
        raise PipelineError('Import DataDict lacks Table/Variable; recover links in preprocessing')
    base_index = {}
    for row in base_rows:
        base_index.setdefault((row.get('Table'), row.get('Variable')), []).append(row)
    planned, events = [], []
    if schema_signature(info) != schema_signature(base_info):
        events.append(dict(rule='normalize_metadata_schema', before=info, after=base_info))
    for index, row in enumerate(records(conn, 'DataDict'), 1):
        prior = base_index.get((row.get('Table'), row.get('Variable')), [])
        values = []
        for column in base_info:
            field = column['name']
            if field not in row and len(prior) > 1:
                raise PipelineError('Ambiguous baseline metadata identity; cannot retain absent fields')
            original = row.get(field) if field in row else prior[0].get(field) if prior else None
            try:
                value = metadata_value(original, column['type'])
            except PipelineError as exc:
                raise PipelineError(f'DataDict row {index}/{field}: {exc}') from exc
            if field not in row:
                events.append(dict(rule='retain_absent_metadata_field' if prior else 'add_unresolved_metadata_field',
                                   source_row=index, field=field, after=value,
                                   baseline_key=[row.get('Table'), row.get('Variable')]))
            if type(value) != type(original) or value != original:
                events.append(dict(rule='normalize_metadata_storage', source_row=index, field=field,
                                   before=evidence_value(original), after=value, declaration=column['type']))
            if field == 'Units' and isinstance(value, str):
                # Wording with new semantic content, e.g. currency or scale, is
                # deliberately not inferred from a series name or bare "billions".
                units = {r.get('Units') for r in base_rows if r.get('Table') == row.get('Table')}
                if len(units) == 1:
                    baseline = next(iter(units))
                    if isinstance(baseline, str) and baseline.strip() and ' '.join(value.split()).casefold() == ' '.join(baseline.split()).casefold() and value != baseline:
                        events.append(dict(rule='retain_base_unit_label', source_row=index, field=field,
                                           before=value, after=baseline, evidence='case_and_whitespace_only'))
                        value = baseline
            values.append(value)
        planned.append(values)
    return dict(names=names, info=base_info, rows=planned, events=events)


def verify_metadata(conn, plan):
    if schema_signature(columns(conn, 'DataDict')) != schema_signature(plan['info']):
        raise PipelineError('Saved DataDict schema verification failed')
    actual = records(conn, 'DataDict')
    if len(actual) != len(plan['rows']):
        raise PipelineError('Saved DataDict row count verification failed')
    for row, values in zip(actual, plan['rows']):
        for field, value in zip(plan['names'], values):
            if type(row[field]) != type(value) or row[field] != value:
                raise PipelineError(f'Saved DataDict value verification failed: {field}')


def metadata_issues(conn, base_info, base_rows):
    """Report links, required content and unresolved units for upstream rework."""
    issues = []
    def add(table, reason):
        issues.append(dict(table=table if isinstance(table, str) else repr(table),
                           category='metadata', owner_stage='preprocessing', reason=reason))
    series = {t for t in tables(conn) if t.startswith('Series')}
    if 'DataDict' not in tables(conn):
        add('DataDict', 'Missing DataDict; prepare source metadata')
        return issues
    rows = records(conn, 'DataDict')
    linked = {r.get('Table') for r in rows if isinstance(r.get('Table'), str)}
    for table in sorted(series - linked):
        add(table, 'Physical table has no matching DataDict row')
    for table in sorted(linked - series):
        add(table, 'DataDict references a missing physical table')
    required = ('Table', 'Variable', 'Definition', 'Units', 'Source')
    seen = set()
    for row in rows:
        table = row.get('Table')
        if any(not isinstance(row.get(f), str) or not row[f].strip() for f in required):
            add(table, 'Missing/invalid required DataDict content; prepare source metadata')
        key = (table, row.get('Variable'))
        if key in seen:
            add(table, 'Duplicate Table/Variable identity')
        seen.add(key)
    for table in sorted(series & linked):
        selected = [r for r in rows if r.get('Table') == table]
        units = {r.get('Units') for r in selected}
        prior = [r for r in base_rows if r.get('Table') == table]
        baseline_units = {r['Units'].strip() for r in prior if isinstance(r.get('Units'), str)}
        if len(units) != 1:
            add(table, 'Source metadata assigns incompatible units to one physical table')
        elif baseline_units and baseline_units != {u.strip() if isinstance(u, str) else u for u in units}:
            add(table, f'Unit meaning requires preprocessing review: baseline {sorted(baseline_units)!r}; incoming {list(units)!r}')
        if {r.get('Variable') for r in prior} - {r.get('Variable') for r in selected}:
            add(table, 'Incoming metadata omits baseline Variable entries')
    return issues

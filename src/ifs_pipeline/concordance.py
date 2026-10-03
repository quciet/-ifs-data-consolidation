"""Bridge DataGatorLite name mappings into frozen IFs country references.

No value aggregation, allocation, or fuzzy auto-acceptance happens here.
"""
import csv
from collections import defaultdict
from pathlib import Path
import shutil
import unicodedata

from .adapters import country_reference
from .storage import PipelineError, new_id, now, read_json, resource_path, sha256, write_json

def normalize_name(value):
    """Conservative identity lookup: case/Unicode/whitespace, not punctuation removal."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def read_country_names(path, column):
    names, counts = [], defaultdict(int)
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise PipelineError("Source CSV headers are missing or duplicated")
        if column not in reader.fieldnames:
            raise PipelineError(f"Country column not found: {column}")
        for line, row in enumerate(reader, 2):
            if None in row or any(v is None for v in row.values()):
                raise PipelineError(f"Source CSV row {line}: inconsistent column count")
            value = row[column].strip()
            if not value:
                raise PipelineError(f"Source CSV row {line}: country is blank")
            if value not in counts:
                names.append(value)
            counts[value] += 1
    if not names:
        raise PipelineError("Source CSV has no country rows")
    return names, counts


def lookup_index(data, canonical):
    if not isinstance(data, list) or not data:
        raise PipelineError("Datagator country lookup must be a non-empty list")
    index, by_name, seen_codes = defaultdict(set), defaultdict(set), set()
    for item in data:
        name, code = item.get("ifs_name"), item.get("ifs_fipscode")
        alternatives = item.get("alternative_names", [])
        if not isinstance(name, str) or not isinstance(code, str) or not name.strip() or not code.strip():
            raise PipelineError("Datagator entry needs ifs_name and ifs_fipscode")
        if code in seen_codes:
            raise PipelineError(f"Duplicate Datagator IFs code: {code}")
        seen_codes.add(code)
        if canonical.get(code) != name:
            raise PipelineError(f"Datagator identity differs from configured IFs master: {code}/{name}")
        if not isinstance(alternatives, list) or any(not isinstance(v, str) or not v.strip() for v in alternatives):
            raise PipelineError(f"Invalid alternative_names for {code}")
        for alias in [name, code] + alternatives:
            index[normalize_name(alias)].add(code)
        for identifier in [name, code]:
            by_name[normalize_name(identifier)].add(code)
    if seen_codes != set(canonical):
        raise PipelineError("Datagator and configured IFs master do not have the same country universe")
    return index, by_name


def reviewed_mappings(path, targets, source_names):
    result = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["original_name", "matched_name"]:
            raise PipelineError("Use Datagator's original_name,matched_name mapping CSV export")
        for line, row in enumerate(reader, 2):
            if None in row or any(v is None for v in row.values()):
                raise PipelineError(f"Mapping CSV row {line}: inconsistent column count")
            original, matched = row["original_name"].strip(), row["matched_name"].strip()
            if not original:
                raise PipelineError(f"Mapping CSV row {line}: original_name is blank")
            if original in result:
                raise PipelineError(f"Duplicate original_name in reviewed mapping: {original}")
            if original not in source_names:
                raise PipelineError(f"Mapping contains a name absent from the selected source column: {original}")
            if not matched:
                result[original] = None
                continue
            choices = targets.get(normalize_name(matched), set())
            if len(choices) != 1:
                raise PipelineError(f"Invalid or ambiguous reviewed IFs target: {matched}")
            result[original] = next(iter(choices))
    return result


def _csv(path, rows, fields):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def prepare(root, input_path, country_column, source, mapping_path=None,
            reviewed=False, exclude=()):
    """Generate a source-bound concordance bundle; unresolved matches are non-executable."""
    root, input_path = Path(root).resolve(), Path(input_path).resolve()
    if not source.strip():
        raise PipelineError("Give the concordance a source name")
    if reviewed and mapping_path is None:
        raise PipelineError("--reviewed requires --mapping")
    if mapping_path is not None and not reviewed:
        raise PipelineError("Review the Datagator export first, then pass --reviewed to confirm identity-only mappings")
    config = read_json(root / "config.local.json")
    master_path = resource_path(root, config["country_reference"])
    canonical, _ = country_reference(master_path)
    from .resources import datagator_lookup
    datagator_path = datagator_lookup(root)
    index, targets = lookup_index(read_json(datagator_path), canonical)
    hashes = {"input": sha256(input_path), "master": sha256(master_path),
              "datagator": sha256(datagator_path)}
    provenance_path = datagator_path.with_name("provenance.json")
    provenance = read_json(provenance_path) if provenance_path.exists() else {}
    if provenance and provenance.get("local_sha256") != hashes["datagator"]:
        raise PipelineError("Datagator lookup differs from its recorded provenance")
    names, counts = read_country_names(input_path, country_column)
    mappings = {}
    if mapping_path is not None:
        mapping_path = Path(mapping_path).resolve()
        hashes["reviewed_mapping"] = sha256(mapping_path)
        mappings = reviewed_mappings(mapping_path, targets, set(names))
    excluded = set(exclude)
    if excluded - set(names):
        raise PipelineError(f"Exclusions absent from the source: {sorted(excluded - set(names))}")
    if any(mappings.get(name) is not None for name in excluded):
        raise PipelineError("A source name cannot be both reviewed as mapped and explicitly excluded")
    rows, mapped, destination_sources = [], {}, defaultdict(list)
    for original in names:
        choices = set()
        if original in excluded:
            state, method, code = "excluded", "explicit_exclusion", None
        elif original in mappings:
            code = mappings[original]
            state, method = ("mapped", "reviewed_datagator_export") if code else ("unresolved", "blank_reviewed_target")
        else:
            choices = index.get(normalize_name(original), set())
            code = next(iter(choices)) if len(choices) == 1 else None
            state = "mapped" if code else "ambiguous" if choices else "unresolved"
            method = "datagator_exact_alias" if code else "needs_datagator_review"
        if code:
            mapped[original] = (canonical[code], code)
            destination_sources[code].append(original)
        rows.append({"original_name": original, "row_count": counts[original], "status": state,
                     "method": method, "Country": canonical.get(code, ""),
                     "FIPS_CODE": code or "", "candidates": " | ".join(canonical[c] for c in sorted(choices)) if not code else ""})
    collisions = [{"FIPS_CODE": code, "Country": canonical[code], "source_names": sources}
                  for code, sources in sorted(destination_sources.items()) if len(sources) > 1]
    unresolved = sum(r["status"] in ("unresolved", "ambiguous") for r in rows)
    state = ("blocked" if unresolved else "needs_processing_review" if collisions
             else "blocked" if not mapped else "ready")
    # Preserve full canonical roster separately in meaning from the source aliases.
    # This also prevents source coverage gaps from becoming a smaller IFs universe.
    output_aliases = {code: (name, code) for code, name in canonical.items()}
    for original, pair in mapped.items():
        if original in output_aliases and output_aliases[original] != pair:
            raise PipelineError(f"Reviewed source identifier conflicts with canonical IFs code: {original}")
        output_aliases[original] = pair
    for label, path in (("input", input_path), ("master", master_path), ("datagator", datagator_path)):
        if sha256(path) != hashes[label]:
            raise PipelineError(f"{label} changed while preparing concordance; retry with stable files")
    if mapping_path and sha256(mapping_path) != hashes["reviewed_mapping"]:
        raise PipelineError("Reviewed export changed while preparing concordance")
    folder = root / "reference/local/concordance" / new_id()
    folder.mkdir(parents=True)
    if mapping_path:
        shutil.copy2(mapping_path, folder / "reviewed_mapping.csv")
        if sha256(folder / "reviewed_mapping.csv") != hashes["reviewed_mapping"]:
            raise PipelineError("Reviewed export changed while being copied")
    shutil.copy2(master_path, folder / "master.csv")
    shutil.copy2(datagator_path, folder / "datagator.json")
    for label, file in (("master", "master.csv"), ("datagator", "datagator.json")):
        if sha256(folder / file) != hashes[label]:
            raise PipelineError(f"{label} changed while being copied")
    _csv(folder / "matches.csv", rows, ["original_name", "row_count", "status", "method", "Country", "FIPS_CODE", "candidates"])
    _csv(folder / "review_in_datagator.csv",
         [{"original_name": r["original_name"], "matched_name": r["Country"]} for r in rows if r["status"] != "excluded"],
         ["original_name", "matched_name"])
    bundle = {
        "schema_version": 1, "status": state, "created_at": now(), "source": source,
        "input_path": str(input_path), "country_column": country_column,
        "source_sha256": hashes["input"], "master_sha256": hashes["master"],
        "datagator_sha256": hashes["datagator"], "upstream": provenance.get("repository"),
        "upstream_commit": provenance.get("commit"),
        "reviewed_identity_mappings": bool(reviewed),
        "mapped_names": len(mapped), "unresolved_names": unresolved,
        "excluded_names": sorted(excluded), "collisions": collisions,
        "covered_ifs_countries": sorted(destination_sources),
        "absent_ifs_countries": sorted(set(canonical) - set(destination_sources)),
        "operations": ["identity_mapping_only"],
        "files": {"master.csv": hashes["master"], "datagator.json": hashes["datagator"],
                  "matches.csv": sha256(folder / "matches.csv")}
    }
    if provenance:
        write_json(folder / "datagator_provenance.json", provenance)
        bundle["files"]["datagator_provenance.json"] = sha256(folder / "datagator_provenance.json")
    if mapping_path:
        bundle["files"]["reviewed_mapping.csv"] = hashes["reviewed_mapping"]
    if state == "ready":
        _csv(folder / "countries.csv",
             [{"source_code": alias, "Country": name, "FIPS_CODE": code}
              for alias, (name, code) in sorted(output_aliases.items())],
             ["source_code", "Country", "FIPS_CODE"])
        country_reference(folder / "countries.csv")
        bundle["files"]["countries.csv"] = sha256(folder / "countries.csv")
        fields = {"country_reference": str((folder / "countries.csv").relative_to(root)),
                  "concordance_manifest": str((folder / "concordance.json").relative_to(root)),
                  "ignored_country_codes": sorted(excluded)}
        write_json(folder / "recipe_fields.json", fields)
        bundle["recipe_fields"] = fields
    write_json(folder / "concordance.json", bundle)
    report = ["# Datagator concordance", "", f"Status: **{state}**", "",
              f"Source: {source}", f"Mapped source names: {len(mapped)}",
              f"Unresolved/ambiguous names: {unresolved}", f"Explicit exclusions: {len(excluded)}",
              f"IFs countries not covered by this source: {len(bundle['absent_ifs_countries'])}", "",
              "Matching names does not authorize summation, allocation, averaging or imputation.",
              "No source data values have been modified.", ""]
    if collisions:
        report += ["Several distinct source names target the same IFs identity. Determine whether",
                   "they are spelling variants or distinct territories before processing.", ""]
        report += [f"- {r['Country']}: {', '.join(r['source_names'])}" for r in collisions]
    if state != "ready":
        report += ["", "No executable countries.csv was produced. Review matches.csv.",
                   "Use Datagator to review unresolved names; unresolved geographic transformations",
                   "require a separate source/indicator/year-specific processing rule."]
    else:
        report += ["", "Copy recipe_fields.json fields into the source recipe, then ingest the original CSV.",
                   "This bundle is bound to this input hash and country column. Regenerate for a new download."]
    (folder / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return {**bundle, "folder": str(folder), "report": str(folder / "report.md")}


def validate_bundle(root, manifest_path, input_path, recipe):
    """Bind a completed concordance to the actual source, column and recipe."""
    path = resource_path(root, manifest_path)
    data = read_json(path)
    if data.get("schema_version") != 1 or data.get("status") != "ready":
        raise PipelineError("Concordance is not ready; resolve name/territory review first")
    if data.get("operations") != ["identity_mapping_only"] or data.get("collisions"):
        raise PipelineError("Concordance must represent identity mappings only")
    if recipe.get("adapter") != "csv_long":
        raise PipelineError("Datagator concordance bundles currently support csv_long")
    if sha256(input_path) != data.get("source_sha256"):
        raise PipelineError("Concordance was prepared for a different input file; regenerate it")
    if recipe.get("columns", {}).get("country") != data.get("country_column"):
        raise PipelineError("Recipe country column differs from the concordance")
    if sorted(recipe.get("ignored_country_codes", [])) != data.get("excluded_names"):
        raise PipelineError("Recipe exclusions differ from the reviewed concordance")
    if not recipe.get("country_reference"):
        raise PipelineError("Concordance recipe requires its generated country_reference")
    countries = resource_path(root, recipe["country_reference"])
    if sha256(countries) != data.get("files", {}).get("countries.csv"):
        raise PipelineError("Country reference differs from the concordance bundle")
    required = {"countries.csv", "matches.csv", "master.csv", "datagator.json"}
    allowed = required | {"reviewed_mapping.csv", "datagator_provenance.json"}
    files = data.get("files", {})
    if not required.issubset(files) or set(files) - allowed:
        raise PipelineError("Concordance evidence files are missing or unexpected")
    for name, expected in files.items():
        if sha256(path.parent / name) != expected:
            raise PipelineError(f"Concordance evidence changed: {name}")
    return path, data

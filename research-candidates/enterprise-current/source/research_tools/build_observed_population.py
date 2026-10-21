"""Offline observed-row pool builder; never downloads or runs a model.

Example (only after separate I/O admission):
python -m research_tools.build_observed_population --year 2023 \
  --person-csv PERSON --person-sha256 SHA --housing-zip HOUSING \
  --housing-sha256 SHA --source-manifest MANIFEST --source-sha256 SHA \
  --output NEW_DIRECTORY --safety-root OTHER_FILESYSTEM --execute-build

The derived person CSV must be bound by the existing ACS measurement manifest.
All housing CSV columns and all supplied person columns are retained. A pool
represents the declared resident-worker background domain, not an employer
roster, population calibration, care-time estimate or unseen-year validation.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
from pathlib import Path
import stat
import time
import zipfile

from dams_sim.observed_population import (
    DOCUMENT_LIMIT, _read_pinned, _require, build_documents, canonical,
    parse_document, sha256,
)

INPUT_LIMIT = 64 * 1024**2
DECODE_LIMIT = 128 * 1024**2
OUTPUT_LIMIT = 96 * 1024**2
WINDOW_SECONDS = 90
FREEFLOOR = 2 * 1024**3


def build_pool(person_rows, household_rows, *, source_manifest):
    """Pure whole-row join; supplied provenance is data, not authority."""
    return build_documents(person_rows, household_rows, source_manifest=source_manifest)


def _path(value):
    path = Path(value).absolute()
    _require(".." not in path.parts and not any(x.is_symlink() for x in (path, *path.parents)), "unsafe path")
    return path


def _csv(raw):
    with io.StringIO(raw.decode("utf-8-sig"), newline="") as stream:
        reader = csv.DictReader(stream)
        header = reader.fieldnames
        _require(header and len(header) == len(set(header)), "duplicate or empty CSV header")
        out = []
        for row in reader:
            _require(None not in row and all(v is not None for v in row.values()), "ragged CSV row")
            _require(len(out) < 100000, "CSV row ceiling")
            out.append(row)
        return out


def _reference(entries, name, expected, size):
    selected = [x for x in entries if x.get("name") == name]
    _require(len(selected) == 1 and selected[0].get("sha256") == expected
             and type(selected[0].get("bytes")) is int and selected[0]["bytes"] == size,
             "upstream content binding differs")
    return selected[0]


def _write(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def execute(args):
    _require(args.execute_build, "explicit offline build admission required")
    started = time.monotonic()
    output = _path(args.output)
    inputs = [_path(x) for x in (args.person_csv, args.housing_zip, args.source_manifest)]
    _require(not output.exists() and output.parent.is_dir(), "fresh output directory required")
    _require(all(not p.is_relative_to(output) and not output.is_relative_to(p.parent) for p in inputs), "output must be disjoint from source directories")
    roots = (output.parent, _path(args.safety_root))

    def guard():
        _require(time.monotonic() - started < WINDOW_SECONDS, "offline build I/O window ended")
        for root in roots:
            value = os.statvfs(root)
            _require(value.f_bavail * value.f_frsize >= FREEFLOOR, "2GiB freefloor")

    guard()
    identities = [(p.stat().st_dev, p.stat().st_ino, p.stat().st_size,
                   p.stat().st_mtime_ns, p.stat().st_ctime_ns) for p in inputs]
    _require(sum(x[2] for x in identities) <= INPUT_LIMIT, "total input byte ceiling")
    person_raw = _read_pinned(inputs[0], args.person_sha256, INPUT_LIMIT)
    house_raw = _read_pinned(inputs[1], args.housing_sha256, 25 * 1024**2)
    source_raw = _read_pinned(inputs[2], args.source_sha256, 2 * 1024**2)
    guard()
    source = parse_document(source_raw)
    _require(source.get("schema") == "acs-ri-measurement-diagnostic-1"
             and source.get("status") == "DATA_MEASUREMENT_DIAGNOSTIC_ONLY", "upstream measurement receipt required")
    h = _reference(source["files"], f"{args.year}-h-ri.zip", args.housing_sha256, len(house_raw))
    _require(h.get("year") == args.year and h.get("kind") == "h"
             and h.get("ZIP_CRC_accepted") is True, "upstream housing binding differs")
    _reference(source["output_files"], f"{args.year}-person-context.csv", args.person_sha256, len(person_raw))
    members, house_rows, decoded = [], None, 0
    with zipfile.ZipFile(io.BytesIO(house_raw)) as archive:
        entries = archive.infolist()
        _require(entries and len(entries) <= 32 and len({e.filename for e in entries}) == len(entries), "ZIP roster differs")
        _require(sum(e.file_size for e in entries) <= DECODE_LIMIT, "ZIP decode ceiling")
        for entry in entries:
            mode = entry.external_attr >> 16
            _require(not entry.is_dir() and Path(entry.filename).name == entry.filename
                     and "\\" not in entry.filename and not entry.flag_bits & 1
                     and (stat.S_IFMT(mode) in (0, stat.S_IFREG)), "unsafe ZIP member")
            guard()
            with archive.open(entry) as stream:
                raw = stream.read(min(DECODE_LIMIT - decoded, entry.file_size) + 1)
                _require(len(raw) == entry.file_size and not stream.read(1), "ZIP length or EOF differs")
            decoded += len(raw)
            _require(decoded <= DECODE_LIMIT, "ZIP decode ceiling")
            members.append({"name": entry.filename, "bytes": len(raw), "sha256": sha256(raw), "crc32": f"{entry.CRC:08x}", "normal_EOF_CRC": True})
            if entry.filename.lower().endswith(".csv"):
                _require(house_rows is None, "multiple housing CSVs")
                house_rows = _csv(raw)
        _require(house_rows is not None, "missing housing CSV")
    guard()
    provenance = {"reference_year": args.year, "kind": "previously_saved_ACS_measurement_inputs",
                  "upstream_manifest_sha256": args.source_sha256, "upstream_manifest": source,
                  "person_csv": {"name": f"{args.year}-person-context.csv", "bytes": len(person_raw), "sha256": args.person_sha256},
                  "housing_zip": {"name": f"{args.year}-h-ri.zip", "bytes": len(house_raw), "sha256": args.housing_sha256, "members": members}}
    docs = build_pool(_csv(person_raw), house_rows, source_manifest=provenance)
    encoded = {name: canonical(value) for name, value in docs.items()}
    _require(all(len(raw) <= DOCUMENT_LIMIT for raw in encoded.values())
             and sum(map(len, encoded.values())) <= OUTPUT_LIMIT, "output byte ceiling")
    for path, identity in zip(inputs, identities):
        value = path.stat()
        _require((value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns) == identity, "input changed")
    guard()
    output.mkdir()  # No overwrite/resume; a failed write remains visible and incomplete.
    for name in ("pool", "mapping", "manifest"):
        guard()
        _write(output / (name + ".json"), encoded[name])
    return {"status": "OBSERVED_BACKGROUND_POOL_ONLY", "formal_worlds_added": 0,
            "input_file_bytes_read": len(person_raw) + len(house_raw) + len(source_raw),
            "housing_decoded_bytes": decoded, "elapsed_seconds": time.monotonic() - started,
            "files": {name: {"bytes": len(raw), "sha256": sha256(raw)} for name, raw in encoded.items()}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, choices=(2023, 2024), required=True)
    for name in ("person-csv", "person-sha256", "housing-zip", "housing-sha256", "source-manifest", "source-sha256", "output", "safety-root"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--execute-build", action="store_true")
    print(canonical(execute(parser.parse_args(argv))).decode(), end="")


if __name__ == "__main__":
    main()

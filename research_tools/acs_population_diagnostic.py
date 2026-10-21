"""Bounded ACS RI measurement diagnostic; never a Model/calibration admission."""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import stat
import time
import urllib.request
import zipfile

MIB = 1024**2
RAW_LIMIT = 100 * MIB
DECODE_LIMIT = 1024 * MIB
WINDOW = 90.0
FLAGS = ("FAGEP", "FSCHLP", "FSEXP", "FCOWP", "FESRP", "FWAGP",
         "FWKHP", "FWKWNP", "FJWMNP", "FJWTRNSP")
PERSON = ("SERIALNO", "SPORDER", "PUMA", "AGEP", "SEX", "SCHL", "ESR",
          "COW", "INDP", "OCCP", "WKHP", "WKWN", "WAGP", "ADJINC",
          "JWMNP", "JWTRNS", "RELSHIPP", "GCL", "GCR", "GCM", "PWGTP")
HOUSING = ("SERIALNO", "PUMA", "NP", "R18", "R65", "WGTP")
WEIGHTS = tuple("PWGTP" + str(i) for i in range(1, 81))
AGE_BINS = ((0, 15, "00_15"), (16, 24, "16_24"), (25, 34, "25_34"),
            (35, 44, "35_44"), (45, 54, "45_54"), (55, 64, "55_64"),
            (65, 99, "65_plus"))
HOUR_BINS = ((1, 19, "01_19"), (20, 34, "20_34"), (35, 40, "35_40"),
             (41, 59, "41_59"), (60, 98, "60_98"), (99, 99, "99_plus"))


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def save(path, value):
    raw = canonical(value)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return {"name": path.name, "bytes": len(raw), "sha256": digest(raw)}


def metadata_plan():
    return {
        "schema": "acs-ri-measurement-diagnostic-1",
        "selection_reason": "RI files are small; no outcome-based state selection",
        "years": [2023, 2024], "state": "44",
        "source_files": [{"year": y, "kind": k,
                          "name": f"{y}-{k}-ri.zip",
                          "url": f"https://www2.census.gov/programs-surveys/acs/data/pums/{y}/1-Year/csv_{k}ri.zip"}
                         for y in (2023, 2024) for k in ("h", "p")],
        "documentation": {
            "guide": "https://www2.census.gov/programs-surveys/acs/tech_docs/pums/2024ACS_PUMS_User_Guide.pdf",
            "guide_date": "2025-10-16", "guide_pages": [6, 11],
            "accuracy": "https://www2.census.gov/programs-surveys/acs/tech_docs/pums/accuracy/2024AccuracyPUMS.pdf",
            "accuracy_pages": [12, 13],
            "dictionaries": {str(y): f"https://www2.census.gov/programs-surveys/acs/tech_docs/pums/data_dict/PUMS_Data_Dictionary_{y}.pdf" for y in (2023, 2024)},
            "dictionary_dates": {"2023": "2024-10-17", "2024": "2025-10-16"},
            "variables": "https://api.census.gov/data/2024/acs/acs1/pums/variables.html"},
        "person_columns": list(PERSON + FLAGS + WEIGHTS),
        "housing_columns": list(HOUSING),
        "header_aliases": {"STATE": ["STATE", "ST"], "TYPEHUGQ": ["TYPEHUGQ", "TYPE"]},
        "alias_rule": "Exactly one named alias must exist; state values must all be 44; original header is recorded",
        "age_bins": [list(x) for x in AGE_BINS], "hours_bins": [list(x) for x in HOUR_BINS],
        "universes": {
            "age_school_employment": "all person records, with original SCHL/ESR/COW and explicit blank category",
            "industry_hours": "AGEP>=16 and ESR in {1,2}; current civilian employment, hours refer to past 12 months",
            "household_employment": "AGEP>=16 and ESR in {1,2,3,4,5,6}; includes GQ with separate context",
            "employment_codes": {"1": "civilian_at_work", "2": "civilian_job_not_at_work", "3": "unemployed", "4": "armed_at_work", "5": "armed_job_not_at_work", "6": "not_in_labor_force"}},
        "household_context": "HU: presence under18 (R18=1) and65+ (R65=1/2); GQ2/3 separate; not care hours",
        "weights": "PWGTP for people; preserve signed PWGTP1..80; variance=4/80*sum((estimate_r-estimate)^2)",
        "conditional_denominators": {"age_school_employment": ["age_bin", "SCHL"], "industry_hours": ["INDP"], "household_employment": ["household_context"]},
        "arithmetic_probe": {"table": "age_school_employment", "key": ["25_34", "21", "1", "1"]},
        "bounds": {"compressed_bytes": RAW_LIMIT, "per_zip_bytes": 25 * MIB,
                   "decoded_bytes_all_passes": DECODE_LIMIT, "io_window_seconds": WINDOW,
                   "freefloor_bytes": 2 * 1024**3, "redirects": 0, "retries": 0},
        "status": {"engineering_measurement_only": True, "formal_worlds": 0,
                   "calibration_accepted": False, "holdout_accepted": False,
                   "year2024_is_seen_after_this_run": True, "US_firm_representative": False},
        "implementation_sha256": digest(Path(__file__).read_bytes()),
    }


class Guard:
    def __init__(self, roots):
        self.started = time.monotonic()
        self.roots = roots
        self.downloaded = 0
        self.decoded = 0
        self.hash_read_bytes = 0

    def check(self, space=False):
        require(time.monotonic() - self.started < WINDOW, "90-second I/O window ended")
        if space:
            for path in self.roots:
                s = os.statvfs(path)
                require(s.f_bavail * s.f_frsize >= 2 * 1024**3, "2GiB freefloor")

    def count(self, size, *, decoded=False):
        self.check()
        if decoded:
            self.decoded += size
            require(self.decoded <= DECODE_LIMIT, "decoded byte ceiling")
        else:
            self.downloaded += size
            require(self.downloaded <= RAW_LIMIT, "compressed byte ceiling")

    def observation(self):
        return {"elapsed_seconds": time.monotonic() - self.started,
                "downloaded_body_bytes": self.downloaded,
                "decoded_bytes_all_passes": self.decoded,
                "hash_read_bytes": self.hash_read_bytes,
                "physical_IO_bytes_measured": False}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("redirect refused; no additional request")


def file_digest(path, guard):
    h = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        while block := stream.read(65536):
            guard.check()
            guard.hash_read_bytes += len(block)
            count += len(block)
            h.update(block)
    return {"name": path.name, "bytes": count, "sha256": h.hexdigest()}


def download(item, root, guard):
    guard.check(space=True)
    path = root / (item["name"] + ".partial")
    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(item["url"], headers={"Accept-Encoding": "identity"})
    expected = None
    h = hashlib.sha256()
    count = 0
    with path.open("xb") as target:
        with opener.open(request, timeout=max(.1, WINDOW - (time.monotonic() - guard.started))) as response:
            require(response.status == 200 and response.geturl() == item["url"], "unexpected HTTP response")
            require(response.headers.get("Content-Encoding", "identity").lower() == "identity", "encoded ZIP response")
            raw_length = response.headers.get("Content-Length", "")
            require(raw_length.isascii() and raw_length.isdecimal(), "Content-Length required")
            expected = int(raw_length)
            require(0 < expected < 25 * MIB and guard.downloaded + expected + 1 <= RAW_LIMIT, "ZIP length exceeds ceiling")
            while True:
                guard.check()
                block = response.read(min(65536, expected + 1 - count))
                if not block:
                    break
                guard.count(len(block))
                count += len(block)
                target.write(block)
                h.update(block)
                require(count <= expected, "response exceeds Content-Length")
            require(count == expected, "truncated HTTP body")
        target.flush()
        os.fsync(target.fileno())
    final = root / item["name"]
    path.rename(final)
    stored = file_digest(final, guard)
    require(stored["sha256"] == h.hexdigest() and stored["bytes"] == count, "stored ZIP readback differs")
    return {**item, "bytes": count, "content_length": expected, "sha256": h.hexdigest(),
            "HTTP_body_EOF": True, "stored_full_SHA_EOF_checked": True,
            "ZIP_CRC_accepted": False}


class Counted(io.RawIOBase):
    def __init__(self, stream, guard):
        super().__init__()
        self.stream, self.guard = stream, guard

    def readable(self):
        return True

    def readinto(self, buffer):
        raw = self.stream.read(len(buffer))
        self.guard.count(len(raw), decoded=True)
        buffer[:len(raw)] = raw
        return len(raw)

    def close(self):
        self.stream.close()
        super().close()


def alias(headers, names):
    found = [x for x in names if x in headers]
    require(len(found) == 1, "missing or ambiguous declared header alias")
    return found[0]


def records(path, columns, guard, description):
    guard.check(space=True)
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        require(entries and len({x.filename for x in entries}) == len(entries), "duplicate or empty ZIP roster")
        require(sum(x.file_size for x in entries) + guard.decoded <= DECODE_LIMIT, "ZIP decoded projection")
        csv_count = 0
        members = []
        for entry in entries:
            require(not entry.is_dir() and Path(entry.filename).name == entry.filename
                    and not stat.S_ISLNK(entry.external_attr >> 16), "unsafe ZIP member")
            with io.BufferedReader(Counted(archive.open(entry), guard)) as binary:
                if entry.filename.lower().endswith(".csv"):
                    csv_count += 1
                    text = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
                    reader = csv.DictReader(text)
                    headers = reader.fieldnames or []
                    require(len(set(headers)) == len(headers) and all(c in headers for c in columns), "required CSV schema differs")
                    state = alias(headers, ("STATE", "ST"))
                    description["headers"] = headers
                    description["state_column"] = state
                    description["type_column"] = alias(headers, ("TYPEHUGQ", "TYPE")) if "NP" in columns else None
                    for index, row in enumerate(reader):
                        if index % 256 == 0:
                            guard.check()
                        require(None not in row and all(v is not None for v in row.values()), "ragged CSV row")
                        require(row[state] == "44", "not Rhode Island")
                        yield row
                else:
                    while binary.read(65536):
                        pass
            members.append({"name": entry.filename, "bytes": entry.file_size,
                            "crc32": f"{entry.CRC:08x}", "normal_EOF_CRC": True})
        require(csv_count == 1, "expected exactly one CSV per RI ZIP")
        description["members"] = members
        description["ZIP_CRC_accepted"] = True


def integer(value):
    require(value.strip() == value and value not in ("", "+", "-"), "invalid numeric code")
    return int(value)


def band(value, bins):
    for low, high, label in bins:
        if low <= value <= high:
            return label
    raise ValueError("value outside frozen bins")


def hours(value):
    return "not_applicable" if value.strip() in ("", "0") else band(integer(value), HOUR_BINS)


def category(value):
    return value.strip() or "(blank)"


def context(house, type_column):
    kind = house[type_column]
    require(kind in ("1", "2", "3"), "unknown HU/GQ code")
    if kind != "1":
        return "GQ_institutional" if kind == "2" else "GQ_noninstitutional"
    require(house["R18"] in ("0", "1") and house["R65"] in ("0", "1", "2"), "HU presence universe differs")
    return "HU_u18_" + str(int(house["R18"] == "1")) + "_65_" + str(int(house["R65"] != "0"))


def add(table, key, weights):
    row = table.setdefault(key, [0, [0] * 81])
    row[0] += 1
    for i, weight in enumerate(weights):
        row[1][i] += weight


def sdr(values):
    require(len(values) == 81, "SDR requires full estimate and80 replicates")
    return math.sqrt(math.fsum((x - values[0])**2 for x in values[1:]) / 20)


def summarize(table, prefix_length):
    total = [sum(x[1][i] for x in table.values()) for i in range(81)]
    groups = {}
    for key, (_, values) in table.items():
        group = groups.setdefault(key[:prefix_length], [0] * 81)
        for i, value in enumerate(values):
            group[i] += value
    rows = []
    for key, (n, values) in sorted(table.items()):
        group = groups[key[:prefix_length]]
        # Undefined replicate ratios are retained as unestimated, never clipped.
        joint = [v / d for v, d in zip(values, total)] if all(total) else None
        conditional = [v / d for v, d in zip(values, group)] if all(group) else None
        rows.append({"key": list(key), "unweighted_n": n, "weighted_total": values[0],
                     "weighted_total_se": sdr(values),
                     "joint_share": values[0] / total[0] if total[0] else None,
                     "joint_share_se": sdr(joint) if joint is not None else None,
                     "conditional_share": values[0] / group[0] if group[0] else None,
                     "conditional_share_se": sdr(conditional) if conditional is not None else None,
                     "replicate_ratio_unestimated": joint is None or conditional is None})
    return {"person_weighted_total": total[0], "unweighted_n": sum(x[0] for x in table.values()), "cells": rows}


def analyze(year, root, guard, file_receipts):
    house, description = {}, file_receipts[(year, "h")]
    for row in records(root / description["name"], HOUSING, guard, description):
        require(row["SERIALNO"] not in house, "duplicate household key")
        house[row["SERIALNO"]] = row
    type_column = description["type_column"]
    seen, members = set(), collections.Counter()
    under18, over65 = collections.Counter(), collections.Counter()
    tables = {name: {} for name in ("age_school_employment", "industry_hours", "household_employment")}
    counts, blank, allocated = collections.Counter(), collections.Counter(), collections.Counter()
    probe, negative = [0] * 81, 0
    derived = root / f"{year}-person-context.csv"
    output_fields = list(PERSON + FLAGS + WEIGHTS) + ["source_state", "unit_type", "household_context", "age_bin", "hours_bin", "employment_universe", "civilian_employed"]
    with derived.open("x", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=output_fields)
        writer.writeheader()
        description = file_receipts[(year, "p")]
        for row in records(root / description["name"], PERSON + FLAGS + WEIGHTS, guard, description):
            key = row["SERIALNO"], row["SPORDER"]
            require(key not in seen, "duplicate person key")
            seen.add(key)
            require(key[0] in house, "person without housing/GQ reference")
            h = house[key[0]]
            age = integer(row["AGEP"])
            age_bin = band(age, AGE_BINS)
            esr, cow = category(row["ESR"]), category(row["COW"])
            eligible = age >= 16 and esr in ("1", "2", "3", "4", "5", "6")
            require(eligible if age >= 16 else esr in ("(blank)", "0"), "ESR universe/code mismatch")
            employed = eligible and esr in ("1", "2")
            ctx, hour = context(h, type_column), hours(row["WKHP"])
            weights = [integer(row[name]) for name in ("PWGTP",) + WEIGHTS]
            require(weights[0] >= 1, "invalid full person weight")
            negative += sum(x < 0 for x in weights[1:])
            cell = (age_bin, category(row["SCHL"]), esr, cow)
            add(tables["age_school_employment"], cell, weights)
            if cell == ("25_34", "21", "1", "1"):
                for i, w in enumerate(weights):
                    probe[i] += w
            if employed:
                add(tables["industry_hours"], (category(row["INDP"]), hour), weights)
            if eligible:
                add(tables["household_employment"], (ctx, esr), weights)
            counts[ctx] += 1
            members[key[0]] += 1
            under18[key[0]] += age < 18
            over65[key[0]] += age >= 65
            for name in PERSON:
                blank[name] += not row[name].strip()
            for name in FLAGS:
                require(row[name].strip() in ("", "0", "1"), "unknown allocation flag")
                allocated[name] += row[name].strip() == "1"
            writer.writerow({**{c: row[c] for c in PERSON + FLAGS + WEIGHTS},
                             "source_state": row[description["state_column"]], "unit_type": h[type_column],
                             "household_context": ctx, "age_bin": age_bin, "hours_bin": hour,
                             "employment_universe": int(eligible), "civilian_employed": int(employed)})
        target.flush()
        os.fsync(target.fileno())
    for serial, h in house.items():
        if h[type_column] == "1":
            require(members[serial] == integer(h["NP"]), "HU person count differs from NP")
            if members[serial]:
                require(int(h["R18"]) == int(under18[serial] > 0)
                        and int(h["R65"]) == min(2, over65[serial]), "HU composition differs from presence recodes")
    return {"year": year, "household_records": len(house), "person_records": len(seen),
            "unique_person_keys": True, "many_to_one_housing_join": True,
            "HU_NP_R18_R65_checked": True, "HU_GQ_person_counts": dict(counts),
            "blank_original_counts": dict(blank), "allocated_flag1_counts": dict(allocated),
            "negative_replicate_weight_values": negative,
            "tables": {name: summarize(table, 2 if name == "age_school_employment" else 1) for name, table in tables.items()},
            "arithmetic_probe": {"key": ["25_34", "21", "1", "1"], "full_and80_weight_totals": probe}}


def run(plan, output, safety_root):
    require(plan == metadata_plan(), "plan or execution source differs from frozen specification")
    require(not output.exists() and ".." not in output.parts
            and not any(p.is_symlink() for p in (output, *output.parents)), "output must be fresh and non-symlink")
    guard = Guard((safety_root, output.parent))
    guard.check(space=True)
    output.mkdir(mode=0o700)
    save(output / "frozen-plan.json", plan)
    receipts, result, status, error = {}, None, "PARTIAL_NOT_ACCEPTED", None
    output_refs = []
    try:
        for item in plan["source_files"]:
            entry = download(item, output, guard)
            receipts[(item["year"], item["kind"])] = entry
            save(output / (item["name"] + ".download.json"), entry)
        result = {"status": "DATA_MEASUREMENT_DIAGNOSTIC_ONLY", **plan["status"],
                  "years": [analyze(y, output, guard, receipts) for y in plan["years"]]}
        output_refs.append(save(output / "summary.json", result))
        output_refs.extend(file_digest(output / f"{y}-person-context.csv", guard) for y in plan["years"])
        guard.check(space=True)
        require(digest(Path(__file__).read_bytes()) == plan["implementation_sha256"], "execution source changed")
        status = "DATA_MEASUREMENT_DIAGNOSTIC_ONLY"
    except Exception as exc:
        error = type(exc).__name__
        raise
    finally:
        save(output / "manifest.json", {"schema": plan["schema"], "status": status,
             **plan["status"], "error_class": error, "plan_sha256": digest(canonical(plan)),
             "implementation_sha256": plan["implementation_sha256"],
             "files": list(receipts.values()), "output_files": output_refs,
             "counters": guard.observation()})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan"); p.add_argument("--output", required=True, type=Path)
    p = sub.add_parser("run"); p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--plan-sha256", required=True); p.add_argument("--output", type=Path, required=True)
    p.add_argument("--safety-root", type=Path, required=True)
    p.add_argument("--download-official-data", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "plan":
        s = os.statvfs(args.output.parent)
        require(s.f_bavail*s.f_frsize >= 2*1024**3, "2GiB freefloor")
        ref = save(args.output, metadata_plan())
        print(json.dumps(ref)); return 0
    require(args.download_official_data, "explicit official-data download flag required")
    raw = args.plan.read_bytes()
    require(digest(raw) == args.plan_sha256, "frozen plan SHA differs")
    result = run(json.loads(raw), args.output.absolute(), args.safety_root)
    print(json.dumps({"status": result["status"], "formal_worlds": 0, "calibration_accepted": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

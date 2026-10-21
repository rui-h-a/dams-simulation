"""Versioned observed-row entry profiles, not an ability or care-time estimator.

The v1 domain is RI household residents aged 18--64, currently civilian
employed (ESR 1/2), class-of-worker 1, in one declared ACS vintage. Full input
rows and all co-residents remain in the pool. Selection uses positive PWGTP;
signed survey replicates are retained as observations, never probabilities.
These are resident backgrounds, not an employer sampling frame. Co-residents
are context, not additional hires. Reusing a vintage for recruits is a declared
stationary-background scenario, not an observed hiring-transition process.
No Model, network, latent-state generator or mutable RNG cursor is imported.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field, fields
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

ALGORITHM = "whole-row-pwgtp-integer-cdf-v1"
PROFILE_SCHEMA = "dams-observed-entry-profile-1"
POOL_SCHEMA = "dams-observed-population-pool-1"
MANIFEST_SCHEMA = "dams-observed-population-manifest-1"
DOCUMENT_LIMIT = 64 * 1024**2
FLAGS = ("FAGEP", "FSCHLP", "FSEXP", "FCOWP", "FESRP", "FWAGP",
         "FWKHP", "FWKWNP", "FJWMNP", "FJWTRNSP")
REPLICATES = tuple("PWGTP" + str(i) for i in range(1, 81))
PERSON_FIELDS = ("SERIALNO", "SPORDER", "PUMA", "AGEP", "SEX", "SCHL", "ESR",
                 "COW", "INDP", "OCCP", "WKHP", "WKWN", "WAGP", "ADJINC",
                 "JWMNP", "JWTRNS", "RELSHIPP", "GCL", "GCR", "GCM", "PWGTP")
UNMEASURED = ("exact_birth_date", "appointment_tenure", "employment_contract",
              "skill", "judgment", "personality", "motivation", "trust",
              "caregiver_role", "care_hours", "financial_strain", "health_risk",
              "physical_capacity", "language", "origin", "ethnicity",
              "education_to_learning_effect", "hiring_hazard")


def _require(ok, reason):
    if not ok:
        raise ValueError(reason)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       allow_nan=False, ensure_ascii=True) + "\n").encode()


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _sha(value):
    _require(type(value) is str and re.fullmatch("[0-9a-f]{64}", value), "invalid SHA256")
    return value


def parse_document(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            _require(key not in out, "duplicate JSON key")
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def domain(reference_year):
    _require(type(reference_year) is int and reference_year in (2023, 2024), "unsupported ACS vintage")
    return {"reference_year": reference_year, "residence_state": "44", "unit_type": "1",
            "age_completed_min": 18, "age_completed_max": 64,
            "ESR": ["1", "2"], "COW": ["1"],
            "interpretation": "conditional RI resident worker backgrounds, not a firm roster",
            "year2024_is_not_unseen": True}


def measurement_mapping():
    return {"schema": "dams-observed-measurement-map-1", "version": 1,
            "units": {"age_completed_years": "completed calendar years, not exact birthday",
                      "education_code": "SCHL attainment category, not years or ability",
                      "employment_status_code": "ESR released categorical code",
                      "class_of_worker_code": "COW released categorical code",
                      "industry_code": "INDP released industry classification code",
                      "occupation_code": "OCCP released occupation classification code",
                      "usual_weekly_hours_lower_bound": "hours/week;99 means released interval99plus",
                      "worked_from_home": "JWTRNS11 indicator in reported reference commute universe",
                      "usual_weekly_hours": "reported usual paid hours/week in past12months;99 is lower bound",
                      "weeks_worked_past12": "reported weeks worked in past12months",
                      "wage_income_past12": "released rounded/topcoded past12month wage dollars, unadjusted",
                      "adjinc_raw": "integer income adjustment, divide by1000000",
                      "commute_oneway_minutes": "released one-way minutes on reference commute;topcoding may apply",
                      "sex_code": "ACS SEX category, not gender identity",
                      "household_context": "co-residence and reported relationships, not dependency or care hours"},
            "no_conversion": list(UNMEASURED),
            "missing_rule": "retain raw blank/zero/universe separately; do not fillna0",
            "background_rule": "no sex/origin to ability, integrity, care role or eligibility coefficient",
            "topcode_rule": "retain released values; no tail reconstruction or inferred censor flag",
            "calendar_rule": "no future year refresh, exact birthday, schedule or transition inferred"}


@dataclass(frozen=True)
class ObservedPopulationConfig:
    manifest_sha256: str
    pool_sha256: str
    mapping_sha256: str
    firm_key: str
    schema_version: int = 1

    def validate(self):
        _require(type(self.schema_version) is int and self.schema_version == 1, "unknown observed population config")
        for value in (self.manifest_sha256, self.pool_sha256, self.mapping_sha256):
            _sha(value)
        _require(type(self.firm_key) is str and 0 < len(self.firm_key) <= 128
                 and self.firm_key.isascii() and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", self.firm_key), "invalid firm key")
        return self

    def to_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, value):
        _require(type(value) is dict and not set(value) - {f.name for f in fields(cls)}, "unknown observed config key")
        return cls(**value).validate()


@dataclass(frozen=True)
class EntryProfile:
    donor_key: str
    household_key: str
    reference_year: int
    age_completed_years: int
    sex_code: str
    education_code: str
    employment_status_code: str
    class_of_worker_code: str
    industry_code: str
    occupation_code: str
    usual_weekly_hours: int | None
    usual_weekly_hours_lower_bound: int | None
    weeks_worked_past12: int | None
    wage_income_past12: int | None
    adjinc_raw: int
    commute_oneway_minutes: int | None
    worked_from_home: bool | None
    _document: bytes = field(repr=False, compare=True)

    def to_dict(self):
        return parse_document(self._document)

    def to_witness(self):
        return self.to_dict()

    @property
    def raw_person(self):
        return self.to_dict()["raw_person"]

    @property
    def household_context(self):
        return self.to_dict()["household_context"]

    @property
    def missing(self):
        return self.to_dict()["missing"]

    @property
    def units(self):
        return self.to_dict()["units"]

    @property
    def provenance(self):
        return self.to_dict()["provenance"]


def _integer(value, low, high, field_name):
    _require(type(value) is str and re.fullmatch(r"-?[0-9]+", value), "invalid integer: " + field_name)
    number = int(value)
    _require(low <= number <= high, "out-of-range integer: " + field_name)
    return number


def _alias(row, names):
    found = [name for name in names if name in row]
    _require(len(found) == 1, "missing or ambiguous source alias")
    return row[found[0]]


def _raw_row(row, required):
    _require(type(row) is dict and set(required) <= set(row) and len(row) <= 1024, "source row schema differs")
    _require(all(type(k) is str and type(v) is str and len(k) <= 128 and len(v) <= 4096
                 for k, v in row.items()), "source rows must preserve bounded strings")
    return dict(row)


def _house_key(row, year):
    state = _alias(row, ("STATE", "ST", "source_state"))
    _require(state == "44", "source is not RI")
    serial = row["SERIALNO"]
    _require(re.fullmatch(str(year) + r"(?:HU|GQ)[0-9]{7}", serial), "serial/vintage differs")
    return f"{year}/44/{serial}"


def _person_key(row, year):
    return _house_key(row, year) + "/" + str(_integer(row["SPORDER"], 1, 100, "SPORDER"))


def _observations(row):
    observed, missing = {}, {}
    def optional(name, low, high, universe):
        value = row[name].strip()
        missing[name] = {"raw": row[name], "status": "outside_recorded_universe" if value in ("", "0") else "released_value", "universe": universe}
        return None if value in ("", "0") else _integer(value, low, high, name)
    weekly = optional("WKHP", 1, 99, "age16+ worked in past12months")
    weeks = optional("WKWN", 1, 52, "age16+ worked in past12months")
    if weekly == 99:
        missing["WKHP"]["status"] = "interval_99_plus"
    transport = row["JWTRNS"].strip()
    _require(transport in ("", "0") or 1 <= _integer(transport, 1, 12, "JWTRNS") <= 12, "unknown travel mode")
    home_work = None if transport in ("", "0") else int(transport) == 11
    commute = optional("JWMNP", 1, 200, "reference worker excluding worked-from-home")
    _require(not home_work or commute is None, "home-work and commute universe conflict")
    wage_raw = row["WAGP"].strip()
    wage = None if wage_raw in ("", "-1") else _integer(wage_raw, 0, 999999, "WAGP")
    missing["WAGP"] = {"raw": row["WAGP"], "status": "outside_recorded_universe" if wage is None else "released_rounded_topcoded_value"}
    _require(row["SEX"] in ("1", "2"), "unknown measured SEX code")
    _integer(row["SCHL"], 1, 24, "SCHL")
    observed.update(age_completed_years=int(row["AGEP"]), sex_code=row["SEX"], education_code=row["SCHL"],
                    employment_status_code=row["ESR"], class_of_worker_code=row["COW"],
                    industry_code=row["INDP"], occupation_code=row["OCCP"],
                    usual_weekly_hours=None if weekly == 99 else weekly,
                    usual_weekly_hours_lower_bound=99 if weekly == 99 else None,
                    weeks_worked_past12=weeks, wage_income_past12=wage,
                    adjinc_raw=_integer(row["ADJINC"], 1, 9999999, "ADJINC"),
                    commute_oneway_minutes=commute, worked_from_home=home_work)
    for name in ("AGEP", "SEX", "SCHL", "ESR", "COW", "INDP", "OCCP", "JWTRNS", "GCL", "GCR", "GCM", "RELSHIPP"):
        missing[name] = {"raw": row[name], "status": "blank_universe_or_unreported" if not row[name].strip() else "released_category"}
    return observed, missing


def _prepared(pool):
    _require(type(pool) is dict and set(pool) == {"schema", "domain", "source_manifest", "people", "housing"}, "pool schema differs")
    _require(pool["schema"] == POOL_SCHEMA and type(pool["source_manifest"]) is dict, "pool source missing")
    year = pool["domain"].get("reference_year")
    _require(pool["domain"] == domain(year), "population domain differs")
    _require(pool["source_manifest"].get("reference_year") == year, "source vintage differs")
    _require(type(pool["people"]) is list and type(pool["housing"]) is list
             and 0 < len(pool["people"]) <= 100000 and 0 < len(pool["housing"]) <= 100000, "bounded source roster required")
    people, houses, memberships = {}, {}, {}
    for raw in pool["housing"]:
        row = _raw_row(raw, ("SERIALNO", "PUMA", "NP", "R18", "R65"))
        key = _house_key(row, year)
        _require(key not in houses, "duplicate housing identity")
        _require(_alias(row, ("TYPEHUGQ", "TYPE")) in ("1", "2", "3"), "unknown HU/GQ code")
        _integer(row["NP"], 0, 100, "NP")
        houses[key] = row
        memberships[key] = []
    for raw in pool["people"]:
        row = _raw_row(raw, PERSON_FIELDS + FLAGS + REPLICATES)
        key, home = _person_key(row, year), _house_key(row, year)
        _require(key not in people, "duplicate person identity")
        _require(home in houses and row["PUMA"] == houses[home]["PUMA"], "person/housing join differs")
        _integer(row["AGEP"], 0, 99, "AGEP")
        _integer(row["PWGTP"], 1, 2**63-1, "PWGTP")
        for name in REPLICATES:
            _integer(row[name], -(2**63)+1, 2**63-1, name)
        for name in FLAGS:
            _require(row[name].strip() in ("", "0", "1"), "unknown allocation flag")
        people[key] = row
        memberships[home].append(key)
    for key, row in houses.items():
        members = memberships[key]
        if _alias(row, ("TYPEHUGQ", "TYPE")) == "1":
            _require(len(members) == int(row["NP"]), "household roster differs from NP")
            if members:
                _require(row["R18"] == str(int(any(int(people[x]["AGEP"]) < 18 for x in members)))
                         and row["R65"] == str(min(2, sum(int(people[x]["AGEP"]) >= 65 for x in members))), "household composition differs")
    eligible = tuple(sorted(k for k, row in people.items()
                            if _alias(houses[_house_key(row, year)], ("TYPEHUGQ", "TYPE")) == "1"
                            and 18 <= int(row["AGEP"]) <= 64 and row["ESR"] in ("1", "2") and row["COW"] == "1"))
    _require(eligible, "empty declared employee background support")
    cdf, total = [], 0
    for key in eligible:
        _observations(people[key])
        total += int(people[key]["PWGTP"])
        _require(total <= 2**63-1, "CDF integer ceiling")
        cdf.append(total)
    return year, people, houses, memberships, eligible, tuple(cdf)


def build_documents(person_rows, household_rows, *, source_manifest):
    """Pure construction; the caller must separately verify actual source bytes."""
    _require(type(source_manifest) is dict, "source manifest required")
    year = source_manifest.get("reference_year")
    pool = {"schema": POOL_SCHEMA, "domain": domain(year), "source_manifest": source_manifest,
            "people": sorted((dict(r) for r in person_rows), key=lambda r: _person_key(r, year)),
            "housing": sorted((dict(r) for r in household_rows), key=lambda r: _house_key(r, year))}
    _, people, _, _, eligible, cdf = _prepared(pool)
    mapping = measurement_mapping()
    manifest = {"schema": MANIFEST_SCHEMA, "algorithm": ALGORITHM, "domain": domain(year),
                "pool": {"file": "pool.json", "sha256": sha256(canonical(pool)), "bytes": len(canonical(pool))},
                "mapping": {"file": "mapping.json", "sha256": sha256(canonical(mapping)), "bytes": len(canonical(mapping))},
                "source_manifest_sha256": sha256(canonical(source_manifest)),
                "all_person_rows": len(people), "eligible_person_rows": len(eligible),
                "total_sampling_weight": cdf[-1],
                "status": "observed input representation, not behavioral calibration or firm representativeness",
                "formal_worlds_added": 0, "calibration_accepted": False, "holdout_accepted": False}
    return {"pool": pool, "mapping": mapping, "manifest": manifest}


def _read_pinned(path, expected, bound=DOCUMENT_LIMIT):
    path = Path(path)
    _require(path.is_absolute() and ".." not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)), "unsafe input path")
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    before = path.stat()
    _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= bound, "bounded regular input required")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        _require(identity(os.fstat(fd)) == identity(before), "input FD differs")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(bound + 1)
            _require(not stream.read(1), "input exceeds bound")
        _require(len(raw) == before.st_size and identity(path.stat()) == identity(before), "input length/identity differs")
        _require(sha256(raw) == _sha(expected), "input SHA differs")
        return raw
    finally:
        os.close(fd)


class ObservedPopulationPool:
    """Verified private bytes; returned nested data are copies, never pool aliases."""
    @classmethod
    def from_documents(cls, pool, mapping, manifest, *, expected_manifest_sha256,
                       expected_pool_sha256, expected_mapping_sha256):
        raw_pool, raw_mapping, raw_manifest = map(canonical, (pool, mapping, manifest))
        _require(sha256(raw_manifest) == _sha(expected_manifest_sha256)
                 and sha256(raw_pool) == _sha(expected_pool_sha256)
                 and sha256(raw_mapping) == _sha(expected_mapping_sha256), "population document pins differ")
        _require(mapping == measurement_mapping(), "unknown measurement mapping")
        _require(type(manifest) is dict and manifest.get("schema") == MANIFEST_SCHEMA
                 and manifest.get("algorithm") == ALGORITHM, "manifest algorithm differs")
        for name, raw in (("pool", raw_pool), ("mapping", raw_mapping)):
            ref = manifest[name]
            _require(set(ref) == {"file", "sha256", "bytes"} and ref["file"] == name + ".json"
                     and ref["sha256"] == sha256(raw) and type(ref["bytes"]) is int
                     and ref["bytes"] == len(raw), "manifest content reference differs")
        obj = cls()
        obj._pool = parse_document(raw_pool)
        obj._year, obj._people, obj._houses, obj._members, obj._eligible, obj._cdf = _prepared(obj._pool)
        _require(manifest == build_documents(obj._pool["people"], obj._pool["housing"], source_manifest=obj._pool["source_manifest"])["manifest"], "manifest definition or count differs")
        obj.manifest_sha256, obj.pool_sha256, obj.mapping_sha256 = expected_manifest_sha256, expected_pool_sha256, expected_mapping_sha256
        return obj

    @classmethod
    def from_files(cls, manifest_path, *, expected_manifest_sha256, expected_pool_sha256, expected_mapping_sha256):
        path = Path(manifest_path).absolute()
        raw_manifest = _read_pinned(path, expected_manifest_sha256, 2 * 1024**2)
        manifest = parse_document(raw_manifest)
        refs = {}
        for name, expected in (("pool", expected_pool_sha256), ("mapping", expected_mapping_sha256)):
            ref = manifest[name]
            _require(ref["file"] == name + ".json" and ref["sha256"] == expected, "relative population reference differs")
            refs[name] = parse_document(_read_pinned(path.parent / ref["file"], expected))
        obj = cls.from_documents(refs["pool"], refs["mapping"], manifest,
                                expected_manifest_sha256=expected_manifest_sha256,
                                expected_pool_sha256=expected_pool_sha256,
                                expected_mapping_sha256=expected_mapping_sha256)
        _read_pinned(path, expected_manifest_sha256, 2 * 1024**2)
        return obj

    def verify_config(self, config):
        _require(type(config) is ObservedPopulationConfig, "typed observed config required")
        config.validate()
        _require((config.manifest_sha256, config.pool_sha256, config.mapping_sha256)
                 == (self.manifest_sha256, self.pool_sha256, self.mapping_sha256), "configured population differs")

    def draw(self, rng, *, firm_key, person_token):
        ObservedPopulationConfig(self.manifest_sha256, self.pool_sha256, self.mapping_sha256, firm_key).validate()
        _require(type(person_token) is str and 0 < len(person_token) <= 256, "stable person token required")
        _require(type(rng.seed) is int and type(rng.world) is int
                 and (rng.world_context is None or type(rng.world_context) is str), "keyed world RNG required")
        u = rng.uniform("observed_population_donor_v1", firm_key, person_token, self.manifest_sha256)
        _require(type(u) is float and math.isfinite(u) and 0 <= u < 1, "CDF draw must lie in [0,1)")
        numerator, denominator = u.as_integer_ratio()
        ticket = numerator * self._cdf[-1] // denominator
        key = self._eligible[bisect_right(self._cdf, ticket)]
        row = self._people[key]
        home = _house_key(row, self._year)
        household = {"raw_housing": self._houses[home],
                     "members": [{"person_key": k, "raw_person": self._people[k]} for k in sorted(self._members[home])]}
        observed, missing = _observations(row)
        body = {"schema": PROFILE_SCHEMA, "donor_key": key, "household_key": home,
                "reference_year": self._year, "observed": observed, "raw_person": row,
                "household_context": household, "missing": missing,
                "allocation_flags": {k: row[k] for k in FLAGS},
                "units": measurement_mapping()["units"], "unmeasured": list(UNMEASURED),
                "provenance": {"algorithm": ALGORITHM, "manifest_sha256": self.manifest_sha256,
                               "pool_sha256": self.pool_sha256, "mapping_sha256": self.mapping_sha256,
                               "source_manifest_sha256": sha256(canonical(self._pool["source_manifest"])),
                               "row_context_sha256": sha256(canonical({"row": row, "context": household})),
                               "sampling_weight": int(row["PWGTP"]), "total_sampling_weight": self._cdf[-1],
                               "ticket": ticket, "rng_seed": rng.seed, "rng_world": rng.world,
                               "rng_world_context": rng.world_context, "firm_key": firm_key, "person_token": person_token}}
        body["profile_sha256"] = sha256(canonical(body))
        return EntryProfile(key, home, self._year, **observed, _document=canonical(body))

    def restore_profile(self, witness, *, rng, firm_key, person_token):
        _require(type(witness) is dict, "observed entry witness required")
        expected = self.draw(rng, firm_key=firm_key, person_token=person_token)
        _require(canonical(witness) == expected._document, "restored observed profile or source differs")
        return expected

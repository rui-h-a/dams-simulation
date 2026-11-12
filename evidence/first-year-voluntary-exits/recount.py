"""Recount synthetic first-year exits for readers holding the matching state archives."""

from argparse import ArgumentParser
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import tarfile

from verify import ARMS, COUNTS, STUDIES, WINDOW, read_rows, require


def decode(data, expected_hash, label):
    require(sha256(data).hexdigest() == expected_hash, f"SHA-256 differs for {label}")
    return json.loads(data)


def check_arm(receipt_bytes, state_bytes, study, world, arm, row):
    receipt = decode(receipt_bytes, row["arm_receipt_sha256"], (study, world, arm, "arm.json"))
    saved = decode(state_bytes, row["final_state_sha256"], (study, world, arm, "final_state.json"))
    _, branch, end = STUDIES[study]
    require((receipt["branch_day"], receipt["days"]) == (branch, end), "Unexpected horizon or branch day")
    require(receipt["files"]["final_state_json"] == row["final_state_sha256"], "State and arm receipt disagree")
    config = receipt["config"]
    expected_n = 300 if study == "founding-300" else 10000
    require((config["n"], config["world"], receipt["arm"]) == (expected_n, world, arm), "Unexpected arm identity")
    state = saved["state"]
    people = state["people"]
    require(state["day"] == end and len(people) == receipt["ever_people"], "Incomplete final roster")
    first_entry = 1 if study == "founding-300" else branch
    quits = early_quits = recruits = recruit_quits = 0
    for person in people:
        entry = person["entered_day"]
        exit_day = person["exited_day"]
        stop = end if exit_day is None else exit_day
        duration = stop - entry
        require(0 <= entry <= stop <= end and duration == person["active_calendar_days"], "Invalid roster exposure")
        voluntary = person["exit_reason"] == "voluntary_exit"
        early = voluntary and duration < WINDOW
        eligible = first_entry <= entry <= end - WINDOW
        require(not voluntary or exit_day is not None and exit_day < end, "Unobserved voluntary exit")
        quits += voluntary
        early_quits += early
        recruits += eligible
        recruit_quits += early and eligible
    require((quits, early_quits, recruits, recruit_quits) == tuple(row[field] for field in COUNTS),
            f"Aggregate counts differ for {(study, world, arm)}")


def check_founding(root, rows):
    for world in STUDIES["founding-300"][0]:
        for arm in ARMS:
            path = root / f"n300-startup-organic-pre0-post5-w{world}" / arm
            key = "founding-300", world, arm
            check_arm((path / "arm.json").read_bytes(), (path / "final_state.json").read_bytes(), *key, rows[key])


def check_main(root, rows):
    files = ("arm.json", "final_state.json")
    for world in STUDIES["main-10000"][0]:
        path = root / f"dams-a12-n10000-w{world}.tar"
        with tarfile.open(path, "r") as archive:
            entries = {}
            for member in archive:
                parts = PurePosixPath(member.name).parts
                if not member.isfile() or len(parts) < 2 or parts[-2] not in ARMS or parts[-1] not in files:
                    continue
                key = parts[-2], parts[-1]
                require(key not in entries, f"Duplicate tar member {key}")
                entries[key] = member
            require(len(entries) == len(ARMS) * len(files), "Incomplete tar arm roster")
            for arm in ARMS:
                data = []
                for filename in files:
                    with archive.extractfile(entries[arm, filename]) as stream:
                        data.append(stream.read())
                key = "main-10000", world, arm
                check_arm(*data, *key, rows[key])


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--founding", type=Path, help="Root of the retained 300-member founding study")
    parser.add_argument("--main", type=Path, help="Directory holding the ten main study tar archives")
    args = parser.parse_args()
    if args.founding is None and args.main is None:
        parser.error("provide at least one source directory")
    rows = read_rows()
    if args.founding:
        check_founding(args.founding, rows)
        print("300-member founding study: 228 arms matched")
    if args.main:
        check_main(args.main, rows)
        print("10,000-member main study: 40 arms matched")


if __name__ == "__main__":
    main()

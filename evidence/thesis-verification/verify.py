#!/usr/bin/env python3
"""Verify the published arithmetic from fixed DAMS simulation summaries."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from urllib.request import HTTPRedirectHandler, Request, build_opener


RAW_BASE = "https://raw.githubusercontent.com/rui-h-a/dams-simulation"
# Directory, public commit, then (filename, bytes, SHA-256) for each input.
PACKAGES = (
    (
        "r11-10000-paired-results", "2c921307a0aad405b515426c43c8ab156762fcd0", (
            ("SHA256SUMS", 234,
             "f86119af3b19194193b6702cb96fc7ff59548859b64b0c8b5cd4cee033ed8dfb"),
            ("figure.json", 11021,
             "60a4971aaaec50eac87582438c3e34617447114a4c6b080afba269db9bb5df01"),
            ("pooled.json", 98146,
             "9507497f0e295c93fcbf31ed6d29a9a02cb2532dbd10ab63d07973420de1eecc"),
            ("roster.json", 239,
             "ee1c8bc5378089291b9ffde35a97c860ad5ddc500714355bd5e8ee037ed377c9"),
            ("verify.py", 4109,
             "24bfe241654ba295ea04d18786d649e2d61eee33de1c731d6667faf8d1f40e25"),
        ),
    ),
    (
        "r11-10000-precision", "bffe007794a022a2efb9dc9ce9d2c5c69d2e7c6a", (
            ("summary.json", 19776,
             "9a282aaa4e677c4850a4e498e27101caff1b75190f0b75c483e73b57a4fd862c"),
            ("verify_precision.py", 11843,
             "39fa6948ba3c91ab100f90de83303eeabd27f1b93619444d7f846d69609715f8"),
        ),
    ),
    (
        "r11-10000-resources", "968ecadacedd88ad815a5f19de51de2011e853d0", (
            ("arm-resources.csv", 3930,
             "ecfe2af861458f9421043744a522c567c73e7e0f1955c18f943071013fc315b3"),
        ),
    ),
    (
        "r11-10000-scheduling", "aa7e4f77c685064825a307f9eb3b4860da4e0936", (
            ("prehistory.csv", 416,
             "72acfacf045d81cd90de467e7b39864b44c697b5976e8d927b7438a507c6df20"),
            ("results.json", 2265,
             "a9d139e0b1f9ed5eed2ac0ab3545c4a4cb4bfac18b3944b471e2aea3e0c009bb"),
            ("verify.py", 5539,
             "47d23bc2cd77f014aa144aff2c601df1d44ba7a2b3854bc1cf0a2ef7bff90dde"),
        ),
    ),
    (
        "record-procedure", "3ce886a20d9a1f088814909922adffae08d6c5b8", (
            ("manifest.json", 3307,
             "eced3c0379e7c4614d78055b91b633d1938af0dc849a838c2a2a5e1e2e903ba1"),
            ("retained-summary-rows.csv", 21035,
             "b112d22601aed200da8287f533a2e24d8c76f8e094fa6aea9b30d72d2abde3db"),
            ("verify.py", 4359,
             "f1eea57ec8523dd577e73a123c323aaaae8f7bf93e7548a1a337847455ce58b7"),
        ),
    ),
    (
        "startup-marginal", "3ce886a20d9a1f088814909922adffae08d6c5b8", (
            ("provenance.json", 1275,
             "27b041747d86dbaf19d3e12a8da41edb2a752b72aa0cdf9ad4c0ae4daac717fd"),
            ("results.json", 4514,
             "6b214c54dd2dcdfb5964e4dcb65c311649d5b0496daaa7f6aa214376fdb1ed05"),
            ("verify.py", 6579,
             "60c51bd2b2babfa13d9ecd59523775c8b49f4e7e8d7af32dbafc13f48ce2f57f"),
            ("world-endpoints.csv", 11728,
             "f8a2725ca6a294f8931db90c1e1ab8f7af4346d2f00ff28da164d01a9a8dca44"),
        ),
    ),
    (
        "acquisition-matched-worlds", "ce8c43763b823c6636c56244ab4527563354a2d2", (
            ("ma-paired-aggregates.json", 135129,
             "4aab653eeb21e454cbcf6f665e7d96137c4c46971c11c02d6ad3dcaf58829023"),
            ("ma-paired-estimates.json", 13526,
             "5378efb8fd4dc0303e82b7c01e5cd241a725f45ffbb2305a29cee0f9fd6722c7"),
            ("verify_ma.py", 5586,
             "8a11a93314c7ea941b20842619c3f214b36a5920d6de762cf530ecb834cfde65"),
        ),
    ),
    (
        "startup-regret-normalization", "ce38268c9d432df3798ff9a532df00fab6eb0eb9", (
            ("startup-regret-aggregates.json", 211902,
             "ac6d21700b3219c56ae01128a856dfe7e98a420ec171ac3a5435327f489e4c6f"),
            ("startup-regret-estimates.json", 31798,
             "8143d6ada080fbb7c5604d34fa3d7b2c491846e6e2c9eccee8e656ef6e0357a8"),
            ("verify_startup_regret.py", 4657,
             "36309acb3a0616616ec7f7e44a1faaea0492b3b334d7726123cb9dded23867d6"),
        ),
    ),
    (
        "r11-50000-checkpoints", "416d6de4325fe5dea722634ed3106c08b83f86e9", (
            ("checkpoint-files.csv", 2494,
             "c1cf4966137264e708e490e8c9d1891aa34f5eadad5244387f160e3b5af3af27"),
            ("inventory-sizes.csv", 48257,
             "80956daaa973f02ca4012d83cddcd5127d6a786094f841740e145f19f18d1f55"),
            ("provenance.json", 1799,
             "338a628fcfcb16d27b853a833452c2234c6097a71ca9ed27b30ea2773fb41f07"),
            ("results.json", 1634,
             "8072c7ea6038d72c1436a24c1b49b706dad30784cec88d24b0c4688dbd59d1b1"),
            ("verify.py", 5735,
             "d683218cacd7e94e8469b4c8bfd35247573fff9cdac7bfe91153075f69c351f1"),
        ),
    ),
    (
        "r11-10000-terminal-stock", "416d6de4325fe5dea722634ed3106c08b83f86e9", (
            ("results.json", 5437,
             "2fab39af6136574c470cd09f73abedf07e71ac8ed188297a24be5584230f544a"),
            ("terminal.csv", 15551,
             "eaff24a4232c6c03558a275900dcfc7d7a3e1083451c228c304863d15812ed46"),
            ("verify.py", 4709,
             "269d942df8486157434ac90b30dd73a822954d5fc8e73216ecd9f36ed37840ec"),
        ),
    ),
)

CORE = "r11-10000-paired-results"
PRECISION = "r11-10000-precision"
RESOURCE = "r11-10000-resources"
SCHEDULE = "r11-10000-scheduling"
RECORD = "record-procedure"
MARGINAL = "startup-marginal"
ACQUISITION = "acquisition-matched-worlds"
REGRET = "startup-regret-normalization"
CHECKPOINT = "r11-50000-checkpoints"
STOCK = "r11-10000-terminal-stock"
CORE_REPORT = [
    "Verified 75 paired outcome summaries across 10 worlds.",
    "Verified plotted differences and adjusted intervals for 12 primary comparisons.",
]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_cached(path, size):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"Cache entry is not a regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise ValueError(f"Cache entry is not a regular file: {path}")
        return file.read(size + 1)


def require_pin(data, size, digest, label):
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"Byte count or SHA-256 mismatch: {label}")


def obtain(cache, opener, package, commit, name, size, digest):
    cached = cache / digest
    label = f"{package}/{name}"
    try:
        data = read_cached(cached, size)
    except FileNotFoundError:
        if opener is None:
            raise ValueError(f"Offline cache is missing: {label}") from None
        url = f"{RAW_BASE}/{commit}/evidence/{label}"
        request = Request(url, headers={"User-Agent": "DAMS thesis evidence verifier"})
        with opener.open(request, timeout=20) as response:
            data = response.read(size + 1)
        require_pin(data, size, digest, label)
        with tempfile.NamedTemporaryFile(dir=cache, prefix="download-", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            try:
                temporary.write(data)
                temporary.close()
                os.replace(temporary_path, cached)
            finally:
                temporary_path.unlink(missing_ok=True)
        return data
    require_pin(data, size, digest, f"{label} (cached as {cached})")
    return data


def compare(actual, expected, label):
    """Allow final-digit floating-point variation while checking the whole report."""
    if type(actual) is not type(expected):
        raise ValueError(f"Unexpected result type at {label}")
    if isinstance(expected, dict):
        if actual.keys() != expected.keys():
            raise ValueError(f"Unexpected result fields at {label}")
        for key in expected:
            compare(actual[key], expected[key], f"{label}.{key}")
    elif isinstance(expected, list):
        if len(actual) != len(expected):
            raise ValueError(f"Unexpected result length at {label}")
        for index, (value, reference) in enumerate(zip(actual, expected)):
            compare(value, reference, f"{label}[{index}]")
    else:
        same = math.isclose(actual, expected, rel_tol=1e-9) if isinstance(expected, float) else actual == expected
        if not same:
            raise ValueError(f"Unexpected result at {label}")


def run_report(workspace, package, script, *args):
    command = [sys.executable, "-I", "-S", "-B", str(workspace / package / script), *map(str, args)]
    result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=90)
    if result.returncode:
        error = result.stderr.strip() or result.stdout.strip()
        raise ValueError(f"{package} verifier failed (exit {result.returncode}): {error}")
    return json.loads(result.stdout)


def read_report(workspace, package, filename):
    return json.loads((workspace / package / filename).read_bytes())


def verify(workspace):
    precision = run_report(workspace, PRECISION, "verify_precision.py", workspace / CORE)
    compare(precision, read_report(workspace, PRECISION, "summary.json"), PRECISION)
    # The precision script hashes all five paired-result files and runs the core verifier.
    compare(precision["source"]["original_verifier_output"], CORE_REPORT, CORE)

    scheduling = run_report(workspace, SCHEDULE, "verify.py", workspace / RESOURCE / "arm-resources.csv")
    compare(scheduling, read_report(workspace, SCHEDULE, "results.json"), SCHEDULE)

    record = run_report(workspace, RECORD, "verify.py")
    manifest = read_report(workspace, RECORD, "manifest.json")
    compare(record, {
        "worlds": len(manifest["worlds"]),
        "arm_rows": manifest["arm_rows"],
        "difference": "dams-consensus minus dams-central",
        "contrasts": manifest["contrast"],
    }, RECORD)

    for package, script, expected in (
        (MARGINAL, "verify.py", "results.json"),
        (ACQUISITION, "verify_ma.py", "ma-paired-estimates.json"),
        (REGRET, "verify_startup_regret.py", "startup-regret-estimates.json"),
        (CHECKPOINT, "verify.py", "results.json"),
    ):
        compare(run_report(workspace, package, script), read_report(workspace, package, expected), package)

    stock = run_report(workspace, STOCK, "verify.py", "--print-results")
    compare(stock, read_report(workspace, STOCK, "results.json"), STOCK)

    for line in CORE_REPORT:
        print(line)
    print("Verified precision and multiplicity for 12 main-study comparisons.")
    print("Verified resource schedules for 5, 6, 8 and 10 hosts from 40 arms and 10 prehistories.")
    print("Verified four record-procedure contrasts across 65 paired worlds.")
    print("Verified six founding-startup size baselines and ten adjacent-size contrasts.")
    print("Verified acquisition contrasts across 120 matched worlds and 480 arm aggregates.")
    print("Verified startup regret normalization at 30 and 120 initial members.")
    print("Verified checkpoint timing and storage for four 50,000-member pilot arms.")
    print("Verified terminal stock and 12 paired estimates from 40 main-study arms.")
    print("Passed all nine retained-result checks.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "dams-thesis-verification",
                        help="directory for the pinned input files (default: %(default)s)")
    parser.add_argument("--offline", action="store_true", help="read the cache without making HTTP requests")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error("Python 3.11 or later is required")
    try:
        cache = args.cache.expanduser()
        if cache.is_symlink():
            raise ValueError(f"Cache directory must not be a symbolic link: {cache}")
        if not args.offline:
            cache.mkdir(parents=True, mode=0o700, exist_ok=True)
        opener = None if args.offline else build_opener(NoRedirect())
        with tempfile.TemporaryDirectory(prefix="dams-verification-") as directory:
            workspace = Path(directory)
            for package, commit, files in PACKAGES:
                target = workspace / package
                target.mkdir()
                for name, size, digest in files:
                    (target / name).write_bytes(obtain(cache, opener, package, commit, name, size, digest))
            verify(workspace)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

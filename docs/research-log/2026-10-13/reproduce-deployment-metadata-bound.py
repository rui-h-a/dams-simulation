"""Offline lower-bound reproduction of the actual003 deployment refusal.

Run: python3 -I -B reproduce-deployment-metadata-bound.py
This transcribes the frozen adapter's admission formula and actual wrapper
arguments. It deliberately uses zero stdin/script sizes: real sizes are
nonnegative, so failure already follows without private deployment bytes.
It does not import the runtime, reconstruct the full guest packet, call a
provider, execute a Model, or validate the rest of the cloud lifecycle.
"""
import json

RUNTIME_MANIFEST_SHA256 = "f73385f8c9aed38048ad41c66274e962fe0f5846760f1143224fd59e6f83fc79"
FIRST_FAULT_SHA256 = "8690ca7e485bf7bb71db7183a5a24fceee962283c6013a634495d884d179b6fd"
METADATA_CAP = 8 * 1024**2 + 1
STDERR_BOUND = 65536
MINIMUM_ARGV_OVERHEAD = 8192

def reservation(input_bytes, stdout_bound, ssh_script_bytes, argv_overhead):
    return input_bytes + stdout_bound + 1 + ssh_script_bytes + STDERR_BOUND + argv_overhead

def main():
    results = []
    for call in ("fresh-current-deployment-preparation", "install-exact-single-service"):
        stdout_bound = 8 * 1024**2
        minimum = reservation(0, stdout_bound, 0, MINIMUM_ARGV_OVERHEAD)
        assert minimum == 8462337 and minimum > METADATA_CAP
        results.append({
            "callsite": call, "metadata_cap_bytes": METADATA_CAP,
            "stdout_bound_bytes": stdout_bound,
            "minimum_complete_reservation_bytes": minimum,
            "minimum_excess_bytes": minimum - METADATA_CAP,
            "original_admission_predicate": minimum <= METADATA_CAP,
            "actual_input_and_script_bytes_not_reconstructed": True,
        })
    print(json.dumps({
        "status": "DETERMINISTIC_ADMISSION_REFUSAL_REPRODUCED_BY_LOWER_BOUND",
        "runtime_manifest_sha256": RUNTIME_MANIFEST_SHA256,
        "first_fault_sha256": FIRST_FAULT_SHA256,
        "results": results, "provider_calls": 0, "Model_calls": 0,
        "full_lifecycle_validated": False,
    }, sort_keys=True, indent=2))

if __name__ == "__main__":
    main()

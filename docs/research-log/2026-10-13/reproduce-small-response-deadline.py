"""Reproduce the last cloud deadline failure from a published closed witness.

No provider, credentials, Model, filesystem mutation or external dependency.
Values are dated observed events, not forecasts or a new GCP benchmark.
"""
from decimal import Decimal as D
import json

cutoff=D("1791891725.5421162")
stdout_complete=D("1791891725.4529843")
final_script_ready=D("1791891725.4559479")
final_reservation=D("1791891725.692175")
result={"original_whole_seconds":30,"readiness_RPC_count":4,
 "guest_failed":False,"complete_stdout":"DAMS-RETAINED-READY\n",
 "complete_stdout_bytes":20,"complete_stderr_bytes":0,
 "complete_error_file":"null\n","error_file_bytes":5,
 "remaining_after_complete_stdout_seconds":str(cutoff-stdout_complete),
 "remaining_before_final_seconds":str(cutoff-final_script_ready),
 "final_reservation_after_cutoff_seconds":str(final_reservation-cutoff),
 "final_readiness_remote_process_spawned":False,
 "comparison_original_whole_seconds":45,"comparison_four_RPC_seconds":39.990,
 "last_verified_cloud_day":1080,"formal_cloud_pairs_added":0,
 "first_fault_SHA256":"3033dd86385cc83f60964ce7a38f8d12857554a95971319749394d14963f9790",
 "complete_closed_evidence_review_SHA256":"a9ea3568bdf4739d4b2f6c29a7be40c443233888d3cdcc106f748b7b4ca06191"}
assert len(result["complete_stdout"].encode())==20
assert len(result["complete_error_file"].encode())==5
assert stdout_complete<cutoff and final_script_ready<cutoff<final_reservation
assert 30<result["comparison_four_RPC_seconds"]<45
print(json.dumps(result,sort_keys=True,indent=2))

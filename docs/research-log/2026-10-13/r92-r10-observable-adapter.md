# Retained R10 engineering observables

The observable adapter accepts the exact `longitudinal-enterprise-task-structure-10` constituent summaries and organization-task schema 2. The outer two-firm engine retains its R8 version. Calendar, configuration, source, seed, world, physical-unit scope and original receipt bindings are checked without constructing or advancing a Model.

`project_r10_engineering_observables(evidence)` requires typed, byte-pinned references to the original profile, predeclared scenarios, joint results, execution/native-restoration metadata, closed supervisor terminal and narrow mechanism/accounting receipts. The result is an `EngineeringObservablePacket` with explicit units. Corporate-group stocks and two-firm system flows remain separate.

The existing receipts cover engineering and accounting subsets. Projection does not perform a complete independent raw or economic validator, add formal worlds, calibrate parameters, certify historical blindness or complete cross-enterprise holdout validation. This engineering packet cannot enter the calibration/validation prediction-packet adapter.

Use `packet.to_dict()` or `export_r10_engineering_packet(packet)` for checked export. They verify the content checksum and reproject the original references before returning data. Nested maps remain ordinary Python objects; `dataclasses.asdict` alone is not a validation gate. Engineering claims are fixed constructor invariants.

The tests read a caller-supplied retained engineering evidence packet:

```sh
DAMS_R10_ENGINEERING_EVIDENCE_PACKET=/path/to/retained-engineering-packet.json python3 -B -m unittest discover -s tests -p 'test_enterprise_prediction_adapter_r*.py' -v
python3 -B -m unittest discover -s tests -p 'test_enterprise_validation.py' -v
```

With the reviewed retained inputs, 19 R10 controls, 14 prior adapter controls and 13 protocol controls passed; five additional independent outer-binding and packet-integrity attacks were rejected. When the environment variable is absent, the 19 retained-input integration tests explicitly skip, and their coverage must not be reported as executed. No private evidence locations or operational data are embedded in the test source. No new Model, trajectory, SQL or provider call is required by projection.

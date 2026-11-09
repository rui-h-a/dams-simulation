# Reproducing the enterprise-model results

The study archive, the published estimates, and the software that generates new
worlds serve different purposes. Use the procedure below that corresponds to
the evidence you need.

## Recompute the published 10,000-member estimates

From a checkout of
[the public evidence revision](https://github.com/rui-h-a/dams-simulation/tree/2c921307a0aad405b515426c43c8ab156762fcd0/evidence/r11-10000-paired-results):

    cd evidence/r11-10000-paired-results
    shasum -a 256 -c SHA256SUMS
    python3 -I -S -B verify.py

The three input files are the frozen roster, paired outcomes and figure values.
The verifier uses the Python standard library. It checks ten paired worlds,
recomputes 75 paired outcome summaries and verifies the plotted differences and
adjusted intervals for the 12 primary comparisons. The command needs no model
checkout beyond this bundle, external Python packages or simulation. This
arithmetic check does not establish the provenance of an input file or repeat
validation of its original event journal.

## Bind the study before regenerating it

Use the recorded revision, the effective configuration for each arm, the
assigned world identifiers and random seed, the execution and analysis
dependencies, and the applicable protocol and thresholds. An engine
`source_sha256` identifies the sorted top-level `dams_sim/*.py` files. The
same digest can occur in revisions with different study drivers, design code
or templates. The `research_tools/source_commits.py` tool locates matching
engine revisions. Verify the remaining inputs separately before treating a
candidate revision as equivalent.

The [10,000-member as-run revision](https://github.com/rui-h-a/dams-simulation/tree/956725516ddac7f7c2336b0ccc23a69c5d7a27c5)
records seed `20261017`, worlds `1000`–`1009`, context `multinational`,
organic growth, baseline variant, two shared pre-adoption years and five
post-adoption years. Each world has the same four assigned arms:
`control-hierarchy`, `control-process`, `dams-witness` and `linear-witness`.
Use the saved plan and the effective arm configurations in the original
receipts as the assignment and configuration evidence. A plan listing one
world records that batch's subset of the ten-world assignment.

| Item | SHA-256 |
|---|---|
| As-run engine digest | `76246e9fdddf566e1db24f0d7da58afa4fa2a44a467889340010205a6ddb3f77` |
| As-run `run.sh` | `2a98bbde9c4f18258ba6631c2073239e22ca6638971e651ac2c10c491b7eec13` |
| As-run `research_tools/r11_study.py` | `d24d55c19733a6c184fdc53b3b9e448b48d1c537fb506d3d8aa60d0940c71fb2` |
| As-run `research_tools/r11_design.py` | `b0957838561bb85c1e4f719f6271ab3b7c254b30ce4f9d88d4d75a818d00c5e4` |
| As-run `candidate-evidence/calendar-networks-r7.json` | `6dbf244bcf7babf4d857564f93e09547afcb437cfc32fbc721c6519565ab6e40` |
| As-run `pyproject.toml` | `c8b2d35910153137f9bfdfea127c9b43675b6f46e9c088f28dc05ccaf7f1d515` |
| As-run `uv.lock` | `ba061682fb1b67f1c91beeccf9f36352dd68a912020662d9b74b376f4239e00c` |
| Result-freeze `docs/r11-tableA-pilot10k.json` | `9300c144033af970553cbe78e93200c45c298e8fc3a4598d800b343c636f22f8` |
| Published `roster.json` | `ee1c8bc5378089291b9ffde35a97c860ad5ddc500714355bd5e8ee037ed377c9` |
| Published `verify.py` | `24bfe241654ba295ea04d18786d649e2d61eee33de1c731d6667faf8d1f40e25` |

The [study protocol](https://github.com/rui-h-a/dams-simulation/blob/956725516ddac7f7c2336b0ccc23a69c5d7a27c5/docs/r11-formal-protocol.md)
defines the primary outcomes and pairing. The result-freeze threshold table
belongs to the analysis revision. It is distinct from the configuration that
generated the worlds.

## Generate a world with the pinned launcher

From the as-run checkout, the following command prepares the locked Python
3.14.2 environment and runs the four assigned arms for world `1000` locally:

    DAMS_CLOUD_PRIVATE_CONFIG= ./run.sh --spec enterprise-r11 \
      --scale 10000 --context multinational --growth organic \
      --pre-years 2 --post-years 5 --worlds 1000 --variant baseline \
      --arms control-hierarchy,control-process,dams-witness,linear-witness \
      --jobs 4 --output runs/multinational/w1000

The empty cloud setting selects the local branch. For worlds `1001`–`1009`,
change the world number and output directory together. Run with a new output
directory or resume an existing directory only when its full bound plan
matches. The launcher calls the study driver, raw-evidence inference and the
figure writer. One world yields one paired contrast per outcome and cannot
supply a paired Student t interval. The figure for such a batch identifies
unestimated effects. `--jobs` controls worker count. At this revision the
local enterprise branch does not forward `--runtime-limits`; resource limits
for a full local workload must be enforced outside that branch.

For preparation only, use the following from the as-run checkout:

    DAMS_CLOUD_PRIVATE_CONFIG= ./run.sh --spec enterprise-r11 \
      --scale 10000 --prepare-only

This installs or reuses the locked toolchain and runs the environment doctor.
On macOS ARM64, preparation passed with an empty online cache and with a
populated cache under network denial. First installation needs network access
unless the required toolchain and packages are already available. Preparation
does not admit a workload or establish that the full study runs on the host.

## Evidence from a small clean-environment run

The [retained execution receipt](../evidence/r11-10000-precision/clean-small-run-proof.json)
records an earlier macOS clean-checkout run at
[`ee21467`](https://github.com/rui-h-a/dams-simulation/tree/ee214679e42e9281423718fcba7ea1130c092c1c).
It completed two paired 30-member startup worlds over one year and produced
paired outcomes and inference. The four arm records and their retained output
hashes were rechecked. This is end-to-end execution evidence for that small
workload. The 10,000-member preparation test and the public recomputation of
its results cover different parts of the workflow.

## Pool retained arm outputs

In the [result-freeze revision](https://github.com/rui-h-a/dams-simulation/tree/d79a74eb18dbb6ab29559dccea1b0f12faafa863),
the historical pooler reads the saved run directories without generating new
worlds:

    python3 research_tools/projection/pool_validated_arms.py STUDY_ROOT ANALYSIS_OUT

Set `STUDY_ROOT` to the directory containing the separate `w1000` through
`w1009` directories. Use a separate `ANALYSIS_OUT` directory. The command
checks the three retained files per arm against the producer receipts and
checks the linked prehistory record. Compare the resulting roster against
the frozen assignment before interpreting the outputs. The pooler does not
require every assigned world to be present and does not repeat original event
validation. The retained main-study archive contains the 40 arm receipts and
their retained summary, trajectory and final-state files. The original
journals identified by those receipts are required for event-level
revalidation.

The launcher also has separate inspection and zero-step recovery actions.
Inspection classifies the files under a run directory. Zero-step recovery
requires the matching original checkpoint JSON and its native SQLite ledger,
plus a fresh destination. A successful recovery tests the state at that
checkpoint, independently of the validation of the complete study, the
availability of off-site copies, and the results of future model steps.

## Other enterprise comparisons

The labels below abbreviate the argument lists. Expand H, P, D and L to the
four full arm names above. Use seed `20261017` and the exact archived
assignment and effective configuration. Read a recorded engine digest as
one provenance field, not as the identity of an entire workflow.

| Study | Recorded source | Design and assignment |
|---|---|---|
| Founding, 30, 120 and 300 members | R11.3. The completed 30-member retry records `2d7446a`; the 120- and 300-member archives record engine digest `487ab3fd…`. | Startup, organic growth, zero pre-adoption years, five post-adoption years, H/P/D/L. Worlds 1000–1299 at 30 and 120 members and 1000–1056 at 300. |
| Mid-life adoption | R11.3 `93e5b87`; the traditional run records engine digest `487ab3fd…`. | Initial population 300, organic growth, two pre-adoption years, five post-adoption years, H/D. Traditional worlds 1000–1064, listed 1000–1119, century 1000–1010, unicorn 1000–1118. |
| Acquisition | R11.3 `93e5b87`. | Listed, 300 initial members, organic growth plus acquisition, two pre-adoption years, five post-adoption years, H/D. Worlds 1000–1119 in each integration package. Variants `baseline` and `ma-centralize`. |
| Robustness variants | R11.3 `93e5b87`. | The 300-member founding assignment, H/D, with the declared `--variant` for each comparison. |
| Sickness spells and demand-matched cases | R11.4. `e027d28` for spells, `981bf50` for the founding demand-matched case; the traditional demand-matched archive records engine digest `5f0497a8…`. | The founding or traditional assignment, H/D, with `sickness-spells` or `demand-matched`. |
| Partial 300-member extensions | R11.4, engine digest `5f0497a8…`. | Traditional worlds 1000–1064, with each amendment's growth, timing, record-process or ten-year arms. Keep complete and incomplete cases distinct. |

The supplementary 10,000-member runs and the 50,000- and 100,000-member
probes did not finish their assigned horizons. The projections are separate
analyses under stated conditions, documented in
`research_tools/projection/README.md` at the result-freeze revision.

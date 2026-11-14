# Five-year operating outcomes in the 50,000-member pilot

Two synthetic multinational worlds start with 50,000 members each. Each world
retains the hierarchy in one arm and adopts DAMS with independently witnessed
records in the other. Both arms share 731 days before adoption and run through
31 December 2026. The comparison changes allocation, transition and the record
procedure together. The two worlds fall short of the original variance check
and the event journals were not replayed in full against the daily series.

Run `python3 -I -S -B verify.py` with Python 3.11 or later. The verifier uses
local files and the standard library.

- `daily.csv` contains the retained daily revenue, operating expenditure and
  active member-days. The post-adoption net operating rate uses total revenue
  minus total operating expenditure, then divides by exposure in member-years.
  One member-year is 365.25 active member-days.
- `confirmation-histogram.csv` contains counts by delay among confirmed records
  over all seven years. The mean weights each duration by its count.
- `manifest.json` records input hashes, the identities of the original engine,
  plan and exports, and terminal review and commit queue counts. Claims in
  either queue remain unfinished at the terminal day.

The script reports DAMS minus hierarchy for each world. These inputs support
recomputation of the stated contrasts. Event replay and recovery require the
original databases and checkpoint files.

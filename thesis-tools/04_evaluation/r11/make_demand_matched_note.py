"""Render retained Amendment-6 summaries; raw acceptance remains pending."""
import hashlib
import json
import math
from pathlib import Path


def main():
    here=Path(__file__).resolve().parent
    source=here/'robustness/demand-matched-founding.json'
    data=source.read_bytes(); value=json.loads(data)
    n=value['worlds_common']
    if type(n) is not int or n<2 or n!=value['worlds_variant'] or n!=value['worlds_baseline']:
        raise ValueError('paired summary roster differs; raw admission still required')
    contrast=value['outcomes']['net_operating_result_per_member_year']['variant_contrast']
    change=value['outcomes']['net_operating_result_per_member_year']['change_from_baseline']
    if contrast['worlds']!=n or change['worlds']!=n:raise ValueError('summary count differs')
    mean=contrast['mean'];lo,hi=contrast['ci95'];delta=change['mean']
    if not all(type(x) in (int,float) and math.isfinite(x) for x in (mean,lo,hi,delta)) or not lo<=mean<=hi:
        raise ValueError('invalid retained summary')
    text=(f'% Retained summary SHA256 {hashlib.sha256(data).hexdigest()}; not original-data acceptance.\n'
          f'The retained founding summary contains {n} paired world identities. '
          f'Its net-operating contrast is ${mean:.2f}$ resource units per member-year '
          f'(95\\% interval $[{lo:.2f}, {hi:.2f}]$), a change of ${delta:.2f}$ from the capacity-bound baseline. '
          'This additional comparison fails the original sign-and-interval robustness criterion: '
          'its interval includes zero, which establishes neither equivalence nor a benefit. '
          'Complete original-data acceptance remains pending.\n')
    target=here.parents[1]/'05_thesis/generated/r11_demand_matched_note.tex'
    target.write_text(text)
    print(json.dumps({'source_sha256':hashlib.sha256(data).hexdigest(),'output':str(target),'worlds':n,'formal_worlds_added':0}))


if __name__=='__main__':main()

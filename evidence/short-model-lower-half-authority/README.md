# Formal decision share held by the lower-ranked half

The data cover 32 matched synthetic worlds with 120 members in four 30-member guilds, daily allocation updates and a central record. Each value is the within-guild formal share held by the 15 lowest-ranked members at day 60, averaged across the four guilds. Each policy ranks members using its own scores. A world is the paired unit. The source calculation checked the 160 retained end-state digests against their manifests and recovered all 155 means in the thesis's original Lorenz data, including both overlapping tier curves.

Run `python3 -I -S -B verify.py` in this directory with Python 3.10 or later. It recomputes the five means, pairs DAMS and linear credit by world and calculates an unadjusted Student-t interval for the mean difference. The contrast is a retrospective description of the specified synthetic worlds. It concerns formal decision shares within guilds and supplies no observations of exercised authority.

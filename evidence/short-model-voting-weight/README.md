# Turnout and unexercised formal weight

The retained 60-day, 120-member synthetic comparison uses five policies with daily updates and a central record in each of 32 matched worlds. Each daily observation averages four guilds. The extraction checks 9,600 daily rows against their retained state histories and verifies that all 38,400 guild decisions had positive participating weight.

Let A be the total formal weight of the attendees. When A is positive, their exercised weights are the attending shares divided by A, with zero for absentees. Their gains sum to 1 − A, which equals the absentees' total formal weight. The total variation from the formal weights is thus 1 − A. The attendance fraction counts people, including those with zero formal weight. This diagnostic describes the weight exercised in the modeled ballot and leaves other sources of influence unmeasured.

Run `python3 -I -S -B verify.py` in this directory with Python 3.10 or later. The verifier averages daily measurements within each arm and pairs policies by world. The unadjusted 95% Student-t intervals summarize exploratory contrasts in percentage points.

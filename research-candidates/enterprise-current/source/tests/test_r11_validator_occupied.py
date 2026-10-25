"""The linear occupied-slot computation equals the former quadratic form."""
import random
import unittest

from research_tools.validate_longitudinal import LEAVING_KINDS, occupied_slots


def former(people, members, events):
    return {people[i]['slot'] for i in members if not any(k in {'exit', 'retirement', 'layoff', 'voluntary_exit'} and v['person'] == i for k, e, v in events)}


class OccupiedSlotsTest(unittest.TestCase):
    def test_equals_former_form(self):
        rng = random.Random(20261024)
        kinds = sorted(LEAVING_KINDS) + ['work', 'vote', 'entry', 'review', 'decision']
        for _ in range(500):
            n = rng.randint(0, 40)
            people = {i: {'slot': rng.randint(0, 25)} for i in range(n + 10)}
            members = rng.sample(range(n + 10), n)
            events = []
            for j in range(rng.randint(0, 60)):
                k = rng.choice(kinds)
                v = {'person': rng.randint(0, n + 12)} if k in LEAVING_KINDS or rng.random() < .7 else {'guild': 1}
                events.append((k, f'e{j}', v))
            self.assertEqual(occupied_slots(people, members, events), former(people, members, events))

    def test_missing_person_on_leaving_event_still_fails(self):
        people = {0: {'slot': 0}}
        with self.assertRaises(KeyError):
            former(people, [0], [('exit', 'e', {})])
        with self.assertRaises(KeyError):
            occupied_slots(people, [0], [('exit', 'e', {})])
        # Fail-closed where the former form did not: no member left to test.
        self.assertEqual(former(people, [], [('exit', 'e', {})]), set())
        with self.assertRaises(KeyError):
            occupied_slots(people, [], [('exit', 'e', {})])


if __name__ == '__main__':
    unittest.main()

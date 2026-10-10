"""Daily, domain-scoped organization ABM with bounded information and review capacity.

    All behavioural coefficients are explicit design assumptions, not calibrated
    estimates. The evidence backends are stylized trust scenarios, not consensus
    clients or measurements of blockchain performance.
"""
from __future__ import annotations

import dataclasses
import heapq
import json
import math
import statistics
import time
import sys
from collections import defaultdict

from .authority import authority, gini, tier_authority, total_variation
from .config import Config
from .randomness import WorldRandom


def clip(x: float, low: float = 0, high: float = 1) -> float:
    return min(high, max(low, x))


@dataclasses.dataclass
class Agent:
    id: int
    guild: int
    team: int
    site: int
    skill: float                  # researcher latent state, never supplied to voting
    tenure: float                 # observed appointment proxy, not a target correlation
    care_hours: float             # synthetic work constraint, no demographic attribution
    autonomy: float
    reciprocity: float
    fatigue: float = 0.1
    trust: float = 0.7
    learning: float = 0.0
    confirmed: float = 0.0
    share: float = 0.0
    effort_memory: float = 0.5


@dataclasses.dataclass(order=True)
class Claim:
    ready: int
    event: str = dataclasses.field(compare=False)
    agent: int = dataclasses.field(compare=False)
    created: int = dataclasses.field(compare=False)
    observed: float = dataclasses.field(compare=False)
    audit_detected: bool = dataclasses.field(compare=False)
    fraudulent: bool = dataclasses.field(compare=False)
    correction: bool = dataclasses.field(default=False, compare=False)
    priority: float = 0.0


class Model:
    """Nested agents→teams→guilds→sites; sparse team and cross-guild ring links.

    Day order: decay credit; commit yesterday's reviewed claims; update shares; freeze
    weights; vote from noisy current signals; choose effort/cooperation; reveal
    outcomes; submit observed claims; review prior claims; schedule appeals.
    Simultaneous updates use a common morning snapshot and stable event IDs.
    """
    def __init__(self, config: Config, *, storage_dir=None, page_options=None,native_owner_dir=None,known_latest_floor=None,journal_chunk_bytes=None):
        if config.longitudinal is not None:
            from .longitudinal_model import LongitudinalEngine
            self._long = LongitudinalEngine(config, storage_dir=storage_dir,page_options=page_options,native_owner_dir=native_owner_dir,known_latest_floor=known_latest_floor,journal_chunk_bytes=journal_chunk_bytes)
            return
        if page_options is not None or native_owner_dir is not None or known_latest_floor is not None:
            raise ValueError('native page checkpoints require an opt-in longitudinal model')
        if journal_chunk_bytes is not None:
            raise ValueError('journal chunks require an opt-in longitudinal model')
        self.config = config.validate()
        self.rng = WorldRandom(config.seed, config.world)
        self.agents: list[Agent] = []
        self.members: dict[int, list[int]] = defaultdict(list)
        self.teams: dict[int, list[int]] = defaultdict(list)
        for i in range(config.n):
            if i % 1000 == 0:
                self.check_resources()
            guild = i % config.guilds
            position = i // config.guilds
            team = guild * (math.ceil(config.n / config.guilds / config.team_size)+1) + position // config.team_size
            a = Agent(i, guild, team, guild % config.sites,
                      math.exp(0.4 * self.rng.normal("skill", i)),
                      self.rng.uniform("tenure", i),
                      0.3 * self.rng.uniform("care", i),
                      self.rng.uniform("autonomy", i),
                      self.rng.uniform("reciprocity", i))
            self.agents.append(a)
            self.members[guild].append(i)
            self.teams[team].append(i)
        self.day = 0
        self.queues: dict[int, list[Claim]] = {g: [] for g in self.members}
        self.appeals: dict[int, list[Claim]] = {g: [] for g in self.members}
        self.commits: list[Claim] = []
        self.seen: set[str] = set()
        self.history: list[dict] = []
        self.confirmation_delays: list[int] = []
        self.appeal_delays: list[int] = []
        self.authority_changes: list[float] = []
        self.metrics: dict[str, float] = defaultdict(float)
        self.review_credit = {g: 0.0 for g in self.members}
        self.appeal_credit = {g: 0.0 for g in self.members}
        self._update_authority()

    def __getattr__(self, name):
        engine = self.__dict__.get('_long')
        if engine is not None:
            return getattr(engine, name)
        raise AttributeError(name)

    def _update_authority(self) -> None:
        p = self.config
        for ids in self.members.values():
            c = [self.agents[i].confirmed for i in ids]
            if p.regime in {"hierarchy", "hierarchy_tenure"}:
                score = c if p.hierarchy_basis == "performance" and p.regime == "hierarchy" else [self.agents[i].tenure for i in ids]
                weights = tier_authority(score, p.hierarchy_weights)
            else:
                exponent = {"equal": 0.0, "linear": 1.0, "sublinear": p.alpha}[p.regime]
                weights = authority(c, exponent, zero_policy="equal")
            self.authority_changes.append(math.fsum(abs(self.agents[i].share-v) for i, v in zip(ids, weights))/2)
            for i, value in zip(ids, weights):
                self.agents[i].share = value

    def _attack_active(self, day: int) -> bool:
        p = self.config
        return p.attack != "none" and p.attack_budget_hours_per_day > 0 and p.attack_start_day <= day < p.attack_stop_day

    def check_resources(self) -> None:
        if '_long' in self.__dict__:
            self._long.check_disk()
        try:
            import resource
        except ImportError:
            raise RuntimeError("RSS enforcement unavailable on this platform; no silently unsupported run")
        measured = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        measured_mb = measured/(1024*1024) if sys.platform == "darwin" else measured/1024
        if measured_mb > self.config.max_rss_mb:
            raise MemoryError(f"RSS resource stop: {measured_mb:.1f} MB > {self.config.max_rss_mb} MB")

    def _commit(self, day: int) -> None:
        if self.config.backend == "consensus" and self.config.quorum_unavailable_start_day <= day < self.config.quorum_unavailable_stop_day:
            self.metrics["quorum_unavailable_days"] += 1
            return
        while self.commits and self.commits[0].ready <= day:
            claim = heapq.heappop(self.commits)
            if claim.event in self.seen:
                self.metrics["duplicate_records_rejected"] += 1
                continue
            self.seen.add(claim.event)
            a = self.agents[claim.agent]
            # Evidence becomes available only now. Decayed value reflects claim age.
            value = claim.observed * (1-self.config.decay_per_day)**(day-claim.created)
            a.confirmed += max(0.0, value)
            a.trust = clip(a.trust + 0.015)
            self.metrics["confirmed_records"] += 1
            self.metrics["fraudulent_records_accepted"] += int(claim.fraudulent)
            self.confirmation_delays.append(day-claim.created)
            if claim.correction:
                self.metrics["corrections"] += 1
                self.appeal_delays.append(day-claim.created)

    def step(self, *, reverse_agents: bool = False) -> None:
        if '_long' in self.__dict__:
            return self._long.step(reverse_agents=reverse_agents)
        p, day = self.config, self.day
        if day >= p.days:
            raise ValueError("world has reached its specified horizon")
        # Decay previously held values before adding new dated records.
        for a in self.agents:
            a.confirmed *= 1-p.decay_per_day
        self._commit(day)
        if day % p.update_interval_days == 0:
            self._update_authority()
        snapshot = {a.id: (a.share, a.trust, a.fatigue, a.learning) for a in self.agents}
        decision_losses, participation, influence_divergence = [], [], []
        for guild, ids in sorted(self.members.items()):
            # Hidden state becomes outcome evidence after vote; agents see only signals.
            theta = self.rng.normal("proposal_value", day, guild)
            votes, effective, participation_flags = [], [], []
            for i in ids:
                share, trust, fatigue, learning = snapshot[i]
                signal = theta + p.decision_noise_sd/(1+learning) * self.rng.normal("decision_signal", day, i) + p.shared_signal_sd*self.rng.normal("shared_signal", day, guild)
                participates = self.rng.uniform("participate", day, i) < clip(0.25+0.65*trust-0.35*fatigue)
                participation_flags.append(participates)
                effective.append(share if participates else 0.0)
                votes.append(1.0 if signal >= 0 else 0.0)
            mass = math.fsum(effective)
            if mass:
                observed_vote = math.fsum(w*v for w, v in zip(effective, votes))/mass
                accepted = observed_vote >= 0.5
                loss = abs(theta) if accepted != (theta >= 0) else 0.0
                e = [v/mass for v in effective]
                influence_divergence.append(total_variation([snapshot[i][0] for i in ids], e))
                self.metrics["decision_errors"] += int(loss > 0)
                self.metrics["decisions_completed"] += 1
            else:
                # Abstention is an explicit unresolved decision, not a perfect outcome.
                loss = abs(theta)
                self.metrics["decisions_unresolved"] += 1
            decision_losses.append(loss)
            participation.append(sum(participation_flags)/len(ids))
        # Choices depend on observed recognition and current formal weights, not latent skill.
        efforts, cooperation = {}, {}
        # Hold the actor cohort fixed across budget treatments: only each
        # actor's allocated time changes, not the number of potential targets.
        attack_n = min(p.n, max(1, math.ceil(p.n*p.attack_cohort_fraction)))
        active = self._attack_active(day)
        ids_to_visit = list(range(p.n))
        if reverse_agents:
            ids_to_visit.reverse()
        for i in ids_to_visit:
            a = self.agents[i]
            share, trust, fatigue, _ = snapshot[i]
            autonomy = a.autonomy * p.autonomy_response * (len(self.members[a.guild])*share-1)
            cooperation[i] = clip(a.reciprocity*trust*(1-fatigue))
            # One synthetic workday = one hour-equivalent. Pool reservations are
            # divisible, charged uniformly, and expire if unused; no free reviewers.
            review_reservation = 0.1*p.review_capacity_per_member_day
            appeal_reservation = 0.15*p.appeal_capacity_per_member_day
            time_limit = max(0.0, 1-a.care_hours-review_reservation-appeal_reservation-0.03-0.02-0.05*cooperation[i])
            attack_time = p.attack_budget_hours_per_day/attack_n if active and i < attack_n else 0.0
            if attack_time > time_limit + 1e-12:
                raise ValueError("attack budget is infeasible under the individual daily time constraint")
            time_limit -= attack_time
            if p.behavior_rule == "linear_response":
                chosen_effort = 0.5 + 0.35*trust + autonomy - 0.4*fatigue - a.care_hours
            elif p.behavior_rule == "satisficing":
                # A transparent alternative: desired effort drops after observed
                # accumulated recognition reaches the age-scaled aspiration.
                satisfied = a.confirmed/max(1, day) >= 0.5
                chosen_effort = (0.45 if satisfied else 0.8) + autonomy - 0.3*fatigue
            else:
                chosen_effort = a.effort_memory + 0.1*(trust-0.5) + autonomy - 0.2*fatigue
            efforts[i] = clip(chosen_effort, 0.0, time_limit)
            booked_time = efforts[i]+a.care_hours+review_reservation+appeal_reservation+0.03+0.02+0.05*cooperation[i]+attack_time
            self.metrics["max_individual_time_booked_hours"] = max(self.metrics["max_individual_time_booked_hours"], booked_time)
            if active and p.attack == "freeride" and i < attack_n:
                reduction = min(efforts[i], p.attack_budget_hours_per_day/attack_n)
                efforts[i] -= reduction
                cooperation[i] = 0.0
        daily_output = 0.0
        # Team means computed from simultaneous choices; deterministic reduction order.
        team_help = {team: statistics.fmean(cooperation[i] for i in ids) for team, ids in self.teams.items()}
        for i in range(p.n):
            a = self.agents[i]
            cross = (i+1) % p.n
            help_input = (team_help[a.team] + cooperation[cross])/2
            shock = 0.7 if p.fault_start_day <= day < p.fault_stop_day and a.site == 0 else 1.0
            difficulty = 0.5 + self.rng.uniform("task_difficulty", day, i)
            quality_noise = math.exp(0.1*self.rng.normal("task_output", day, i))
            produced = a.skill*(1+a.learning)*efforts[i]*(1-a.fatigue)*quality_noise*shock/difficulty
            produced *= 1+p.cooperation_strength*help_input
            daily_output += produced
            observed = max(0.0, produced + p.review_error_sd*self.rng.normal("observed_work", day, i))
            fraudulent = active and p.attack == "forge" and i < attack_n
            if fraudulent:
                observed += p.attack_budget_hours_per_day/attack_n
                self.metrics["fraudulent_records_submitted"] += 1
            # A stylized audit sensor; no reviewer reads the hidden produced value.
            audit = self.rng.uniform("review_audit", day, i) < (0.6 if fraudulent else 0.02)
            event = f"work:{day}:{i}"
            # A common keyed lottery breaks simultaneous arrival ties. Numeric
            # or lexicographic person IDs do not receive fixed queue privilege.
            priority = self.rng.uniform("review_priority", day, i)
            claim = Claim(day+1, event, i, day, observed, audit, fraudulent, priority=priority)
            censor = active and p.attack == "censor" and i >= attack_n
            resistance = p.administrative_censorship_exposure[("central", "witness", "consensus").index(p.backend)]
            censor_probability = min(1.0, p.attack_budget_hours_per_day/p.n) * resistance
            if censor and self.rng.uniform("censor", day, i) < censor_probability:
                self.metrics["censored_records"] += 1
                a.trust = clip(a.trust-0.03)
            else:
                heapq.heappush(self.queues[a.guild], claim)
            if active and p.attack == "duplicate" and i < attack_n:
                # Same identity/event cannot be scored twice. Attack still consumes review slots.
                duplicate = dataclasses.replace(claim, ready=day+1)
                heapq.heappush(self.queues[a.guild], duplicate)
            attack_time = p.attack_budget_hours_per_day/attack_n if active and i < attack_n else 0.0
            workload = efforts[i] + attack_time
            a.fatigue = clip(a.fatigue + 0.15*workload - 0.1*(1-workload) - 0.03)
            a.learning = clip(a.learning + p.learning_rate*efforts[i], 0, 2)
            a.effort_memory = efforts[i]
        self.metrics["produced_work_units"] += daily_output
        self.metrics["effort_hours"] += math.fsum(efforts.values())
        self.metrics["cooperation_units"] += math.fsum(cooperation.values())
        self.metrics["decision_regret_units"] += math.fsum(decision_losses)
        if active:
            self.metrics["attack_budget_hours"] += p.attack_budget_hours_per_day
            self.metrics["attack_hours_consumed"] += p.attack_budget_hours_per_day
        self._review(day)
        self.day += 1
        backlog = sum(len(q) for q in self.queues.values()) + len(self.commits)
        self.metrics["backlog_peak_records"] = max(self.metrics["backlog_peak_records"], backlog)
        if day % p.trace_every_days == 0 or self.day == p.days:
            tvs, gaps, ginis = [], [], []
            for ids in self.members.values():
                c = [self.agents[i].confirmed for i in ids]
                shares = [self.agents[i].share for i in ids]
                tvs.append(total_variation(shares, authority(c, 1, zero_policy="equal")))
                ginis.append(gini(shares))
                gaps.append(gini(shares)-gini(c))
            self.history.append({"day": day, "output_work_units": daily_output,
                                 "review_backlog_records": backlog, "appeal_backlog_records": sum(len(q) for q in self.appeals.values()),
                                 "decision_regret_units": math.fsum(decision_losses), "participation_fraction": statistics.fmean(participation),
                                 "formal_effective_weight_tv": statistics.fmean(influence_divergence) if influence_divergence else None,
                                 "authority_contribution_tv": statistics.fmean(tvs), "signed_gini_gap": statistics.fmean(gaps),
                                 "gini_authority": statistics.fmean(ginis), "mean_trust": statistics.fmean(a.trust for a in self.agents),
                                 "mean_fatigue": statistics.fmean(a.fatigue for a in self.agents)})

    def _review(self, day: int) -> None:
        p = self.config
        # One common reviewer-hour budget; extra backend verification is charged
        # rather than granting the replicated scenario free processing capacity.
        index = ("central", "witness", "consensus").index(p.backend)
        unit_cost = p.backend_review_multipliers[index]
        settlement = p.backend_settlement_days[index]
        for guild, ids in sorted(self.members.items()):
            self.review_credit[guild] = len(ids)*p.review_capacity_per_member_day
            self.appeal_credit[guild] = len(ids)*p.appeal_capacity_per_member_day
            queue = self.queues[guild]
            # Capacity is daily, not bankable: unused capacity expires.
            available = int(self.review_credit[guild]/unit_cost)
            self.review_credit[guild] = 0.0
            for _ in range(available):
                if not queue or queue[0].ready > day:
                    break
                claim = heapq.heappop(queue)
                self.metrics["review_hours"] += 0.1
                self.metrics["verification_resource_units"] += unit_cost-1
                accepted = claim.observed >= 0.25 and not claim.audit_detected
                if accepted:
                    heapq.heappush(self.commits, dataclasses.replace(claim, ready=day+settlement))
                else:
                    self.metrics["reviews_rejected"] += 1
                    self.metrics["fraudulent_records_detected"] += int(claim.fraudulent)
                    self.metrics["honest_records_rejected"] += int(not claim.fraudulent)
                    a = self.agents[claim.agent]
                    a.trust = clip(a.trust-0.025)
                    # Appeal decisions use the member's observed outcome/recognition.
                    if claim.observed >= 0.25 and a.trust > 0.2:
                        heapq.heappush(self.appeals[guild], dataclasses.replace(claim, ready=day+p.appeal_delay_days))
            appeal_slots = int(self.appeal_credit[guild])
            self.appeal_credit[guild] = 0.0
            for _ in range(appeal_slots):
                queue_a = self.appeals[guild]
                if not queue_a or queue_a[0].ready > day:
                    break
                claim = heapq.heappop(queue_a)
                self.metrics["appeal_hours"] += 0.15
                # Independent reinspection sensor, not an oracle correction.
                detected = self.rng.uniform("appeal_audit", claim.created, claim.agent) < (0.9 if claim.fraudulent else 0.01)
                if not detected:
                    heapq.heappush(self.commits, dataclasses.replace(claim, ready=day+settlement, correction=True))
                else:
                    self.metrics["appeals_denied"] += 1

    def run(self, stop_day: int | None = None) -> Model:
        target = self.config.days if stop_day is None else stop_day
        if not self.day <= target <= self.config.days:
            raise ValueError("stop_day must be between current day and horizon")
        start = time.monotonic()
        while self.day < target:
            self.check_resources()
            if time.monotonic()-start > self.config.max_wall_seconds:
                raise TimeoutError(f"resource stop at day {self.day}; no complete result")
            self.step()
        self.check_resources()
        return self

    def summary(self) -> dict:
        if '_long' in self.__dict__:
            return self._long.summary()
        p = self.config
        out = dict(self.metrics)
        out.update({"model_version": "0.1.0", "world": p.world, "regime": p.regime, "backend": p.backend,
                    "attack": p.attack, "n": p.n, "days_completed": self.day, "update_interval_days": p.update_interval_days,
                    "confirmation_mean_days": statistics.fmean(self.confirmation_delays) if self.confirmation_delays else None,
                    "confirmation_p95_days": quantile(self.confirmation_delays, 0.95),
                    "appeal_mean_days": statistics.fmean(self.appeal_delays) if self.appeal_delays else None,
                    "unfinished_records": sum(len(q) for q in self.queues.values())+len(self.commits),
                    "unfinished_appeals": sum(len(q) for q in self.appeals.values()),
                    "last_authority_contribution_tv": self.history[-1]["authority_contribution_tv"] if self.history else None,
                    "last_signed_gini_gap": self.history[-1]["signed_gini_gap"] if self.history else None})
        return out

    def state(self) -> dict:
        if '_long' in self.__dict__:
            return self._long.state()
        return {"config": self.config.to_dict(), "day": self.day, "agents": [dataclasses.asdict(a) for a in self.agents],
                "queues": {g: [dataclasses.asdict(c) for c in q] for g, q in self.queues.items()},
                "appeals": {g: [dataclasses.asdict(c) for c in q] for g, q in self.appeals.items()},
                "commits": [dataclasses.asdict(c) for c in self.commits], "seen": sorted(self.seen), "history": self.history,
                "confirmation_delays": self.confirmation_delays, "appeal_delays": self.appeal_delays,
                "authority_changes": self.authority_changes, "metrics": dict(self.metrics),
                "review_credit": self.review_credit, "appeal_credit": self.appeal_credit}

    def write_state(self, stream) -> None:
        """Canonical raw state, streaming agents/claims rather than copying all.

        Bytes match canonical(state()); this is a serialization change only.
        Seen IDs still require exact lexicographic sorting; resource estimates
        explicitly include that retained-event and sorting cost.
        """
        if '_long' in self.__dict__:
            raise ValueError('longitudinal raw state requires write_final_state(path), including its exact sidecar')
        from .storage import canonical
        fields = self.state_fields()
        stream.write("{")
        for number, key in enumerate(sorted(fields)):
            if number: stream.write(",")
            stream.write(canonical(key)); stream.write(":")
            kind, value = fields[key]
            if kind == "array":
                stream.write("[")
                for i, item in enumerate(value):
                    if i: stream.write(",")
                    stream.write(canonical(dataclasses.asdict(item)))
                stream.write("]")
            elif kind == "queues":
                stream.write("{")
                for i, (guild, queue) in enumerate(sorted(value.items())):
                    if i: stream.write(",")
                    stream.write(canonical(str(guild))); stream.write(":[")
                    for j, claim in enumerate(queue):
                        if j: stream.write(",")
                        stream.write(canonical(dataclasses.asdict(claim)))
                    stream.write("]")
                stream.write("}")
            else:
                for chunk in json.JSONEncoder(sort_keys=True, separators=(",", ":"), allow_nan=False).iterencode(value):
                    stream.write(chunk)
        stream.write("}")

    def state_fields(self):
        return {"config": ("value", self.config.to_dict()), "day": ("value", self.day),
                "agents": ("array", self.agents), "queues": ("queues", self.queues),
                "appeals": ("queues", self.appeals), "commits": ("array", self.commits),
                "seen": ("value", sorted(self.seen)), "history": ("value", self.history),
                "confirmation_delays": ("value", self.confirmation_delays),
                "appeal_delays": ("value", self.appeal_delays),
                "authority_changes": ("value", self.authority_changes),
                "metrics": ("value", dict(self.metrics)),
                "review_credit": ("value", self.review_credit), "appeal_credit": ("value", self.appeal_credit)}

    @classmethod
    def restore(cls, value: dict, *, storage_dir=None) -> Model:
        if value.get('model_version') in ('longitudinal-1', 'longitudinal-endogenous-growth-1', 'longitudinal-enterprise-causal-3'):
            from .longitudinal_model import LongitudinalEngine
            obj = cls.__new__(cls)
            obj._long = LongitudinalEngine.restore(value, storage_dir=storage_dir)
            return obj
        # Rebuild topology from the saved population, avoiding a second generated
        # population and duplicated initialization/RNG work during recovery.
        obj = cls.__new__(cls)
        obj.config = Config.from_dict(value["config"])
        obj.rng = WorldRandom(obj.config.seed, obj.config.world)
        obj.members, obj.teams = defaultdict(list), defaultdict(list)
        obj.day = value["day"]
        if not 0 <= obj.day <= obj.config.days:
            raise ValueError("checkpoint day is invalid")
        obj.agents = [Agent(**a) for a in value["agents"]]
        if len(obj.agents) != obj.config.n or [a.id for a in obj.agents] != list(range(obj.config.n)):
            raise ValueError("checkpoint population/IDs differ from configuration")
        for a in obj.agents:
            obj.members[a.guild].append(a.id); obj.teams[a.team].append(a.id)
        for field in ("queues", "appeals"):
            queues = {int(g): [Claim(**c) for c in q] for g, q in value[field].items()}
            for q in queues.values():
                heapq.heapify(q)
            setattr(obj, field, queues)
        obj.commits = [Claim(**c) for c in value["commits"]]
        heapq.heapify(obj.commits)
        obj.seen = set(value["seen"])
        for field in ("history", "confirmation_delays", "appeal_delays", "authority_changes"):
            setattr(obj, field, value[field])
        obj.metrics = defaultdict(float, value["metrics"])
        obj.review_credit = {int(g): v for g, v in value["review_credit"].items()}
        obj.appeal_credit = {int(g): v for g, v in value["appeal_credit"].items()}
        return obj

    def write_checkpoint(self, path):
        if '_long' not in self.__dict__:
            raise ValueError('grouped checkpoint API requires an opt-in longitudinal model')
        return self._long.write_snapshot(path)

    def write_final_state(self, path):
        if '_long' not in self.__dict__:return self.write_checkpoint(path)
        return self._long.write_snapshot(path,final=True)

    @classmethod
    def restore_checkpoint(cls, path, *, storage_dir=None, expected_config=None,page_options=None,known_latest_floor=None,native_owner_dir=None):
        from .longitudinal_model import LongitudinalEngine, verify_snapshot,load_snapshot_envelope,verify_native_image
        if page_options is not None:
            from .longitudinal_storage import ExactLedger
            from .native_checkpoint_owner import CheckpointOwner,default_owner_directory
            from .storage import canonical,digest
            value,original=load_snapshot_envelope(path,expected_config=expected_config)
            descriptor=value.get('native_checkpoint')
            if descriptor is None or storage_dir is None or known_latest_floor is None:
                raise ValueError('native restore requires native descriptor, fresh directory and external latest floor')
            owner=CheckpointOwner(native_owner_dir or default_owner_directory(page_options,value['config_sha256']),
                source_sha256=value['source_sha256'],config_sha256=value['config_sha256'])
            try:
                owned=owner.for_checkpoint(path,minimum_floor=known_latest_floor)
                if owned['checkpoint']!=original:raise ValueError('native checkpoint parsed bytes differ from owner pin')
                def verify(database,desc,receipt):
                    verify_native_image(value['state'],database,desc,expected_state_sha256=value['state_semantic_sha256'])
                    if owner.for_checkpoint(path)['checkpoint']!=original:raise ValueError('native checkpoint changed before restore')
                ledger=ExactLedger.from_pages(storage_dir,descriptor,page_options=page_options,known_latest_floor=owned['floor'],verify_export=verify)
                obj=cls.__new__(cls)
                obj._long=LongitudinalEngine.restore(value['state'],storage_dir=storage_dir,ledger_instance=ledger,native_owner=owner)
                if obj._long.semantic_digest()!=value['state_semantic_sha256']:
                    raise ValueError('restored full longitudinal state differs')
                return obj
            except BaseException:
                owner.close()
                if 'ledger' in locals():ledger.close()
                raise
        value, database = verify_snapshot(path, expected_config=expected_config)
        obj = cls.__new__(cls)
        obj._long = LongitudinalEngine.restore(value['state'], storage_dir=storage_dir, ledger_snapshot=database)
        if obj._long.semantic_digest() != value['state_semantic_sha256']:
            raise ValueError('restored full longitudinal state differs')
        return obj

    def fork(self, new_config, *, storage_dir=None,page_options=None,native_owner_dir=None):
        if '_long' not in self.__dict__:
            raise ValueError('shared-history branch API requires a longitudinal parent')
        obj = self.__class__.__new__(self.__class__)
        obj._long = self._long.fork(new_config, storage_dir=storage_dir,page_options=page_options,native_owner_dir=native_owner_dir)
        return obj


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    x = sorted(values)
    index = (len(x)-1)*q
    low = math.floor(index)
    high = math.ceil(index)
    return x[low] + (x[high]-x[low])*(index-low)

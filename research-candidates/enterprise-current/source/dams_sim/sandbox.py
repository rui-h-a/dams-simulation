"""Nested-sandbox graph for the `dams-isa` arm (pure state machine; no engine imports).

A sandbox is a unit of scoped authority, privacy and verification-processing
quota.  Guilds and teams are sandboxes; a cross-project sandbox may be created
for a task that spans guilds and closed when the task ends.  Sandboxes form a
graph (a sandbox may have several parents, a member may belong to several
sandboxes).  The implicit parent of every parentless sandbox is `root`.

Rights.  Inside its own scope (itself and all descendants) a sandbox has full
default rights.  Anything upward (to an ancestor) or lateral (to a sibling or
an unrelated sandbox) needs approval: by the target for upward requests, by the
common parent for lateral ones.  Each request is logged; approval latency and
the rejection probability are configuration parameters.  An approval becomes a
standing grant that takes effect after the latency and expires after
`grant_validity_days`.

Protected core.  Identity, the record substrate, allocation rules, joint
confirmation and recovery cannot be modified by any sandbox; attempts are
blocked and logged.  Reading another sandbox's records without a grant is
blocked and logged (privacy scope).

Lifecycle.  create / split / merge / close.  Members, confirmed credit and open
claims are carried to the successors; the closed sandbox keeps its history and
points at its heirs.  Totals are conserved (tested).

Self-modification.  A sandbox may change its own local parameters inside declared
bounds.  A change first runs on a shadow copy for a window; it is adopted only if
the health metric does not degrade, then watched for a further window and rolled
back automatically if health falls below its pre-adoption level.  A change that
touches an allocation rule needs the parent's approval first.
"""
from __future__ import annotations

import copy

ROOT = 'root'
PROTECTED_CORE = ('identity', 'record_substrate', 'allocation_rules', 'joint_confirmation', 'recovery')
ALLOCATION_PARAMETERS = ('authority_alpha', 'allocation_rule')
LOCAL_BOUNDS = {'exception_fraction': (0.02, 0.30)}
KINDS = ('guild', 'team', 'project')
# Logged event kinds that are counted as engine metrics (and independently re-counted by the raw validator).
COUNTED_EVENTS = {'sandbox_create': 'sandboxes_created', 'sandbox_split': 'sandboxes_split', 'sandbox_merge': 'sandboxes_merged',
                  'sandbox_close': 'sandboxes_closed', 'privacy_block': 'privacy_blocks', 'core_block': 'core_blocks',
                  'selfmod_adopt': 'selfmod_adoptions', 'selfmod_rollback': 'selfmod_rollbacks', 'fault_inject': 'faults_injected',
                  'fault_detected': 'faults_detected', 'fault_recovered': 'faults_recovered'}


class SandboxError(ValueError):
    pass


class SandboxGraph:
    def __init__(self, config, uniform):
        """config: AutomationConfig (approval/lifecycle/self-modification parameters);
        uniform(process, *identity): keyed uniform draw in (0,1)."""
        self.c = config
        self.u = uniform
        self.sandboxes = {}     # id -> record
        self.primary = {}       # person -> leaf sandbox id used for quota attribution
        self.grants = {}        # 'actor|target|action' -> [effective_day, expiry_day]
        self.denials = {}       # 'actor|target|action' -> day of the last refusal (a refusal stands for that day)
        self.shadows = {}       # sandbox -> active shadow/watch record
        self.usage = {}         # sandbox -> {'own': units, 'borrowed_in': units, 'lent_out': units}
        self.log = []           # entries since the last drain (journalled by the caller)
        self.series = {}        # str(day) -> {sandbox: confirmed claims}  (fault runs only)
        self.sizes = {}         # str(day) -> {sandbox: members}            (fault runs only)
        self.fault = None       # injected-fault record

    # ---- serialisation ----
    def to_dict(self):
        return {'sandboxes': copy.deepcopy(self.sandboxes), 'primary': {str(k): v for k, v in sorted(self.primary.items())},
                'grants': copy.deepcopy(self.grants), 'denials': dict(self.denials), 'shadows': copy.deepcopy(self.shadows), 'usage': copy.deepcopy(self.usage),
                'series': copy.deepcopy(self.series), 'sizes': copy.deepcopy(self.sizes), 'fault': copy.deepcopy(self.fault)}

    @classmethod
    def from_dict(cls, value, config, uniform):
        g = cls(config, uniform)
        g.sandboxes = copy.deepcopy(value['sandboxes']); g.primary = {int(k): v for k, v in value['primary'].items()}
        g.grants = copy.deepcopy(value['grants']); g.denials = dict(value.get('denials', {})); g.shadows = copy.deepcopy(value['shadows']); g.usage = copy.deepcopy(value['usage'])
        g.series = copy.deepcopy(value.get('series', {})); g.sizes = copy.deepcopy(value.get('sizes', {})); g.fault = copy.deepcopy(value.get('fault'))
        return g

    def drain(self):
        out, self.log = self.log, []
        return out

    def _note(self, day, kind, **fields):
        entry = {'kind': kind, 'day': day, **fields}
        self.log.append(entry)
        return entry

    # ---- structure ----
    def live(self):
        return sorted(i for i, s in self.sandboxes.items() if s['closed'] is None)

    def create(self, day, sid, kind, parents=(), members=(), credit=None, open_claims=()):
        if sid in self.sandboxes or sid == ROOT:raise SandboxError('sandbox id in use: '+sid)
        if kind not in KINDS:raise SandboxError('unknown sandbox kind')
        for p in parents:
            if p not in self.sandboxes or self.sandboxes[p]['closed'] is not None:raise SandboxError('parent must be a live sandbox')
        self.sandboxes[sid] = {'kind': kind, 'parents': sorted(parents), 'members': sorted(set(members)), 'created': day, 'closed': None,
                               'heirs': [], 'credit': {str(k): float(v) for k, v in (credit or {}).items()},
                               'open': sorted(open_claims), 'local': {'exception_fraction': self.c.exception_fraction, 'false_reject_probability': self.c.false_reject_probability},
                               'history': [[day, 'create', {'kind': kind, 'parents': sorted(parents)}]]}
        self.usage.setdefault(sid, {'own': 0., 'borrowed_in': 0., 'lent_out': 0.})
        if kind != 'project':
            for m in self.sandboxes[sid]['members']:self.primary[m] = sid
        self._note(day, 'sandbox_create', sandbox=sid, sandbox_kind=kind, parents=sorted(parents))
        return sid

    def ancestors(self, sid):
        out, stack = set(), list(self.sandboxes[sid]['parents'])
        while stack:
            p = stack.pop()
            if p not in out:out.add(p); stack.extend(self.sandboxes[p]['parents'])
        return out

    def descendants(self, sid):
        return {i for i, s in self.sandboxes.items() if s['closed'] is None and sid in self.ancestors(i)}

    def in_scope(self, actor, target):
        return actor == target or target in self.descendants(actor)

    def depth(self, sid):
        parents = self.sandboxes[sid]['parents']
        return 1+max((self.depth(p) for p in parents), default=0)

    def common_parent(self, a, b):
        common = (self.ancestors(a)|{a}) & (self.ancestors(b)|{b})
        return max(common, key=lambda i: (self.depth(i), i)) if common else ROOT

    def add_member(self, day, sid, person, primary=False):
        s = self.sandboxes[sid]
        if person not in s['members']:
            s['members'] = sorted(s['members']+[person])
        if primary or (s['kind'] != 'project' and person not in self.primary):self.primary[person] = sid

    def remove_person(self, person):
        for s in self.sandboxes.values():
            if person in s['members']:s['members'] = [m for m in s['members'] if m != person]
        self.primary.pop(person, None)

    def sandbox_members(self, sid):
        return list(self.sandboxes[sid]['members'])

    def leaves_under(self, sid):
        """Live non-project sandboxes that descend from (or are) `sid`, following heirs of closed ones."""
        s = self.sandboxes[sid]
        if s['closed'] is None:
            kids = sorted(i for i in self.descendants(sid) if self.sandboxes[i]['kind'] != 'project')
            return [i for i in kids if not any(i in self.ancestors(j) for j in kids)] or [sid]
        out = []
        for h in s['heirs']:out.extend(self.leaves_under(h))
        return sorted(set(out))

    # ---- lifecycle (history, claims and credit carried over) ----
    def _carry(self, day, src, dst, members=True):
        a, b = self.sandboxes[src], self.sandboxes[dst]
        for k, v in a['credit'].items():b['credit'][k] = b['credit'].get(k, 0.)+v
        b['open'] = sorted(set(b['open'])|set(a['open']))
        if members:b['members'] = sorted(set(b['members'])|set(a['members']))

    def _retire(self, day, sid, heirs, why):
        s = self.sandboxes[sid]
        s['closed'] = day; s['heirs'] = sorted(heirs)
        s['history'].append([day, why, {'heirs': sorted(heirs), 'members': len(s['members']), 'credit': sum(s['credit'].values()), 'open': len(s['open'])}])
        s['credit'] = {}; s['open'] = []
        self._note(day, 'sandbox_'+why, sandbox=sid, heirs=sorted(heirs))

    def close(self, day, sid, heir=None):
        s = self.sandboxes[sid]
        if s['closed'] is not None:raise SandboxError('sandbox already closed')
        heir = heir or (s['parents'][0] if s['parents'] else None)
        if heir is None or heir not in self.sandboxes or self.sandboxes[heir]['closed'] is not None or heir == sid:
            raise SandboxError('closing a sandbox needs a live heir to receive its credit and open claims')
        self._carry(day, sid, heir, members=s['kind'] != 'project')
        for m in s['members']:
            if self.primary.get(m) == sid:
                alt = heir if self.sandboxes[heir]['kind'] != 'project' else self._other_primary(m, sid)
                if alt is None:self.primary.pop(m, None)
                else:self.primary[m] = alt
        self._retire(day, sid, [heir], 'close')

    def _other_primary(self, person, closing):
        for i in sorted(self.sandboxes):
            s = self.sandboxes[i]
            if s['closed'] is None and i != closing and s['kind'] != 'project' and person in s['members']:return i
        return None

    def split(self, day, sid):
        s = self.sandboxes[sid]
        if s['closed'] is not None:raise SandboxError('sandbox already closed')
        members = s['members']; half = (len(members)+1)//2
        halves = (members[:half], members[half:])
        if not halves[1]:raise SandboxError('a sandbox needs at least two members to split')
        ids = []
        for suffix, mine in zip('ab', halves):
            cid = f'{sid}.{suffix}'
            part_credit = {str(m): s['credit'][str(m)] for m in mine if str(m) in s['credit']}
            self.create(day, cid, s['kind'], s['parents'], mine, credit=part_credit)
            self.sandboxes[cid]['local'] = dict(s['local']); self.sandboxes[cid]['history'] = copy.deepcopy(s['history'])+self.sandboxes[cid]['history']
            ids.append(cid)
        # Open claims follow their claimant; claims without a member (e.g. project-level) go to the first child.
        owner = {}
        for k, cid in zip(halves, ids):
            for m in k:owner[m] = cid
        for claim in s['open']:
            person = int(claim.split(':')[0]) if ':' in str(claim) else None
            cid = owner.get(person, ids[0])
            self.sandboxes[cid]['open'] = sorted(set(self.sandboxes[cid]['open'])|{claim})
        stray = {k: v for k, v in s['credit'].items() if int(k) not in owner}
        for k, v in stray.items():self.sandboxes[ids[0]]['credit'][k] = self.sandboxes[ids[0]]['credit'].get(k, 0.)+v
        self._retire(day, sid, ids, 'split')
        return ids

    def merge(self, day, a, b, new_id=None):
        sa, sb = self.sandboxes[a], self.sandboxes[b]
        if a == b or sa['closed'] is not None or sb['closed'] is not None:raise SandboxError('merge needs two distinct live sandboxes')
        nid = new_id or f'{a}+{b}'
        parents = sorted(set(sa['parents'])|set(sb['parents']))
        self.create(day, nid, sa['kind'] if sa['kind'] == sb['kind'] else 'project', parents)
        for src in (a, b):
            self._carry(day, src, nid)
        n = self.sandboxes[nid]
        n['history'] = copy.deepcopy(sa['history'])+copy.deepcopy(sb['history'])+n['history']
        for m in n['members']:
            if n['kind'] != 'project':self.primary[m] = nid
        n['local'] = dict(sa['local'])
        self._retire(day, a, [nid], 'merge'); self._retire(day, b, [nid], 'merge')
        return nid

    def rebalance(self, day, split_above, merge_below):
        """Split live leaf sandboxes above `split_above` members; merge sibling leaves below `merge_below`."""
        done = []
        frozen = self.fault['sandbox'] if self.fault is not None and self.fault['active'] else None   # no restructuring during its fault
        for sid in self.live():
            s = self.sandboxes[sid]
            if sid != frozen and s['closed'] is None and s['kind'] != 'project' and split_above and len(s['members']) > split_above and not self.descendants(sid):
                done.append(('split', self.split(day, sid)))
        if merge_below:
            for sid in self.live():
                s = self.sandboxes[sid]
                if sid == frozen or s['closed'] is not None or s['kind'] == 'project' or len(s['members']) >= merge_below or self.descendants(sid):continue
                mates = [o for o in self.live() if o != sid and o != frozen and self.sandboxes[o]['parents'] == s['parents'] and self.sandboxes[o]['kind'] == s['kind']
                         and not self.descendants(o)]
                if mates:
                    mate = min(mates, key=lambda o: (len(self.sandboxes[o]['members']), o))
                    done.append(('merge', self.merge(day, *sorted((sid, mate)))))
        return done

    # ---- scoped authority, approvals, privacy, protected core ----
    def _grant_key(self, actor, target, action):
        return f'{actor}|{target}|{action}'

    def request(self, day, actor, target, action):
        """Ask to act on `target` from `actor`. Returns the logged decision dict.
        Own scope needs no approval. Otherwise: upward -> target approves; lateral -> common parent approves."""
        if target not in self.sandboxes:raise SandboxError('unknown target sandbox')
        if self.in_scope(actor, target):
            return {'decision': 'in_scope', 'approved': True}
        key = self._grant_key(actor, target, action)
        g = self.grants.get(key)
        if g is not None and g[0] <= day < g[1]:
            return {'decision': 'granted', 'approved': True}
        if g is not None and day < g[0]:
            return {'decision': 'pending', 'approved': False}
        if self.denials.get(key) == day:
            return {'decision': 'rejected', 'approved': False}
        direction = 'upward' if target in self.ancestors(actor) else 'lateral'
        approver = target if direction == 'upward' else self.common_parent(actor, target)
        rejected = self.u('sandbox_approval', day, actor, target, action) < self.c.approval_rejection_probability
        effective = day+self.c.approval_latency_days
        entry = self._note(day, 'sandbox_request', actor=actor, target=target, action=action, direction=direction, approver=approver,
                           approved=not rejected, effective_day=None if rejected else effective)
        if rejected:
            self.denials[key] = day
        else:
            self.denials.pop(key, None)
            self.grants[key] = [effective, effective+self.c.grant_validity_days]
        return {'decision': 'rejected' if rejected else ('approved' if effective <= day else 'pending'), 'approved': (not rejected) and effective <= day, 'entry': entry}

    def read_records(self, day, reader, owner):
        """Privacy scope: records of another sandbox are readable only inside scope or with a valid read grant."""
        if self.in_scope(reader, owner):return True
        g = self.grants.get(self._grant_key(reader, owner, 'read_records'))
        if g is not None and g[0] <= day < g[1]:return True
        self._note(day, 'privacy_block', actor=reader, target=owner, action='read_records')
        return False

    def modify_core(self, day, sandbox, item):
        """No sandbox can modify the protected core; the attempt is blocked and logged."""
        self._note(day, 'core_block', actor=sandbox, item=item, protected=item in PROTECTED_CORE)
        return False

    # ---- quota consumption attribution ----
    def attribute(self, sandbox, units, *, lender=None):
        u = self.usage.setdefault(sandbox, {'own': 0., 'borrowed_in': 0., 'lent_out': 0.})
        if lender is None:u['own'] += units
        else:
            u['borrowed_in'] += units
            self.usage.setdefault(lender, {'own': 0., 'borrowed_in': 0., 'lent_out': 0.})['lent_out'] += units

    # ---- self-modification with shadow window and recovery ----
    def local(self, sandbox, name):
        return self.sandboxes[sandbox]['local'][name]

    def propose(self, day, sandbox, name, value):
        """Returns 'blocked', 'needs_approval', 'rejected', 'shadow' or 'busy'."""
        s = self.sandboxes[sandbox]
        if name in PROTECTED_CORE:
            self.modify_core(day, sandbox, name); return 'blocked'
        if name in ALLOCATION_PARAMETERS:
            parents = s['parents'] or [ROOT]
            if parents[0] == ROOT or parents[0] not in self.sandboxes:
                self._note(day, 'selfmod_needs_approval', sandbox=sandbox, parameter=name, approver=ROOT); return 'needs_approval'
            r = self.request(day, sandbox, parents[0], 'allocation:'+name)
            self._note(day, 'selfmod_needs_approval', sandbox=sandbox, parameter=name, approver=parents[0], approved=r['approved'])
            return 'needs_approval' if r['decision'] != 'rejected' else 'rejected'
        if name not in LOCAL_BOUNDS or not LOCAL_BOUNDS[name][0] <= value <= LOCAL_BOUNDS[name][1]:
            self._note(day, 'selfmod_out_of_bounds', sandbox=sandbox, parameter=name, value=value); return 'blocked'
        if sandbox in self.shadows:return 'busy'
        self.shadows[sandbox] = {'phase': 'shadow', 'name': name, 'value': value, 'previous': s['local'][name], 'start': day,
                                 'end': day+self.c.shadow_window_days, 'n': 0, 'live_cost': 0., 'shadow_cost': 0., 'baseline_health': None}
        self._note(day, 'selfmod_shadow_start', sandbox=sandbox, parameter=name, value=value, previous=s['local'][name])
        return 'shadow'

    def observe(self, sandbox, live_cost, shadow_cost=0.):
        """Per-claim cost (wrong decisions + weighted human load) under the live and, in the shadow phase, candidate setting."""
        sh = self.shadows.get(sandbox)
        if sh is None:return
        sh['n'] += 1; sh['live_cost'] += live_cost; sh['shadow_cost'] += shadow_cost

    def settle(self, day):
        """Close due shadow windows: adopt if health did not degrade, else roll back; watch adopted changes."""
        for sandbox in sorted(self.shadows):
            sh = self.shadows[sandbox]
            if day < sh['end']:continue
            s = self.sandboxes[sandbox]
            if sh['phase'] == 'shadow':
                n = max(1, sh['n'])
                live_health, shadow_health = 1-sh['live_cost']/n, 1-sh['shadow_cost']/n
                if sh['n'] and shadow_health >= live_health-self.c.health_tolerance:
                    s['local'][sh['name']] = sh['value']
                    self.shadows[sandbox] = {**sh, 'phase': 'watch', 'start': day, 'end': day+self.c.shadow_window_days, 'n': 0,
                                             'live_cost': 0., 'shadow_cost': 0., 'baseline_health': live_health}
                    s['history'].append([day, 'selfmod_adopt', {'name': sh['name'], 'value': sh['value']}])
                    self._note(day, 'selfmod_adopt', sandbox=sandbox, parameter=sh['name'], value=sh['value'], live_health=live_health, shadow_health=shadow_health)
                else:
                    del self.shadows[sandbox]
                    s['history'].append([day, 'selfmod_rollback', {'name': sh['name'], 'phase': 'shadow'}])
                    self._note(day, 'selfmod_rollback', sandbox=sandbox, parameter=sh['name'], value=sh['value'], phase='shadow',
                               live_health=live_health, shadow_health=shadow_health)
            else:
                n = max(1, sh['n']); health = 1-sh['live_cost']/n
                if sh['n'] and health < sh['baseline_health']-self.c.health_tolerance:
                    s['local'][sh['name']] = sh['previous']
                    s['history'].append([day, 'selfmod_rollback', {'name': sh['name'], 'phase': 'watch'}])
                    self._note(day, 'selfmod_rollback', sandbox=sandbox, parameter=sh['name'], value=sh['value'], phase='watch',
                               live_health=health, baseline_health=sh['baseline_health'])
                del self.shadows[sandbox]


    # ---- fault injection, health monitor, containment ----
    def note_commit(self, day, person):
        sb = self.primary.get(person)
        if sb is not None:
            row = self.series.setdefault(str(day), {}); row[sb] = row.get(sb, 0)+1

    def record_sizes(self, day):
        self.sizes[str(day)] = {i: len(s['members']) for i, s in sorted(self.sandboxes.items()) if s['closed'] is None and s['kind'] != 'project' and s['members']}

    def inject(self, day):
        """Start the configured fault (idempotent). Returns True on the day it is injected."""
        c = self.c
        if c.fault_mode == 'none' or self.fault is not None or day < c.fault_start_day or c.fault_sandbox not in self.sandboxes:return False
        sid = self.leaves_under(c.fault_sandbox)[0]   # the named sandbox, or its first live successor after a split or merge
        target = self.sandboxes[sid]
        self.fault = {'mode': c.fault_mode, 'sandbox': sid, 'start': day, 'end': day+c.fault_days, 'detected': None, 'recovered': None,
                      'previous': target['local']['false_reject_probability'], 'active': True}
        if c.fault_mode == 'bad_parameter':
            target['local']['false_reject_probability'] = c.fault_bad_false_reject
        self._note(day, 'fault_inject', sandbox=sid, mode=c.fault_mode, until=day+c.fault_days)
        return True

    def service_down(self, sandbox, day):
        f = self.fault
        return f is not None and f['mode'] == 'service_stop' and f['active'] and f['sandbox'] == sandbox and day < f['end']

    def monitor(self, day, sandbox, processed, wrong, outage):
        """Daily health check of one sandbox: a heartbeat failure (outage) or a wrong-decision share above the
        threshold is a detected fault; a bad local parameter is rolled back to its declared default."""
        f = self.fault; c = self.c
        if f is None or not f['active'] or f['sandbox'] != sandbox:return
        if f['mode'] == 'service_stop':
            if outage and f['detected'] is None:
                f['detected'] = day; self._note(day, 'fault_detected', sandbox=sandbox, mode=f['mode'], signal='verification heartbeat')
        elif f['mode'] == 'bad_parameter':
            if f['detected'] is None and processed >= c.fault_detect_min_claims and wrong/processed > c.fault_detect_threshold:
                f['detected'] = day; self._note(day, 'fault_detected', sandbox=sandbox, mode=f['mode'], signal='wrong-decision share', share=wrong/processed)
                self.sandboxes[sandbox]['local']['false_reject_probability'] = f['previous']
                f['active'] = False; f['recovered'] = day
                self.sandboxes[sandbox]['history'].append([day, 'fault_rollback', {'restored': f['previous']}])
                self._note(day, 'fault_recovered', sandbox=sandbox, mode=f['mode'], via='rollback')

    def end_fault_if_due(self, day):
        f = self.fault
        if f is not None and f['active'] and day >= f['end']:
            f['active'] = False; f['recovered'] = day
            if f['mode'] == 'bad_parameter':
                self.sandboxes[f['sandbox']]['local']['false_reject_probability'] = f['previous']
            self._note(day, 'fault_recovered', sandbox=f['sandbox'], mode=f['mode'], via='fault window ended')

    def containment(self):
        """Did the failure stay inside its sandbox? Throughput = confirmed claims per member-day, fault window vs
        the declared baseline window. Returns None when no fault was injected."""
        f = self.fault; c = self.c
        if f is None:return None
        start, end = f['start'], f['end']
        def rate(sandbox, lo, hi):
            days = [d for d in range(lo, hi)]
            commits = sum(self.series.get(str(d), {}).get(sandbox, 0) for d in days)
            sizes = [self.sizes[str(d)][sandbox] for d in days if str(d) in self.sizes and sandbox in self.sizes[str(d)]]
            return commits/(sum(sizes)/len(sizes)*len(days)) if sizes else None
        affected, measured = [], 0
        for sb in sorted({s for row in self.sizes.values() for s in row}):
            base, during = rate(sb, start-c.fault_baseline_days, start), rate(sb, start, end)
            if not base or during is None:continue
            measured += 1
            if 1-during/base > c.fault_drop_threshold:affected.append(sb)
        own = f['sandbox']
        others = [s for s in affected if s != own]
        return {'mode': f['mode'], 'sandbox': own, 'start_day': start, 'end_day': end, 'detected_day': f['detected'], 'recovered_day': f['recovered'],
                'days_to_detection': None if f['detected'] is None else f['detected']-start,
                'days_to_recovery': None if f['recovered'] is None else f['recovered']-start,
                'faulty_sandbox_throughput_dropped': own in affected, 'other_sandboxes_dropped': others,
                'other_sandboxes_dropped_count': len(others), 'sandboxes_measured': measured,
                'contained': not others, 'drop_threshold': c.fault_drop_threshold}

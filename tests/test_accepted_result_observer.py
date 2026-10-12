"""Tiny synthetic metadata/transport controls, never scientific evidence."""
import base64
import contextlib
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).parents[1]/'research_tools'/'accepted_result_observer.py'
spec = importlib.util.spec_from_file_location('accepted_result_observer', MODULE)
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


class DefinitelyNotApplied(RuntimeError):
    definitely_not_applied = True


class FakeTransport:
    """In-memory Git Data ABI; no subprocess, credentials, or network."""
    def __init__(self):
        self.heads = {repo:'a'*40 for repo in observer.REPOS}
        self.calls = []; self.trees = {}; self.commits = {}
        self.fail_repo_once = None; self.tree_unknown_once = False
        self.drift_before_patch = False; self.ref_gets = {}

    def api(self, method, endpoint, body=None, *, timeout):
        if not 0 < timeout <= 45: raise AssertionError('finite deadline bound required')
        self.calls.append((method, endpoint, body))
        repo = next(r for r in observer.REPOS if endpoint.startswith('/repos/'+r+'/'))
        suffix = endpoint[len('/repos/'+repo):]
        if self.fail_repo_once == repo:
            self.fail_repo_once = None
            raise DefinitelyNotApplied('synthetic closed transport failure')
        if method == 'GET' and suffix == '/git/ref/heads/main':
            self.ref_gets[repo] = self.ref_gets.get(repo,0)+1
            if self.drift_before_patch and self.ref_gets[repo] == 2:
                self.heads[repo] = 'd'*40
            return {'object':{'sha':self.heads[repo]}}
        if method == 'GET' and suffix.startswith('/git/commits/'):
            return {'tree':{'sha':'b'*40}}
        if method == 'POST' and suffix == '/git/trees':
            if self.tree_unknown_once:
                self.tree_unknown_once = False
                raise RuntimeError('synthetic unknown mutation outcome')
            target = hashlib.sha1(observer.canonical(body)).hexdigest()
            self.trees[target] = {r['path']:r['content'].encode() for r in body['tree']}
            return {'sha':target}
        if method == 'POST' and suffix == '/git/commits':
            target = hashlib.sha1(observer.canonical(body)).hexdigest()
            self.commits[target] = self.trees[body['tree']]
            return {'sha':target}
        if method == 'PATCH' and suffix == '/git/refs/heads/main':
            if body['force'] is not False: raise AssertionError('force refused')
            self.heads[repo] = body['sha']
            return {'object':{'sha':body['sha']}}
        if method == 'GET' and suffix.startswith('/contents/'):
            path, commit = suffix[len('/contents/'):].split('?ref=')
            if commit not in self.commits or path not in self.commits[commit]:
                return {'not_found':True}
            raw = self.commits[commit][path]
            return {'content':base64.b64encode(raw).decode(),
                    'sha':hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()}
        raise AssertionError((method, suffix))


class MetadataFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='observer-metadata-control-')
        self.base = Path(self.tmp.name).resolve()
        self.science = self.base/'synthetic-study'; self.science.mkdir()
        self.state = self.base/'observer-state'; self.state.mkdir()
        self.output = self.base/'prepared'; self.output.mkdir()
        roots = []
        for n,repo in enumerate((observer.REPOS[0], observer.REPOS[1], observer.REPOS[1])):
            p = self.base/f'physical-{n}'; p.mkdir(); roots.append((p,repo))
        self.roots = tuple(roots)
        self.root_patch = patch.object(observer, 'PHYSICAL_ROOTS', self.roots)
        self.root_patch.start()
        self.effects = [{ 'initial_n':300, 'stage':'confirmation', 'world':52000,
                          'endpoint':name, 'effect':-.125, 'unit':'synthetic units',
                          'target_halfwidth':.1, 'reference_denominator':1., 'treatment_denominator':1.}
                         for name in sorted(observer.ENDPOINTS)]
        self.study = {'source_sha256':observer.CORE, 'pipeline_driver_sha256':observer.DRIVER,
                      'entry_sha256':observer.ENTRY, 'runtime_sha256':'f'*64,
                      'declaration':{'schema':'dams-small-enterprise-five-year-v1', 'initial_population':300,
                         'seed':20261008, 'days_including_settlement_tail':1917, 'full_observation_years':5,
                         'arms':list(observer.ARMS), 'pilot_world_ids':list(range(42000,42004)),
                         'pilot_worlds':4, 'confirmation_world_start':52000,
                         'primary_endpoints':[[e['endpoint'],e['target_halfwidth'],e['unit']] for e in self.effects]}}
        self.identity = {'source_sha256':observer.CORE, 'pipeline_driver_sha256':observer.DRIVER,
                         'spec_sha256':observer.sha(observer.canonical(self.study['declaration']))}
        pairs=[self.pair(w,'pilot') for w in range(42000,42004)]+[self.pair(52000,'confirmation')]
        self.study['pilot_inventory']=[row for refs in pairs[:4]
                                      for row in json.loads((self.science/refs['inventory']['path']).read_bytes())['rows']]
        study_ref = self.put('study.json', self.study)
        roster = {'status':'frozen', 'world_count':66, 'world_ids':list(range(52000,52066)),
                  'pilot_worlds_excluded':True, 'study_sha256':observer.sha(observer.canonical(self.study))}
        roster_ref = self.put('confirmation-roster.json', roster)
        self.request = {'schema':'dams-accepted-result-observer-v1','study_root':str(self.science),
                        'study':study_ref, 'confirmation_roster':roster_ref,
                        'pairs':pairs,
                        'previous_confirmation_count':0, 'event_day':'2026-10-12',
                        'observed_utc':'2026-10-11T00:00:00Z',
                        'operation_prefix':'root-n300-observer', 'undefined_policy':'reject'}
        self.input = self.base/'input.json'; self.save_input()

    def tearDown(self):
        self.root_patch.stop(); self.tmp.cleanup()

    def put(self, relative, value):
        raw = observer.canonical(value)+b'\n' if not isinstance(value, bytes) else value
        path = self.science/relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        return {'path':relative, 'sha256':observer.sha(raw)}

    def csv_bytes(self, effects):
        stream = io.StringIO(newline=''); writer = csv.DictWriter(stream,fieldnames=list(effects[0]))
        writer.writeheader(); writer.writerows(effects)
        return stream.getvalue().encode()

    def pair(self, world, stage, *, status='full-paired-raw-validated'):
        pair = f'{stage}/world-{world}'; rows = []; cases = []
        for arm,regime in observer.ARMS.items():
            config = {'n':300,'days':1917,'seed':20261008,'world':world,'regime':regime,
                      'backend':'central','max_rss_mb':128,'max_events':1000000}
            cid = observer.sha(observer.canonical({k:v for k,v in config.items() if k not in observer.RESOURCE_FIELDS}))
            attempt = f'{pair}/cases/{cid}/attempt-001'
            summary = {'n':300,'world':world,'days_completed':1917,'regime':regime,'backend':'central'}
            summary_ref = self.put(attempt+'/summary.json',summary)
            manifest = {'status':'complete','exit_code':0,'scientific_case_id':cid,'config':config,
                        'config_sha256':observer.sha(observer.canonical(config)), 'source_sha256':observer.CORE,
                        'pipeline_driver_sha256':observer.DRIVER,'output_sha256':{'summary.json':summary_ref['sha256']},
                        'final_state_descriptor':{'state_semantic_sha256':'e'*64}}
            ref = self.put(attempt+'/manifest.json',manifest)
            rows.append({'case_id':cid,'config':config,'tags':{'arm_id':arm,'world':world,'parent_case_id':None}})
            cases.append({'case_id':cid,'arm':arm,'attempt':attempt,'manifest_sha256':ref['sha256'],
                          'summary':summary,'state_semantic_sha256':'e'*64})
        effects = [{**e,'stage':stage,'world':world} for e in self.effects]
        result = {'status':status,'world':world,'stage':stage,'initial_n':300,'days':1917,
                  'observation_years':5,'identity':self.identity,'entry_sha256':observer.ENTRY,
                  'cases':cases,'effects':effects,'producer_workers_absent':True}
        return {'result':self.put(pair+'/paired-result.json',result),
                'inventory':self.put(pair+'/inventory.json',{'identity':self.identity,'entry_sha256':observer.ENTRY,'rows':rows}),
                'effects':self.put(pair+'/effects.csv',self.csv_bytes(effects))}

    def save_input(self):
        self.input.write_bytes(observer.canonical(self.request)+b'\n')
        self.input_sha = observer.sha(self.input.read_bytes())

    def prepare(self):
        self.save_input()
        self.plan = observer.inspect(self.input,self.input_sha)
        self.plan_path = self.output/'prepared-metadata.json'
        self.plan_path.write_bytes(observer.canonical(self.plan)+b'\n')
        self.plan_sha = observer.sha(self.plan_path.read_bytes())
        return self.plan

    def grant(self, cap=100):
        if not hasattr(self,'enrollment'):
            authority={'schema':'dams-observer-root-parent-authority-v1','issued_by':'ROOT',
                       'state_root':str(self.state),'operation_prefix':self.plan['operation_prefix'],'day':self.plan['day'],
                       'max_events':57,'max_provider_calls':0,'max_gh_api_calls':cap,
                       'node_deadline_utc':'2026-10-12T23:00:00Z','global_deadline_utc':'2026-10-12T23:30:00Z',
                       'frozen_science':self.plan['public_event']['frozen_science']}
            self.parent_path=self.base/'root-parent-authority.json'
            self.parent_path.write_bytes(observer.canonical(authority)+b'\n')
            self.parent_sha=observer.sha(self.parent_path.read_bytes())
            self.enrollment=observer.enroll(self.state,self.parent_path,self.parent_sha,
                                           now=lambda:datetime(2026,10,12,12,tzinfo=timezone.utc))
        value = {'schema':'dams-observer-root-sync-grant-v1','issued_by':'ROOT','plan_sha256':self.plan_sha,
                 'operation_prefix':self.plan['operation_prefix'],'day':self.plan['day'],
                 'deadline_utc':'2026-10-12T23:00:00Z','max_events':57,'max_provider_calls':0,
                 'max_gh_api_calls':cap,'expected_parents':{r:'a'*40 for r in observer.REPOS},
                 'physical_roots':[str(p) for p,_ in self.roots], 'state_root':str(self.state),
                 'observer_source_sha256':observer.sha(MODULE.read_bytes()),
                 'journal_floor':self.enrollment['journal_floor'],
                 'enrollment_sha256':self.enrollment['enrollment_sha256']}
        self.grant_path = self.base/'grant.json'; self.grant_path.write_bytes(observer.canonical(value)+b'\n')
        self.grant_sha = observer.sha(self.grant_path.read_bytes())

    def sync(self, transport, when=None):
        return observer.synchronize(self.plan_path,self.plan_sha,self.grant_path,self.grant_sha,
                    self.state,transport,now=lambda:when or datetime(2026,10,12,12,tzinfo=timezone.utc))

    def test_default_is_preparation_only(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            observer.main(['--input',str(self.input),'--input-sha256',self.input_sha,'--output',str(self.output)])
        self.assertEqual(json.loads(out.getvalue())['GitHub_calls'],0)
        self.assertFalse((self.state/'observer-journal.jsonl').exists())
        self.assertFalse(self.roots[0][0].joinpath(observer.CURRENT).exists())

    def test_source_mismatch_rejected(self):
        self.study['source_sha256']='0'*64
        self.request['study']=self.put('study.json',self.study); self.save_input()
        with self.assertRaisesRegex(observer.Refused,'source/runtime'): observer.inspect(self.input,self.input_sha)

    def test_partial_world_does_not_count(self):
        self.request['pairs'].append(self.pair(52001,'confirmation',status='running'))
        plan=self.prepare()
        self.assertEqual(plan['public_event']['N300']['confirmation_complete'],1)
        self.assertEqual(plan['public_event']['retained_incomplete_receipts'][0]['world'],52001)

    def test_complete_receipt_with_partial_selected_case_rejected(self):
        refs=self.request['pairs'][-1]; result=json.loads((self.science/refs['result']['path']).read_bytes())
        case=result['cases'][0]; path=case['attempt']+'/manifest.json'
        manifest=json.loads((self.science/path).read_bytes()); manifest['status']='failed'
        case['manifest_sha256']=self.put(path,manifest)['sha256']
        refs['result']=self.put(refs['result']['path'],result); self.save_input()
        with self.assertRaisesRegex(observer.Refused,'completed manifest'):observer.inspect(self.input,self.input_sha)

    def test_prior_failed_attempt_is_retained_and_ignored(self):
        refs=self.request['pairs'][-1]; result=json.loads((self.science/refs['result']['path']).read_bytes())
        path=result['cases'][0]['attempt'].replace('attempt-001','attempt-000')+'/manifest.json'
        ref=self.put(path,{'status':'failed','source_sha256':'bad-history'})
        self.assertEqual(self.prepare()['public_event']['N300']['confirmation_complete'],1)
        self.assertEqual(observer.sha((self.science/path).read_bytes()),ref['sha256'])

    def test_duplicate_and_gap_refused(self):
        self.request['pairs'].append(self.request['pairs'][-1]); self.save_input()
        with self.assertRaisesRegex(observer.Refused,'duplicate world'):observer.inspect(self.input,self.input_sha)
        self.request['pairs'].pop(); self.request['pairs'][-1]=self.pair(52001,'confirmation'); self.save_input()
        with self.assertRaisesRegex(observer.Refused,'gap'):observer.inspect(self.input,self.input_sha)

    def test_engineering_world_refused(self):
        self.request['pairs'][-1]=self.pair(90000,'confirmation'); self.save_input()
        with self.assertRaisesRegex(observer.Refused,'engineering'):observer.inspect(self.input,self.input_sha)

    def test_relative_escape_and_symlink_refused(self):
        self.request['study']['path']='../outside.json'; self.save_input()
        with self.assertRaises(observer.Refused):observer.inspect(self.input,self.input_sha)
        self.request['study']['path']='study.json'; p=self.science/'study.json'
        target=self.base/'target.json'; p.rename(target); p.symlink_to(target); self.save_input()
        with self.assertRaises(OSError):observer.inspect(self.input,self.input_sha)

    def test_undefined_explicitly_retained_no_world_selection(self):
        refs=self.request['pairs'][-1]; result=json.loads((self.science/refs['result']['path']).read_bytes())
        result['effects'][0].update(effect=None,reference_denominator=0.)
        refs['result']=self.put(refs['result']['path'],result)
        refs['effects']=self.put(refs['effects']['path'],self.csv_bytes(result['effects']))
        self.save_input()
        with self.assertRaisesRegex(observer.Refused,'undefined'):observer.inspect(self.input,self.input_sha)
        self.request['undefined_policy']='retain'
        plan=self.prepare(); pair=plan['public_event']['new_verified_pairs'][0]
        self.assertEqual(pair['effects'][0]['effect_status'],'undefined-exposure')
        self.assertEqual(plan['public_event']['N300']['confirmation_complete'],1)

    def test_csv_nonfinite_refused(self):
        refs=self.request['pairs'][-1]
        raw=(self.science/refs['effects']['path']).read_bytes().replace(b'-0.125',b'inf',1)
        refs['effects']=self.put(refs['effects']['path'],raw);self.save_input()
        with self.assertRaisesRegex(observer.Refused,'nonfinite'):observer.inspect(self.input,self.input_sha)

    def test_success_readback_same_three_physical_roots(self):
        self.prepare(); self.grant(); transport=FakeTransport(); result=self.sync(transport)
        self.assertEqual(result['events_total'],1)
        self.assertEqual(result['provider_calls'],0)
        for path,value in self.plan['files'].items():
            for root,_ in self.roots:self.assertEqual((root/path).read_bytes(),observer.canonical(value)+b'\n')
        self.assertFalse(result['independent_full_raw_verification_performed'])
        self.assertTrue(all(c[2]['force'] is False for c in transport.calls if c[0]=='PATCH'))

    def test_remote_drift_stops_before_update(self):
        self.prepare(); self.grant(); transport=FakeTransport(); transport.heads[observer.REPOS[0]]='d'*40
        with self.assertRaisesRegex(observer.Refused,'drift'):self.sync(transport)
        self.assertFalse(any(c[0]!='GET' for c in transport.calls))

    def test_pre_patch_remote_drift_never_forces(self):
        self.prepare(); self.grant(); transport=FakeTransport(); transport.drift_before_patch=True
        with self.assertRaisesRegex(observer.Refused,'drift'):self.sync(transport)
        self.assertFalse(any(c[0]=='PATCH' for c in transport.calls))

    def test_partial_two_repo_resume_preserves_completed_unit_and_counters(self):
        self.prepare(); self.grant(); transport=FakeTransport();transport.fail_repo_once=observer.REPOS[1]
        with self.assertRaises(DefinitelyNotApplied):self.sync(transport)
        prefix=(self.state/'observer-journal.jsonl').read_bytes(); before=len(transport.calls)
        self.assertNotEqual(transport.heads[observer.REPOS[0]],'a'*40)
        result=self.sync(transport)
        self.assertTrue((self.state/'observer-journal.jsonl').read_bytes().startswith(prefix))
        self.assertEqual(result['events_total'],1)
        self.assertEqual(result['API_calls_total'],len(transport.calls))
        self.assertFalse(any(c[0]!='GET' and '/repos/'+observer.REPOS[0]+'/' in c[1] for c in transport.calls[before:]))

    def test_deadline_no_calls(self):
        self.prepare();self.grant();transport=FakeTransport()
        with self.assertRaisesRegex(observer.Refused,'deadline'):self.sync(transport,datetime(2026,10,13,tzinfo=timezone.utc))
        self.assertEqual(transport.calls,[])

    def test_api_cap_persists_no_reset_on_resume_or_new_grant(self):
        self.prepare();self.grant(cap=2);transport=FakeTransport()
        with self.assertRaisesRegex(observer.Refused,'counter exhausted'):self.sync(transport)
        self.assertEqual(len(transport.calls),2)
        prefix=(self.state/'observer-journal.jsonl').read_bytes()
        with self.assertRaisesRegex(observer.Refused,'counter exhausted'):self.sync(transport)
        self.assertEqual(len(transport.calls),2)
        self.assertEqual((self.state/'observer-journal.jsonl').read_bytes(),prefix)
        self.grant(cap=100)
        with self.assertRaisesRegex(observer.Refused,'cannot reset'):self.sync(transport)

    def test_unknown_mutation_never_blindly_reissued(self):
        self.prepare();self.grant();transport=FakeTransport();transport.tree_unknown_once=True
        with self.assertRaises(RuntimeError):self.sync(transport)
        with self.assertRaisesRegex(observer.Refused,'unknown mutating'):self.sync(transport)
        self.assertEqual(sum(c[0]=='POST' for c in transport.calls),1)

    def test_metadata_changed_after_preparation_no_calls(self):
        self.prepare();self.grant();transport=FakeTransport()
        p=self.science/self.request['pairs'][-1]['result']['path'];p.write_bytes(p.read_bytes()+b' ')
        with self.assertRaisesRegex(observer.Refused,'SHA differs'):self.sync(transport)
        self.assertEqual(transport.calls,[])

    def test_event_cap_all_prior_begins_preserved(self):
        self.prepare();self.grant();transport=FakeTransport()
        journal=observer.Journal(self.state,self.enrollment['enrollment_sha256'])
        try:
            for n in range(57):
                event=observer.sha(str(n).encode())
                journal.append('OP_BEGIN',event_id=event,plan_sha256='f'*64,
                               expected_parents={r:'a'*40 for r in observer.REPOS})
                journal.append('OP_DONE',event_id=event,commits=[])
        finally:journal.close()
        before=(self.state/'observer-journal.jsonl').read_bytes()
        with self.assertRaisesRegex(observer.Refused,'event counter'):self.sync(transport)
        self.assertTrue((self.state/'observer-journal.jsonl').read_bytes().startswith(before))
        self.assertEqual(transport.calls,[])

    def test_cap2_delete_journal_same_grant_makes_zero_extra_calls(self):
        self.prepare();self.grant(cap=2);transport=FakeTransport()
        with self.assertRaisesRegex(observer.Refused,'counter exhausted'):self.sync(transport)
        self.assertEqual(len(transport.calls),2)
        marker=(self.state/'enrollment.json').read_bytes()
        (self.state/'observer-journal.jsonl').unlink() # Synthetic owned counterexample only.
        with self.assertRaises((observer.Refused,FileNotFoundError)):self.sync(transport)
        self.assertEqual(len(transport.calls),2)
        self.assertEqual((self.state/'enrollment.json').read_bytes(),marker)
        self.assertEqual(len(list((self.state/'issued-begin').iterdir())),2)
        self.assertFalse((self.state/'observer-journal.jsonl').exists())

    def test_truncated_to_initial_header_retained_issued_begin_refuses(self):
        self.prepare();self.grant(cap=2);transport=FakeTransport()
        with self.assertRaises(observer.Refused):self.sync(transport)
        p=self.state/'observer-journal.jsonl';first=p.read_bytes().splitlines(keepends=True)[0]
        p.write_bytes(first) # Same inode, original header, but later issued calls survive.
        with self.assertRaisesRegex(observer.Refused,'truncated'):self.sync(transport)
        self.assertEqual(len(transport.calls),2)

    def test_lost_marker_never_recreated(self):
        self.prepare();self.grant();transport=FakeTransport()
        (self.state/'enrollment.json').unlink()
        with self.assertRaises((observer.Refused,FileNotFoundError)):self.sync(transport)
        self.assertEqual(transport.calls,[])
        self.assertFalse((self.state/'enrollment.json').exists())

    def test_enrollment_exclusive_and_cannot_move_original_parent_to_new_namespace(self):
        self.prepare();self.grant()
        with self.assertRaisesRegex(observer.Refused,'fresh owned empty'):observer.enroll(self.state,self.parent_path,self.parent_sha,
                         now=lambda:datetime(2026,10,12,12,tzinfo=timezone.utc))
        other=self.base/'bypass';other.mkdir()
        with self.assertRaisesRegex(observer.Refused,'another native namespace'):observer.enroll(other,self.parent_path,self.parent_sha,
                         now=lambda:datetime(2026,10,12,12,tzinfo=timezone.utc))
        self.assertEqual(list(other.iterdir()),[])

    def test_child_deadline_can_shorten_but_not_extend_parent_or_original_operation(self):
        self.prepare();self.grant();transport=FakeTransport()
        grant=json.loads(self.grant_path.read_bytes());grant['deadline_utc']='2026-10-12T23:15:00Z'
        self.grant_path.write_bytes(observer.canonical(grant)+b'\n');self.grant_sha=observer.sha(self.grant_path.read_bytes())
        with self.assertRaisesRegex(observer.Refused,'cannot reset or extend'):self.sync(transport)
        self.assertEqual(transport.calls,[])
        grant['deadline_utc']='2026-10-12T12:30:00Z'
        self.grant_path.write_bytes(observer.canonical(grant)+b'\n');self.grant_sha=observer.sha(self.grant_path.read_bytes())
        transport.fail_repo_once=observer.REPOS[1]
        with self.assertRaises(DefinitelyNotApplied):self.sync(transport)
        before=len(transport.calls);grant['deadline_utc']='2026-10-12T13:00:00Z'
        self.grant_path.write_bytes(observer.canonical(grant)+b'\n');self.grant_sha=observer.sha(self.grant_path.read_bytes())
        with self.assertRaisesRegex(observer.Refused,'deadline regressed'):self.sync(transport)
        self.assertEqual(len(transport.calls),before)

    def test_parent_authority_is_rechecked_before_each_api(self):
        self.prepare();self.grant();transport=FakeTransport();original=transport.api
        def api(method,endpoint,body=None,*,timeout):
            result=original(method,endpoint,body,timeout=timeout)
            if len(transport.calls)==2:self.parent_path.write_bytes(self.parent_path.read_bytes()+b' ')
            return result
        transport.api=api
        with self.assertRaisesRegex(observer.Refused,'SHA differs'):self.sync(transport)
        self.assertEqual(len(transport.calls),2)

    def test_public_acceptance_count_is_distinct_from_observer_generation(self):
        public=self.prepare()['public_event']
        self.assertEqual(public['newly_accepted_formal_worlds'],1)
        self.assertEqual(public['observer_Model_calls'],0)
        self.assertEqual(public['observer_generated_worlds'],0)
        self.assertNotIn('new_scientific_worlds',public)
        self.assertEqual(public['operation_namespace_day'],'2026-10-12')
        self.assertEqual(public['observed_utc'],'2026-10-11T00:00:00Z')
        self.assertEqual(self.plan['files'][observer.CURRENT]['observed_utc'],public['observed_utc'])

    def test_observed_time_must_be_utc_and_not_future(self):
        self.request['observed_utc']='2026-10-11T00:00:00+01:00';self.save_input()
        with self.assertRaisesRegex(observer.Refused,'UTC'):observer.inspect(self.input,self.input_sha)
        self.request['observed_utc']='2026-10-15T00:00:00Z';self.save_input()
        with self.assertRaisesRegex(observer.Refused,'future'):observer.inspect(self.input,self.input_sha,
                                now=lambda:datetime(2026,10,12,12,tzinfo=timezone.utc))

    def test_acknowledged_source_floor_cannot_regress_on_later_child_grant(self):
        self.prepare();self.grant(cap=2);transport=FakeTransport()
        original_grant=self.grant_path.read_bytes()
        with self.assertRaises(observer.Refused):self.sync(transport)
        rows=[json.loads(line) for line in (self.state/'observer-journal.jsonl').read_bytes().splitlines()]
        grant=json.loads(original_grant)
        grant['journal_floor']={'records':len(rows),'head_sha256':observer.sha(observer.canonical(rows[-1]))}
        self.grant_path.write_bytes(observer.canonical(grant)+b'\n');self.grant_sha=observer.sha(self.grant_path.read_bytes())
        with self.assertRaisesRegex(observer.Refused,'counter exhausted'):self.sync(transport)
        self.assertEqual(len(transport.calls),2)
        self.grant_path.write_bytes(original_grant);self.grant_sha=observer.sha(original_grant)
        with self.assertRaisesRegex(observer.Refused,'source floor regressed'):self.sync(transport)
        self.assertEqual(len(transport.calls),2)

    def test_cumulative_journal_cap_refuses_before_issued_witness_or_append(self):
        self.prepare();self.grant()
        journal=observer.Journal(self.state,self.enrollment['enrollment_sha256'])
        path=self.state/'observer-journal.jsonl';before=path.read_bytes()
        try:
            with patch.object(observer,'JOURNAL_BOUND',len(before)+10):
                with self.assertRaisesRegex(observer.Refused,'before append'):
                    journal.append('API_BEGIN',call=0,event_id='f'*64,key='synthetic',method='GET',
                                   endpoint='/repos/rui-h-a/dams-simulation/git/ref/heads/main',body_sha256='e'*64)
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(list((self.state/'issued-begin').iterdir()),[])
        finally:journal.close()

    def test_missing_issued_witness_refuses_without_new_calls(self):
        self.prepare();self.grant(cap=2);transport=FakeTransport()
        with self.assertRaises(observer.Refused):self.sync(transport)
        (self.state/'issued-begin'/'000000.json').unlink()
        with self.assertRaisesRegex(observer.Refused,'retained floor'):self.sync(transport)
        self.assertEqual(len(transport.calls),2)

    def test_root_journal_floor_rejects_truncated_history(self):
        self.prepare();self.grant();transport=FakeTransport()
        value=json.loads(self.grant_path.read_bytes())
        value['journal_floor']={'records':1,'head_sha256':'e'*64}
        self.grant_path.write_bytes(observer.canonical(value)+b'\n')
        self.grant_sha=observer.sha(self.grant_path.read_bytes())
        with self.assertRaisesRegex(observer.Refused,'floor'):self.sync(transport)
        self.assertEqual(transport.calls,[])


if __name__ == '__main__':
    unittest.main()

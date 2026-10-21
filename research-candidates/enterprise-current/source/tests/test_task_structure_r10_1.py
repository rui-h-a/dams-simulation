"""Pure missing-obligation counterexample; no Model or new trajectories."""
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import unittest

from dams_sim.config import Config
from dams_sim.storage import canonical
from research_tools.validate_organization_tasks import verify_organization_accounts

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=ROOT/'.task-structure-validation'


class MissingTaskObligation(unittest.TestCase):
    def test_coherent_omission_rejected_against_complete_installed_growth(self):
        # Retained R10 default raw is reused only for a tamper control. No
        # old scientific state is certified as executed under R10.1 source.
        origin=PRIVATE/'native-two-trajectories/default-v1/final-state.sqlite'
        if not origin.exists():self.skipTest('requires preserved original R10 engineering raw fixture; no Model fallback')
        header=json.loads((origin.with_suffix('.json')).read_bytes())['state']
        raw=copy.deepcopy(header['config']);raw['organization_tasks']['schema_version']=2
        cfg=Config.from_dict(raw)
        db=sqlite3.connect(':memory:')
        source=sqlite3.connect(origin.resolve().as_uri()+'?mode=ro&immutable=1',uri=True);source.backup(db);source.close()
        def convert(value):
            value=copy.deepcopy(value);value['schema_version']=2
            value['structure']={'department_count':2,'observed_installed_departments':2,
                'initial_units':{'0':1.,'1':1.},'objectives':[list(w) for w in cfg.organization_tasks.department_objectives],
                'task_families':{'0':0,'1':1},'assignments':{str(i):i%2 for i in range(cfg.n)},
                'training_remaining':{},'pending':[],'history':[],'replans':[]}
            if value['last_plan'] is not None:
                for allocation in value['last_plan']['allocations'].values():
                    allocation.update(reorganization_hours=0.,remaining_reorganization_hours=0.)
            return value
        try:
            for seq,kind,payload in list(db.execute('SELECT seq,kind,payload FROM journal')):
                v=json.loads(payload)
                if kind=='day_end':v['organization_tasks_state']=convert(v['organization_tasks_state'])
                elif kind=='organization_plan':
                    for allocation in v['allocations'].values():allocation.update(reorganization_hours=0.,remaining_reorganization_hours=0.)
                elif kind=='organization_tasks':v['task_families']={'0':0,'1':1}
                db.execute('UPDATE journal SET payload=? WHERE seq=?',(canonical(v).decode(),seq))
            db.commit()
            state=copy.deepcopy(header);state['config']=cfg.to_dict();state['organization_tasks_state']=convert(state['organization_tasks_state'])
            oldfile=PRIVATE/'r10-original-freeze-source/research_tools/validate_organization_tasks.py'
            spec=importlib.util.spec_from_file_location('preserved_r10_task_validator',oldfile)
            old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
            # Demonstrated original blind spot: coherent no-op task history
            # accepts installed 2->3 while obligation evidence is absent.
            self.assertTrue(old.verify_organization_accounts(cfg,state,db)['valid'])
            with self.assertRaisesRegex(ValueError,'required task reorganization obligation'):
                verify_organization_accounts(cfg,state,db)
        finally:db.close()

    def test_preserved_v1_task_subset_stays_accepted(self):
        origin=PRIVATE/'native-two-trajectories/default-v1/final-state.sqlite'
        if not origin.exists():self.skipTest('requires preserved original R10 engineering raw fixture; no Model fallback')
        header=json.loads(origin.with_suffix('.json').read_bytes())['state']
        result=verify_organization_accounts(Config.from_dict(header['config']),header,origin)
        self.assertTrue(result['valid']);self.assertEqual(result['days_verified'],60)
        self.assertEqual(result['formal_worlds_added'],0)


if __name__=='__main__':unittest.main()

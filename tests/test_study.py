import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from dams_sim.config import Config
from dams_sim.cli import run_world
from dams_sim.storage import atomic_json
from research_tools.study import DRIVER_SHA, case_id, saved_case, records_for, seal_ensemble


class StudyIntegrityTests(unittest.TestCase):
    def test_modified_summary_cannot_enter_ensemble(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=Config(n=12,days=4,guilds=2)
            original=saved_case(root,p)
            case=next(root.iterdir())
            changed=dict(original);changed['produced_work_units']=1e12
            (case/'summary.json').write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError,'integrity mismatch'):
                saved_case(root,p)

    def test_successful_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=Config(n=12,days=4,guilds=2)
            case=root/('case-'+case_id(p));case.mkdir()
            run_world(p,case,checkpoint_day=2)
            m=json.loads((case/'manifest.json').read_text())
            m['research_driver_sha256']=DRIVER_SHA;atomic_json(case/'manifest.json',m)
            first=saved_case(root,p);second=saved_case(root,p)
            self.assertEqual(first,second)
            self.assertEqual(len(list(root.iterdir())),2)

    def test_orphan_before_manifest_is_preserved_then_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=Config(n=12,days=4,guilds=2)
            case=root/('case-'+case_id(p));case.mkdir()
            summary=saved_case(root,p)
            self.assertEqual(summary['days_completed'],4)
            self.assertTrue((case/'interruption.json').is_file())
            self.assertEqual(len(list(root.iterdir())),2)
            self.assertEqual(len(records_for(root)),1)
            seal_ensemble(root,{'status':'complete'})
            m=json.loads((root/'ensemble_manifest.json').read_text())
            self.assertEqual(m['noncomplete_attempts'],1)

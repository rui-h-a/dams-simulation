import hashlib
import os
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'research_tools'))
from cloud_control import GuardError
from cloud_archive import (CODEC, CHUNK_BYTES, MAX_ENCODED_CHUNK, encode_file, restore_file,
                           validate_manifest, decode_chunk, safe_name)


class ArchiveTests(unittest.TestCase):
    def encode(self, root, data, callback=None):
        source = root/'source'; source.write_bytes(data); objects = {}
        def publish(path, chunk):
            objects[chunk['encoded_sha256']] = path.read_bytes()
            if callback:
                callback(source, chunk)
        manifest = encode_file(source,publish,max_raw_bytes=len(data),minimum_free_bytes=0,scratch=root)
        return source, manifest, objects

    def restore(self, root, manifest, objects, destination=None):
        def fetch(chunk,path):
            data = objects[chunk['encoded_sha256']]
            if len(data)>chunk['encoded_bytes']:
                raise GuardError('synthetic bounded input rejected')
            path.write_bytes(data)
        return restore_file(manifest,fetch,destination or root/'restored',max_raw_bytes=manifest['raw_bytes'],minimum_free_bytes=0)

    def test_empty_small_and_multichunk_exact_bytes(self):
        for data in (b'',b'bytes\x00\xff\r\n',b'a'*CHUNK_BYTES+os.urandom(CHUNK_BYTES)+b'last'):
            with self.subTest(size=len(data)),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);source,manifest,objects=self.encode(root,data)
                self.assertEqual(manifest['codec'],CODEC)
                self.assertEqual(manifest['chunk_bytes'],CHUNK_BYTES)
                self.assertEqual(self.restore(root,manifest,objects),hashlib.sha256(data).hexdigest())
                self.assertEqual((root/'restored').read_bytes(),source.read_bytes())
                self.assertEqual(manifest['encoded_bytes'],sum(c['encoded_bytes'] for c in manifest['chunks']))
                self.assertTrue(all(c['encoded_bytes']<=MAX_ENCODED_CHUNK for c in manifest['chunks']))

    def test_fixed_boundaries_exact_dedup_across_file_prefixes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);_,a,objects=self.encode(root,b'x'*CHUNK_BYTES+b'a')
            _,b,other=self.encode(root,b'x'*CHUNK_BYTES+b'b')
            self.assertEqual(a['chunks'][0],b['chunks'][0])
            self.assertEqual(len(set(objects)|set(other)),3)

    def test_malformed_roster_and_unknown_schema_are_rejected_before_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);_,good,objects=self.encode(root,b'a'*CHUNK_BYTES+b'b')
            mutations=[lambda x:x.update(codec='unknown'),lambda x:x.update(schema=True),
                       lambda x:x.update(extra=1),lambda x:x.update(raw_bytes=True),
                       lambda x:x.update(encoded_bytes=x['encoded_bytes']+1),
                       lambda x:x.update(raw_sha256='bad'),lambda x:x['chunks'].pop(),
                       lambda x:x['chunks'].append(deepcopy(x['chunks'][0])),
                       lambda x:x['chunks'].reverse(),lambda x:x['chunks'][1].update(offset=0),
                       lambda x:x['chunks'][0].update(raw_bytes=1),
                       lambda x:x['chunks'][0].update(encoded_bytes=MAX_ENCODED_CHUNK+1),
                       lambda x:x['chunks'][0].update(encoded_sha256='G'*64),
                       lambda x:x['chunks'][0].update(extra=1)]
            for mutate in mutations:
                bad=deepcopy(good);mutate(bad)
                with self.subTest(manifest=bad),self.assertRaises(GuardError):
                    validate_manifest(bad)

    def test_truncated_trailing_wrong_chunk_sha_and_bomb_leave_no_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);_,good,objects=self.encode(root,b'expected bytes')
            encoded=next(iter(objects.values()))
            for stream,raw_length in ((encoded[:-1],good['raw_bytes']),(encoded+b'extra',good['raw_bytes']),
                                     (encoded+encoded,good['raw_bytes']),(zlib.compress(b'x'*1_000_000),good['raw_bytes'])):
                bad=deepcopy(good);chunk=bad['chunks'][0]
                chunk.update(encoded_sha256=hashlib.sha256(stream).hexdigest(),encoded_bytes=len(stream))
                bad['encoded_bytes']=len(stream)
                with self.subTest(encoded=len(stream)),self.assertRaises(GuardError):
                    self.restore(root,bad,{chunk['encoded_sha256']:stream})
                self.assertFalse((root/'restored').exists())
            for field in ('raw_sha256','encoded_sha256'):
                bad=deepcopy(good);bad['chunks'][0][field]='0'*64
                with self.assertRaises((GuardError,KeyError)):
                    self.restore(root,bad,objects)
                self.assertFalse((root/'restored').exists())

    def test_missing_chunk_and_whole_file_mismatch_leave_no_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);_,good,objects=self.encode(root,b'a'*CHUNK_BYTES+b'b')
            missing=dict(objects);missing.pop(good['chunks'][-1]['encoded_sha256'])
            with self.assertRaises(KeyError):self.restore(root,good,missing)
            self.assertFalse((root/'restored').exists())
            bad=deepcopy(good);bad['raw_sha256']='0'*64
            with self.assertRaises(GuardError):self.restore(root,bad,objects)
            self.assertFalse((root/'restored').exists())

    def test_raw_cap_and_capacity_admission_precede_publish_fetch_or_target_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,manifest,objects=self.encode(root,b'raw')
            with self.assertRaises(GuardError):
                encode_file(source,lambda *_:self.fail('published before admission'),max_raw_bytes=2,minimum_free_bytes=0,scratch=root)
            with self.assertRaises(GuardError):
                restore_file(manifest,lambda *_:self.fail('fetched before admission'),root/'new'/'output',max_raw_bytes=2,minimum_free_bytes=0)
            with patch('cloud_archive.shutil.disk_usage',return_value=type('Usage',(),{'free':0})()):
                with self.assertRaises(GuardError):
                    restore_file(manifest,lambda *_:self.fail('fetched before capacity admission'),root/'new'/'output',max_raw_bytes=3,minimum_free_bytes=0)
            self.assertFalse((root/'new').exists())

    def test_source_mutation_even_with_restored_mtime_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def mutate(source,chunk):
                before=source.stat();source.write_bytes(b'y'*before.st_size)
                os.utime(source,ns=(before.st_atime_ns,before.st_mtime_ns))
            with self.assertRaises(GuardError):self.encode(root,b'x'*100,mutate)

    def test_untrusted_names_and_existing_symlink_target_rejected(self):
        for name in ('','/absolute','../a','a/../b','a//b','a/./b','a\\b','a\x00b','a\nb'):
            with self.subTest(name=name),self.assertRaises(GuardError):safe_name(name)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);_,manifest,objects=self.encode(root,b'raw');(root/'link').symlink_to(root/'source')
            with self.assertRaises(GuardError):self.restore(root,manifest,objects,root/'link')
            self.assertEqual((root/'source').read_bytes(),b'raw')


if __name__ == '__main__':
    unittest.main()

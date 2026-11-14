"""Synthetic-only tests. No real repository identities, network or module execution."""
import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error
import zipfile
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/module-custody'))
import custody
import validate_lock

def sha(data): return hashlib.sha256(data).hexdigest()
def encode(value): return (json.dumps(value,sort_keys=True,indent=2)+'\n').encode()

class Fixture:
    def __init__(self,root):
        self.root=root
        self.module='github.com/example-upstream/example-module'
        self.version='v0.1.0'
        self.files={'go.mod':('module '+self.module+'\n\ngo 1.25.1\n').encode(),
                    'example.go':b'package example\nconst Value = 1\n'}
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            for name,data in self.files.items(): z.writestr(self.module+'@'+self.version+'/'+name,data)
        # Match Go's canonical cached .info envelope; preservation is byte exact.
        self.raw={'info':json.dumps({'Version':self.version,'Time':'2025-01-01T00:00:00Z'},separators=(',',':')).encode(),
                  'mod':self.files['go.mod'],'zip':stream.getvalue()}
        self.sum=custody.h1([(self.module+'@'+self.version+'/'+name,data) for name,data in self.files.items()])
        self.modsum=custody.h1([('go.mod',self.files['go.mod'])])
        fmap={name:sha(data) for name,data in self.files.items()}
        artifacts={ext:{'path':'package/'+self.version+'.'+ext,'bytes':len(raw),'sha256':sha(raw)} for ext,raw in self.raw.items()}
        self.manifest={'schema':'original-adapter-custody-v1','modules':[{'path':self.module,'version':self.version,'sum':self.sum,'go_mod_sum':self.modsum,'file_count':len(fmap),'normalized_files':fmap,'artifacts':artifacts}]}
        self.blobs={'bundle/manifest.json':encode(self.manifest)}|{'bundle/'+artifacts[ext]['path']:raw for ext,raw in self.raw.items()}
        self.lock={'schema_version':1,'go_version':'go1.25.1','app':{'id':1,'installation_id':2,'slug':'example-app'},
                   'consumer':{'repository':'example-org/example-service','repository_id':3,'base_commit':'1'*40,'module':'github.com/example-org/example-service','files':{'go.mod':'1'*64,'go.sum':'2'*64}},
                   'repositories':[{'repository':'example-org/example-custody','id':4}],
                   'modules':[{'path':self.module,'version':self.version,'sum':self.sum,'go_mod_sum':self.modsum,'go_mod_sha256':sha(self.files['go.mod']),'files_digest':custody.canonical(fmap),'file_count':len(fmap)}],
                   'custody':{'repository':'example-org/example-custody','repository_id':4,'commit':'2'*40,'tree':'3'*40,'manifest_path':'bundle/manifest.json','manifest_sha256':sha(self.blobs['bundle/manifest.json']),
                              'blobs':[{'path':name,'mode':'100644','bytes':len(data),'git_blob_sha':hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest(),'sha256':sha(data)} for name,data in self.blobs.items()],
                              'modules':[{'path':self.module,'version':self.version,'sum':self.sum,'go_mod_sum':self.modsum}]}}
        for name,data in self.blobs.items():
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        self.lockpath=root/'lock.json';self.lockpath.write_bytes(encode(self.lock))
    def cache(self,where):
        base=where/'cache/download'/self.module/'@v';base.mkdir(parents=True)
        for ext,raw in self.raw.items():(base/(self.version+'.'+ext)).write_bytes(raw)
        (base/(self.version+'.ziphash')).write_text(self.sum)
        directory=where/(self.module+'@'+self.version);directory.mkdir(parents=True)
        for name,data in self.files.items():(directory/name).write_bytes(data)
        return base,directory

class LockTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();self.fixture=Fixture(self.root)
    def tearDown(self): self.temp.cleanup()
    def invalid(self,mutate):
        lock=copy.deepcopy(self.fixture.lock);mutate(lock);self.fixture.lockpath.write_bytes(encode(lock))
        with self.assertRaises(validate_lock.InvalidLock):validate_lock.load(self.fixture.lockpath)
    def test_valid_data_only_lock(self):self.assertEqual(validate_lock.load(self.fixture.lockpath),self.fixture.lock)
    def test_arbitrary_executable_field_rejected(self):self.invalid(lambda x:x.update(executable='run.sh'))
    def test_arbitrary_endpoint_field_rejected(self):self.invalid(lambda x:x['custody'].update(url='https://example.invalid/payload'))
    def test_duplicate_json_field_rejected(self):
        self.fixture.lockpath.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaises(validate_lock.InvalidLock):validate_lock.load(self.fixture.lockpath)
    def test_unsafe_consumer_path_rejected(self):self.invalid(lambda x:x['consumer']['files'].update({'../outside':'3'*64}))
    def test_unsafe_blob_path_rejected(self):self.invalid(lambda x:x['custody']['blobs'][0].update(path='../manifest.json'))
    def test_duplicate_repository_rejected(self):self.invalid(lambda x:x['repositories'].append(x['repositories'][0]))
    def test_cross_owner_repository_rejected(self):self.invalid(lambda x:x['repositories'].append({'repository':'another-org/repository','id':9}))
    def test_wrong_repository_id_rejected(self):self.invalid(lambda x:x['custody'].update(repository_id=99))
    def test_custody_version_drift_rejected(self):self.invalid(lambda x:x['custody']['modules'][0].update(version='v0.2.0'))
    def test_local_directory_replace_rejected(self):self.invalid(lambda x:x['modules'][0].update(replacement={'path':'../source','version':'v0.1.0','sum':self.fixture.sum,'commit':'a'*40}))
    def test_unknown_nested_field_rejected(self):self.invalid(lambda x:x['modules'][0].update(command='execute'))
    def test_module_duplicates_rejected(self):self.invalid(lambda x:x['modules'].append(x['modules'][0]))
    def test_boolean_numeric_id_rejected(self):self.invalid(lambda x:x['app'].update(id=True))
    def test_lock_symlink_rejected(self):
        link=self.root/'linked.json';link.symlink_to(self.fixture.lockpath)
        with self.assertRaises(validate_lock.InvalidLock):validate_lock.load(link)

class CustodyContentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();self.fixture=Fixture(self.root/'bundle-source')
        self.cache=self.root/'cache';self.base,self.directory=self.fixture.cache(self.cache)
    def tearDown(self):self.temp.cleanup()
    def test_proxy_exact_original_bytes(self):
        proxy=self.root/'proxy';m=custody.custody_proxy(self.fixture.lock,self.fixture.root,proxy)
        self.assertEqual(m,self.fixture.manifest)
        for ext,data in self.fixture.raw.items():self.assertEqual((proxy/self.fixture.module/'@v'/(self.fixture.version+'.'+ext)).read_bytes(),data)
    def test_proxy_output_reuse_rejected(self):
        proxy=self.root/'proxy';proxy.mkdir()
        with self.assertRaises(validate_lock.InvalidLock):custody.custody_proxy(self.fixture.lock,self.fixture.root,proxy)
    def test_manifest_changed_rejected(self):
        (self.fixture.root/'bundle/manifest.json').write_text('{}')
        with self.assertRaises(validate_lock.InvalidLock):custody.custody_proxy(self.fixture.lock,self.fixture.root,self.root/'proxy')
    def test_custody_artifact_tamper_rejected(self):
        path=self.fixture.root/'bundle/package'/('v0.1.0.zip');path.write_bytes(path.read_bytes()+b'tamper')
        with self.assertRaises(validate_lock.InvalidLock):custody.custody_proxy(self.fixture.lock,self.fixture.root,self.root/'proxy')
    def test_cache_positive(self):custody.verify_custody_cache(self.fixture.lock,self.fixture.manifest,self.cache)
    def test_missing_artifacts_each_fail_closed(self):
        for ext in ['info','mod','zip','ziphash']:
            with self.subTest(ext=ext):
                path=self.base/(self.fixture.version+'.'+ext);data=path.read_bytes();path.unlink()
                with self.assertRaises(validate_lock.InvalidLock):custody.verify_custody_cache(self.fixture.lock,self.fixture.manifest,self.cache)
                path.write_bytes(data)
    def test_missing_unpacked_go_mod_rejected(self):
        (self.directory/'go.mod').unlink()
        with self.assertRaises(validate_lock.InvalidLock):custody.verify_custody_cache(self.fixture.lock,self.fixture.manifest,self.cache)
    def test_unpacked_source_tamper_rejected(self):
        (self.directory/'example.go').write_text('package changed')
        with self.assertRaises(validate_lock.InvalidLock):custody.verify_custody_cache(self.fixture.lock,self.fixture.manifest,self.cache)
    def test_extra_unpacked_file_rejected(self):
        (self.directory/'extra.go').write_text('package extra')
        with self.assertRaises(validate_lock.InvalidLock):custody.verify_custody_cache(self.fixture.lock,self.fixture.manifest,self.cache)
    def test_cache_symlink_rejected(self):
        path=self.base/(self.fixture.version+'.info');outside=self.root/'outside';outside.write_bytes(path.read_bytes());path.unlink();path.symlink_to(outside)
        with self.assertRaises(validate_lock.InvalidLock):custody.verify_custody_cache(self.fixture.lock,self.fixture.manifest,self.cache)
    def test_module_zip_traversal_rejected(self):
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as z:z.writestr(self.fixture.module+'@'+self.fixture.version+'/../escape',b'x')
        with self.assertRaises(validate_lock.InvalidLock):custody.zip_files(raw.getvalue(),self.fixture.module,self.fixture.version)
    def test_module_zip_duplicate_rejected(self):
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as z:
            for _ in range(2):z.writestr(self.fixture.module+'@'+self.fixture.version+'/go.mod',b'x')
        with self.assertRaises(validate_lock.InvalidLock):custody.zip_files(raw.getvalue(),self.fixture.module,self.fixture.version)

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        commands=self.root/'_runner_file_commands';commands.mkdir()
        self.envfile=commands/('set_env_'+str(uuid.uuid4()));self.envfile.touch()
        self.environment=mock.patch.dict(os.environ,{'RUNNER_TEMP':str(self.root),'GITHUB_WORKSPACE':str(self.root),'GITHUB_RUN_ID':'1','GITHUB_RUN_ATTEMPT':'1','GITHUB_ENV':str(self.envfile),'MODULE_CUSTODY_TOKEN':'synthetic-token-not-a-real-credential'},clear=True);self.environment.start()
        self.args=argparse.Namespace(state=self.root/'module-custody/state.json')
    def tearDown(self):self.environment.stop();self.temp.cleanup()
    def test_early_state_failure_revokes_token(self):
        calls=[]
        class Response:
            status=204
            def __enter__(self):return self
            def __exit__(self,*args):return False
        class Opener:
            def open(self,request,timeout):
                calls.append((request.get_method(),request.full_url))
                if request.get_method()=='DELETE':return Response()
                raise urllib.error.HTTPError(request.full_url,401,'denied',{},None)
        with mock.patch.object(custody,'acquire',side_effect=validate_lock.InvalidLock('state_bad')),mock.patch.object(custody.urllib.request,'build_opener',return_value=Opener()):
            with self.assertRaises(validate_lock.InvalidLock):custody.prepare(self.args)
        self.assertEqual([x[0] for x in calls],['DELETE','GET'])
        self.assertTrue(json.loads((self.root/'module-custody-early-failure.json').read_text())['token_revoked'])
        self.assertTrue(all(not os.environ.get(k) for k in custody.AUTH_ENV))
    def test_failed_revocation_stays_failure(self):
        opener=mock.Mock();opener.open.side_effect=TimeoutError()
        with mock.patch.object(custody,'acquire',side_effect=validate_lock.InvalidLock('early_failure')),mock.patch.object(custody.urllib.request,'build_opener',return_value=opener):
            with self.assertRaises(validate_lock.InvalidLock):custody.prepare(self.args)
        self.assertFalse(json.loads((self.root/'module-custody-early-failure.json').read_text())['token_revoked'])
        self.assertNotIn('MODULE_CUSTODY_TOKEN',os.environ)
    def test_clear_auth_includes_signing_key_ssh_and_pat(self):
        os.environ.update({k:'synthetic' for k in custody.AUTH_ENV});custody.clear_auth()
        self.assertTrue(all(k not in os.environ for k in custody.AUTH_ENV))
        self.assertEqual(set(self.envfile.read_text().splitlines()),{k+'=' for k in custody.AUTH_ENV})
    def test_multiline_private_key_old_record_scrubbed(self):
        old=self.envfile.parent/('set_env_'+str(uuid.uuid4()))
        unrelated=b'PLATFORM_ARTIFACT=synthetic-platform-value\r\nOTHER<<KEEP\nAPP_PRIVATE_KEY=literal-non-assignment\nKEEP\n'
        old.write_bytes(b'APP_PRIVATE_KEY<<SECRET\n-----BEGIN PRIVATE KEY-----\nsynthetic-only\n-----END PRIVATE KEY-----\nSECRET\nGH_GPA_TOKEN=synthetic-pat\n'+unrelated)
        result=custody.clear_auth()
        self.assertEqual(old.read_bytes(),b'APP_PRIVATE_KEY=\nGH_GPA_TOKEN=\n'+unrelated)
        self.assertEqual(result['nonempty_source_records_removed'],2)
        self.assertTrue(result['unrelated_records_preserved'])
    def test_cleanup_preserves_other_file_command_types(self):
        other=self.envfile.parent/('save_state_'+str(uuid.uuid4()));other.write_text('token=synthetic-revoked-token\n')
        custody.clear_auth();self.assertEqual(other.read_text(),'token=synthetic-revoked-token\n')
    def test_linux_cr_inside_unrelated_heredoc_is_preserved(self):
        raw=b'OTHER<<A=B\nA=B\r\nAPP_PRIVATE_KEY=literal-body-not-assignment\nA=B\n'
        cleaned,removed=custody.scrub_env_bytes(raw)
        self.assertEqual(cleaned,raw);self.assertEqual(removed,0)
    def test_environment_file_symlink_rejected(self):
        outside=self.root/'outside';outside.write_text('APP_PRIVATE_KEY=synthetic\n')
        link=self.envfile.parent/('set_env_'+str(uuid.uuid4()));link.symlink_to(outside)
        with self.assertRaises(OSError):custody.clear_auth()
        self.assertEqual(outside.read_text(),'APP_PRIVATE_KEY=synthetic\n')
    def test_unterminated_source_record_fails_without_raw_error(self):
        self.envfile.write_text('APP_PRIVATE_KEY<<NEVER\nsynthetic-secret\n')
        with self.assertRaisesRegex(validate_lock.InvalidLock,'source_env_record_unterminated'):custody.clear_auth()
    def test_git_wrapper_path_does_not_break_inventory(self):
        import shutil
        import subprocess
        real_git=shutil.which('git',path='/usr/bin:/bin');self.assertIsNotNone(real_git)
        source=self.root/'source';source.mkdir();(source/'go.mod').write_text('module example.invalid/test\n');(source/'lock.json').write_text('{}')
        subprocess.run([real_git,'init','-q'],cwd=source,check=True,env=custody.clean_environment())
        subprocess.run([real_git,'add','.'],cwd=source,check=True,env=custody.clean_environment())
        wrapper=self.root/'bin';wrapper.mkdir();shutil.copyfile(custody.HERE/'git_guard.py',wrapper/'git');(wrapper/'git').chmod(0o700)
        os.environ.update(PATH=str(wrapper)+':/usr/bin:/bin',MODULE_CUSTODY_REAL_GIT=real_git,MODULE_CUSTODY_BLOCKED_OWNERS='["example-upstream"]')
        self.assertEqual(custody.consumer_inventory(source,'lock.json'),{'go.mod':custody.sha(source/'go.mod')})
    def test_integrity_failure_cleanup_is_bounded(self):
        self.args.state.parent.mkdir();self.args.state.write_bytes(encode({'phase':'prepared','schema_version':1,'schema_sha256':custody.sha(custody.HERE/'lock.schema.json'),'run_id':'1','attempt':'1','workspace':str(self.root)}))
        (self.args.state.parent/'temporary').write_text('owned');outside=self.root/'keep';outside.write_text('preserved')
        with mock.patch.object(custody,'guard',side_effect=validate_lock.InvalidLock('changed')):
            with self.assertRaises(validate_lock.InvalidLock):custody.cleanup(self.args)
        self.assertFalse(self.args.state.parent.exists());self.assertEqual(outside.read_text(),'preserved')
        self.assertFalse(json.loads((self.root/'module-custody-cleanup.json').read_text())['pass'])
    def test_cleanup_rejects_outside_path(self):
        bad=argparse.Namespace(state=self.root/'unowned/state.json')
        with self.assertRaises(validate_lock.InvalidLock):custody.cleanup(bad)
    def test_preexisting_unowned_directory_preserved(self):
        self.args.state.parent.mkdir();marker=self.args.state.parent/'keep';marker.write_text('unowned')
        with self.assertRaises(validate_lock.InvalidLock):custody.cleanup(self.args)
        self.assertEqual(marker.read_text(),'unowned')
    def test_previous_run_directory_preserved(self):
        self.args.state.parent.mkdir();self.args.state.write_bytes(encode({'phase':'prepared','schema_version':1,'schema_sha256':custody.sha(custody.HERE/'lock.schema.json'),'run_id':'old','attempt':'1','workspace':str(self.root)}))
        with self.assertRaises(validate_lock.InvalidLock):custody.cleanup(self.args)
        self.assertTrue(self.args.state.exists())

if __name__=='__main__':unittest.main()

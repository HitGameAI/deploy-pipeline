"""Synthetic failure diagnostics only: no network, credentials, or library code."""
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock
import urllib.parse

sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/module-custody'))
import custody

MODULE='github.com/example-org/example-module'
VERSION='v0.1.0'
REQUESTS=[MODULE+'@'+VERSION]


class DownloadErrors(unittest.TestCase):
    def diagnose(self, rows, env=None):
        raw='\n'.join(json.dumps(x) for x in rows).encode()
        with mock.patch.dict(os.environ,{},clear=True):
            return custody.failed_download_evidence(raw,REQUESTS,env or {})

    def test_known_request_preserves_error_identity_and_hash(self):
        error='reading version: 404 Not Found'
        result=self.diagnose([{'Path':MODULE,'Version':VERSION,'Error':error,'Extra':'never serialized'}])
        self.assertEqual(result['parse_status'],'complete')
        self.assertEqual(result['errors'],[{'Path':MODULE,'Version':VERSION,'Error':error,
                          'error_sha256':hashlib.sha256(error.encode()).hexdigest(),
                          'sanitized_error_sha256':hashlib.sha256(error.encode()).hexdigest(),
                          'error_class':'not_found_or_access','error_truncated':False}])
        self.assertNotIn('never serialized',json.dumps(result))

    def test_all_available_credentials_and_encoded_forms_removed(self):
        token='synthetic-source-credential-A7qZ'
        key='-----BEGIN PRIVATE KEY-----\nSYNTHETIC_KEY_BODY_8832\n-----END PRIVATE KEY-----'
        nested='synthetic-nested-password-4zY'
        encoded=[token,urllib.parse.quote(token,safe=''),base64.b64encode(token.encode()).decode(),
                 base64.b64encode(('x-access-token:'+token).encode()).decode(),key,'SYNTHETIC_KEY_BODY_8832',nested]
        error='failed: '+ ' | '.join(encoded)
        with mock.patch.dict(os.environ,{'APP_PRIVATE_KEY':key,'CONFIG_SECRETS_JSON':json.dumps({'nested':{'password':nested}})},clear=True):
            result=custody.failed_download_evidence(json.dumps({'Path':MODULE,'Version':VERSION,'Error':error}).encode(),REQUESTS,{'MODULE_CUSTODY_TOKEN':token})
        safe=result['errors'][0]['Error']
        self.assertTrue(all(value not in safe for value in encoded))
        self.assertIn('[REDACTED]',safe)

    def test_url_userinfo_token_patterns_and_authorization_removed(self):
        secrets=['url-user:opaque-url-password','ghs_'+'x'*36,'github_pat_'+'z'*44,'opaque-bearer-value','opaque-query-password']
        error='https://'+secrets[0]+'@github.com/example-org/example-module '+secrets[1]+' '+secrets[2]+' Authorization: Bearer '+secrets[3]+' password='+secrets[4]
        result=self.diagnose([{'Path':MODULE,'Version':VERSION,'Error':error}])
        self.assertTrue(all(value not in result['errors'][0]['Error'] for value in secrets))
        self.assertIn('https://[REDACTED]@github.com/',result['errors'][0]['Error'])

    def test_unknown_module_and_wrong_version_never_emit_fields(self):
        result=self.diagnose([{'Path':'github.com/unknown/secret-path','Version':VERSION,'Error':'DO_NOT_COPY_UNKNOWN'},
                              {'Path':MODULE,'Version':'v9.9.9','Error':'DO_NOT_COPY_VERSION'}])
        self.assertEqual(result['errors'],[])
        self.assertEqual(result['ignored_unknown_module_rows'],2)
        self.assertNotIn('DO_NOT_COPY',json.dumps(result))
        self.assertNotIn('secret-path',json.dumps(result))

    def test_malformed_or_duplicate_json_does_not_escape_raw_output(self):
        for raw in [b'{not-json DO_NOT_COPY_RAW',b'{"Path":"DO_NOT_COPY_RAW","Path":"duplicate"}']:
            with mock.patch.dict(os.environ,{},clear=True):
                result=custody.failed_download_evidence(raw,REQUESTS,{})
            self.assertEqual(result['parse_status'],'unparseable_json_or_redaction_failure')
            self.assertNotIn('DO_NOT_COPY_RAW',json.dumps(result))

    def test_redaction_precedes_error_truncation(self):
        token='synthetic-boundary-token-'+'t'*300
        result=self.diagnose([{'Path':MODULE,'Version':VERSION,'Error':'x'*2000+token+'tail'*100}],{'MODULE_CUSTODY_TOKEN':token})
        row=result['errors'][0]
        self.assertTrue(row['error_truncated'])
        self.assertLessEqual(len(row['Error'].encode()),2060)
        self.assertNotIn('synthetic-boundary',row['Error'])

    def nested_run(self, returncode, stdout):
        tree=ast.parse(Path(custody.__file__).read_text())
        acquire=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='acquire')
        function=next(n for n in acquire.body if isinstance(n,ast.FunctionDef) and n.name=='run')
        module=ast.Module(body=[function],type_ignores=[])
        fake=mock.Mock(return_value=types.SimpleNamespace(returncode=returncode,stdout=stdout,stderr=b''))
        namespace=dict(custody.__dict__,subprocess=types.SimpleNamespace(run=fake,DEVNULL=-3),commands=[],evidence={})
        exec(compile(module,'bound nested run','exec'),namespace)
        return namespace,fake

    def test_success_run_unchanged_no_failure_field(self):
        namespace,fake=self.nested_run(0,b'{"Path":"example"}')
        argv=['/fixed/go','mod','download','-json',*REQUESTS];env={'A':'B'}
        self.assertEqual(namespace['run'](argv,'/work',env),'\x7b"Path":"example"}')
        self.assertEqual(namespace['evidence'],{})
        fake.assert_called_once_with(argv,cwd='/work',env=env,capture_output=True,stdin=-3,timeout=1200)

    def test_failed_download_adds_diagnostics_and_keeps_original_error(self):
        raw=json.dumps({'Path':MODULE,'Version':VERSION,'Error':'unknown revision v0.1.0'}).encode()
        namespace,_=self.nested_run(1,raw)
        with mock.patch.dict(os.environ,{},clear=True),self.assertRaisesRegex(custody.InvalidLock,'^go_or_git_command_failed$'):
            namespace['run'](['/fixed/go','mod','download','-json',*REQUESTS],'/work',{})
        evidence=namespace['evidence']['failed_module_downloads']
        self.assertEqual(len(evidence),1)
        self.assertEqual(evidence[0]['errors'][0]['error_class'],'version_not_found')
        self.assertEqual(namespace['commands'][0]['exit_code'],1)

    def test_other_failed_commands_unchanged(self):
        namespace,_=self.nested_run(1,b'not copied')
        with self.assertRaisesRegex(custody.InvalidLock,'^go_or_git_command_failed$'):
            namespace['run'](['/fixed/go','mod','verify'],'/work',{})
        self.assertEqual(namespace['evidence'],{})


if __name__=='__main__':unittest.main()

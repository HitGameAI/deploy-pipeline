"""Exercise real Go file-proxy acquisition against a synthetic GitHub API double."""
import argparse
import base64
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest import mock
import urllib.error
import uuid

from test_module_custody import Fixture, encode, sha, custody

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--go',type=Path,required=True);args=parser.parse_args()
    go=args.go.resolve(strict=True)
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary).resolve();source=root/'source';source.mkdir();runner=root/'runner';runner.mkdir()
        fixture=Fixture(root/'upstream')
        files={'go.mod':('module github.com/example-org/example-service\n\ngo 1.25.1\n\nrequire '+fixture.module+' '+fixture.version+'\n').encode(),
               'go.sum':(fixture.module+' '+fixture.version+' '+fixture.sum+'\n'+fixture.module+' '+fixture.version+'/go.mod '+fixture.modsum+'\n').encode(),
               'main.go':b'package main\nfunc main() {}\n'}
        for name,data in files.items():(source/name).write_bytes(data)
        fixture.lock['consumer']['files']={name:sha(data) for name,data in files.items()}
        lockpath=source/'.ci/module-custody.json';lockpath.parent.mkdir();lockpath.write_bytes(encode(fixture.lock))
        local_env={'PATH':'/usr/bin:/bin','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1','GIT_AUTHOR_NAME':'Synthetic','GIT_AUTHOR_EMAIL':'synthetic@example.invalid','GIT_COMMITTER_NAME':'Synthetic','GIT_COMMITTER_EMAIL':'synthetic@example.invalid'}
        for command in [['git','init','-q'],['git','add','.'],['git','-c','core.hooksPath=/dev/null','commit','-qm','Synthetic fixture']]:
            subprocess.run(command,cwd=source,env=local_env,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        commanddir=runner/'_runner_file_commands';commanddir.mkdir()
        envfile=commanddir/('set_env_'+str(uuid.uuid4()));output=root/'output';pathfile=root/'path'
        for path in [envfile,output,pathfile]:path.touch()
        environment={'PATH':str(go.parent)+':/usr/bin:/bin','RUNNER_TEMP':str(runner),'GITHUB_WORKSPACE':str(source),
                     'GITHUB_REPOSITORY':'example-org/example-service','GITHUB_REPOSITORY_ID':'3',
                     'GITHUB_RUN_ID':'1','GITHUB_RUN_ATTEMPT':'1','GITHUB_ENV':str(envfile),'GITHUB_OUTPUT':str(output),'GITHUB_PATH':str(pathfile),
                     'GO_AUTH_METHOD':'app-token','APP_ID':'1','GOPROXY':'off',
                     'MODULE_CUSTODY_TOKEN':'synthetic-token-not-a-real-credential',
                     'MODULE_CUSTODY_INSTALLATION_ID':'2','MODULE_CUSTODY_APP_SLUG':'example-app'}
        requests=[]
        class Response(io.BytesIO):
            def __init__(self,value=None,status=200):super().__init__(b'' if value is None else json.dumps(value).encode());self.status=status
        class Opener:
            def open(self,request,timeout):
                method=request.get_method();url=request.full_url;requests.append((method,url))
                assert url.startswith('https://api.github.com/'),url
                if method=='DELETE':assert url.endswith('/installation/token');return Response(status=204)
                if url.endswith('/installation/repositories?per_page=1'):raise urllib.error.HTTPError(url,401,'expected',{},io.BytesIO())
                if '/installation/repositories?' in url:return Response({'repositories':[{'full_name':'example-org/example-custody','id':4}]})
                if url.endswith('/repos/example-org/example-custody'):return Response({'id':4})
                if '/git/commits/' in url:return Response({'sha':'2'*40,'tree':{'sha':'3'*40}})
                if '/git/trees/' in url:return Response({'truncated':False,'tree':[{'path':x['path'],'type':'blob','mode':x['mode'],'sha':x['git_blob_sha']} for x in fixture.lock['custody']['blobs']]})
                if '/git/blobs/' in url:
                    blob=next(x for x in fixture.lock['custody']['blobs'] if url.endswith(x['git_blob_sha']))
                    return Response({'sha':blob['git_blob_sha'],'encoding':'base64','content':base64.b64encode(fixture.blobs[blob['path']]).decode()})
                raise AssertionError('Unexpected API request')
        task=argparse.Namespace(lock='.ci/module-custody.json',state=runner/'module-custody/state.json')
        with mock.patch.dict(os.environ,environment,clear=True),mock.patch.object(custody.urllib.request,'build_opener',return_value=Opener()):
            custody.validate(task)
            try:custody.prepare(task)
            except Exception:
                receipt=runner/'module-custody-acquisition.json'
                if receipt.exists():print(receipt.read_text())
                raise
            # Simulate the runner applying the environment file between steps.
            for line in envfile.read_text().splitlines():
                key,value=line.split('=',1);os.environ[key]=value
            os.environ['PATH']=pathfile.read_text().strip()+':'+os.environ['PATH']
            custody.guard(task)
            receipt=json.loads((runner/'module-custody-acquisition.json').read_text())
            assert receipt['pass'] and receipt['source_modules']==1 and receipt['logical_modules']==2
            custody.cleanup(task)
            assert json.loads((runner/'module-custody-cleanup.json').read_text())['pass']
            assert not task.state.parent.exists()
        assert [x[0] for x in requests[-2:]]==['DELETE','GET']
        print(json.dumps({'pass':True,'real_go_file_proxy':True,'external_network_requests':0,'synthetic_api_requests':len(requests),'source_code_executed':False,'go_version':subprocess.check_output([go,'version'],text=True).strip(),'guard_pass':True,'cleanup_pass':True}))

if __name__=='__main__':main()

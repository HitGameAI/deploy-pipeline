"""Opt-in, data-locked module custody. Executes Go tooling, never fetched code."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
import zipfile

from validate_lock import InvalidLock, load, relative, require

HERE = Path(__file__).resolve().parent
AUTH_ENV = ('APP_ID', 'APP_PRIVATE_KEY', 'GIT_SSH_PRIVATE_KEY', 'GH_GPA_TOKEN',
            'GH_TOKEN', 'GITHUB_TOKEN', 'MODULE_CUSTODY_TOKEN', 'MODULE_GITHUB_TOKEN',
            'CONFIG_SECRETS_JSON', 'PIPELINE_SECRETS', 'SSH_AUTH_SOCK')

def digest(data): return hashlib.sha256(data).hexdigest()
def sha(path): return digest(Path(path).read_bytes())
def read(path): return json.loads(Path(path).read_text())
def save(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2)+'\n')
def canonical(value): return digest(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())
def escaped(path): return ''.join('!'+c.lower() if c.isupper() else c for c in path)
def h1(items):
    raw=''.join(digest(data)+'  '+name+'\n' for name,data in sorted(items)).encode()
    return 'h1:'+base64.b64encode(hashlib.sha256(raw).digest()).decode()
def stream(raw):
    decoder=json.JSONDecoder();items=[]
    while raw.strip():
        item,end=decoder.raw_decode(raw.lstrip());items.append(item);raw=raw.lstrip()[end:]
    return items

def output(name, value):
    path=os.environ.get('GITHUB_OUTPUT')
    require(path, 'github_output_missing')
    boundary='CUSTODY_'+uuid.uuid4().hex
    with open(path,'a') as handle: handle.write(name+'<<'+boundary+'\n'+str(value)+'\n'+boundary+'\n')

def export(values):
    path=os.environ.get('GITHUB_ENV')
    require(path, 'github_environment_file_missing')
    with open(path,'a') as handle:
        for key,value in values.items():
            require('\n' not in str(value) and '\r' not in str(value), 'unsafe_environment_value')
            handle.write(key+'='+str(value)+'\n')

def scrub_env_bytes(raw):
    # Parse complete records, so key-like text inside an unrelated heredoc is
    # never mistaken for an assignment. Unrelated records stay byte-for-byte.
    # The hosted Linux runner splits on LF only: CR remains part of a delimiter
    # or value. Do not normalize CRLF and accidentally terminate another value.
    parts=raw.split(b'\n');lines=[part+b'\n' for part in parts[:-1]]
    if parts[-1]:lines.append(parts[-1])
    result=[];i=0;removed=0
    sensitive={name.encode() for name in AUTH_ENV}
    while i<len(lines):
        start=i;line=lines[i].removesuffix(b'\n');i+=1
        if not line:result.append(lines[start]);continue
        eq=line.find(b'=');heredoc=line.find(b'<<')
        if eq>=0 and (heredoc<0 or eq<heredoc):
            key=line[:eq];value=line[eq+1:]
        elif heredoc>=0:
            key=line[:heredoc];delimiter=line[heredoc+2:]
            require(key and delimiter,'source_env_record_invalid')
            value=[]
            while i<len(lines) and lines[i].removesuffix(b'\n')!=delimiter:
                value.append(lines[i]);i+=1
            require(i<len(lines),'source_env_record_unterminated')
            i+=1
        else:raise InvalidLock('source_env_record_invalid')
        if key in sensitive:
            result.append(key+b'=\n');removed+=bool(value)
        else:result.extend(lines[start:i])
    return b''.join(result),removed

def scrub_source_env_records():
    temp=Path(os.environ.get('RUNNER_TEMP','')).resolve(strict=True)
    current=Path(os.environ.get('GITHUB_ENV','')).absolute()
    directory=temp/'_runner_file_commands'
    pattern=r'set_env_[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
    require(current.parent==directory and re.fullmatch(pattern,current.name) and
            directory.is_dir() and not directory.is_symlink(),'source_env_directory_binding')
    paths=sorted(p for p in directory.iterdir() if re.fullmatch(pattern,p.name))
    require(current in paths and len(paths)<=512,'source_env_inventory')
    changed=records=0
    for path in paths:
        descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            before=os.fstat(descriptor)
            require(stat.S_ISREG(before.st_mode) and before.st_uid==os.getuid() and
                    before.st_nlink==1 and before.st_size<=16*1024*1024,'source_env_file_ownership')
            with os.fdopen(descriptor,'rb',closefd=False) as handle:raw=handle.read()
        finally:os.close(descriptor)
        cleaned,count=scrub_env_bytes(raw)
        if cleaned!=raw:
            latest=path.lstat()
            require((latest.st_ino,latest.st_size,latest.st_mtime_ns)==
                    (before.st_ino,before.st_size,before.st_mtime_ns),'source_env_concurrent_change')
            temporary=directory/('.custody-scrub-'+uuid.uuid4().hex)
            try:
                descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,stat.S_IMODE(before.st_mode))
                with os.fdopen(descriptor,'wb') as handle:
                    handle.write(cleaned);handle.flush();os.fsync(handle.fileno())
                os.replace(temporary,path)
            finally:temporary.unlink(missing_ok=True)
            require(scrub_env_bytes(path.read_bytes())[1]==0,'source_env_cleanup_incomplete')
            changed+=1;records+=count
    return {'pass':True,'files_examined':len(paths),'files_changed':changed,
            'nonempty_source_records_removed':records,'unrelated_records_preserved':True,
            'scope':'current_runner_file_commands_set_env_records',
            'platform_credentials_not_claimed_absent':True}

def clear_auth():
    try:
        return scrub_source_env_records()
    finally:
        try:export({key:'' for key in AUTH_ENV})
        finally:
            for key in AUTH_ENV:os.environ.pop(key,None)

def state_location(path):
    temp=Path(os.environ.get('RUNNER_TEMP','')).resolve(strict=True)
    require(str(temp) not in ('/',str(Path.cwd())), 'invalid_runner_temp')
    require(path.absolute()==temp/'module-custody/state.json' and not path.is_symlink(), 'state_location')
    if path.parent.exists(): require(not path.parent.is_symlink(), 'state_directory_symlink')
    return temp

def safe_file(root, name):
    rel=relative(name);path=root/rel
    require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root), 'required_regular_file')
    require(not any(x.is_symlink() for x in path.parents if x!=root and x.is_relative_to(root)), 'intermediate_symlink')
    return path

def clean_environment():
    return {'PATH':os.environ.get('PATH','/usr/bin:/bin'),'LANG':'C.UTF-8',
            'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_SYSTEM':'/dev/null','GIT_CONFIG_GLOBAL':'/dev/null',
            'GIT_CONFIG_COUNT':'0','GIT_TERMINAL_PROMPT':'0','GIT_ALLOW_PROTOCOL':'https',
            'GIT_NO_REPLACE_OBJECTS':'1','GOENV':'off','GOAUTH':'off','GOWORK':'off','GOTOOLCHAIN':'local'}

def trusted_git():
    git=os.environ.get('MODULE_CUSTODY_REAL_GIT') or shutil.which('git')
    require(git and Path(git).is_absolute() and Path(git).is_file(),'trusted_git_missing')
    return git

def execution_git_policy():
    return {'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_SYSTEM':'/dev/null','GIT_CONFIG_GLOBAL':'/dev/null',
            'GIT_TERMINAL_PROMPT':'0','GIT_ALLOW_PROTOCOL':'https','GIT_NO_REPLACE_OBJECTS':'1',
            'GIT_CONFIG_PARAMETERS':'',
            'GIT_CONFIG_COUNT':'3','GIT_CONFIG_KEY_0':'credential.helper','GIT_CONFIG_VALUE_0':'',
            'GIT_CONFIG_KEY_1':'core.hooksPath','GIT_CONFIG_VALUE_1':'/dev/null',
            'GIT_CONFIG_KEY_2':'core.fsmonitor','GIT_CONFIG_VALUE_2':'false'}

def consumer_inventory(workspace, lock_path):
    env=clean_environment()
    p=subprocess.run([trusted_git(),'-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false','ls-files','-z'],cwd=workspace,env=env,capture_output=True,timeout=30)
    require(p.returncode==0, 'tracked_source_inventory_failed')
    names=p.stdout.decode().split('\0');names=[x for x in names if x]
    require(len(names)==len(set(names)) and lock_path in names, 'tracked_lock_inventory')
    return {name:sha(safe_file(workspace,name)) for name in names if name!=lock_path}

def validate(args):
    state_location(args.state)
    workspace=Path(os.environ.get('GITHUB_WORKSPACE','')).resolve(strict=True)
    relative(args.lock)
    require(args.lock not in ('go.mod','go.sum'), 'invalid_lock_path')
    lock_path=safe_file(workspace,args.lock);lock=load(lock_path)
    require(args.lock not in lock['consumer']['files'], 'self_hash_forbidden')
    require(lock['consumer']['repository']==os.environ.get('GITHUB_REPOSITORY') and
            str(lock['consumer']['repository_id'])==os.environ.get('GITHUB_REPOSITORY_ID'), 'caller_identity')
    require(os.environ.get('GO_AUTH_METHOD')=='app-token', 'enabled_requires_app_token')
    require(str(lock['app']['id'])==os.environ.get('APP_ID'), 'source_app_identity')
    require(consumer_inventory(workspace,args.lock)==lock['consumer']['files'], 'consumer_source_drift')
    actual_head=subprocess.run([trusted_git(),'-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false','rev-parse','--verify','HEAD'],cwd=workspace,env=clean_environment(),capture_output=True,timeout=30)
    require(actual_head.returncode==0 and re.fullmatch('[0-9a-f]{40}',actual_head.stdout.decode().strip()),'consumer_checkout_commit')
    require(not args.state.parent.exists(), 'state_reuse_forbidden')
    args.state.parent.mkdir(mode=0o700)
    state={'schema_version':1,'phase':'validated','workspace':str(workspace),'lock_relative':args.lock,
           'lock_sha256':sha(lock_path),'schema_sha256':sha(HERE/'lock.schema.json'),
           'run_id':os.environ.get('GITHUB_RUN_ID'),'attempt':os.environ.get('GITHUB_RUN_ATTEMPT'),
           'checkout_commit':actual_head.stdout.decode().strip(),'source_credentials_removed':False,'token_revoked':False}
    save(args.state,state)
    output('owner',lock['consumer']['repository'].split('/')[0])
    output('repositories','\n'.join(sorted(x['repository'].split('/')[1] for x in lock['repositories'])))
    output('state',str(args.state))
    print('Module custody data lock validated')

def state_and_lock(path):
    state_location(path);state=read(path)
    require(state['schema_version']==1 and state['schema_sha256']==sha(HERE/'lock.schema.json'), 'state_schema_binding')
    require(state['run_id']==os.environ.get('GITHUB_RUN_ID') and state['attempt']==os.environ.get('GITHUB_RUN_ATTEMPT'), 'state_run_binding')
    workspace=Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
    require(str(workspace)==state['workspace'], 'state_workspace_binding')
    lock_path=safe_file(workspace,state['lock_relative'])
    require(sha(lock_path)==state['lock_sha256'], 'lock_changed')
    lock=load(lock_path)
    require(consumer_inventory(workspace,state['lock_relative'])==lock['consumer']['files'], 'consumer_source_drift')
    return state,lock,workspace

def zip_files(raw, path, version):
    files={};prefix=path+'@'+version+'/'
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        require(sum(x.file_size for x in archive.infolist())<=1024*1024*1024, 'module_archive_size')
        for row in archive.infolist():
            if row.is_dir(): continue
            require(row.filename.startswith(prefix), 'module_zip_prefix')
            name=row.filename[len(prefix):];relative(name)
            require(name not in files and ((row.external_attr>>16)&0o170000)!=0o120000, 'module_zip_duplicate_or_symlink')
            files[name]=archive.read(row)
    require(files, 'empty_module_zip')
    return files

def directory_digest(directory):
    require(directory.is_dir() and not directory.is_symlink(), 'module_directory_missing')
    files={}
    for path in directory.rglob('*'):
        require(not path.is_symlink(), 'module_directory_symlink')
        if path.is_file(): files[path.relative_to(directory).as_posix()]=sha(path)
    return canonical(files),len(files)

def custody_proxy(lock, root, proxy):
    spec=lock['custody'];manifest_path=safe_file(root,spec['manifest_path'])
    require(sha(manifest_path)==spec['manifest_sha256'], 'custody_manifest_hash')
    manifest=read(manifest_path)
    require(manifest['schema']=='original-adapter-custody-v1', 'unsupported_custody_manifest')
    expected={x['path']:x for x in spec['modules']}
    require(len(manifest['modules'])==len(expected) and {x['path'] for x in manifest['modules']}==set(expected), 'custody_module_identity')
    require(not proxy.exists(), 'proxy_reuse_forbidden')
    all_modules={x['path']:x for x in lock['modules']}
    prepared=[]
    for row in manifest['modules']:
        require(all(row[k]==expected[row['path']][k] for k in ['path','version','sum','go_mod_sum']), 'custody_historical_checksum')
        require(set(row['artifacts'])=={'info','mod','zip'}, 'custody_artifact_types')
        blobs={}
        for ext,record in row['artifacts'].items():
            file=safe_file(manifest_path.parent,record['path']);data=file.read_bytes()
            require(len(data)==record['bytes'] and digest(data)==record['sha256'], 'custody_artifact_hash')
            blobs[ext]=data
        require(json.loads(blobs['info'])['Version']==row['version'], 'custody_info_version')
        files=zip_files(blobs['zip'],row['path'],row['version'])
        fmap={name:digest(data) for name,data in files.items()}
        require(fmap==row['normalized_files'] and len(files)==row['file_count'], 'custody_complete_files')
        require(files['go.mod']==blobs['mod'] and h1([('go.mod',blobs['mod'])])==row['go_mod_sum'], 'custody_go_mod_h1')
        require(h1([(row['path']+'@'+row['version']+'/'+name,data) for name,data in files.items()])==row['sum'], 'custody_zip_h1')
        package=all_modules[row['path']]
        require(canonical(fmap)==package['files_digest'] and len(files)==package['file_count'] and digest(blobs['mod'])==package['go_mod_sha256'], 'custody_locked_package_content')
        prepared.append((row,blobs))
    for row,blobs in prepared:
        dest=proxy/escaped(row['path'])/'@v';dest.mkdir(parents=True)
        for ext,data in blobs.items():
            out=dest/(row['version']+'.'+ext);out.write_bytes(data);out.chmod(0o444)
    return manifest

def verify_custody_cache(lock, manifest, cache):
    expected={x['path']:x for x in lock['custody']['modules']}
    require(len(expected)==len(manifest['modules']), 'custody_cache_set')
    for row in manifest['modules']:
        require(all(row[k]==expected[row['path']][k] for k in ['path','version','sum','go_mod_sum']), 'custody_cache_identity')
        base=cache/'cache/download'/escaped(row['path'])/'@v'
        for ext,record in row['artifacts'].items():
            file=safe_file(base,row['version']+'.'+ext)
            require(file.stat().st_size==record['bytes'] and sha(file)==record['sha256'], 'custody_required_cache_artifact')
        require(safe_file(base,row['version']+'.ziphash').read_text()==row['sum'], 'custody_cached_h1')
        directory=cache/(escaped(row['path'])+'@'+row['version'])
        actual,count=directory_digest(directory)
        require(actual==canonical(row['normalized_files']) and count==len(row['normalized_files']), 'custody_unpacked_files')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): raise InvalidLock('api_redirect_forbidden')

def acquire(args):
    state,lock,workspace=state_and_lock(args.state)
    require(state['phase']=='validated', 'prepare_requires_fresh_validation')
    root=args.state.parent;token=os.environ.get('MODULE_CUSTODY_TOKEN','')
    require(len(token)>20, 'source_token_missing')
    require(str(lock['app']['installation_id'])==os.environ.get('MODULE_CUSTODY_INSTALLATION_ID') and
            lock['app']['slug']==os.environ.get('MODULE_CUSTODY_APP_SLUG'), 'installation_identity')
    helper=root/'credential-helper.sh';cache=root/'gomodcache';proxy=root/'file-proxy'
    evidence={'pass':False,'schema_version':1,'phase':'prepare','errors':[],'repository_mutations':0,'source_executed':False}
    api_events=[];commands=[]
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    repositories={x['repository']:x['id'] for x in lock['repositories']}
    def api(path,method='GET'):
        require((method=='DELETE' and path=='/installation/token') or (method=='GET' and (path.startswith('/installation/repositories?') or any(path=='/repos/'+r or path.startswith('/repos/'+r+'/git/') for r in repositories))), 'api_scope')
        request=urllib.request.Request('https://api.github.com'+path,method=method,headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json'})
        try:
            with opener.open(request,timeout=60) as response:
                code=response.status;data=None if code==204 else json.load(response)
        except urllib.error.HTTPError as error:
            code=error.code;data=None;error.close()
        api_events.append({'method':method,'path':path,'status':code})
        return code,data
    def get(path):
        code,data=api(path);require(code==200,'github_read_failed');return data
    def run(argv,cwd,env,expected=0):
        process=subprocess.run([str(x) for x in argv],cwd=cwd,env=env,capture_output=True,stdin=subprocess.DEVNULL,timeout=1200)
        commands.append({'program':Path(str(argv[0])).name,'operation':argv[1] if len(argv)>1 else '', 'exit_code':process.returncode,'stdout_sha256':digest(process.stdout),'stderr_sha256':digest(process.stderr)})
        require(process.returncode==expected,'go_or_git_command_failed')
        return process.stdout.decode()
    try:
        go=shutil.which('go');git=trusted_git();require(go and git,'required_tool_missing')
        env=clean_environment()
        require(run([go,'version'],root,env).strip().split()[:3]==['go','version',lock['go_version']], 'effective_go_version_mismatch')
        actual={}
        for page in range(1,11):
            data=get('/installation/repositories?per_page=100&page='+str(page))
            actual.update({x['full_name']:x['id'] for x in data['repositories']})
            if len(data['repositories'])<100:break
        else:raise InvalidLock('installation_inventory_truncated')
        require(actual==repositories, 'token_repository_set_mismatch')
        evidence['identity']={'expected_app_id':lock['app']['id'],
                              'installation_id':int(os.environ['MODULE_CUSTODY_INSTALLATION_ID']),
                              'app_slug':os.environ['MODULE_CUSTODY_APP_SLUG'],
                              'repositories':[{'repository':name,'id':value} for name,value in sorted(actual.items())],
                              'requested_permission':{'contents':'read'}}
        for name,repo_id in repositories.items():
            require(get('/repos/'+name)['id']==repo_id,'source_repository_id')
        spec=lock['custody'];commit=get('/repos/'+spec['repository']+'/git/commits/'+spec['commit'])
        require(commit['sha']==spec['commit'] and commit['tree']['sha']==spec['tree'],'custody_commit_tree')
        tree=get('/repos/'+spec['repository']+'/git/trees/'+spec['tree']+'?recursive=1')
        require(not tree.get('truncated'),'custody_tree_truncated')
        entries={x['path']:x for x in tree['tree']};bundle=root/'custody'
        for blob in spec['blobs']:
            entry=entries.get(blob['path'])
            require(entry and entry['type']=='blob' and entry['mode']==blob['mode'] and entry['sha']==blob['git_blob_sha'],'custody_blob_tree_binding')
            item=get('/repos/'+spec['repository']+'/git/blobs/'+entry['sha'])
            require(item['encoding']=='base64' and item['sha']==entry['sha'],'custody_blob_encoding')
            raw=base64.b64decode(item['content'])
            require(len(raw)==blob['bytes'] and digest(raw)==blob['sha256'] and hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==entry['sha'],'custody_blob_bytes')
            dest=bundle/relative(blob['path']);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
        manifest=custody_proxy(lock,bundle,proxy)
        evidence['custody']={key:spec[key] for key in ['repository','repository_id','commit','tree','manifest_path','manifest_sha256','blobs','modules']}
        owner=lock['consumer']['repository'].split('/')[0]
        blocked=sorted({x['path'].split('/')[1] for x in lock['modules'] if 'replacement' in x}|{x['path'].split('/')[1] for x in spec['modules']})
        require(owner.lower() not in {x.lower() for x in blocked},'organization_cannot_be_blocked')
        prefixes=['github.com/'+owner+'/*']+['github.com/'+x+'/*' for x in blocked]
        policy={'GOMODCACHE':str(cache),'GOCACHE':str(root/'build-cache'),'GOENV':'off','GOAUTH':'off','GOWORK':'off','GOTOOLCHAIN':'local',
                'GOPRIVATE':prefixes[0],'GONOPROXY':','.join(prefixes),'GONOSUMDB':','.join(prefixes),
                'GOVCS':','.join(x+':off' for x in prefixes[1:])+','+prefixes[0]+':git,public:git|hg,private:off'}|execution_git_policy()
        env.update(policy,GOSUMDB='sum.golang.org',GOPROXY=os.environ.get('GOPROXY') or 'https://proxy.golang.org,direct')
        offline=env|{'GOPROXY':proxy.as_uri(),'GONOPROXY':'none','GOSUMDB':'off','GOVCS':'*:off','GIT_ALLOW_PROTOCOL':'none'}
        require(not cache.exists(),'cache_reuse_forbidden')
        requests=[x['path']+'@'+x['version'] for x in spec['modules']]
        seeded=stream(run([go,'mod','download','-json']+requests,root,offline))
        require(len(seeded)==len(requests) and all(not x.get('Error') for x in seeded),'custody_file_only_seed')
        verify_custody_cache(lock,manifest,cache)
        guarddir=root/'bin';guarddir.mkdir();shutil.copyfile(HERE/'git_guard.py',guarddir/'git');(guarddir/'git').chmod(0o700)
        allowed='|'.join(name for repo in repositories for name in (repo,repo+'.git'))
        helper.write_text('#!/bin/sh\n[ "$1" = get ] || exit 0\nprotocol= host= path=\nwhile IFS="=" read -r key value; do\n case "$key" in protocol) protocol=$value;;host) host=$value;;path) path=$value;;esac\ndone\n[ "$protocol" = https ] && [ "$host" = github.com ] || exit 0\ncase "$path" in '+allowed+') printf "username=x-access-token\\npassword=%s\\n" "$MODULE_CUSTODY_TOKEN";;esac\n')
        helper.chmod(0o700)
        env.update(PATH=str(guarddir)+':'+str(Path(go).parent)+':/usr/bin:/bin',MODULE_CUSTODY_REAL_GIT=git,MODULE_CUSTODY_BLOCKED_OWNERS=json.dumps(blocked),MODULE_CUSTODY_TOKEN=token)
        settings=[('credential.helper',''),('credential.helper',str(helper)),('credential.useHttpPath','true'),('core.hooksPath','/dev/null'),('core.fsmonitor','false'),('http.sslVerify','true')]
        env['GIT_CONFIG_COUNT']=str(len(settings))
        for i,(key,value) in enumerate(settings):env['GIT_CONFIG_KEY_'+str(i)]=key;env['GIT_CONFIG_VALUE_'+str(i)]=value
        probe=root/'probe';probe.mkdir();(probe/'go.mod').write_text('module custody.invalid/source-probe\n\ngo '+lock['go_version'].removeprefix('go')+'\n')
        requests=[(x.get('replacement') or x)['path']+'@'+x['version'] for x in lock['modules']]
        downloads=stream(run([go,'mod','download','-json']+requests,probe,env))
        require(len(downloads)==len(requests) and all(not x.get('Error') for x in downloads),'complete_source_download')
        by_source={(x['Path'],x['Version']):x for x in downloads};require(len(by_source)==len(requests),'duplicate_source_download')
        cached=[];module_receipts=[]
        for row in lock['modules']:
            source=row.get('replacement') or row;data=by_source[(source['path'],row['version'])]
            require(data['Sum']==source['sum'] and data['GoModSum']==row['go_mod_sum'] and sha(data['GoMod'])==row['go_mod_sha256'],'module_checksums')
            if 'replacement' in row:
                origin=data.get('Origin',{})
                require(origin.get('VCS')=='git' and origin.get('URL')=='https://'+source['path'] and origin.get('Hash')==source['commit'],'replacement_origin')
            files=zip_files(Path(data['Zip']).read_bytes(),source['path'],row['version'])
            fmap={name:digest(value) for name,value in files.items()}
            require(canonical(fmap)==row['files_digest'] and len(files)==row['file_count'],'module_full_source_content')
            directory=cache/(escaped(source['path'])+'@'+row['version'])
            require(directory_digest(directory)==(row['files_digest'],row['file_count']),'module_unpacked_content')
            base=cache/'cache/download'/escaped(source['path'])/'@v'
            hashes={ext:sha(safe_file(base,row['version']+'.'+ext)) for ext in ['info','mod','zip','ziphash']}
            cached.append({'path':source['path'],'version':row['version'],'files_digest':row['files_digest'],'file_count':row['file_count'],'artifacts':hashes})
            receipt={'path':row['path'],'version':row['version'],'original_sum':row['sum'],
                     'source_path':data['Path'],'source_version':data['Version'],'sum':data['Sum'],
                     'go_mod_sum':data['GoModSum'],'go_mod_sha256':sha(data['GoMod']),
                     'files_digest':canonical(fmap),'file_count':len(files),'artifacts':hashes}
            if 'replacement' in row:receipt['origin']={key:origin[key] for key in ['VCS','URL','Hash']}
            module_receipts.append(receipt)
        graph=stream(run([go,'list','-mod=readonly','-m','-json','all'],workspace,env))
        expected={x['path']:x['version'] for x in lock['modules']}|{lock['consumer']['module']:''}
        require(len(graph)==len(expected) and {x['Path']:x.get('Version','') for x in graph}==expected,'selected_graph_drift')
        mappings={x['path']:x['replacement'] for x in lock['modules'] if 'replacement' in x}
        actual={x['Path']:x['Replace'] for x in graph if x.get('Replace')}
        require(set(actual)==set(mappings),'unexpected_module_replace')
        for name,row in mappings.items():require(actual[name]['Path']==row['path'] and actual[name].get('Version')==row['version'] and actual[name].get('Sum')==row['sum'],'replacement_graph_drift')
        evidence['modules']=module_receipts
        evidence['selected_graph']=[{'path':x['Path'],'version':x.get('Version','')} for x in graph]
        require(run([go,'mod','verify'],workspace,env).strip()=='all modules verified','go_module_verify')
        verify_custody_cache(lock,manifest,cache)
        require(consumer_inventory(workspace,state['lock_relative'])==lock['consumer']['files'],'consumer_source_drift')
        state.update(phase='prepared',policy=policy,cached_modules=cached,blocked_owners=blocked,real_git=git)
        evidence.update({'pass':True,'lock_sha256':state['lock_sha256'],'checkout_commit':state['checkout_commit'],'logical_modules':len(graph),'source_modules':len(downloads),'source_files':sum(x['file_count'] for x in lock['modules']),'organization_replacements':len(mappings),'custody_modules':len(spec['modules']),'custody_blob_count':len(spec['blobs']),'custody_h1_checks':2*len(spec['modules']),'complete_source_content_verified':True,'selected_path_versions_unchanged':True,'consumer_files_unchanged':True})
    except Exception as error:
        evidence['errors'].append(type(error).__name__+':'+(str(error) if isinstance(error,InvalidLock) else 'source_acquisition_failed'))
    finally:
        helper.unlink(missing_ok=True)
        try:
            revoked=api('/installation/token','DELETE')[0]==204
            revoked=revoked and api('/installation/repositories?per_page=1')[0]==401
        except Exception:revoked=False
        state['token_revoked']=revoked;state['source_credentials_removed']=False
        evidence.update(token_revoked=revoked,source_helper_removed=not helper.exists(),api_events=api_events,commands=commands)
        evidence['pass']=evidence['pass'] and revoked
        try:
            evidence['source_env_record_cleanup']=clear_auth()
            state['source_credentials_removed']=not helper.exists()
        except Exception:
            evidence['pass']=False;evidence['errors'].append('source_env_cleanup_failed')
        save(args.state,state);save(root.parent/'module-custody-acquisition.json',evidence)
    require(evidence['pass'],'source_acquisition_gate_failed')
    # Keep only the namespace denial wrapper: it has no credential material.
    export(state['policy']|{'MODULE_CUSTODY_REAL_GIT':state['real_git'],'MODULE_CUSTODY_BLOCKED_OWNERS':json.dumps(state['blocked_owners'])})
    require(os.environ.get('GITHUB_PATH'),'github_path_file_missing')
    with open(os.environ['GITHUB_PATH'],'a') as handle:handle.write(str(root/'bin')+'\n')
    print('Module custody acquisition and token revocation passed')

def prepare(args):
    # The normal acquisition finally revokes and clears its token. This outer
    # boundary also covers failures while reading state/lock or checking the
    # installation identity, before that inner finally has been entered.
    token=os.environ.get('MODULE_CUSTODY_TOKEN','')
    try:
        acquire(args)
    finally:
        if token and os.environ.get('MODULE_CUSTODY_TOKEN'):
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
            revoked=False
            try:
                request=urllib.request.Request('https://api.github.com/installation/token',method='DELETE',headers={'Authorization':'Bearer '+token})
                with opener.open(request,timeout=60) as response:revoked=response.status==204
                request=urllib.request.Request('https://api.github.com/installation/repositories?per_page=1',headers={'Authorization':'Bearer '+token})
                try:
                    with opener.open(request,timeout=60):revoked=False
                except urllib.error.HTTPError as error:
                    revoked=revoked and error.code==401;error.close()
            except Exception:revoked=False
            finally:
                try:cleanup=clear_auth()
                except Exception:cleanup={'pass':False}
            state_location(args.state)
            save(args.state.parent.parent/'module-custody-early-failure.json',{'pass':False,'source_token_revoke_attempted':True,'token_revoked':revoked,'source_auth_environment_cleared':not any(os.environ.get(k) for k in AUTH_ENV),'source_env_record_cleanup':cleanup})

def guard(args):
    state,lock,workspace=state_and_lock(args.state)
    require(state['phase']=='prepared' and state['token_revoked'] and state['source_credentials_removed'],'prepared_credential_gate')
    require(all(os.environ.get(k)==v for k,v in state['policy'].items()),'execution_source_policy_drift')
    root=args.state.parent;cache=root/'gomodcache'
    manifest_path=safe_file(root/'custody',lock['custody']['manifest_path'])
    require(sha(manifest_path)==lock['custody']['manifest_sha256'],'manifest_changed')
    verify_custody_cache(lock,read(manifest_path),cache)
    for row in state['cached_modules']:
        base=cache/'cache/download'/escaped(row['path'])/'@v'
        require(all(sha(safe_file(base,row['version']+'.'+ext))==value for ext,value in row['artifacts'].items()),'module_cache_artifact_changed')
        require(directory_digest(cache/(escaped(row['path'])+'@'+row['version']))==(row['files_digest'],row['file_count']),'module_cache_source_changed')
    require(not any(os.environ.get(key) for key in AUTH_ENV),'source_auth_environment_remains')
    print('Module custody cache and source guard passed')

def cleanup(args):
    state_location(args.state)
    result={'pass':True,'source_credentials_cleared':False,'task_cache_removed':False,'platform_credentials_not_claimed_absent':True}
    owned=False
    try:
        if args.state.parent.exists():
            require(args.state.is_file() and not args.state.is_symlink(),'cleanup_owner_state_missing')
            state=read(args.state)
            require(state.get('schema_version')==1 and state.get('schema_sha256')==sha(HERE/'lock.schema.json') and
                    state.get('run_id')==os.environ.get('GITHUB_RUN_ID') and
                    state.get('attempt')==os.environ.get('GITHUB_RUN_ATTEMPT') and
                    state.get('workspace')==str(Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)),
                    'cleanup_state_not_owned_by_this_run')
            owned=True
            if state.get('phase')=='prepared':guard(args)
            else:result['pass']=False
    except Exception as error:
        result.update({'pass':False,'error_class':type(error).__name__})
    finally:
        try:
            result['source_env_record_cleanup']=clear_auth();result['source_credentials_cleared']=True
        except Exception:
            result.update(error_class='SourceEnvironmentCleanupFailure')
            result['pass']=False
        if owned and args.state.parent.exists():
            # Go's extracted directory mode is read-only; change only the owned task subtree.
            for path in args.state.parent.rglob('*'):
                if path.is_dir() and not path.is_symlink():path.chmod(0o700)
            shutil.rmtree(args.state.parent)
        result['task_cache_removed']=not args.state.parent.exists()
        result['owned_task_directory']=owned
        save(args.state.parent.parent/'module-custody-cleanup.json',result)
    require(result['pass'],'module_custody_cleanup_integrity_gate')
    print('Module custody cleanup passed')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('operation',choices=['validate','prepare','guard','cleanup','clear-auth'])
    parser.add_argument('--lock');parser.add_argument('--state',type=Path)
    args=parser.parse_args()
    if args.operation=='clear-auth':clear_auth();return
    require(args.state is not None,'state_required')
    if args.operation=='validate':require(args.lock,'lock_required')
    globals()[args.operation](args)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Module custody failed: '+(str(error) if isinstance(error,InvalidLock) else type(error).__name__),file=sys.stderr)
        raise SystemExit(1)

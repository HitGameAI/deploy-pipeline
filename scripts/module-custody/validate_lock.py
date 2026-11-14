"""Small strict validator for the bundled, data-only lock schema (stdlib only)."""
import json
from pathlib import Path, PurePosixPath
import re

class InvalidLock(ValueError):
    pass

def require(ok, code):
    if not ok:
        raise InvalidLock(code)

def relative(value):
    path = PurePosixPath(value)
    require(bool(value) and not path.is_absolute() and str(path) == value and
            all(x not in ('', '.', '..') for x in value.split('/')) and '\\' not in value,
            'unsafe_relative_path')
    return path

def validate(value, schema):
    if 'const' in schema:
        require(type(value) is type(schema['const']) and value == schema['const'], 'constant_mismatch')
    kind = schema.get('type')
    types = {'object': dict, 'array': list, 'string': str, 'integer': int}
    if kind:
        require(type(value) is types[kind], 'invalid_field_type')
    if kind == 'object':
        require(set(schema.get('required', [])) <= set(value), 'required_field_missing')
        require(len(value) >= schema.get('minProperties', 0) and len(value) <= schema.get('maxProperties', 1000000), 'object_size')
        for key, item in value.items():
            if 'propertyNames' in schema:
                validate(key, schema['propertyNames'])
            if key in schema.get('properties', {}):
                validate(item, schema['properties'][key])
            else:
                additional = schema.get('additionalProperties', True)
                require(additional is not False, 'unknown_field')
                if isinstance(additional, dict): validate(item, additional)
    elif kind == 'array':
        require(schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 1000000), 'array_size')
        for item in value: validate(item, schema['items'])
    elif kind == 'string':
        require(schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 1000000), 'string_size')
        if 'pattern' in schema: require(re.fullmatch(schema['pattern'], value), 'string_pattern')
    elif kind == 'integer':
        require(value >= schema.get('minimum', 0), 'integer_range')

def load(path, schema_path=None):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 8*1024*1024, 'lock_file')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    value = json.loads(path.read_text(), object_pairs_hook=pairs)
    schema_path = schema_path or Path(__file__).with_name('lock.schema.json')
    validate(value, json.loads(schema_path.read_text()))
    repositories = {x['repository']: x['id'] for x in value['repositories']}
    require(len(repositories) == len(value['repositories']) == len(set(repositories.values())), 'duplicate_repository')
    owner = value['consumer']['repository'].split('/')[0]
    require(all(x.startswith(owner+'/') for x in repositories), 'cross_owner_source_repository')
    require(repositories.get(value['custody']['repository']) == value['custody']['repository_id'], 'custody_repository_not_allowed')
    require({'go.mod', 'go.sum'} <= set(value['consumer']['files']), 'missing_consumer_module_files')
    for name in value['consumer']['files']: relative(name)
    modules = {x['path']: x for x in value['modules']}
    require(len(modules) == len(value['modules']) and value['consumer']['module'] not in modules, 'duplicate_or_main_dependency')
    for row in modules.values():
        relative(row['path'])
        if 'replacement' in row:
            rep = row['replacement']
            relative(rep['path'])
            require(rep['version'] == row['version'] and rep['path'].startswith('github.com/'+owner+'/'), 'replacement_not_same_version_organization')
            require(rep['path'][len('github.com/'):] in repositories and rep['path'].count('/') == 2, 'replacement_repository_not_allowed')
            require(row['path'].startswith('github.com/') and row['path'].count('/') == 2, 'unsupported_upstream_namespace')
    custody = value['custody']
    relative(custody['manifest_path'])
    blobs = {x['path']: x for x in custody['blobs']}
    require(len(blobs) == len(custody['blobs']) and custody['manifest_path'] in blobs, 'custody_blob_inventory')
    require(blobs[custody['manifest_path']]['sha256'] == custody['manifest_sha256'], 'manifest_blob_binding')
    require(sum(x['bytes'] for x in blobs.values()) <= 256*1024*1024, 'custody_total_size')
    for name in blobs: relative(name)
    seen = set()
    for row in custody['modules']:
        require(row['path'] not in seen and row['path'] in modules and 'replacement' not in modules[row['path']], 'custody_module_set')
        require(row['path'].startswith('github.com/') and row['path'].count('/') == 2, 'custody_namespace')
        require(all(row[k] == modules[row['path']][k] for k in ['path', 'version', 'sum', 'go_mod_sum']), 'custody_module_binding')
        seen.add(row['path'])
    return value

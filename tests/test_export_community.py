"""Public exports preserve working features, exclude runtime state and redact reports."""
from pathlib import Path
import json
import hashlib
import pytest
from scripts.export_community import export_community, main, is_excluded, scan_export

def write(root, name, text):
    path=root/name
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(text,encoding='utf-8')

def fixture(tmp_path):
    source=tmp_path/'source'
    write(source,'LICENSE','MIT License\n')
    write(source,'LICENSE.md','MIT License\n')
    write(source,'README.md','RETINUE MIT public README\n')
    write(source,'README.community.md','old fallback must not override\n')
    write(source,'server/kingdom.py','value = 1\n')
    write(source,'server/kingdom_import.py','value = 2\n')
    write(source,'webui/src/pages/KingdomHub.tsx','export const Hub = 1;\n')
    write(source,'webui/src/pages/KingdomOperationsPage.tsx','export const Ops = 1;\n')
    write(source,'webui/src/App.tsx','import Hub from "./pages/KingdomHub";\n')
    write(source,'retinue-data/state.sqlite','private database fixture\n')
    write(source,'.integration/config.json','private configuration fixture\n')
    write(source,'docs/evidence/note.md','private audit note\n')
    return source

def test_export_preserves_modules_metadata_and_existing_readme(tmp_path):
    source=fixture(tmp_path)
    dest=tmp_path/'public'
    report=tmp_path/'scan.json'
    result=export_community(source,dest,report)
    assert result['scan'] == 'clean' and result['features_preserved']
    for name in ('LICENSE','LICENSE.md','server/kingdom.py','server/kingdom_import.py',
        'webui/src/pages/KingdomHub.tsx','webui/src/pages/KingdomOperationsPage.tsx','webui/src/App.tsx'):
        assert (dest/name).read_bytes() == (source/name).read_bytes()
    assert (dest/'README.md').read_bytes() == (source/'README.md').read_bytes()
    assert not (dest/'.git').exists()
    assert not (dest/'retinue-data').exists()
    assert not (dest/'.integration').exists()
    assert not (dest/'docs/evidence').exists()
    assert report.is_file() and dest not in report.parents

def test_export_is_idempotent_without_history_changes(tmp_path):
    source=fixture(tmp_path)
    dest=tmp_path/'public'
    report=tmp_path/'scan.json'
    argv=['--source',str(source),'--out',str(dest),'--report',str(report)]
    assert main(argv) == 0
    assert main(argv) == 0
    assert not (dest/'.git').exists()

def test_dirty_report_exposes_no_identifier_values_and_preserves_old_export(tmp_path):
    source=fixture(tmp_path)
    dest=tmp_path/'public'
    report=tmp_path/'scan.json'
    assert export_community(source,dest,report)['scan'] == 'clean'
    identifier='someone'+'@'+'example.invalid'
    write(source,'leak.md',identifier)
    result=export_community(source,dest,report)
    assert result['scan'] == 'needs_review'
    assert result['identifier_hits'] == 1
    assert identifier not in report.read_text(encoding='utf-8')
    assert not (dest/'leak.md').exists()

@pytest.mark.parametrize('name',['.integration/config.json','retinue.db','capture.jsonl',
    'private.key','private.token','.env','core.egg-info/PKG-INFO','node_modules/module.js',
    'STATE.DB','SECRET.PEM','PRIVATE.PFX','.ENV','.Integration/config.json'])
def test_private_artifacts_are_excluded(name):
    assert is_excluded(name)

def test_mit_source_metadata_is_consistent():
    root=Path(__file__).resolve().parents[1]
    assert (root/'LICENSE').read_bytes() == (root/'LICENSE.md').read_bytes()
    assert 'MIT License' in (root/'LICENSE').read_text(encoding='utf-8')
    assert 'Copyright (c) 2026 JKL Thinking' in (root/'LICENSE').read_text(encoding='utf-8')
    assert 'license = {text = "MIT"}' in (root/'pyproject.toml').read_text(encoding='utf-8')
    assert json.loads((root/'webui/package.json').read_text())['license'] == 'MIT'
    assert json.loads((root/'webui/package-lock.json').read_text())['packages']['']['license'] == 'MIT'

def test_loopback_does_not_hide_another_address_on_same_line(tmp_path):
    write(tmp_path,'source.txt','localhost 127.0.0.1 and '+'8.8.'+'4.4')
    identifiers,_=scan_export(tmp_path)
    assert identifiers == ['source.txt:1:address']

def test_documentation_cidr_does_not_hide_another_address_or_path(tmp_path):
    write(tmp_path,'source.txt','10.0.0.0/8 then '+'10.2.'+'3.4'+' and /'+'root'+'/secret')
    identifiers,_=scan_export(tmp_path)
    assert identifiers == ['source.txt:1:address','source.txt:1:machine-path']

def test_each_match_is_scanned_even_next_to_documentation_addresses(tmp_path):
    write(tmp_path,'source.txt','192.0.2.3 and '+'10.2.'+'3.4'+' and '+'172.20.'+'1.4')
    identifiers,_=scan_export(tmp_path)
    assert identifiers == ['source.txt:1:address','source.txt:1:address']

def test_fixed_documentation_addresses_and_networks_are_safe(tmp_path):
    write(tmp_path,'source.txt','127.0.0.1 0.0.0.0 192.0.2.3 198.51.100.7 203.0.113.9 10.0.0.0/8')
    assert scan_export(tmp_path) == ([],[])

def test_windows_paths_are_reported_without_values(tmp_path):
    windows='C:'+chr(92)+'Users'+chr(92)+'example'
    write(tmp_path,'source.txt',windows)
    identifiers,_=scan_export(tmp_path)
    assert identifiers == ['source.txt:1:machine-path']
    assert windows not in identifiers[0]

@pytest.mark.parametrize('prefix',['sk-'+'proj-','sk-'+'ant-api03-','github'+'_pat_'])
def test_provider_specific_tokens_are_reported(tmp_path,prefix):
    write(tmp_path,'source.txt',prefix+'a'*40)
    _,credentials=scan_export(tmp_path)
    assert credentials == ['source.txt:1:credential']

def test_jwt_and_quoted_keys_are_candidates_even_in_test_files(tmp_path):
    jwt='eyJ'+'a'*12+'.'+'b'*16+'.'+'c'*24
    write(tmp_path,'tests/fixture.txt',jwt+'\n'+json.dumps({'access_token':'synthetic-'+('x'*20)}))
    _,credentials=scan_export(tmp_path)
    assert credentials == ['tests/fixture.txt:1:credential','tests/fixture.txt:2:credential']

def test_synthetic_password_needs_review_and_does_not_replace_old_output(tmp_path):
    source=fixture(tmp_path)
    dest=tmp_path/'public'
    report=tmp_path/'scan.json'
    assert export_community(source,dest,report)['scan'] == 'clean'
    write(source,'tests/new_fixture.txt',json.dumps({'password':'synthetic-'+('p'*20)}))
    result=export_community(source,dest,report)
    assert result['scan'] == 'needs_review'
    assert result['credential_hits'] == 1
    assert not (dest/'tests/new_fixture.txt').exists()

def approved_fixture(root,line,category,matched):
    write(root,'scripts/public_scan_allowlist.json',json.dumps({'version':1,'entries':[{
        'file':'tests/fixture.txt','category':category,
        'line_sha256':hashlib.sha256(line.encode()).hexdigest(),
        'match_sha256':hashlib.sha256(matched.encode()).hexdigest(),
        'reason':'negative-test','review_note':'Independently reviewed generated synthetic negative fixture.'}]}))

def test_reviewed_exact_fixture_matches_only_same_file_line_and_candidate(tmp_path):
    line=json.dumps({'password':'synthetic-'+('s'*20)})
    from scripts.export_community import CREDENTIAL_RE
    matched=CREDENTIAL_RE.search(line).group(0)
    write(tmp_path,'tests/fixture.txt',line)
    approved_fixture(tmp_path,line,'credential',matched)
    assert scan_export(tmp_path) == ([],[])
    write(tmp_path,'tests/fixture.txt',line+' '+json.dumps({'access_token':'unreviewed-'+('s'*20)}))
    assert len(scan_export(tmp_path)[1]) == 2
    write(tmp_path,'tests/fixture.txt',line)
    write(tmp_path,'tests/another.txt',line)
    assert scan_export(tmp_path)[1] == ['tests/another.txt:1:credential']

def test_invalid_or_broad_allowlist_fails_closed(tmp_path):
    line=json.dumps({'password':'synthetic-'+('s'*20)})
    write(tmp_path,'tests/fixture.txt',line)
    write(tmp_path,'scripts/public_scan_allowlist.json',json.dumps({'version':1,'entries':[
        {'file':'tests/*','reason':'all-tests','category':'credential'}]}))
    assert scan_export(tmp_path)[1] == ['tests/fixture.txt:1:credential']

def test_shared_read_only_scan_does_not_write_export(tmp_path):
    source=fixture(tmp_path)
    dest=tmp_path/'public'
    assert main(['--scan-only','--source',str(source),'--out',str(dest)]) == 0
    assert not dest.exists()

def test_atomic_export_retries_one_transient_directory_handle(tmp_path,monkeypatch):
    source=fixture(tmp_path)
    dest=tmp_path/'public'
    report=tmp_path/'scan.json'
    original=Path.rename
    denied=[]
    def rename(path,target):
        if path.name.startswith('.public-export-') and not denied:
            denied.append(True)
            raise PermissionError('synthetic transient file handle')
        return original(path,target)
    monkeypatch.setattr(Path,'rename',rename)
    assert export_community(source,dest,report)['scan'] == 'clean'
    assert len(denied) == 1
    assert (dest/'README.md').is_file()

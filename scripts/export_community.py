#!/usr/bin/env python3
"""Export a clean, feature-complete public source snapshot.

Private runtime state, credentials, build dependencies and internal evidence
are excluded. Generic modules, routes, environment names and product themes
remain intact: an export never stubs or removes working business features.
Findings contain only relative paths, line numbers and categories, not values.
The destination is atomically replaced; reports live outside the export.
No Git history is created or rewritten by this utility.
"""
from __future__ import annotations
import argparse
import hashlib
import ipaddress
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIR_NAMES = frozenset({'.git','.venv','.integration','__pycache__',
    '.pytest_cache','.ruff_cache','node_modules','dist','build','retinue-data',
    'retinue-server-data'})
EXTRA_EXCLUDE = frozenset({'scripts/install-server.sh',
    'scripts/backfill_data_governance.py','docs/examples/kingdom-context.md',
    'docs/examples/kingdom-collaboration-contract-v1.md'})
PREFIX_EXCLUDE = ('server/static/','docs/evidence/','docs/design/audit-')
IPV4_RE = re.compile(r'(?<![\w.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w.])')
PATH_RE = re.compile(r'/(?:root|home|Users)/|(?<![A-Za-z0-9])[A-Za-z]:[\\/]|\\\\[^\\\s]+\\')
EMAIL_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
DOCUMENTATION_NETWORKS = frozenset({'10.0.0.0/8','100.64.0.0/10','127.0.0.0/8',
    '169.254.0.0/16','172.16.0.0/12','192.168.0.0/16'})
DOCUMENTATION_ADDRESSES = tuple(ipaddress.ip_network(cidr) for cidr in
    ('192.0.2.0/24','198.51.100.0/24','203.0.113.0/24'))
CREDENTIAL_RE = re.compile(r'\bsk-(?:proj-|ant-[A-Za-z0-9-]*-)?[A-Za-z0-9_-]{16,}|'
    r'\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}|'
    r'\bAKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|'
    r'\b(rtn|rts|rtd)_[A-Za-z0-9_-]{30,}|'
    r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|'
    r'["\']?\b(?:app_secret|client_secret|secret_key|password|access_token|'
    r'refresh_token|api_key|authorization)["\']?\s*[:=]\s*["\'][^"\'\n]{8,}',re.I)
_CLOUD='tencent'
FINGERPRINT_RE=re.compile(rf'{_CLOUD}yun|{_CLOUD}cloudcr|mirrors\.cloud\.{_CLOUD}\.com',re.I)
ALLOWLIST_CATEGORIES = frozenset({'address','machine-path','email','credential'})
ALLOWLIST_REASONS = frozenset({'synthetic-test','negative-test','sanitizer-rule',
    'public-third-party-notice','synthetic-demo','version-constant','svg-path-constant'})

def reviewed_allowlist(root: Path) -> set[tuple[str,str,str,str]]:
    """Exact matches only; invalid/unknown manifests provide no exceptions.

    This file is review policy, never populated from new findings automatically.
    Both the complete source line and individual candidate must be unchanged.
    There is no file, directory, localhost-line, or test-directory exemption.
    """
    path=root/'scripts'/'public_scan_allowlist.json'
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data,dict) or data.get('version') != 1:
            return set()
        entries=data.get('entries')
        if not isinstance(entries,list):
            return set()
        result=set()
        for item in entries:
            if not isinstance(item,dict) or item.get('category') not in ALLOWLIST_CATEGORIES:
                return set()
            if item.get('reason') not in ALLOWLIST_REASONS or not item.get('review_note'):
                return set()
            name=item.get('file')
            line_hash=item.get('line_sha256')
            match_hash=item.get('match_sha256')
            if not isinstance(name,str) or name.startswith('/') or '..' in Path(name).parts:
                return set()
            if not all(isinstance(h,str) and re.fullmatch('[0-9a-f]{64}',h)
                for h in (line_hash,match_hash)):
                return set()
            result.add((name,item['category'],line_hash,match_hash))
        return result
    except (OSError,ValueError,TypeError):
        return set()

def is_excluded(relative: str) -> bool:
    relative=relative.replace('\\','/').lower()
    parts=relative.split('/')
    return (relative in EXTRA_EXCLUDE or
        any(relative.startswith(p) for p in PREFIX_EXCLUDE) or
        any(p in SKIP_DIR_NAMES or p.endswith('.egg-info') for p in parts) or
        relative.endswith(('.tsbuildinfo','.db','.sqlite','.sqlite3','.log',
            '.jsonl','.pem','.key','.token','.p12','.pfx')) or
        any(p == '.env' or p.startswith('.env.') and not p.endswith('.example') for p in parts))

def git_files(source: Path) -> list[str] | None:
    try:
        top=subprocess.check_output(['git','rev-parse','--show-toplevel'],cwd=source,
            stderr=subprocess.DEVNULL).decode().strip()
        if Path(top).resolve() != source.resolve():
            return None
        blobs=[subprocess.check_output(['git','ls-files','-z'],cwd=source),
            subprocess.check_output(['git','ls-files','-z','--others','--exclude-standard'],cwd=source)]
        return sorted({p for b in blobs for p in b.decode('utf-8').split('\0') if p})
    except (OSError,subprocess.CalledProcessError):
        return None

def text_files(root: Path):
    for p in sorted(root.rglob('*')):
        if not p.is_file() or is_excluded(p.relative_to(root).as_posix()):
            continue
        try:
            yield p,p.read_text(encoding='utf-8')
        except (UnicodeDecodeError,OSError):
            continue

def _scan(root: Path,pattern,category: str,allowed=None) -> list[str]:
    findings=[]
    reviewed=reviewed_allowlist(root)
    for p,content in text_files(root):
        for line,text in enumerate(content.splitlines(),1):
            for match in pattern.finditer(text):
                if not (allowed and allowed(match,text)):
                    name=p.relative_to(root).as_posix()
                    identity=(name,category,hashlib.sha256(text.encode('utf-8')).hexdigest(),
                        hashlib.sha256(match.group(0).encode('utf-8')).hexdigest())
                    if identity not in reviewed:
                        findings.append(f'{name}:{line}:{category}')
    return findings

def _allowed_ip(match,text: str) -> bool:
    try:
        address=ipaddress.ip_address(match.group(0))
    except ValueError:
        return True  # Not an IP address, such as a dotted numeric version.
    if address.is_loopback or address.is_unspecified:
        return True
    if any(address in network for network in DOCUMENTATION_ADDRESSES):
        return True
    suffix=re.match(r'/[0-9]{1,2}(?![0-9])',text[match.end():])
    return bool(suffix and match.group(0)+suffix.group(0) in DOCUMENTATION_NETWORKS)

def scan_export(root: Path) -> tuple[list[str],list[str]]:
    return (_scan(root,IPV4_RE,'address',_allowed_ip)+
        _scan(root,PATH_RE,'machine-path')+_scan(root,EMAIL_RE,'email'),
        _scan(root,CREDENTIAL_RE,'credential'))

def scan_internal_names(root: Path) -> list[str]:
    """Generic product module/theme names are not private identifiers."""
    return []

def scan_fingerprints(root: Path) -> list[str]:
    return _scan(root,FINGERPRINT_RE,'private-mirror')

def _rename_directory(source: Path,dest: Path) -> None:
    """Bound short Windows antivirus/indexer handle races without losing backups."""
    for attempt,delay in enumerate((0.05,0.1,0.2,0.4,0)):
        try:
            source.rename(dest)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(delay)

def export_community(source: Path,dest: Path,report: Path) -> dict[str,object]:
    source,dest,report=source.resolve(),dest.resolve(),report.resolve()
    if dest == source or dest in source.parents:
        raise ValueError('destination cannot be source or its ancestor')
    if report == dest or dest in report.parents:
        raise ValueError('report must be outside destination')
    dest.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.public-export-',dir=dest.parent))
    names=git_files(source)
    if names is None:
        names=sorted(p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file())
    copied=[]
    excluded=[]
    try:
        for name in names:
            src=source/name
            if is_excluded(name) or src == dest or dest in src.parents:
                excluded.append(name)
                continue
            if not src.is_file():
                continue
            # Symlinked deployment data must not be followed into a public tree.
            if src.is_symlink() or source not in src.resolve().parents:
                raise ValueError('source symlinks/out-of-tree files are not exportable')
            target=staging/name
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(src,target)
            copied.append(name)
        for short in ('README','SECURITY','CONTRIBUTING','NOTICE'):
            extension='' if short == 'NOTICE' else '.md'
            fallback=staging/(short+'.community'+extension)
            target=staging/(short+extension)
            if not target.exists() and fallback.exists():
                shutil.copy2(fallback,target)
        identifiers,credentials=scan_export(staging)
        fingerprints=scan_fingerprints(staging)
        findings=identifiers+credentials+fingerprints
        # A dirty candidate is not installed as the public output.
        if not findings:
            previous=dest.with_name(dest.name+'.previous')
            if previous.exists():
                raise ValueError('previous export exists; inspect before retrying')
            if dest.exists():
                _rename_directory(dest,previous)
            try:
                _rename_directory(staging,dest)
            except OSError:
                if previous.exists() and not dest.exists():
                    _rename_directory(previous,dest)
                raise
            if previous.exists():
                shutil.rmtree(previous)
        result={'copied':len(copied),'excluded':len(excluded),'git_history_changed':False,
            'features_preserved':True,'identifier_hits':len(identifiers),
            'credential_hits':len(credentials),'fingerprint_hits':len(fingerprints),
            'scan':'needs_review' if findings else 'clean','findings':findings,
            'finding_interpretation':'Candidates require source-aware review; no test directory is exempt.'}
        report.parent.mkdir(parents=True,exist_ok=True)
        report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        return result
    finally:
        if staging.exists():
            shutil.rmtree(staging)

def main(argv=None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=ROOT)
    p.add_argument('--out',type=Path,default=ROOT/'dist'/'community-export')
    p.add_argument('--report',type=Path,default=ROOT/'dist'/'community-export-scan.json')
    p.add_argument('--scan-only',action='store_true',help='shared read-only whole-source handoff scan')
    args=p.parse_args(argv)
    if args.scan_only:
        identifiers,credentials=scan_export(args.source)
        fingerprints=scan_fingerprints(args.source)
        result={'scan':'needs_review' if identifiers or credentials or fingerprints else 'clean',
            'identifier_hits':len(identifiers),'credential_hits':len(credentials),
            'fingerprint_hits':len(fingerprints),'findings':identifiers+credentials+fingerprints}
        print(json.dumps(result))
        return 0 if result['scan'] == 'clean' else 2
    try:
        result=export_community(args.source,args.out,args.report)
    except ValueError as exc:
        p.error(str(exc))
    print(json.dumps({k:v for k,v in result.items() if k != 'findings'}))
    return 2 if result['scan'] != 'clean' else 0

if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Repack the verified supplied Windows runtime; only resources/app.asar changes."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from urllib.parse import urlsplit
import zipfile

ARCHIVE_SHA256 = 'b0ec400dbe6dd60c5b17c1e1d178b933f43966fb06a0cb70dea42cf541db1676'
ARCHIVE_SIZE = 157426933
MAX_RUNTIME_SIZE = 400 * 1024 * 1024
MAX_MEMBERS = 2000
RUNTIME_PREFIX = 'dist/win-unpacked/'
BRIDGE_FILES = ('.env.example', 'LICENSE', 'Start-Private-Proxy.cmd', 'package.json', 'package-lock.json', 'README.md',
                'bridge/README.md', 'bridge/env.mjs', 'bridge/start.mjs', 'bridge/bootstrap.mjs', 'bridge/client.mjs',
                'bridge/tunnel-stream.mjs', 'bridge/lock.mjs', 'bridge/tls-resume.mjs', 'bridge/limits.mjs',
                'bridge/fast-open.mjs', 'bridge/session-queue.mjs', 'bridge/diagnostics.mjs',
                'bridge/budget.mjs', 'bridge/policy.mjs', 'bridge/data/iana-special.json',
                'bridge/check-remote.mjs', 'docs/WINDOWS-STARTUP.md', 'docs/PERFORMANCE.md',
                'docs/USER-CHROME-WINDOWS.md')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_archive(path):
    if Path(path).stat().st_size != ARCHIVE_SIZE:
        raise ValueError('Supplied archive size mismatch')
    if digest(path) != ARCHIVE_SHA256:
        raise ValueError('Supplied archive SHA256 mismatch')


def extract_runtime(archive, destination, max_size=MAX_RUNTIME_SIZE, prefix=RUNTIME_PREFIX):
    destination = Path(destination)
    members = []
    names = set()
    size = 0
    for info in archive.infolist():
        if not info.filename.startswith(prefix):
            continue
        relative = info.filename[len(prefix):]
        if not relative:
            continue
        parts = (relative[:-1] if info.is_dir() else relative).split('/')
        reserved = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} | {f'{kind}{number}' for kind in ('COM', 'LPT') for number in range(1, 10)}
        if '\\' in relative or any(not part or part in {'.', '..'} or ':' in part or part.endswith((' ', '.')) or part.split('.')[0].upper() in reserved or any(ord(char) < 32 for char in part) for part in parts):
            raise ValueError('Unsafe ZIP runtime path')
        key = relative.rstrip('/').casefold()
        if key in names or stat.S_ISLNK(info.external_attr >> 16):
            raise ValueError('Duplicate or linked ZIP runtime path')
        names.add(key)
        size += info.file_size
        if size > max_size or len(names) > MAX_MEMBERS or info.flag_bits & 1:
            raise ValueError('ZIP runtime limits exceeded')
        members.append((info, relative))
    required = {'chromeprotegido.exe', 'resources/app.asar', 'license.electron.txt', 'licenses.chromium.html'}
    if not required.issubset(names):
        raise ValueError('Supplied runtime or licenses missing')
    hashes = {}
    for info, relative in members:
        output = destination / relative
        if info.is_dir():
            output.mkdir(parents=True, exist_ok=True)
            continue
        output.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, output.open('wb') as target:
            shutil.copyfileobj(source, target)
        hashes[relative] = digest(output)
    return hashes


def verify_runtime(runtime, hashes):
    actual = {p.relative_to(runtime).as_posix() for p in Path(runtime).rglob('*') if p.is_file()}
    if actual != set(hashes):
        raise ValueError('Runtime file list changed')
    for name, expected in hashes.items():
        if name != 'resources/app.asar' and digest(Path(runtime) / name) != expected:
            raise ValueError(f'Runtime file changed: {name}')


def asar_command(asar_cli):
    cli = Path(asar_cli).resolve()
    package = json.loads((cli.parent.parent / 'package.json').read_text(encoding='utf-8'))
    if package.get('name') != '@electron/asar' or package.get('version') != '3.4.1':
        raise ValueError('Use pinned @electron/asar 3.4.1')
    node = shutil.which('node')
    if not node:
        raise ValueError('Node.js is required for the pinned ASAR tool')
    return [node, str(cli)]


def stage_sources(root, destination, smoke=False):
    sources = {}
    excluded = {'node_modules', 'test', 'integration', 'docs', '.superpowers', '.git', 'dist'}
    for source in sorted((Path(root) / 'user-chrome').rglob('*')):
        relative = source.relative_to(Path(root) / 'user-chrome')
        if set(relative.parts) & excluded:
            continue
        if source.is_symlink():
            raise ValueError('Source symlinks are not allowed')
        if not source.is_file():
            continue
        if source.suffix.lower() in {'.exe', '.dll', '.zip', '.pem', '.key'} or source.name.lower().startswith('.env'):
            raise ValueError('Unexpected binary or secret in app sources')
        target = Path(destination) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        sources[relative.as_posix()] = digest(target)
    if smoke:
        harness = Path(root) / 'user-chrome/integration/private-proxy.electron.js'
        target = Path(destination) / 'integration/private-proxy.electron.js'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(harness, target)
        manifest = Path(destination) / 'package.json'
        data = json.loads(manifest.read_text(encoding='utf-8'))
        data['main'] = 'integration/private-proxy.electron.js'
        manifest.write_text(json.dumps(data), encoding='utf-8')
    return sources


def pack_asar(root, output, asar_cli, smoke=False):
    command = asar_command(asar_cli)
    with tempfile.TemporaryDirectory(prefix='user-chrome-sources-') as temporary:
        source = Path(temporary) / 'app'
        source.mkdir()
        hashes = stage_sources(root, source, smoke=smoke)
        subprocess.run([*command, 'pack', str(source), str(output)], check=True, timeout=120)
        extracted = Path(temporary) / 'verify'
        subprocess.run([*command, 'extract', str(output), str(extracted)], check=True, timeout=120)
        for path in source.rglob('*'):
            if path.is_file() and digest(path) != digest(extracted / path.relative_to(source)):
                raise ValueError(f'ASAR round-trip mismatch: {path.name}')
        return hashes


def validate_bundle_secrets(bundle):
    private_key = re.compile(rb'-----BEGIN ((?:[A-Z0-9]+ )*PRIVATE KEY)-----\r?\n.{32,}?-----END \1-----', re.DOTALL)
    for path in Path(bundle).rglob('*'):
        if not path.is_file():
            continue
        relative = path.relative_to(bundle).as_posix()
        if path.name.lower().startswith('.env'):
            template = (b'# Copie este arquivo para .env e defina TUNNEL_TOKEN com o valor do Render.\n'
                        b'# Exemplo sem credenciais reais.\nTUNNEL_TOKEN=\n')
            actual = path.read_bytes().replace(b'\r\n', b'\n')
            if relative != '.env.example' or actual != template:
                raise ValueError(f'Unexpected key or credential file: {relative}')
        if path.suffix.lower() in {'.key', '.pem', '.pfx', '.p12'}:
            raise ValueError(f'Unexpected key or credential file: {relative}')
        if private_key.search(path.read_bytes()):
            raise ValueError(f'Private key block in bundle: {path.relative_to(bundle)}')


def launcher_bytes(root, endpoint):
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'wss' or parsed.netloc != parsed.hostname or parsed.path != '/tunnel' or parsed.query or parsed.fragment:
        raise ValueError('Endpoint must be public wss://HOST/tunnel')
    if not parsed.hostname or any(char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-' for char in parsed.hostname):
        raise ValueError('Invalid endpoint hostname')
    data = (Path(root) / 'Start-Private-Proxy.cmd').read_bytes().replace(b'\r\n', b'\n')
    settings = f'setlocal\nset "PCP_PROTECTED_BROWSER_EXE=%~dp0ChromeProtegido\\ChromeProtegido.exe"\nif not defined TUNNEL_URL set "TUNNEL_URL={endpoint}"\n'
    return data.replace(b'setlocal\n', settings.encode(), 1).replace(b'\n', b'\r\n')


def build_bundle(root, archive_path, output, asar_cli, endpoint):
    root, output = Path(root), Path(output)
    verify_archive(archive_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='user-chrome-package-') as temporary:
        bundle = Path(temporary) / 'bundle'
        runtime = bundle / 'ChromeProtegido'
        with zipfile.ZipFile(archive_path) as archive:
            hashes = extract_runtime(archive, runtime)
        sources = pack_asar(root, runtime / 'resources/app.asar', asar_cli)
        verify_runtime(runtime, hashes)
        for name in BRIDGE_FILES:
            target = bundle / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, target)
        (bundle / 'Start-Private-Proxy.cmd').write_bytes(launcher_bytes(root, endpoint))
        manifest = json.loads((root / 'package.json').read_text(encoding='utf-8'))
        for package in ('ws', 'ipaddr.js'):
            directory = root / 'node_modules' / package
            if not directory.is_dir() or json.loads((directory / 'package.json').read_text(encoding='utf-8'))['version'] != manifest['dependencies'][package]:
                raise ValueError('Run npm ci --omit=dev --ignore-scripts before packaging')
            shutil.copytree(directory, bundle / 'node_modules' / package)
        revision = os.environ.get('GITHUB_SHA')
        if revision and not re.fullmatch(r'[0-9a-f]{40,64}', revision):
            raise ValueError('Invalid CI source commit')
        report = {'archiveSha256': ARCHIVE_SHA256, 'sourceCommit': revision, 'originalRuntimeSha256': hashes,
                  'appAsarSha256': digest(runtime / 'resources/app.asar'), 'sourceSha256': sources,
                  'asarVersion': '3.4.1', 'endpoint': endpoint}
        (bundle / 'User-Chrome-Provenance.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        validate_bundle_secrets(bundle)
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(bundle.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(bundle).as_posix())
        with zipfile.ZipFile(output) as archive:
            for name, expected in hashes.items():
                if name != 'resources/app.asar' and hashlib.sha256(archive.read('ChromeProtegido/' + name)).hexdigest() != expected:
                    raise ValueError(f'Final ZIP runtime mismatch: {name}')
    checksum = output.with_suffix('.sha256')
    checksum.write_text(f'{digest(output)}  {output.name}\n', encoding='ascii')
    return output


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True, help='Path to the original runtime main.zip (size and SHA-256 are verified)')
    parser.add_argument('--output', type=Path, default=root / 'dist/PrivateCloudProxy-UserChrome-Windows.zip')
    parser.add_argument('--asar', type=Path, default=root / 'tools/user-chrome/node_modules/@electron/asar/bin/asar.js')
    parser.add_argument('--endpoint', default=os.environ.get('TUNNEL_URL'), help='Public WSS endpoint (wss://HOST/tunnel); required unless TUNNEL_URL is set')
    args = parser.parse_args()
    if not args.endpoint:
        parser.error('Endpoint required: pass --endpoint wss://HOST/tunnel or set TUNNEL_URL')
    archive = args.archive
    print(build_bundle(root, archive, args.output, args.asar, args.endpoint))


if __name__ == '__main__':
    main()

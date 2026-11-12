"""Download the pinned official k6 CLI and verify its published SHA-256."""
import hashlib
import json
from pathlib import Path
import platform
import tarfile
import urllib.request
import zipfile
from common import LOCAL

VERSION='2.3.0'
arch={'arm64':'arm64','aarch64':'arm64','x86_64':'amd64','AMD64':'amd64'}.get(platform.machine())
system={'Darwin':'macos','Linux':'linux'}.get(platform.system())
if not arch or not system:raise RuntimeError('Use macOS, Linux, or WSL2 with Docker Desktop integration')
folder=LOCAL/'tools/k6';folder.mkdir(parents=True,exist_ok=True)
name=f'k6-v{VERSION}-{system}-{arch}'+('.zip' if system=='macos' else '.tar.gz')
base=f'https://github.com/grafana/k6/releases/download/v{VERSION}/'
archive=folder/name
if not archive.exists():urllib.request.urlretrieve(base+name,archive)
checks=urllib.request.urlopen(base+f'k6-v{VERSION}-checksums.txt',timeout=30).read().decode()
expected=next(line.split()[0] for line in checks.splitlines() if line.strip().endswith(name))
actual=hashlib.sha256(archive.read_bytes()).hexdigest()
if actual!=expected:raise RuntimeError('k6 archive checksum mismatch; remove the incomplete local archive and retry')
if system=='macos':
    with zipfile.ZipFile(archive) as source:
        item=next(n for n in source.namelist() if n.endswith('/k6'));(folder/'k6').write_bytes(source.read(item))
else:
    with tarfile.open(archive) as source:
        item=next(n for n in source.getmembers() if n.name.endswith('/k6'));(folder/'k6').write_bytes(source.extractfile(item).read())
(folder/'k6').chmod(0o755)
(folder/'verified.json').write_text(json.dumps({'version':VERSION,'url':base+name,'sha256':actual},indent=2))
print('Installed verified official k6',VERSION,'in',folder,'(local, ignored)')

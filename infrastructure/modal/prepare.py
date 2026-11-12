"""Prepare only local, verified Linux/amd64 assets for Modal file mounts."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "infrastructure/.local/modal/assets"
JAR = ROOT / "backend/build/libs/sdu-course-registration-backend-0.1.0-SNAPSHOT.jar"
JRE_URL = "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9/OpenJDK21U-jre_x64_linux_hotspot_21.0.13_9.tar.gz"
K6_URL = "https://github.com/grafana/k6/releases/download/v2.3.0/k6-v2.3.0-linux-amd64.tar.gz"
JRE_SHA = "a535a1df9c6284d9618872984e026ae0352d1f7e9e0605425c0e6114f47351dd"
K6_SHA = "39c3117b6af817592dcd0ce4242105c0a7af10948c2a425306f0be8f7a8a8ab1"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(url, expected, target):
    if target.exists() and sha(target) == expected:
        return
    with tempfile.NamedTemporaryFile(dir=target.parent) as temporary:
        with urllib.request.urlopen(url, timeout=30) as response:
            shutil.copyfileobj(response, temporary)
        temporary.flush()
        if sha(Path(temporary.name)) != expected:
            raise RuntimeError("Official release checksum did not match the pinned digest")
        shutil.copyfile(temporary.name, target)


def prepare():
    if not JAR.is_file():
        raise RuntimeError("Build the Spring Boot JAR with the Gradle wrapper and JDK 21 first")
    ASSETS.mkdir(parents=True, exist_ok=True)
    download(JRE_URL, JRE_SHA, ASSETS / "jre.tar.gz")
    archive = ASSETS / "k6.tar.gz"
    download(K6_URL, K6_SHA, archive)
    with tarfile.open(archive) as source:
        with source.extractfile("k6-v2.3.0-linux-amd64/k6") as executable, (ASSETS / "k6").open("wb") as output:
            shutil.copyfileobj(executable, output)
    (ASSETS / "k6").chmod(0o755)
    subprocess.run(["go", "build", "-trimpath", "-o", str(ASSETS / "gateway"), str(Path(__file__).with_name("gateway.go"))],
                   env={**os.environ, "CGO_ENABLED": "0", "GOOS": "linux", "GOARCH": "amd64"}, check=True)
    (ASSETS / "gateway-source.json").write_text(json.dumps({
        "source_sha256": sha(Path(__file__).with_name("gateway.go")), "binary_sha256": sha(ASSETS / "gateway")}))
    print(f"Prepared verified JRE and k6 plus static gateway. Boot JAR SHA-256 {sha(JAR)}")


if __name__ == "__main__":
    prepare()

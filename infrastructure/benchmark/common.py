"""Local benchmark boundary: no caller-provided database URL or destructive target."""
from pathlib import Path
import hashlib
import json
import os
import subprocess

ROOT = Path(__file__).resolve().parents[2]
LOCAL = ROOT / 'infrastructure/.local/benchmark'
RESULTS = ROOT / 'infrastructure/k6/results'
ENV_FILE = LOCAL / 'environment'
COMPOSE_FILE = ROOT / 'infrastructure/monitoring/compose.yaml'
PROJECT = 'sdu-registration-benchmark'
JAVA = Path(os.environ['JAVA_HOME']) if os.environ.get('JAVA_HOME') else None


def settings():
    if not ENV_FILE.exists():
        raise RuntimeError('Run dataset.py init first')
    return dict(line.split('=', 1) for line in ENV_FILE.read_text().splitlines() if line and not line.startswith('#'))


def compose(*args, **kwargs):
    return subprocess.run(['docker', 'compose', '--env-file', str(ENV_FILE), '-f', str(COMPOSE_FILE), *args],
                          cwd=ROOT, check=True, text=True, **kwargs)


def sql(statement):
    values = settings()
    env = dict(os.environ, PGPASSWORD=values['BENCH_DB_PASSWORD'], PGCONNECT_TIMEOUT='3')
    result = subprocess.run(['psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-h', '127.0.0.1', '-p', '16432',
                             '-U', 'benchmark_migrator', '-d', 'portal_benchmark'], input=statement, env=env,
                            text=True, capture_output=True, timeout=45)
    if result.returncode:
        error = result.stderr
        for value in values.values():
            if len(value) >= 16:
                error = error.replace(value, '[REDACTED]')
        raise RuntimeError('Isolated benchmark SQL failed: ' + error[:1000])
    return result.stdout.strip()


def guard(allow_unmarked=False, require_stopped=False):
    container = compose('ps', '-q', 'postgres', capture_output=True).stdout.strip()
    if not container:
        raise RuntimeError('Dedicated benchmark PostgreSQL must be running')
    label = subprocess.check_output(['docker', 'inspect', '--format', '{{index .Config.Labels "com.docker.compose.project"}}', container], text=True).strip()
    if label != PROJECT:
        raise RuntimeError('Refusing database operation: wrong Compose project')
    row = json.loads(sql("SELECT json_build_object('database',current_database(),'user',current_user,'marker',shobj_description(oid,'pg_database')) FROM pg_database WHERE datname=current_database();"))
    if row['database'] != 'portal_benchmark' or row['user'] != 'benchmark_migrator':
        raise RuntimeError('Refusing database operation: wrong database/identity')
    expected = 'SDU-PHASE6-ISOLATED:' + settings()['BENCH_SAFETY_TOKEN']
    if row['marker'] != expected and not (allow_unmarked and row['marker'] is None):
        raise RuntimeError('Refusing database operation: missing/wrong isolated marker')
    if allow_unmarked and row['marker'] is None:
        # Bootstrap may mark an empty installation, never an unmarked populated restore.
        for table in ['students.students', 'catalog.courses', 'catalog.academic_terms', 'identity.test_accounts']:
            if sql(f"SELECT to_regclass('{table}') IS NOT NULL;") == 't' and sql(f"SELECT EXISTS(SELECT 1 FROM {table});") == 't':
                raise RuntimeError('Refusing to mark an unrecognized populated database')
    if require_stopped and compose('ps', '--status', 'running', '-q', 'backend', capture_output=True).stdout.strip():
        raise RuntimeError('Stop the dedicated backend before resetting fixture state')


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def revision():
    files = {}
    for folder in ['backend/src', 'infrastructure/k6', 'infrastructure/benchmark', 'infrastructure/monitoring']:
        for path in sorted((ROOT / folder).rglob('*')):
            if path.is_file() and not any(p in {'results', '__pycache__'} for p in path.parts):
                files[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ['backend/gradle.lockfile','backend/build.gradle.kts','backend/settings.gradle.kts','backend/.dockerignore','backend/gradle/wrapper/gradle-wrapper.properties']:
        files[name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    return {'sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(), 'files': files}

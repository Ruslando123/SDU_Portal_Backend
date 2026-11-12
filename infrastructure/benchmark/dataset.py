"""Deterministic synthetic fixture and guarded reset for the dedicated benchmark cluster."""
import argparse
import concurrent.futures
import csv
import datetime as dt
import hashlib
import http.cookiejar
import io
import json
import os
from pathlib import Path
import random
import secrets
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
from common import ROOT, LOCAL, ENV_FILE, JAVA, compose, guard, literal, revision, settings, sql

SEED = 20261106
VARIANTS = ('phase6-v1', 'semester-v1')


def variant_manifest(variant):
    return json.loads((Path(__file__).parent / 'datasets' / (variant + '.json')).read_text())


def meeting_rows(sections, manifest):
    schedule = manifest['schedule']
    start = dt.datetime.fromisoformat(schedule['firstMondayUtc'])
    weeks, weekly = schedule['weeks'], schedule['meetingsPerWeek']
    duration = dt.timedelta(minutes=schedule['durationMinutes'])
    kind = 'meeting' if manifest['variant'] == 'phase6-v1' else manifest['variant'] + '-meeting'
    rows = []
    for i, section in enumerate(sections):
        weekdays = sorted((i % 5 + 2 * slot) % 5 for slot in range(weekly))
        for week in range(weeks):
            for slot, weekday in enumerate(weekdays):
                at = start + dt.timedelta(days=7 * week + weekday, hours=2 * ((i // 5) % 5))
                index = i * weeks * weekly + week * weekly + slot
                rows.append((identity(kind, index), section['id'], at, at + duration, 'LECTURE', 'true', 'Synthetic Room'))
    return rows

def identity(kind, index=0):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f'sdu-synthetic:{SEED}:{kind}:{index}'))


def initialize():
    LOCAL.mkdir(parents=True, exist_ok=True, mode=0o700)
    LOCAL.chmod(0o700)
    if ENV_FILE.exists():
        print('Existing isolated environment retained; no credentials replaced.')
        return
    keys = ['BENCH_DB_PASSWORD', 'BENCH_RUNTIME_PASSWORD', 'BENCH_MONITOR_PASSWORD',
            'BENCH_METRICS_PASSWORD', 'BENCH_GRAFANA_PASSWORD', 'BENCH_STUDENT_PASSWORD', 'BENCH_SAFETY_TOKEN']
    values = {k: secrets.token_urlsafe(32) for k in keys}
    ENV_FILE.write_text(''.join(f'{k}={v}\n' for k, v in values.items()))
    ENV_FILE.chmod(0o600)
    for name, key in [('metrics-password', 'BENCH_METRICS_PASSWORD'), ('grafana-password', 'BENCH_GRAFANA_PASSWORD')]:
        path = LOCAL / name
        path.write_text(values[key]); path.chmod(0o644)  # Parent 0700; read-only mounts for service UIDs.
    print('Created isolated benchmark environment; secrets were not printed.')


def migrate():
    guard(allow_unmarked=True)
    if JAVA is None or not (JAVA/"bin/java").exists():
        raise RuntimeError("Set JAVA_HOME to a JDK 21 installation before migration")
    values = settings()
    env = dict(os.environ, JAVA_HOME=str(JAVA), DB_URL='jdbc:postgresql://127.0.0.1:16432/portal_benchmark',
               DB_MIGRATION_USERNAME='benchmark_migrator', DB_MIGRATION_PASSWORD=values['BENCH_DB_PASSWORD'],
               DB_RUNTIME_PASSWORD=values['BENCH_RUNTIME_PASSWORD'])
    subprocess.run([str(ROOT / 'backend/gradlew'), '-p', str(ROOT / 'backend'),
                    '-Dorg.gradle.java.home=' + str(JAVA), 'migrate', '--console=plain'], env=env, check=True)
    marker = 'SDU-PHASE6-ISOLATED:' + values['BENCH_SAFETY_TOKEN']
    sql(f"COMMENT ON DATABASE portal_benchmark IS {literal(marker)}; CREATE EXTENSION IF NOT EXISTS pg_stat_statements; CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    role_exists = sql("SELECT count(*) FROM pg_roles WHERE rolname='benchmark_monitor';") == '1'
    sql(("ALTER ROLE" if role_exists else "CREATE ROLE") + ' benchmark_monitor LOGIN PASSWORD ' + literal(values['BENCH_MONITOR_PASSWORD']) + '; GRANT pg_monitor TO benchmark_monitor; GRANT CONNECT ON DATABASE portal_benchmark TO benchmark_monitor; GRANT USAGE ON SCHEMA public TO benchmark_monitor; GRANT SELECT ON pg_stat_statements TO benchmark_monitor;')
    print('Dedicated database migrated and marked; diagnostics enabled, durability unchanged.')


def copy(table, columns, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter='\t', lineterminator='\n')
    for row in rows:
        writer.writerow(['\\N' if value is None else str(value) for value in row])
    return f"COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '\\N');\n" + buffer.getvalue() + '\\.\n'


def seed(variant='phase6-v1'):
    guard(require_stopped=True)
    definition = variant_manifest(variant)
    expected = definition['counts']
    if definition['seed'] != SEED:
        raise RuntimeError('Fixture manifest seed does not match its identity generator')
    values = settings()
    # One shared, random, isolated fixture secret; BCrypt cost 10 is verified on every real login.
    verifier = '{bcrypt}' + sql('SELECT crypt(' + literal(values['BENCH_STUDENT_PASSWORD']) + ",gen_salt('bf',10));")
    rng = random.Random(SEED)
    now = dt.datetime.now(dt.timezone.utc)
    term = identity('term'); policy = identity('policy'); enroll_window = identity('enroll-window'); withdraw_window = identity('withdraw-window')
    students = []
    courses = [{'id': identity('course', i), 'offering': identity('offering', i), 'sections': []} for i in range(expected['courses'])]
    sections = []
    for i in range(expected['sections']):
        c = courses[i % 250]
        capacity = 30 if i == 599 else (100 if i < 100 else rng.choice([20, 40, 60, 100]))
        section = {'id': identity('section', i), 'offering': c['offering'], 'course': c['id'], 'capacity': capacity}
        c['sections'].append(section['id']); sections.append(section)
    for i in range(expected['students']):
        target = sections[i % 100]
        students.append({'student': identity('student', i), 'principal': identity('principal', i), 'snapshot': identity('snapshot', i),
                         'login': f'load{i:05d}', 'section': target['id'], 'course': target['course']})
    tables = ['identity.spring_session_attributes', 'identity.spring_session', 'audit.audit_events', 'operations.idempotency_records',
              'enrollment.enrollments', 'enrollment.student_terms', 'students.completed_courses', 'students.eligibility_snapshots',
              'rules.offering_policy_eligibility', 'catalog.offering_prerequisites', 'sections.section_meetings', 'sections.section_instructors',
              'sections.instructors', 'sections.course_sections', 'catalog.course_offerings', 'catalog.courses',
              'rules.registration_windows', 'rules.registration_policies', 'rules.term_policy_gates', 'catalog.academic_terms', 'identity.test_accounts', 'students.students']
    statement = 'BEGIN; TRUNCATE ' + ','.join(tables) + ';\n'
    statement += copy('students.students', 'student_id,student_number,display_name,created_at', [(s['student'], 'synthetic-' + s['login'], 'Synthetic Load Student', now) for s in students])
    statement += copy('identity.test_accounts', 'principal_id,student_id,login_name,password_hash,role,enabled,auth_version,created_at', [(s['principal'], s['student'], s['login'], verifier, 'STUDENT', 'true', 1, now) for s in students])
    statement += copy('catalog.academic_terms', 'term_id,code,starts_on,ends_on,timezone_name', [(term, 'SYNTHETIC-PHASE6', '2026-11-09', '2027-05-01', 'UTC')])
    statement += copy('rules.term_policy_gates', 'term_id,configuration_state,revision,updated_at', [(term, 'PREPARING', 1, now)])
    statement += copy('rules.registration_policies', 'policy_id,term_id,cohort_code,policy_version,credit_limit,created_at', [(policy, term, 'SYNTHETIC', 1, '18.00', now)])
    statement += copy('rules.registration_windows', 'window_id,policy_id,operation_type,opens_at,closes_at,is_closed,closed_at', [(w, policy, op, now-dt.timedelta(days=1), now+dt.timedelta(days=7), 'false', None) for w, op in [(enroll_window, 'ENROLL'), (withdraw_window, 'WITHDRAW')]])
    statement += copy('catalog.courses', 'course_id,code,title,created_at', [(c['id'], f'SYN{i:03d}', f'Synthetic Course {i:03d}', now) for i,c in enumerate(courses)])
    statement += copy('catalog.course_offerings', 'offering_id,term_id,course_id,title,credits,publication_status,draft_revision,published_at', [(c['offering'], term, c['id'], f'Synthetic Offering {i:03d}', '3.00', 'PUBLISHED', 1, now) for i,c in enumerate(courses)])
    statement += copy('sections.course_sections', 'section_id,offering_id,term_id,section_code,capacity,enrolled_count,publication_status,published_at,config_version', [(s['id'], s['offering'], term, f'S{i:03d}', s['capacity'], 0, 'PUBLISHED', now, 1) for i,s in enumerate(sections)])
    meetings = meeting_rows(sections, definition)
    meeting_copy = copy('sections.section_meetings', 'meeting_id,section_id,starts_at,ends_at,meeting_kind,is_required,venue', meetings)
    statement += meeting_copy
    statement += copy('catalog.offering_prerequisites', 'offering_id,required_course_id,minimum_grade,grade_scheme', [(courses[0]['offering'],courses[249]['id'],'60.00','PERCENT')])
    statement += copy('students.eligibility_snapshots', 'snapshot_id,student_id,source_version,evidence_state,academic_status,captured_at', [(s['snapshot'],s['student'],('phase6-seed-20261106' if variant == 'phase6-v1' else f'{variant}-seed-{SEED}'),'COMPLETE','ACTIVE',now) for s in students])
    statement += copy('students.completed_courses', 'snapshot_id,course_id,passed,grade_value,grade_scheme,completed_on', [(s['snapshot'],courses[249]['id'],'true','80.00','PERCENT','2026-10-01') for s in students])
    statement += copy('rules.offering_policy_eligibility', 'offering_id,policy_id,term_id', [(c['offering'],policy,term) for c in courses])
    statement += copy('enrollment.student_terms', 'student_id,term_id,policy_id,cohort_code,eligibility_snapshot_id,credit_limit_override,created_at', [(s['student'],term,policy,'SYNTHETIC',s['snapshot'],None,now) for s in students])
    statement += f"UPDATE rules.term_policy_gates SET configuration_state='PUBLISHED',revision=2,updated_at=clock_timestamp() WHERE term_id={literal(term)}; COMMIT; ANALYZE;"
    sql(statement)
    observed = json.loads(sql('''
        SELECT json_build_object('students',(SELECT count(*) FROM students.students),
          'courses',(SELECT count(*) FROM catalog.courses),
          'sections',(SELECT count(*) FROM sections.course_sections),
          'meetings',(SELECT count(*) FROM sections.section_meetings),
          'sectionsWithUnexpectedMeetings',(SELECT count(*) FROM (
              SELECT s.section_id FROM sections.course_sections s
              LEFT JOIN sections.section_meetings m USING (section_id)
              GROUP BY s.section_id HAVING count(m.meeting_id) <> ''' + str(definition['schedule']['weeks'] * definition['schedule']['meetingsPerWeek']) + '''
          ) bad));
    '''))
    if observed != {**expected, 'sectionsWithUnexpectedMeetings': 0}:
        raise RuntimeError(f'Seed cardinality verification failed: {observed}')
    (LOCAL/f'manifest-{variant}.json').write_text(json.dumps({
        **definition, 'observed': observed, 'generatedAt': now.isoformat(),
        'meetingCopySha256': hashlib.sha256(meeting_copy.encode()).hexdigest(),
        'sourceRevision': revision()['sha256'],
    }, indent=2))
    manifest = {'variant':variant,'seed':SEED,'createdAt':now.isoformat(),'termId':term,'students':students,'courses':courses,'sections':sections,
                'hot':sections[599], 'successSections':[s['id'] for s in sections[:100]], 'passwordCost':10, 'sourceRevision':revision()['sha256']}
    (LOCAL/'dataset.json').write_text(json.dumps(manifest))
    (LOCAL/'dataset-meta.json').write_text(json.dumps({k:manifest[k] for k in ['variant','seed','termId','hot']}))
    (LOCAL/'sessions.json').unlink(missing_ok=True)
    print(f'Seeded {variant} with fixed seed {SEED}: {expected}')


def reset_run():
    guard(require_stopped=True)
    sql('BEGIN; TRUNCATE audit.audit_events,operations.idempotency_records,enrollment.enrollments; UPDATE sections.course_sections SET enrolled_count=0; COMMIT; SELECT pg_stat_statements_reset();')
    print('Reset only dedicated benchmark outcomes/episodes/counters; retained academic fixture and sessions.')


def authenticate(count):
    guard()
    manifest=json.loads((LOCAL/'dataset.json').read_text())
    old=json.loads((LOCAL/'sessions.json').read_text()) if (LOCAL/'sessions.json').exists() else []
    available={s['login']:s for s in old}
    accounts=manifest['students'][:count]
    password=settings()['BENCH_STUDENT_PASSWORD']
    def login(account):
        if account['login'] in available:
            cached=available[account['login']]
            check=urllib.request.Request('http://127.0.0.1:18080/api/v1/me',headers={'Cookie':'SESSION='+cached['cookie']})
            try:
                with urllib.request.urlopen(check,timeout=10) as response:
                    if response.status==200:return {**cached,'verified_at':time.time()}
            except urllib.error.HTTPError as failure:
                if failure.code!=401:raise

        jar=http.cookiejar.CookieJar();client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        base='http://127.0.0.1:18080'
        with client.open(base+'/api/v1/auth/csrf',timeout=15) as r:token=json.load(r)
        payload=urllib.parse.urlencode({'username':account['login'],'password':password}).encode()
        req=urllib.request.Request(base+'/api/v1/auth/login',payload,headers={token['headerName']:token['token'],'Content-Type':'application/x-www-form-urlencoded'})
        with client.open(req,timeout=15) as r:assert r.status==200
        with client.open(base+'/api/v1/auth/csrf',timeout=15) as r:token=json.load(r)
        cookie=next(c.value for c in jar if c.name=='SESSION')
        return {'login':account['login'],'cookie':cookie,'csrf':token['token'],'student':account['student'],'principal':account['principal'],'verified_at':time.time()}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results=[]
        for i,result in enumerate(pool.map(login,accounts),1):
            results.append(result)
            if i%100==0:print(f'Prepared {i}/{count} real HTTP sessions',flush=True)
    path=LOCAL/'sessions.json';path.write_text(json.dumps(results));path.chmod(0o600)
    print('Prepared',len(results),'real HTTP sessions; no secrets printed.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['init','migrate','seed','reset-run','sessions']);parser.add_argument('--count',type=int,default=50)
    parser.add_argument('--variant',choices=VARIANTS,default='phase6-v1')
    args=parser.parse_args()
    if args.action=='init':initialize()
    elif args.action=='migrate':migrate()
    elif args.action=='seed':seed(args.variant)
    elif args.action=='reset-run':reset_run()
    else:
        if not 1<=args.count<=5000:raise ValueError('Session count must be 1..5000')
        authenticate(args.count)

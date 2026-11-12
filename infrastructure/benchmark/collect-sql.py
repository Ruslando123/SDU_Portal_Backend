"""Capture actual PostgreSQL 18 plans for the isolated benchmark fixture."""
import json
from common import ROOT, LOCAL, RESULTS, guard, literal, sql

guard()
destination=RESULTS/'query-plans';destination.mkdir(exist_ok=True)
data=json.loads((LOCAL/'dataset.json').read_text());student=data['students'][0];term=literal(data['termId']);sid=literal(student['student']);section=literal(student['section']);course=literal(student['course']);offering=literal(data['courses'][0]['offering'])
queries={
    'student-term':f'SELECT policy_id,eligibility_snapshot_id,credit_limit_override FROM enrollment.student_terms WHERE student_id={sid} AND term_id={term} FOR UPDATE',
    'section':f'SELECT * FROM sections.course_sections WHERE section_id={section} FOR UPDATE',
    'own-history':f'SELECT * FROM enrollment.enrollments WHERE student_id={sid} AND term_id={term} ORDER BY enrolled_at DESC,enrollment_id LIMIT 100',
    'published-offering':f"SELECT offering_id FROM catalog.course_offerings WHERE course_id={course} AND term_id={term} AND publication_status='PUBLISHED'",
    'published-sections':f"SELECT * FROM sections.course_sections WHERE offering_id={offering} AND term_id={term} AND publication_status='PUBLISHED' ORDER BY section_id LIMIT 100",
    'meetings':f"SELECT * FROM sections.section_meetings WHERE section_id IN ({section},{literal(data['hot']['id'])}) ORDER BY starts_at,meeting_id",
    'idempotency-absent-scope':f"SELECT * FROM operations.idempotency_records WHERE principal_id={literal(student['principal'])} AND operation_type='ENROLL' AND operation_version=1 AND idempotency_key='00000000-0000-4000-8000-000000000000' FOR UPDATE",
}
rows=[]
def walk(plan):
    yield plan
    for child in plan.get('Plans',[]):yield from walk(child)
for name,query in queries.items():
    text=sql('BEGIN; SET LOCAL statement_timeout=\'5s\'; EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '+query+'; ROLLBACK;')
    explain=json.loads(text);(destination/f'{name}.json').write_text(json.dumps(explain,indent=2));result=explain[0];plan=result['Plan']
    rows.append({'query':name,'execution_ms':result['Execution Time'],'planning_ms':result['Planning Time'],'rows':plan['Actual Rows'],
                 'shared_hits':plan.get('Shared Hit Blocks',0),'shared_reads':plan.get('Shared Read Blocks',0),
                 'nodes':[n['Node Type'] for n in walk(plan)],'indexes':[n['Index Name'] for n in walk(plan) if 'Index Name' in n]})
(destination/'summary.json').write_text(json.dumps(rows,indent=2));print(json.dumps(rows,indent=2))

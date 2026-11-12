import http from 'k6/http';
import exec from 'k6/execution';
import { fail } from 'k6';
import { SharedArray } from 'k6/data';
import { Counter, Rate } from 'k6/metrics';
import { uuid } from './lib/api.js';

const sessions = new SharedArray('cloud synthetic sessions', () => JSON.parse(open(__ENV.SDU_SESSIONS)));
const origin = __ENV.SDU_ORIGIN;
const fixture = '10000000-0000-4000-8000-00000000000';
const term = `termId=${fixture}2`;
const journeys = new Counter('cloud_journeys');
const unexpected = new Rate('cloud_unexpected');

if (!origin?.startsWith('https://') || sessions.length !== 10 || !__ENV.SDU_CONNECT || !__ENV.SDU_GATE_KEY) {
  throw new Error('Cloud smoke requires the orchestrator and exactly ten prepared sessions');
}

export const options = {
  scenarios: { smoke: { executor: 'per-vu-iterations', vus: 10, iterations: 1, maxDuration: '30s', gracefulStop: '0s' } },
  discardResponseBodies: false,
  systemTags: ['status', 'method', 'name', 'scenario', 'expected_response', 'error_code'],
  summaryTrendStats: ['avg', 'min', 'p(50)', 'p(95)', 'p(99)', 'max', 'count'],
  thresholds: { cloud_unexpected: ['rate==0'], cloud_journeys: ['count==10'], http_req_failed: ['rate==0'] },
};

function call(session, method, path, name, status, verify, key) {
  const headers = {
    Authorization: `Bearer ${__ENV.SDU_CONNECT}`, 'X-SDU-Benchmark-Key': __ENV.SDU_GATE_KEY,
    Cookie: `SESSION=${session.cookie}`, [session.header]: session.csrf,
  };
  if (key) headers['Idempotency-Key'] = key;
  const response = http.request(method, origin + path, null, {
    headers, tags: { name }, redirects: 0, timeout: '5s', responseCallback: http.expectedStatuses(status),
  });
  let valid = response.status === status;
  if (valid) {
    try { valid = verify(response.json(), response); } catch (_) { valid = false; }
  }
  unexpected.add(!valid);
  if (!valid) fail(`${name} failed validation (HTTP ${response.status})`);
  return response;
}

export default function () {
  const session = sessions[exec.vu.idInTest - 1];
  const section = `${fixture}8`;
  call(session, 'GET', `/api/v1/courses/${fixture}6/sections?${term}`, 'course_sections', 200,
    (body) => Array.isArray(body.sections) && body.sections.some((item) => item.id === section));
  const enrollPath = `/api/v1/sections/${section}/enrollments`;
  const key = uuid();
  const created = call(session, 'POST', enrollPath, 'enroll', 201,
    (body, response) => body.code === 'ENROLLED' && typeof body.enrollmentId === 'string'
      && response.headers['Idempotency-Replayed'] === 'false', key);
  const id = created.json('enrollmentId');
  call(session, 'GET', `/api/v1/me/schedule?${term}`, 'schedule', 200,
    (body) => Array.isArray(body.enrollments) && body.enrollments.some((item) => item.sectionId === section));
  call(session, 'DELETE', `/api/v1/enrollments/${id}`, 'withdraw', 200,
    (body) => body.code === 'WITHDRAWN' && body.enrollmentId === id, uuid());
  call(session, 'POST', enrollPath, 'enroll_replay', 201,
    (body, response) => body.enrollmentId === id && response.body === created.body
      && response.headers['Idempotency-Replayed'] === 'true', key);
  journeys.add(1);
}

export function handleSummary(data) {
  return { [__ENV.SDU_SUMMARY]: JSON.stringify(data) };
}

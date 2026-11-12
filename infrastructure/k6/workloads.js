import { SharedArray } from 'k6/data';
import exec from 'k6/execution';
import { check, sleep, fail } from 'k6';
import { call, login, uuid } from './lib/api.js';

const mode = __ENV.MODE || 'smoke';
const dataset = JSON.parse(open(__ENV.DATASET_META || '../.local/benchmark/dataset-meta.json'));
const students = new SharedArray('synthetic students', () => JSON.parse(open(__ENV.DATASET || '../.local/benchmark/dataset.json')).students);
const courses = new SharedArray('published courses', () => JSON.parse(open(__ENV.DATASET || '../.local/benchmark/dataset.json')).courses);
const sessions = new SharedArray('synthetic sessions', () => mode === 'auth' ? [] : JSON.parse(open(__ENV.SESSIONS || '../.local/benchmark/sessions.json')));
const vus = Number(__ENV.VUS || 10);
const duration = __ENV.DURATION || '20s';
const rate = Number(__ENV.RATE || 100);
const maxVus = Number(__ENV.MAX_VUS || Math.max(vus, 50));
const configs = {
  smoke: {executor: 'per-vu-iterations', vus, iterations: 1, maxDuration: '90s', exec: 'smoke'},
  distributed: {executor: 'per-vu-iterations', vus, iterations: 1, maxDuration: '90s', exec: 'distributed'},
  auth: {executor: 'per-vu-iterations', vus, iterations: 1, maxDuration: '90s', exec: 'authentication'},
  warmup: {executor: 'constant-vus', vus: Math.min(vus, 10), duration, exec: 'arrivalRead'},
  normal: {executor: 'constant-vus', vus, duration, exec: 'journey'},
  'catalog-read': {executor: 'constant-vus', vus, duration, exec: 'catalogRead'},
  'browse-read': {executor: 'constant-vus', vus, duration, exec: 'browseRead'},
  arrival: {executor: 'constant-arrival-rate', rate, timeUnit: '1s', duration, preAllocatedVUs: vus, maxVUs: maxVus, exec: 'arrivalRead'},
  ramp: {executor: 'ramping-arrival-rate', startRate: Math.max(1, Math.floor(rate / 4)), timeUnit: '1s', preAllocatedVUs: vus, maxVUs: maxVus,
    stages: [{duration: '10s', target: rate}, {duration, target: rate}, {duration: '5s', target: 1}], exec: 'arrivalRead'},
  hot: {executor: 'shared-iterations', vus, iterations: Number(__ENV.ITERATIONS || 2000), maxDuration: '180s', exec: 'hot'},
  spike: {executor: 'ramping-arrival-rate', startRate: 5, timeUnit: '1s', preAllocatedVUs: vus, maxVUs: maxVus,
    stages: [{duration: '5s', target: 5}, {duration: '1s', target: rate}, {duration: '10s', target: rate}, {duration: '1s', target: 5}, {duration: '15s', target: 5}], exec: 'registrationSpike'},
  ladder: {executor: 'ramping-vus', startVUs: 0, stages: [{duration: '10s', target: vus}, {duration, target: vus}, {duration: '5s', target: 0}], exec: 'journey'},
};
if (!configs[mode] || vus < 1 || vus > 5000 || maxVus > 5000) throw new Error('Invalid bounded workload configuration');
export const options = {
  scenarios: {[mode]: configs[mode]},
  noCookiesReset: true,
  // Resource UUIDs, login/session IDs, raw URLs, VU and iteration IDs never become metric labels.
  systemTags: ['status', 'method', 'name', 'scenario', 'expected_response', 'error_code', 'proto'],
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(50)', 'p(95)', 'p(99)', 'count'],
  thresholds: {
    portal_unexpected: [{threshold: 'rate<0.005', abortOnFail: true, delayAbortEval: '5s'}],
    portal_timeouts: [{threshold: 'count==0', abortOnFail: true}],
    dropped_iterations: [{threshold: 'count==0', abortOnFail: true, delayAbortEval: '5s'}],
    portal_api_latency: ['p(99)<2000'],
    ...(['catalog-read', 'browse-read'].includes(mode) ? {checks: ['rate==1'], portal_api_latency: ['p(95)<1000','p(99)<2000']} : {}),
    ...(['smoke','normal','distributed','hot','spike','ladder'].includes(mode) ? {'portal_enrollment_latency{result:success}': ['p(95)<1000']} : {}),
    ...(mode === 'hot' ? {'portal_enrollment_latency{result:business_denial}': ['p(95)<1000']} : {}),
  },
};
function actor(index = __VU - 1) {
  if (!sessions[index] || !students[index]) fail('Insufficient distinct authenticated synthetic students');
  return {session: sessions[index], student: students[index]};
}
function number() { return ((__VU * 1103515245 + __ITER * 12345 + dataset.seed) >>> 0) / 4294967296; }
function catalog(account, includeInstructors = false) {
  const r = number();
  const index = r < .6 ? Math.floor(r / .6 * 10) : r < .9 ? 10 + Math.floor((r - .6) / .3 * 60) : 70 + Math.min(179, Math.floor((r - .9) / .1 * 180));
  const details = includeInstructors ? '&includeInstructors=true' : '';
  return call('GET', `/api/v1/courses/${courses[index].id}/sections?termId=${dataset.termId}${details}`, 'sections', account.session);
}
function publishedCourses(account) {
  return call('GET', `/api/v1/courses?termId=${dataset.termId}&available=false&limit=24`, 'catalog', account.session);
}
export function catalogRead() {
  const account = actor();
  if ((__VU + __ITER) % 5 === 0) check(catalog(account).value, { 'sections returned': (body) => Array.isArray(body.sections) });
  else check(publishedCourses(account).value, { 'published catalog returned': (body) => Array.isArray(body.items) });
  sleep(1.25);
}
export function browseRead() {
  const account = actor();
  check(publishedCourses(account).value, { 'published catalog returned': (body) => Array.isArray(body.items) });
  check(catalog(account, true).value, { 'sections and instructors returned': (body) => Array.isArray(body.sectionDetails) });
  const schedule = call('GET', `/api/v1/me/schedule?termId=${dataset.termId}&includeCourseTitles=true`, 'schedule', account.session);
  check(schedule.value, { 'schedule and course titles returned': (body) => Array.isArray(body.enrollments) && !!body.courseTitles });
  sleep(1.25);
}
function writeAndWithdraw(account, replay = false) {
  const key = uuid();
  let result = call('POST', `/api/v1/sections/${account.student.section}/enrollments`, 'enroll', account.session, key);
  for (let attempt = 0; !['success', 'business_denial', 'replay'].includes(result.result) && attempt < 2; attempt++) {
    sleep(.1); result = call('POST', `/api/v1/sections/${account.student.section}/enrollments`, 'enroll', account.session, key);
  }
  if (!['success', 'business_denial', 'replay'].includes(result.result)) fail('Unresolved intention: same-key recovery required');
  if (result.value.code === 'ENROLLED') {
    call('GET', `/api/v1/me/schedule?termId=${dataset.termId}`, 'schedule', account.session);
    const target = `/api/v1/enrollments/${result.value.enrollmentId}`;
    const withdrawalKey = uuid();
    let withdrawal = call('DELETE', target, 'withdraw', account.session, withdrawalKey);
    if (['network', 'unavailable', 'technical'].includes(withdrawal.result)) {
      sleep(.1);
      withdrawal = call('DELETE', target, 'withdraw', account.session, withdrawalKey);
    }
    if (withdrawal.value.code !== 'WITHDRAWN') fail('Withdrawal did not complete; reconcile authoritative own state');
    if (replay) call('POST', `/api/v1/sections/${account.student.section}/enrollments`, 'enroll', account.session, key);
  }
}
export function authentication() {
  const account = students[__VU - 1]; const session = login(account, __ENV.BENCH_STUDENT_PASSWORD);
  call('GET', '/api/v1/me', 'me', session);
  call('POST', '/api/v1/auth/logout', 'logout', session);
}
export function smoke() {
  const account = actor(); catalog(account);
  call('GET', `/api/v1/me/enrollments?termId=${dataset.termId}`, 'enrollments', account.session);
  writeAndWithdraw(account, true);
}
export function journey() {
  const account = actor(); const r = number();
  if (r < .4) catalog(account);
  else if (r < .7) {
    call('GET', `/api/v1/me/enrollments?termId=${dataset.termId}`, 'enrollments', account.session);
    call('GET', `/api/v1/me/schedule?termId=${dataset.termId}`, 'schedule', account.session);
  } else writeAndWithdraw(account, r > .95);
  sleep(.5 + r);
}
export function arrivalRead() {
  if (mode === "warmup") sleep(.2);
  const account = actor();
  switch ((exec.scenario.iterationInTest + __VU) % 3) {
    case 0: catalog(account); break;
    case 1: call('GET', `/api/v1/me/enrollments?termId=${dataset.termId}`, 'enrollments', account.session); break;
    default: call('GET', `/api/v1/me/schedule?termId=${dataset.termId}`, 'schedule', account.session);
  }
}
export function distributed() {
  const account = actor();
  call('POST', `/api/v1/sections/${account.student.section}/enrollments`, 'enroll', account.session, uuid());
}
export function hot() {
  const account = actor(exec.scenario.iterationInTest);
  call('POST', `/api/v1/sections/${dataset.hot.id}/enrollments`, 'enroll', account.session, uuid());
  sleep(Number(__ENV.HOT_PAUSE || 0));
}
export function registrationSpike() { writeAndWithdraw(actor()); }
export function handleSummary(data) {
  return { [__ENV.SUMMARY || 'summary.json']: JSON.stringify(data, null, 2) };
}

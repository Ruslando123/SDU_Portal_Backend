import http from 'k6/http';
import exec from 'k6/execution';
import { sleep } from 'k6';
import { SharedArray } from 'k6/data';
import { Counter, Gauge, Rate, Trend } from 'k6/metrics';
import { uuid } from './lib/api.js';

const spec = JSON.parse(__ENV.SDU_PROFILE);
const actors = new SharedArray('isolated cloud actors', () => JSON.parse(open(__ENV.SDU_SESSIONS)));
const origin = __ENV.SDU_ORIGIN;
const fixture = '10000000-0000-4000-8000-00000000000';
const term = `termId=${fixture}2`;
const phase = __ENV.SDU_PHASE;
const profile = phase === 'warmup' ? spec.warmup_profile : spec.profile;
const vus = phase === 'warmup' ? spec.warmup_vus : spec.vus;
const openModel = phase === 'measure' && spec.arrival_rate !== null;
const timed = ['steady-read', 'mixed', 'open-read'].includes(profile);
const windowSeconds = phase === 'warmup' ? spec.warmup_seconds : spec.steady_seconds;
const rampSeconds = phase === 'warmup' ? spec.ramp_elapsed ?? spec.ramp_seconds : 0;
const ladder = phase === 'warmup' && spec.diagnostic_only;
const ladderStages = [{duration: '36s', target: 1500}, {duration: '15s', target: 1500},
  {duration: '12s', target: 2000}, {duration: '15s', target: 2000}, {duration: '12s', target: 2500}];
const ladderWindows = [[1500, 36, 51], [2000, 63, 78]];
const ladderRequests = new Counter('cloud_ladder_requests');
const ladderErrors = new Counter('cloud_ladder_errors');
const ladderLatency = new Trend('cloud_ladder_latency', true);
const ladderIterations = new Counter('cloud_ladder_iterations');
function ladderLevel() {
  const elapsed = (Date.now() - exec.scenario.startTime) / 1000;
  return ladderWindows.find(([, start, end]) => start <= elapsed && elapsed < end)?.[0];
}
const categories = ['success', 'business_denial', 'rate_limited', 'unavailable', 'timeout', 'transport', 'http_error', 'invalid_response', 'gateway_limit'];
const denied = new Set(['SECTION_FULL', 'ALREADY_ENROLLED', 'CREDIT_LIMIT_EXCEEDED', 'SCHEDULE_CONFLICT',
  'PREREQUISITE_NOT_MET', 'REGISTRATION_CLOSED', 'ACADEMIC_INELIGIBLE', 'COHORT_INELIGIBLE']);
const outcomes = new Counter('cloud_outcomes');
const allUnexpected = new Counter('cloud_all_unexpected');
const windowUnexpected = new Counter('cloud_window_unexpected');
const safetyRate = new Rate('cloud_safety_rate');
const latency = new Trend('cloud_window_latency', true);
const successfulRead = new Trend('cloud_successful_read', true);
const readRoutes = [spec.catalog_path ? 'catalog' : 'sections', 'schedule', 'own_enrollments', 'me'];
const requests = new Counter('cloud_window_requests');
const scenarioStart = new Gauge('cloud_scenario_start_ms');
const iterationsStarted = new Counter('cloud_iterations_started');
const windowIterations = new Counter('cloud_window_iterations_started');
const enrolled = new Counter('cloud_enrollments');
const withdrawn = new Counter('cloud_withdrawals');
const full = new Counter('cloud_section_full');
const logins = new Counter('cloud_logins');
const traceMatches = new Counter('cloud_trace_echoes');
let traceSequence = 0;
let recoveryPending = false;
const bucketSeconds = 5;
const buckets = timed ? Array.from({ length: windowSeconds / bucketSeconds }, (_, bucket) => bucket) : [];

if (!origin?.startsWith('https://') || actors.length !== vus || ![5, 10, 50, 100, 200, 500, 1000, 1500, 2000, 2500, 5000].includes(vus)
    || !['connect-token', 'encrypted-tunnel'].includes(spec.transport)
    || Boolean(__ENV.SDU_CONNECT) !== (spec.transport === 'connect-token')
    || !__ENV.SDU_GATE_KEY || !['warmup', 'measure'].includes(phase)
    || (timed && ![30, 45, 60, 120].includes(windowSeconds))
    || (!timed && !['hot-section', 'login-burst'].includes(profile))) {
  throw new Error('Cloud benchmark configuration rejected');
}

export const options = {
  scenarios: { bench: openModel
    ? { executor: 'constant-arrival-rate', rate: spec.arrival_rate, timeUnit: '1s', preAllocatedVUs: vus,
        duration: `${windowSeconds}s`, gracefulStop: '20s' }
    : rampSeconds
    ? { executor: 'ramping-vus', startVUs: spec.ramp_start_vus ?? 50, stages: [
        ...(ladder ? ladderStages : [{ duration: `${rampSeconds}s`, target: vus }]),
        { duration: `${windowSeconds}s`, target: vus },
      ], gracefulStop: '20s' }
    : timed
    ? { executor: 'constant-vus', vus, duration: `${windowSeconds}s`, gracefulStop: '20s' }
    : { executor: 'per-vu-iterations', vus, iterations: 1, maxDuration: '30s', gracefulStop: '0s' } },
  systemTags: ['status', 'method', 'name', 'scenario', 'expected_response', 'error_code'],
  summaryTrendStats: ['p(50)', 'p(95)', 'p(99)', 'count'],
  thresholds: {
    cloud_safety_rate: [{ threshold: 'rate<0.05', abortOnFail: true, delayAbortEval: '5s' }],
    ...(ladder ? Object.fromEntries(ladderWindows.flatMap(([level]) => [
      [`cloud_ladder_requests{level:${level}}`, ['count>=0']], [`cloud_ladder_errors{level:${level}}`, ['count>=0']],
      [`cloud_ladder_iterations{level:${level}}`, ['count>=0']], [`cloud_ladder_latency{level:${level}}`, ['p(95)>=0']],
    ])) : {}),
    ...Object.fromEntries(categories.map((outcome) => [`cloud_outcomes{outcome:${outcome}}`, ['count>=0']])),
    ...Object.fromEntries(readRoutes.map((route) => [`cloud_successful_read{route:${route}}`, ['p(95)>=0']])),
    ...Object.fromEntries(buckets.flatMap((bucket) => [
      [`cloud_window_requests{bucket:${bucket}}`, ['count>=0']],
      [`cloud_window_unexpected{bucket:${bucket}}`, ['count>=0']],
      [`cloud_window_latency{bucket:${bucket}}`, ['p(95)>=0']],
    ])),
  },
};

function inWindow() {
  const elapsed = Date.now() - exec.scenario.startTime - rampSeconds * 1000;
  return !timed || (elapsed >= 0 && elapsed < windowSeconds * 1000);
}

function call(actor, method, path, name, expected, verify, key, form, attempt = 1) {
  const trace = `${phase === 'warmup' ? '10000000' : '20000000'}-0000-4000-8000-${(exec.vu.idInTest * 100000 + ++traceSequence).toString(16).padStart(12, '0')}`;
  const headers = {
    'X-SDU-Benchmark-Key': __ENV.SDU_GATE_KEY,
    Cookie: `SESSION=${actor.cookie}`, [actor.header]: actor.csrf,
    'X-Correlation-Id': trace,
  };
  if (__ENV.SDU_CONNECT) headers.Authorization = `Bearer ${__ENV.SDU_CONNECT}`;
  if (key) headers['Idempotency-Key'] = key;
  const started = Date.now();
  if (spec.reconcile && key) {
    const principal = actor.student.replace(/^80/, '81');
    console.log(`SDU_START ${trace} ${name} ${key} ${attempt} ${principal} ${path.split('/')[4]} ${started}`);
  }
  const response = http.request(method, origin + path, form ?? null, {
    headers, tags: { name }, redirects: 0, timeout: '4s',
  });
  let body;
  try { body = response.json(); } catch (_) { body = null; }
  let kind;
  if (response.status === 0) {
    kind = response.error_code === 1050 || /timeout/i.test(response.error ?? '') ? 'timeout' : 'transport';
  } else if (response.headers['X-Sdu-Gateway-Limit'] === '1' || response.headers['X-SDU-Gateway-Limit'] === '1') {
    kind = 'gateway_limit';
  } else if (response.status === 429) kind = 'rate_limited';
  else if (response.status === 503) kind = 'unavailable';
  else if ([403, 409].includes(response.status) && denied.has(body?.code)) kind = 'business_denial';
  else if (response.status === expected) kind = verify(body) ? 'success' : 'invalid_response';
  else kind = 'http_error';
  const acceptable = kind === 'success' || (kind === 'rate_limited' && profile === 'login-burst')
    || (kind === 'business_denial' && profile === 'hot-section' && body?.code === 'SECTION_FULL');
  const echoed = response.headers['X-Correlation-Id'] === trace;
  if (spec.reconcile && (key || !acceptable)) {
    const result = /^[A-Z][A-Z_]{1,63}$/.test(body?.code) ? body.code : '-';
    const stored = /^[a-f0-9-]{36}$/.test(body?.correlationId) ? body.correlationId : '-';
    const hop = response.headers['X-Sdu-Benchmark-Hop'];
    const source = ['backend', 'gateway-error'].includes(hop) ? hop : 'unattributed';
    const enrollment = kind === 'success' && /^[a-f0-9-]{36}$/.test(body?.enrollmentId) ? body.enrollmentId : '-';
    console.log(`SDU_RESULT ${trace} ${name} ${response.status} ${kind} ${source} ${started} ${Date.now()} ${result} ${stored} ${Number(!acceptable)} ${enrollment}`);
  }
  traceMatches.add(Number(echoed));
  if (!acceptable) {
    allUnexpected.add(1);
    // Keep only bounded diagnostics. Never record URLs, headers, payloads or account IDs.
    const hop = response.headers['X-Sdu-Benchmark-Hop'];
    const source = ['backend', 'gateway-error'].includes(hop) ? hop : 'unattributed';
    const protocol = ['HTTP/1.1', 'HTTP/2.0', 'HTTP/2'].includes(response.proto) ? response.proto : 'unknown';
    const server = (response.headers.Server || '').toLowerCase();
    const serverKind = ['envoy', 'modal', 'nginx', 'cloudflare'].includes(server) ? server : server ? 'other' : 'absent';
    const content = response.body?.trim();
    const signature = content === 'Bad Gateway' || content === '502 Bad Gateway' ? 'bad_gateway'
      : content === 'Backend unavailable' ? 'sdu_gateway'
      : content?.startsWith('upstream connect error or disconnect/reset before headers') ? 'upstream_reset' : 'other';
    const provider = ['X-Modal-Request-Id', 'X-Request-Id'].map((header) => response.headers[header] || '')
      .find((value) => /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value)) ?? '-';
    console.warn(`unexpected route=${name} method=${method} status=${response.status} outcome=${kind} source=${source} trace=${trace} started_ms=${started} ended_ms=${Date.now()} protocol=${protocol} server=${serverKind} signature=${signature} echoed=${echoed} provider=${provider}`);
  }
  safetyRate.add(!acceptable);
  const level = ladder && ladderLevel();
  if (level) {
    const tags = { level: String(level) };
    ladderRequests.add(1, tags);
    ladderErrors.add(Number(!acceptable), tags);
    if (response.status !== 0) ladderLatency.add(response.timings.duration, tags);
  }
  const elapsed = Date.now() - exec.scenario.startTime - rampSeconds * 1000;
  if (!timed || (elapsed >= 0 && elapsed < windowSeconds * 1000)) {
    const tags = timed ? { bucket: String(Math.floor(elapsed / (bucketSeconds * 1000))) } : undefined;
    requests.add(1, tags);
    windowUnexpected.add(Number(!acceptable), tags);
    outcomes.add(1, { outcome: kind });
    if (response.status !== 0) latency.add(response.timings.duration, tags);
    if (method === 'GET' && kind === 'success') successfulRead.add(response.timings.duration, { route: name });
  }
  return { kind, body };
}

function read(actor, i) {
  switch (i % 4) {
    case 0:
      if (spec.catalog_path) {
        return call(actor, 'GET', `/api/v1/courses?${term}`, 'catalog', 200,
          (body) => Array.isArray(body?.items) && body.items.length > 0);
      }
      return call(actor, 'GET', `/api/v1/courses/${fixture}6/sections?${term}`, 'sections', 200,
        (body) => Array.isArray(body?.sections) && body.sections.length === 21);
    case 1:
      return call(actor, 'GET', `/api/v1/me/schedule?${term}`, 'schedule', 200, (body) => Array.isArray(body?.enrollments));
    case 2:
      return call(actor, 'GET', `/api/v1/me/enrollments?${term}`, 'own_enrollments', 200, Array.isArray);
    default:
      return call(actor, 'GET', '/api/v1/me', 'me', 200, (body) => body?.studentId === actor.student);
  }
}

function write(actor, method, path, label, status, validate) {
  const key = uuid();
  let result = call(actor, method, path, label, status, validate, key);
  // A lost response may follow a commit. Repeat only the identical intent with its existing key.
  if (['timeout', 'transport', 'unavailable'].includes(result.kind)) {
    result = call(actor, method, path, label, status, validate, key, undefined, 2);
  }
  return result;
}

function enroll(actor, section) {
  return write(actor, 'POST', `/api/v1/sections/${section}/enrollments`, 'enroll', 201,
    (body) => body?.code === 'ENROLLED' && typeof body.enrollmentId === 'string' && body.sectionId === section);
}

export default function () {
  const iteration = exec.vu.iterationInScenario;
  if (iteration === 0 && timed && !openModel && spec.spread_start) sleep((exec.vu.idInTest - 1) * 1.25 / vus);
  const started = Date.now();
  iterationsStarted.add(1);
  const level = ladder && ladderLevel();
  if (level) ladderIterations.add(1, { level: String(level) });
  if (inWindow()) windowIterations.add(1);
  const actor = actors[exec.vu.idInTest - 1];
  if (iteration === 0) scenarioStart.add(exec.scenario.startTime);
  if (profile === 'login-burst') {
    const result = call(actor, 'POST', '/api/v1/auth/login', 'login', 200,
      (body) => body?.studentId === actor.student, null, { username: actor.login, password: __ENV.SDU_STUDENT_PASSWORD });
    if (result.kind === 'success') logins.add(1);
  } else if (profile === 'hot-section') {
    const result = enroll(actor, `${fixture}8`);
    if (result.kind === 'success') enrolled.add(1);
    if (result.kind === 'business_denial' && result.body?.code === 'SECTION_FULL') full.add(1);
  } else {
    const result = read(actor, iteration + exec.vu.idInTest);
    if (profile === 'mixed' && !recoveryPending && result.kind === 'success' && (iteration + exec.vu.idInTest) % 4 === 0) {
      const created = enroll(actor, spec.hot_mixed ? '90000000-0000-4000-8000-000000000001' : actor.section);
      if (created.kind === 'success') {
        enrolled.add(1);
        const id = created.body.enrollmentId;
        const removed = write(actor, 'DELETE', `/api/v1/enrollments/${id}`, 'withdraw', 200,
          (body) => body?.code === 'WITHDRAWN' && body.enrollmentId === id);
        if (removed.kind === 'success') withdrawn.add(1);
        else if (spec.reconcile) recoveryPending = true;
      } else if (spec.reconcile && created.kind !== 'business_denial') recoveryPending = true;
    }
    if (!openModel) sleep(Math.max(0, 1.25 - (Date.now() - started) / 1000));
  }
}

export function handleSummary(data) {
  return { [__ENV.SDU_SUMMARY]: JSON.stringify(data) };
}

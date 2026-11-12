import http from 'k6/http';
import { Counter, Rate, Trend } from 'k6/metrics';
import { randomBytes } from 'k6/crypto';

export const requests = new Counter('portal_requests');
export const unexpected = new Rate('portal_unexpected');
export const outcomes = new Counter('portal_outcomes');
export const apiLatency = new Trend('portal_api_latency', true);
export const enrollmentLatency = new Trend('portal_enrollment_latency', true);
export const enrollmentRequests = new Counter('portal_enrollment_requests');
export const enrollmentSuccesses = new Counter('portal_enrollment_successes');
export const businessDenials = new Counter('portal_business_denials');
export const networkErrors = new Counter('portal_network_errors');
export const timeouts = new Counter('portal_timeouts');
export const replayCount = new Counter('portal_replays');
const business = new Set(['SECTION_FULL', 'ALREADY_ENROLLED', 'CREDIT_LIMIT_EXCEEDED', 'SCHEDULE_CONFLICT', 'PREREQUISITE_NOT_MET', 'REGISTRATION_CLOSED', 'ACADEMIC_INELIGIBLE', 'COHORT_INELIGIBLE']);
export const base = __ENV.BASE_URL || 'http://127.0.0.1:18080';

export function uuid() {
  const bytes = new Uint8Array(randomBytes(16)); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, n => n.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
export function call(method, path, endpoint, session, key, body, form = false) {
  const headers = {};
  if (session) { headers.Cookie = `SESSION=${session.cookie}`; headers['X-CSRF-TOKEN'] = session.csrf; }
  if (key) headers['Idempotency-Key'] = key;
  if (body !== undefined) headers['Content-Type'] = form ? 'application/x-www-form-urlencoded' : 'application/json';
  const response = http.request(method, base + path, body === undefined ? null : body,
    {headers, tags: {name: endpoint}, timeout: '20s', redirects: 0});
  return observe(response, endpoint);
}

function observe(response, endpoint) {
  let value = {}; try { value = response.json() || {}; } catch (_) { }
  let result;
  if (response.status === 0) result = 'network';
  else if (response.status === 429) result = 'rate_limited';
  else if (response.status === 503) result = 'unavailable';
  else if (response.status >= 500) result = 'technical';
  else if (response.status >= 200 && response.status < 300) result = response.headers['Idempotency-Replayed'] === 'true' ? 'replay' : 'success';
  else if (business.has(value.code)) result = 'business_denial';
  else result = 'unexpected';
  const failed = !['success', 'replay', 'business_denial'].includes(result);
  requests.add(1, {endpoint}); unexpected.add(failed, {endpoint}); outcomes.add(1, {endpoint, result});
  apiLatency.add(response.timings.duration, {endpoint, result});
  if (response.status === 0) { networkErrors.add(1); if (/timeout/i.test(response.error || '')) timeouts.add(1); }
  if (result === 'business_denial') businessDenials.add(1, {code: value.code});
  if (result === 'replay') replayCount.add(1);
  if (endpoint === 'enroll') {
    enrollmentRequests.add(1); enrollmentLatency.add(response.timings.duration, {result});
    if (value.code === 'ENROLLED' && result !== 'replay') enrollmentSuccesses.add(1);
  }
  return {response, value, result};
}

export function login(account, password) {
  const jar = http.cookieJar(); jar.clear(base);
  const csrf = call('GET', '/api/v1/auth/csrf', 'csrf');
  const token = csrf.value;
  if (csrf.result !== 'success' || typeof token.token !== 'string') throw new Error('Synthetic CSRF setup failed');
  const result = http.post(base + '/api/v1/auth/login', {username: account.login, password}, {
    headers: {[token.headerName]: token.token}, tags: {name: 'login'}, timeout: '20s', redirects: 0,
  });
  const observed = observe(result, 'login');
  if (observed.result !== 'success') throw new Error(`Synthetic login failed: status ${result.status}`);
  const refreshed = call('GET', '/api/v1/auth/csrf', 'csrf');
  return {cookie: jar.cookiesForURL(base).SESSION[0], csrf: refreshed.value.token};
}

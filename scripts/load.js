import http from 'k6/http';
import { check } from 'k6';
import { Trend, Rate } from 'k6/metrics';
const latency = new Trend('navigation_ms', true);
const successful = new Trend('successful_navigation_ms',true);
const incorrect = new Rate('navigation_failed');
const users = Number(__ENV.USERS || 20);
export const options = { noCookiesReset:true, scenarios: { journeys: { executor: 'per-vu-iterations', vus: users, iterations: 3, maxDuration: '90s' } }, thresholds: { navigation_failed: ['rate==0'] } };
export default function () {
  // Fixed loopback origin prevents running a synthetic workload against a public API.
  const origin = 'http://127.0.0.1:'+Number(__ENV.LOAD_PORT || 18926);
  const url = origin+'/api/navigation';
  const initial = http.get(url);
  const res = http.post(url, JSON.stringify({action:'message',revision:initial.json('revision'),text:'Bus first, drive after 45 minutes.'}), {headers:{Origin:origin,'Content-Type':'application/json'}});
  latency.add(res.timings.duration);
  const ok = check(res, {'correct threshold': r => r.status === 200 && r.json('agent_plan.fallback_drive_over_minutes') === 45});
  if (ok) successful.add(res.timings.duration);
  incorrect.add(!ok);
}

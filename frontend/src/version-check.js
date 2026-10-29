import { conditionalFetch } from './transport.js';

const interval = 5 * 60 * 1000;
const endpoints = { futures: '/api/futures/v1/version', options: '/api/options/v1/version', seats: '/api/seats/v1/meta/status' };

export function watchVersion(module, report, env = globalThis, request = conditionalFetch) {
  let baseline, updated = false, stopped = false, timer, controller, last = -Infinity;
  const clear = () => env.clearTimeout(timer);
  const schedule = () => {
    clear();
    if (!stopped && !env.document.hidden) timer = env.setTimeout(check, Math.max(0, interval - (env.Date.now() - last)));
  };
  async function check() {
    if (stopped || env.document.hidden || controller) return;
    if (env.Date.now() - last < interval) { schedule(); return; }
    clear();
    last = env.Date.now();
    const current = controller = new AbortController();
    try {
      const response = await request(endpoints[module], { signal: current.signal });
      if (!response.ok) throw Error('version unavailable');
      const data = await response.json();
      if (stopped || current.signal.aborted) return;
      const value = module === 'seats' ? data.meta?.data_version : data.version;
      if (typeof value !== 'string' || !value) throw Error('version missing');
      baseline ??= value;
      updated = value !== baseline;
      report({ updated, failed: false });
    } catch {
      if (!stopped && !current.signal.aborted) report({ updated, failed: true });
    } finally {
      if (controller === current) { controller = null; schedule(); }
    }
  }
  const visibility = () => {
    if (env.document.hidden) { clear(); if (controller) { controller.abort(); controller = null; last = -Infinity; } }
    else check();
  };
  env.document.addEventListener('visibilitychange', visibility);
  env.addEventListener('focus', check);
  check();
  return () => {
    stopped = true; clear(); controller?.abort();
    env.document.removeEventListener('visibilitychange', visibility);
    env.removeEventListener('focus', check);
  };
}

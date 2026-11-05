// Delta bases stay in memory; full responses use the browser private HTTP cache.
const deltaType = 'dashboard-json-delta';
let activeRequests = 0, warmController;
const warmed = new Set();

function requestKey(url, options) {
 const parsed = new URL(url, globalThis.location?.href || 'https://dashboard.invalid/');
 parsed.hash = ''; parsed.searchParams.sort();
 return JSON.stringify([parsed.href, [...new Headers(options.headers)].sort(), options.credentials || 'same-origin', options.mode || 'cors', options.redirect || 'follow']);
}

export function applyDelta(before, ops) {
  let result = before;
  for (const [op, path, value] of ops) {
    if (!Array.isArray(path) || path.some(key => ['__proto__','constructor','prototype'].includes(key))) throw Error('Invalid delta path');
    if (!path.length) { if (op !== 'set') throw Error('Invalid root edit'); result = value; continue; }
    let target = result;
    for (const key of path.slice(0,-1)) {
      if (!target || !Object.hasOwn(target,key)) throw Error('Missing delta base');
      target = target[key];
    }
    const key = path.at(-1);
    if (!target || typeof target !== 'object' || Array.isArray(target) && (!Number.isInteger(key) || key < 0 || key > target.length)) throw Error('Invalid delta target');
    if (op === 'set') target[key] = value;
    else if (op === 'remove' && Object.hasOwn(target,key)) Array.isArray(target) ? target.splice(key,1) : delete target[key];
    else throw Error('Invalid delta operation');
  }
  return result;
}

export function createConditionalFetch(limit = 8 * 1024 * 1024) {
 const cache = new Map(), pending = new Map(); let bytes = 0;
 async function request(url, options, key, full) {
  const previous = full ? null : cache.get(key), headers = new Headers(options.headers);
  if (!previous) { headers.delete('If-None-Match'); headers.delete('A-IM'); }
  if (previous) { headers.set('If-None-Match', previous.tag); headers.set('A-IM', deltaType); }
  const response = await fetch(url, {...options, headers, cache:previous || full ? 'no-store' : 'no-cache'});
  if (response.status === 304 && !previous) {
    if (full) throw Error('Missing response body');
    return request(url, options, key, true);
  }
  if (!response.ok && response.status !== 304) return {text:await response.text(), status:response.status, headers:response.headers};
  const tag = response.headers.get('etag') || (response.status === 304 ? previous?.tag : null);
  let text;
  if (response.status === 304) text = previous.text;
  else if (response.status === 226) {
    try {
      const patch = await response.json();
      if (!previous || response.headers.get('im') !== deltaType || response.headers.get('delta-base') !== previous.tag || patch.base !== previous.tag || patch.version !== tag) throw Error('Delta base changed');
      text = JSON.stringify(applyDelta(JSON.parse(previous.text), patch.ops));
    } catch (error) {
      if (options.signal?.aborted || full) throw error;
      return request(url, options, key, true);
    }
  } else text = await response.text();
  if (options.signal?.aborted) throw options.signal.reason;
  const size = text.length * 2 + key.length * 2 + 256;
  if (cache.has(key)) { bytes -= cache.get(key).size; cache.delete(key); }
  if (tag && size <= limit) {
    while (cache.size && bytes + size > limit) { const first = cache.keys().next().value; bytes -= cache.get(first).size; cache.delete(first); }
    cache.set(key, {tag, text, size}); bytes += size;
  }
  const outputHeaders = new Headers(response.headers);
  for (const key of ['content-encoding','content-length','im','delta-base']) outputHeaders.delete(key);
  outputHeaders.set('content-type','application/json');
  return {text, status:200, headers:outputHeaders};
 }
 return function conditionalFetch(url, options = {}, full = false) {
  if (options.method && options.method.toUpperCase() !== 'GET') return fetch(url, options);
  if (options.signal?.aborted) return Promise.reject(options.signal.reason || new DOMException('请求已取消','AbortError'));
  const key = requestKey(url, options), flightKey = key + full;
  let entry = pending.get(flightKey);
  if (!entry) {
   warmController?.abort(); activeRequests++;
   const controller = new AbortController();
   entry = {controller, users:0};
   entry.promise = request(url, {...options, signal:controller.signal}, key, full).finally(() => {
    activeRequests--; if (pending.get(flightKey) === entry) pending.delete(flightKey);
   });
   pending.set(flightKey, entry);
  }
  entry.users++;
  return new Promise((resolve, reject) => {
   let finished = false;
   const finish = (error, result) => {
    if (finished) return;
    finished = true; options.signal?.removeEventListener('abort', abort); entry.users--;
    if (error) reject(error); else resolve(new Response(result.text, {status:result.status, headers:result.headers}));
   };
   const abort = () => {
    finish(options.signal.reason || new DOMException('请求已取消','AbortError'));
    if (!entry.users) { if (pending.get(flightKey) === entry) pending.delete(flightKey); entry.controller.abort(); }
   };
   options.signal?.addEventListener('abort', abort, {once:true});
   entry.promise.then(result => finish(null, result), error => finish(error));
  });
 };
}
export const conditionalFetch = createConditionalFetch();

// Warm code/entry documents only, one at a time; foreground API work wins.
export function warmNavigation(urls, env = globalThis) {
 const connection = env.navigator?.connection;
 const limited = () => connection?.saveData || /(^|-)2g|3g/.test(connection?.effectiveType || '');
 if (limited()) return () => {};
 const queue = [...new Set(urls.filter(Boolean).map(url => new URL(url, env.location.href).href))].filter(url => new URL(url).origin === env.location.origin && !warmed.has(url)).slice(0,5);
 let stopped = false, timer, idle, controller, index = 0;
 const busy = () => env.document.hidden || env.document.readyState !== 'complete' || activeRequests || env.document.querySelector('[aria-busy="true"]');
 const schedule = () => { if (!stopped && index < queue.length) timer = env.setTimeout(() => { if (limited()) return; if (busy()) schedule(); else idle = env.requestIdleCallback ? env.requestIdleCallback(run) : env.setTimeout(run,0); },2000); };
 async function run() {
  if (stopped) return;
  if (limited()) return;
  if (busy()) { schedule(); return; }
  controller = new AbortController(); warmController = controller;
  try {
   const response = await env.fetch(queue[index], {signal:controller.signal, priority:'low', cache:new URL(queue[index]).pathname.endsWith('.js')?'force-cache':'no-cache'});
   await response.arrayBuffer(); if (response.ok && !controller.signal.aborted) warmed.add(queue[index]); index++;
  } catch { if (!controller.signal.aborted) index++; }
  finally { if (warmController === controller) warmController = null; schedule(); }
 }
 const visibility = () => { if (env.document.hidden) controller?.abort(); };
 env.document.addEventListener('visibilitychange',visibility); schedule();
 return () => { stopped = true; env.clearTimeout(timer); if (env.cancelIdleCallback) env.cancelIdleCallback(idle); else env.clearTimeout(idle); controller?.abort(); env.document.removeEventListener('visibilitychange',visibility); };
}

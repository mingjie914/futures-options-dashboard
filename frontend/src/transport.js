// Transport-only memory cache: never persisted in sessionStorage or localStorage.
const deltaType = 'dashboard-json-delta';

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
 const cache = new Map(); let bytes = 0;
 return async function conditionalFetch(url, options = {}, full = false) {
  if (options.method && options.method.toUpperCase() !== 'GET') return fetch(url, options);
  const previous = full ? null : cache.get(url), headers = new Headers(options.headers);
  if (full) { headers.delete('If-None-Match'); headers.delete('A-IM'); }
  if (previous) { headers.set('If-None-Match', previous.tag); headers.set('A-IM', deltaType); }
  const response = await fetch(url, {...options, headers, cache:'no-store'});
  if (response.status === 304 && !previous) {
    if (full) throw Error('Missing response body');
    return conditionalFetch(url, options, true);
  }
  if (!response.ok && response.status !== 304) return response;
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
      return conditionalFetch(url, options, true);
    }
  } else text = await response.text();
  const size = text.length * 2 + String(url).length * 2 + 256;
  if (cache.has(url)) { bytes -= cache.get(url).size; cache.delete(url); }
  if (tag && size <= limit) {
    while (cache.size && bytes + size > limit) { const first = cache.keys().next().value; bytes -= cache.get(first).size; cache.delete(first); }
    cache.set(url, {tag, text, size}); bytes += size;
  }
  const outputHeaders = new Headers(response.headers);
  for (const key of ['content-encoding','content-length','im','delta-base']) outputHeaders.delete(key);
  outputHeaders.set('content-type','application/json');
  return new Response(text, {status:200, headers:outputHeaders});
}

}
export const conditionalFetch = createConditionalFetch();

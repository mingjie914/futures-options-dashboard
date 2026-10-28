/**
 * Build the un-fitted, quoted volatility surface summary for each expiry.
 *
 * `iv` is represented as a decimal (for example, 0.25 for 25%).  The
 * returned skew is in volatility percentage points.
 */

const CONTRACT_SIDES = new Set(['C', 'P']);

function finiteNumber(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function positiveFinite(value) {
  const number = finiteNumber(value);
  return number !== null && number > 0 ? number : null;
}

function validQuote(row) {
  if (!row || !CONTRACT_SIDES.has(row.cp)) return false;
  const bid = positiveFinite(row.bid);
  const ask = positiveFinite(row.ask);
  const iv = positiveFinite(row.iv);
  const t = positiveFinite(row.t);
  if (bid === null || ask === null || iv === null || t === null || ask < bid) return false;
  const mid = (bid + ask) / 2;
  return (ask - bid) / mid <= 0.2;
}

function monthForward(monthRows, month) {
  const fromMonth = positiveFinite(month?.forward);
  if (fromMonth !== null) return fromMonth;
  const row = monthRows.find((candidate) => positiveFinite(candidate?.forward) !== null);
  return row ? positiveFinite(row.forward) : null;
}

function interpolate(points, target, valueKey) {
  const exact = points.find((point) => finiteNumber(point?.value) === target);
  if (exact) return finiteNumber(exact[valueKey]);
  const usable = points
    .filter((point) => finiteNumber(point?.[valueKey]) !== null && finiteNumber(point?.value) !== null)
    .map((point) => ({ x: point.value, y: point[valueKey] }))
    .sort((left, right) => left.x - right.x);
  if (!usable.length) return null;

  for (const point of usable) {
    if (point.x === target) return point.y;
  }
  const lower = usable.filter((point) => point.x < target).at(-1);
  const upper = usable.find((point) => point.x > target);
  if (!lower || !upper) return null;
  return lower.y + (upper.y - lower.y) * ((target - lower.x) / (upper.x - lower.x));
}

function interpolateDelta(rows, cp, target) {
  const points = rows
    .filter((row) => row.cp === cp && validQuote(row))
    .map((row) => ({ value: finiteNumber(row.delta), iv: finiteNumber(row.iv) }))
    .filter((point) => point.value !== null && point.iv !== null);
  return interpolate(points, target, 'iv');
}

function buildPoints(rows, forward) {
  if (forward === null) return [];
  const byStrike = new Map();
  for (const row of rows) {
    const strike = positiveFinite(row?.strike);
    if (strike === null || !CONTRACT_SIDES.has(row?.cp)) continue;
    if (!byStrike.has(strike)) byStrike.set(strike, new Map());
    const sideRows = byStrike.get(strike);
    if (!sideRows.has(row.cp)) sideRows.set(row.cp, row);
  }

  return [...byStrike.entries()]
    .sort(([left], [right]) => left - right)
    .map(([strike, sideRows]) => {
      const cp = strike < forward ? 'P' : 'C';
      const row = sideRows.get(cp);
      return {
        x: strike / forward,
        iv: validQuote(row) ? row.iv : null,
        cp,
        strike,
      };
    })
}

function monthMetrics(month, rows) {
  const forward = monthForward(rows, month);
  const points = buildPoints(rows, forward);
  const atm = interpolate(
    points.map((point) => ({ value: point.x, iv: point.iv })),
    1,
    'iv',
  );
  const call25 = interpolateDelta(rows, 'C', 0.25);
  const put25 = interpolateDelta(rows, 'P', -0.25);
  const validCount = points.reduce((count, point) => count + (point.iv === null ? 0 : 1), 0);
  return {
    id: month?.id ?? null,
    code: month?.code ?? null,
    calendar_days: month?.calendar_days ?? null,
    trading_days: month?.trading_days ?? null,
    forward,
    atm_iv: atm,
    call25_iv: call25,
    put25_iv: put25,
    skew25_pp: call25 === null || put25 === null ? null : (put25 - call25) * 100,
    points,
    valid_count: validCount,
  };
}

export function surfaceMetrics(rows, months) {
  const inputRows = Array.isArray(rows) ? rows : [];
  const inputMonths = Array.isArray(months) ? months : [];
  const grouped = new Map();
  for (const row of inputRows) {
    const key = String(row?.month);
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(row);
  }
  return inputMonths.map(month => monthMetrics(month, grouped.get(String(month?.id)) || []));
}

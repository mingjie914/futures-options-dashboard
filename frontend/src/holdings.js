// Compare only matching contracts within the same trading day; missing stays missing.
export function holdingRows(rows, previous, basis) {
  if (basis !== 'snapshot') return rows;
  const old = new Map((previous?.rows || []).map(r => [r.code, r]));
  return rows.map(row => {
    const before = old.get(row.code);
    const oi_change = Number.isFinite(row.oi) && Number.isFinite(before?.oi) ? row.oi - before.oi : null;
    const deltaVolume = Number.isFinite(row.volume) && Number.isFinite(before?.volume) ? row.volume - before.volume : null;
    return {...row, oi_change, volume: deltaVolume !== null && deltaVolume >= 0 ? deltaVolume : null};
  });
}

// Sum contracts before dividing; a missing observation is not a zero.
export function putCallRatio(rows, field, expectedCount = rows.length) {
  let call = 0, put = 0, calls = 0, puts = 0, covered = 0;
  for (const row of rows) {
    const value = row[field];
    if (!['C', 'P'].includes(row.cp) || !Number.isFinite(value) || value < 0) continue;
    covered++;
    if (row.cp === 'C') { call += value; calls++; }
    else { put += value; puts++; }
  }
  const total = Math.max(rows.length, Number.isFinite(expectedCount) ? expectedCount : 0);
  const reason = covered < total ? '字段不完整' : !calls || !puts ? '缺少 Call 或 Put 合约' : call === 0 ? 'Call 合计为 0' : '';
  return { call: calls ? call : null, put: puts ? put : null, covered, total, reason, ratio: reason ? null : put / call };
}

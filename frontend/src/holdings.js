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

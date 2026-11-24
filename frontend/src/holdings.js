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

const formatQuantity = new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 0});

export const OPTION_PRODUCTS = {IO: '沪深300', HO: '上证50', MO: '中证1000'};
export const OPTION_SIDES = [['P', '认沽', '#2b8167'], ['C', '认购', '#b64d49']];
const TOOLTIP_SIDES = [...OPTION_SIDES].reverse();

export const OPTION_IV_CURVE = {smooth: 0.25, smoothMonotone: 'x', connectNulls: false};

export const OPTION_STRIKE_TOOLTIP = {
  trigger: 'axis', axisPointer: {type: 'line'}, confine: true, renderMode: 'html',
  backgroundColor: '#fff', borderColor: '#e1e5ea', borderWidth: 1, shadowBlur: 0,
  padding: [4, 6], transitionDuration: 0, textStyle: {color: '#455161', fontSize: 11},
  extraCssText: 'white-space:normal;line-height:1.35;max-width:min(210px,calc(100% - 12px));',
};

export function optionStrikeTooltip(items, legs) {
  const strike = items[0]?.axisValue;
  if (!Number.isFinite(strike)) return '';
  const plotted = new Map();
  for (const {seriesId, value} of items) {
    if (value?.[0] === strike && Number.isFinite(value[1])) plotted.set(seriesId, value[1]);
  }
  const current = plotted.get('iv-current') ?? plotted.get('iv');
  const previous = plotted.get('iv-previous');
  const hasIv = Number.isFinite(current);
  const difference = hasIv && Number.isFinite(previous) ? Number((current - previous).toFixed(2)) : null;
  const iv = hasIv ? ` · IV ${current.toFixed(2)}%` : '';
  const dayChange = difference === null ? '' : ` (${difference > 0 ? '+' : ''}${difference.toFixed(2)}%)`;
  const lines = [];
  for (const [side, name, color] of TOOLTIP_SIDES) {
    const {oi, oi_change: change, delta} = legs?.[side] || {};
    const parts = [];
    const holding = plotted.has(side + 'oi') || plotted.has(side + 'base') ||
      plotted.has(side + 'added') || plotted.has(side + 'removed');
    if (holding && Number.isFinite(oi)) {
      const increase = Number.isFinite(change) ? ` (${change > 0 ? '+' : ''}${formatQuantity.format(change)})` : '';
      parts.push(formatQuantity.format(oi) + increase);
      if (Number.isFinite(delta)) parts.push(`Δ${delta.toFixed(2)}`);
    }
    const volume = plotted.get(side + 'volume');
    if (Number.isFinite(volume)) parts.push(`成交 ${formatQuantity.format(volume)}`);
    if (parts.length) lines.push(`<div style="color:${color}">${name} ${parts.join(' ')}</div>`);
  }
  return hasIv || lines.length ? `<div><span aria-hidden="true">▸</span> ${strike}${iv}${dayChange}</div>${lines.join('')}` : '';
}

export function optionSmile(rows,spot) {
  let atm=null;
  if(Number.isFinite(spot))for(const r of rows)if(r.C&&r.P&&(!atm||Math.abs(r.strike-spot)<Math.abs(atm.strike-spot)))atm=r;
  const valid=v=>Number.isFinite(v)&&v>0;
  return rows.map(r=>{
    const c=r.C?.iv,p=r.P?.iv,iv=!Number.isFinite(spot)?null:r===atm?(valid(c)&&valid(p)?(c+p)/2:null):r.strike<spot?p:c;
    return [r.strike,valid(iv)?iv:null];
  });
}

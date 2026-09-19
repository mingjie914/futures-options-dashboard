// Rule-based comparison templates; prices and P&L come from the snapshot API.
export function buildCandidates(rows, monthId, spot, target, maxSpread = .2, minSize = 1) {
  if (!Number.isFinite(spot) || spot <= 0) return [];
  const calls = rows.filter(r => String(r.month) === String(monthId) && r.cp === 'C'
    && r.bid > 0 && r.ask >= r.bid && Number.isFinite(r.iv) && r.t > 0
    && (r.ask - r.bid) / ((r.ask + r.bid) / 2) <= maxSpread
    && r.bid_size >= minSize && r.ask_size >= minSize)
    .sort((a, b) => a.strike - b.strike);
  if (!calls.length) return [];
  const nearest = (items, value) => items.reduce((best, row) =>
    Math.abs(row.strike - value) < Math.abs(best.strike - value) ? row : best);
  const atm = nearest(calls, spot);
  const lower = calls.filter(r => r.strike < atm.strike && r.strike < spot);
  const itm = lower.length ? lower[lower.length - 1] : null;
  const higher = calls.filter(r => r.strike > atm.strike);
  const cap = higher.length ? nearest(higher, Number.isFinite(target) && target > atm.strike ? target : higher[0].strike) : null;
  const leg = (row, side = 'buy') => ({ code: row.code, side, quantity: 1 });
  return [
    { id: 'atm', kind: 'buyer', name: '平值附近买 Call', description: '方向与波动率双重敞口；横盘和 IV 回落会拖累表现。', legs: [leg(atm)] },
    ...(itm ? [{ id: 'itm', kind: 'buyer', name: '实值买 Call', description: '偏重上涨方向；投入权利金通常更高，仍有时间损耗。', legs: [leg(itm)] }] : []),
    ...(cap ? [{ id: 'spread', kind: 'spread', name: '牛市 Call 价差', description: '卖出较高执行价 Call 抵减成本；上涨收益封顶。', legs: [leg(atm), leg(cap, 'sell')] }] : []),
  ];
}

// Same maximum-loss budget, integer groups only; respect visible top-of-book size.
export function budgetGroups(result, candidate, rows, budget) {
  if (!Number.isFinite(result?.max_loss) || result.max_loss <= 0 || !Number.isFinite(budget) || budget <= 0) return 0;
  const byCode = new Map(rows.map(r => [r.code, r]));
  const depth = candidate.legs.map(leg => {
    const row = byCode.get(leg.code);
    return Math.floor((leg.side === 'buy' ? row?.ask_size : row?.bid_size) / leg.quantity);
  });
  if (depth.some(n => !Number.isFinite(n) || n < 1)) return 0;
  return Math.max(0, Math.min(10000, Math.floor(budget / result.max_loss), ...depth));
}

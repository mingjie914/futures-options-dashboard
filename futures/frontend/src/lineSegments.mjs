export function holdingLeaders(contracts) {
  const largest = after => contracts.reduce((best,r) => r.symbol>after && Number.isFinite(r.position) && r.position>0 && (!best || r.position>best.position || r.position===best.position && r.symbol<best.symbol) ? r : best, null)?.symbol || '';
  const activeContract=largest('');
  return {activeContract,secondaryContract:activeContract?largest(activeContract):''};
}

// Calendar padding is skipped, but an explicit missing observation breaks a line.
export function inventoryHistory(points, end = '', years = 5) {
  const all = points.filter(p => !end || p.date <= end).slice().sort((a,b) => a.date.localeCompare(b.date));
  const firstYear = Number(all.at(-1)?.date.slice(0,4)) - years + 1;
  const rows = all.map((p,i) => {
    if (i && p.date === all[i-1].date) throw Error('同一序列存在重复日期');
    const value = Number.isFinite(p.value) ? p.value : null, prev = all[i-1];
    return {...p, value, previous_date: prev?.date, change: value != null && Number.isFinite(prev?.value) ? value - prev.value : null};
  }).filter(p => !years || Number(p.date.slice(0,4)) >= firstYear);
  const lines = Object.entries(Object.groupBy(rows,p=>p.date.slice(0,4))).map(([year,points])=>({year:Number(year),label:year,points:points.map(p=>({x:p.date.slice(5),trade_date:p.date,value:p.value}))}));
  const axis = Array.from({length:366},(_,i)=>new Date(Date.UTC(2000,0,i+1)).toISOString().slice(5,10));
  return {rows, lines, axis};
}

// Several segments share one legend name/color; no interpolated observations.
export function seasonalSelection(lines, showAll=false) {
  const years = [...new Set(lines.map(l => l.year))].sort((a,b) => a-b);
  const visible = new Set(showAll ? years : years.slice(-5));
  return Object.fromEntries(lines.map(l => [l.label, visible.has(l.year)]));
}

export function lineSegments(axis, lines) {
  const index = new Map(axis.map((x, i) => [x, i]));
  return lines.flatMap((line, lineIndex) => {
    const segments = [];
    let points = [];
    const flush = () => {
      if (!points.length) return;
      const data = Array(axis.length).fill(null);
      points.forEach(p => { data[index.get(p.x)] = p.value; });
      segments.push({name: line.label, lineIndex, count: points.length, data});
      points = [];
    };
    for (const point of line.points) {
      if (!index.has(point.x)) continue;
      if (point.value == null || !Number.isFinite(point.value)) flush();
      else points.push(point);
    }
    flush();
    return segments;
  });
}

// Resolve through the rendered segment and original axis index, not display names.
// ECharts can return '-' for nulls and several segments with the same legend name.
export function tooltipRows(params, axis, segments, byLine) {
  const list = Array.isArray(params) ? params : [params];
  const target = list[0]?.axisValue ?? axis[list[0]?.dataIndex];
  const seen = new Set();
  return list.flatMap(p => {
    const segment = segments[p.seriesIndex];
    const x = axis[p.dataIndex];
    if (!segment || x !== target || !Number.isFinite(segment.data[p.dataIndex]) || seen.has(segment.lineIndex)) return [];
    const point = byLine[segment.lineIndex]?.get(x);
    if (!point || !Number.isFinite(point.value)) return [];
    seen.add(segment.lineIndex);
    return [{name: segment.name, lineIndex: segment.lineIndex, point, marker: p.marker || ''}];
  });
}

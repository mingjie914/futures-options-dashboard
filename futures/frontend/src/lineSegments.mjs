// Calendar padding is skipped, but an explicit missing observation breaks a line.
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
      segments.push({name: line.label, lineIndex, data});
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

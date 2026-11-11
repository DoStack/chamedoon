/* Pulse charts: each chart is a JSON spec rendered with ECharts. Every chart has a hover tooltip; a spec with links opens them on click. */
(function () {
  const specs = JSON.parse(document.getElementById('charts').textContent || '{}');
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const fmt = (n) => (n == null ? '—' : Number(n).toLocaleString('en-US'));
  const pal = () => ['--s1', '--s2', '--s3', '--s4', '--s5', '--s6'].map(css);
  const M = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const lbl = (x) => (/^\d{4}-\d{2}-\d{2}$/.test(x) ? M[+x.slice(5, 7)] + ' ' + +x.slice(8, 10) : x);
  const tip = (t) => ({
    trigger: t, confine: true, backgroundColor: css('--surface'), borderColor: css('--line'),
    textStyle: { color: css('--ink'), fontSize: 12 }, axisPointer: { type: 'shadow', shadowStyle: { color: css('--line2') } },
  });
  const ax = () => ({
    axisLine: { lineStyle: { color: css('--line') } }, axisTick: { show: false },
    axisLabel: { color: css('--muted'), fontSize: 11 }, splitLine: { lineStyle: { color: css('--line2') } },
  });
  const base = () => ({
    textStyle: { fontFamily: css('--sans'), color: css('--ink2') }, color: pal(),
    grid: { left: 8, right: 14, top: 34, bottom: 8, containLabel: true }, tooltip: tip('axis'),
    legend: { top: 0, right: 0, icon: 'roundRect', itemWidth: 10, itemHeight: 10, textStyle: { color: css('--muted'), fontSize: 12 } },
  });
  const go = (url) => {
    if (!url) return;
    if (/^https?:/.test(url)) window.open(url, '_blank', 'noopener');
    else location.href = url;
  };
  const colorOf = (s, i) => (s.color ? css(s.color) : pal()[i % 6]);
  const pct = (a, c) => (c ? Math.round((a * 100) / c) + '%' : '—');

  const build = {
    bars(s) {
      const b = base();
      const many = s.series.length > 1;
      return Object.assign(b, {
        legend: Object.assign(b.legend, { show: many }),
        xAxis: Object.assign({ type: 'category', data: s.x.map(lbl), name: s.xname || '', nameTextStyle: { color: css('--muted') } }, ax(), { splitLine: { show: false } }),
        yAxis: Object.assign({ type: 'value', splitNumber: 3 }, ax(), { axisLine: { show: false } }),
        series: s.series.map((x, i) => ({
          type: 'bar', name: x.name, stack: s.stack ? 'a' : undefined, barMaxWidth: 28, barGap: '10%',
          itemStyle: { color: many || !s.color ? colorOf(x, i) : css(s.color), borderRadius: s.stack ? 0 : [3, 3, 0, 0] },
          data: s.warnBelow != null ? x.data.map((v, j) => ({ value: v, itemStyle: { color: css(j < s.warnBelow ? '--warn' : '--s3') } })) : x.data,
        })),
      });
    },
    hbars(s) {
      const b = base();
      const many = s.series.length > 1;
      return Object.assign(b, {
        legend: Object.assign(b.legend, { show: many }),
        grid: { left: 8, right: 52, top: many ? 34 : 8, bottom: 8, containLabel: true },
        xAxis: Object.assign({ type: s.log ? 'log' : 'value', min: s.log ? 1 : undefined, splitNumber: 3 }, ax(), { axisLine: { show: false }, show: many }),
        yAxis: {
          type: 'category', inverse: true, data: s.y, axisLine: { show: false }, axisTick: { show: false },
          axisLabel: { color: css('--ink2'), fontSize: s.mono ? 11 : 12, fontFamily: s.mono ? css('--mono') : css('--sans'), width: 220, overflow: 'truncate' },
        },
        series: s.series.map((x, i) => ({
          type: 'bar', name: x.name, stack: s.stack ? 'a' : undefined, barMaxWidth: 22,
          itemStyle: { color: many || !s.color ? colorOf(x, i) : css(s.color), borderRadius: s.stack ? 0 : [0, 3, 3, 0] },
          label: { show: !many, position: 'right', color: css('--muted'), fontSize: 11, formatter: (p) => fmt(p.value) },
          data: x.data.map((v) => (s.log && !v ? null : v)),
        })),
      });
    },
    line(s) {
      const b = base();
      return Object.assign(b, {
        legend: Object.assign(b.legend, { show: s.series.length > 1 }),
        xAxis: Object.assign({ type: 'category', data: s.x.map(lbl) }, ax(), { splitLine: { show: false } }),
        yAxis: Object.assign({ type: 'value', splitNumber: 3 }, ax(), { axisLine: { show: false } }),
        series: s.series.map((x, i) => ({
          type: 'line', name: x.name, data: x.data, smooth: 0.25, symbolSize: 6, lineStyle: { width: 2 }, connectNulls: false,
          areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: colorOf(x, i) + '40' }, { offset: 1, color: colorOf(x, i) + '00' }] } },
        })),
      });
    },
    funnel(s) {
      const b = base();
      const top = s.steps[0][1] || 0;
      return Object.assign(b, {
        legend: { show: false },
        grid: { left: 8, right: 92, top: 8, bottom: 8, containLabel: true },
        tooltip: Object.assign(tip('item'), {
          formatter: (p) => {
            const prev = p.dataIndex ? s.steps[p.dataIndex - 1][1] : top;
            return `<b>${p.name}</b><br>${fmt(p.value)}<br>${pct(p.value, top)} of first step · ${pct(p.value, prev)} of previous`;
          },
        }),
        xAxis: { type: 'value', show: false, max: top || 1 },
        yAxis: { type: 'category', inverse: true, data: s.steps.map((x) => x[0]), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: css('--ink2'), fontSize: 12 } },
        series: [{
          type: 'bar', data: s.steps.map((x) => x[1]), barWidth: '62%', itemStyle: { color: css(s.color || '--s1'), borderRadius: 4 },
          showBackground: true, backgroundStyle: { color: css('--line2'), borderRadius: 4 },
          label: { show: true, position: 'right', color: css('--ink'), fontSize: 12, formatter: (p) => `${fmt(p.value)}  ${pct(p.value, top)}` },
        }],
      });
    },
    donut(s) {
      const b = base();
      return Object.assign(b, {
        tooltip: Object.assign(tip('item'), { formatter: '{b}: {c} ({d}%)' }),
        legend: { bottom: 0, top: 'auto', left: 'center', textStyle: { color: css('--muted') } },
        series: [{
          type: 'pie', radius: ['48%', '72%'], center: ['50%', '44%'], itemStyle: { borderColor: css('--surface'), borderWidth: 2 },
          label: { color: css('--ink2'), formatter: '{c}' }, data: s.data.filter((d) => d.value),
        }],
      });
    },
    sankey(s) {
      const b = base();
      const color = (n, i) => (/^Rule/.test(n) ? css('--s6') : /^AI:/.test(n) ? css('--s5') : n === 'Accepted' ? css('--s3') : pal()[i % 6]);
      return Object.assign(b, {
        tooltip: Object.assign(tip('item'), {
          formatter: (p) => (p.dataType === 'edge' ? `${p.data.source} → ${p.data.target}<br><b>${fmt(p.data.value)}</b> posts` : `<b>${p.name}</b><br>${fmt(p.value)} posts`),
        }),
        series: [{
          type: 'sankey', left: 8, right: 190, top: 8, bottom: 8, nodeWidth: 12, nodeGap: 12, draggable: false, emphasis: { focus: 'adjacency' },
          label: { color: css('--ink2'), fontSize: 12, formatter: (p) => `${p.name}  ${fmt(p.value)}` },
          lineStyle: { color: 'gradient', opacity: 0.35, curveness: 0.5 },
          data: s.nodes.map((n, i) => Object.assign({ itemStyle: { color: color(n.name, i) } }, n)), links: s.links,
        }],
      });
    },
  };

  const charts = [];
  function draw() {
    charts.forEach((c) => c.dispose());
    charts.length = 0;
    Object.entries(specs).forEach(([id, s]) => {
      const el = document.getElementById('ch-' + id);
      if (!el) return;
      if (s.missing) {
        el.innerHTML = '<div class="missing">PostHog did not answer. It retries on the next refresh.</div>';
        return;
      }
      const c = echarts.init(el, null, { renderer: 'svg' });
      c.setOption(build[s.type](s));
      c.on('click', (p) => {
        if (s.type === 'sankey') return go((s.nodeLinks || {})[p.dataType === 'edge' ? p.data.target : p.name]);
        if (s.type === 'donut') return go((s.data.find((d) => d.name === p.name) || {}).link);
        if (s.links) go(s.links[p.dataIndex]);
      });
      if (s.links || s.nodeLinks || (s.data || []).some((d) => d.link)) el.style.cursor = 'pointer';
      charts.push(c);
    });
  }
  draw();
  addEventListener('resize', () => charts.forEach((c) => c.resize()));
  try {
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => setTimeout(draw, 50));
  } catch (e) { /* old browser */ }

  // Refresh every 60 s while the page is visible and nobody is typing.
  if (document.body.dataset.refresh === '1') {
    setInterval(() => {
      const tag = (document.activeElement || {}).tagName;
      if (!document.hidden && !['INPUT', 'SELECT', 'TEXTAREA'].includes(tag)) location.reload();
    }, 60000);
  }
})();

/* Date range quick picks (Toronto dates as the browser shows them). */
(function () {
  const pad = (n) => String(n).padStart(2, '0');
  const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  document.querySelectorAll('.dr-quick [data-q]').forEach((b) => b.addEventListener('click', () => {
    const form = b.closest('form');
    const t = new Date();
    let from = new Date(t), to = new Date(t);
    const q = b.dataset.q;
    if (q === 'yesterday') { from.setDate(t.getDate() - 1); to = new Date(from); }
    if (q === 'week') { from.setDate(t.getDate() - ((t.getDay() + 6) % 7)); }
    if (q === 'month') { from = new Date(t.getFullYear(), t.getMonth(), 1); }
    if (q === 'lastmonth') { from = new Date(t.getFullYear(), t.getMonth() - 1, 1); to = new Date(t.getFullYear(), t.getMonth(), 0); }
    form.elements.from.value = iso(from);
    form.elements.to.value = iso(to);
    form.submit();
  }));
  document.addEventListener('click', (e) => {
    document.querySelectorAll('details.dr[open]').forEach((d) => { if (!d.contains(e.target)) d.removeAttribute('open'); });
  });
})();

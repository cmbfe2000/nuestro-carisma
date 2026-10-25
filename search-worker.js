// Full-text search worker: accent- and case-insensitive, length-preserving folding
const MARKS = /[̀-ͯ]/g;
const cache = new Map();
function foldChar(c) {
  let f = cache.get(c);
  if (f === undefined) {
    f = c.toLowerCase().normalize('NFD').replace(MARKS, '');
    if (f.length !== 1) f = c.toLowerCase().length === 1 ? c.toLowerCase() : c;
    cache.set(c, f);
  }
  return f;
}
function fold(s) {
  const a = s.toLowerCase().normalize('NFD').replace(MARKS, '');
  if (a.length === s.length) return a;
  let o = '';
  for (let i = 0; i < s.length; i++) o += foldChar(s[i]);
  return o;
}

let DOCS = null; // [{id, t, x, ft, fx}]
let loading = null;

function load(url) {
  if (!loading) loading = fetch(url).then(r => r.json()).then(arr => {
    DOCS = arr.map(([id, t, x]) => ({ id, t, x, ft: fold(t), fx: fold(x) }));
    postMessage({ type: 'ready', n: DOCS.length });
  });
  return loading;
}

function parse(q) {
  q = q.replace(/[“”«»„]/g, '"');
  const phrases = [], terms = [], neg = [];
  q = q.replace(/"([^"]+)"/g, (_, p) => { const f = fold(p.trim()); if (f) phrases.push(f); return ' '; });
  for (let w of q.split(/\s+/)) {
    if (!w) continue;
    let n = false;
    if (w[0] === '-' && w.length > 1) { n = true; w = w.slice(1); }
    w = fold(w).replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}*]+$/gu, '');
    if (!w) continue;
    (n ? neg : terms).push(w);
  }
  return { phrases, terms, neg };
}

function countOcc(hay, needle, cap) {
  let n = 0, i = 0;
  while ((i = hay.indexOf(needle, i)) !== -1) { n++; i += needle.length; if (n >= cap) break; }
  return n;
}

function snippet(d, needles) {
  let pos = -1, len = 0;
  for (const n of needles) {
    const p = d.fx.indexOf(n);
    if (p !== -1 && (pos === -1 || p < pos)) { pos = p; len = n.length; }
  }
  if (pos === -1) return { s: d.x.slice(0, 240), h: [] };
  let a = Math.max(0, pos - 90), b = Math.min(d.x.length, pos + len + 170);
  if (a > 0) { const sp = d.x.indexOf(' ', a); if (sp !== -1 && sp < pos) a = sp + 1; }
  if (b < d.x.length) { const sp = d.x.lastIndexOf(' ', b); if (sp > pos + len) b = sp; }
  const seg = d.x.slice(a, b), fseg = d.fx.slice(a, b);
  const h = [];
  for (const n of needles) {
    let i = 0;
    while ((i = fseg.indexOf(n, i)) !== -1) { h.push([i, i + n.length]); i += n.length; }
  }
  h.sort((x, y) => x[0] - y[0]);
  return { s: (a > 0 ? '… ' : '') + seg + (b < d.x.length ? ' …' : ''), h: h.map(([x, y]) => a > 0 ? [x + 2, y + 2] : [x, y]) };
}

function search(q, titlesOnly) {
  const { phrases, terms, neg } = parse(q);
  const needles = [...phrases, ...terms];
  if (!needles.length) return { results: [], total: 0, needles };
  const res = [];
  for (const d of DOCS) {
    let ok = true, score = 0;
    for (const n of needles) {
      const inT = d.ft.includes(n);
      const inX = !titlesOnly && d.fx.includes(n);
      if (!inT && !inX) { ok = false; break; }
      if (inT) score += 40;
      if (inX) score += Math.min(countOcc(d.fx, n, 60), 60);
    }
    if (!ok) continue;
    for (const n of neg) if (d.ft.includes(n) || d.fx.includes(n)) { ok = false; break; }
    if (!ok) continue;
    if (needles.length > 1 && !phrases.length) {
      const joined = terms.join(' ');
      if (d.ft.includes(joined)) score += 80; else if (d.fx.includes(joined)) score += 25;
    }
    res.push({ id: d.id, score, d });
  }
  res.sort((a, b) => b.score - a.score);
  const out = res.slice(0, 300).map(r => ({ id: r.id, score: r.score, ...snippet(r.d, needles) }));
  return { results: out, total: res.length, needles };
}

onmessage = async (e) => {
  const m = e.data;
  if (m.type === 'load') { await load(m.url); return; }
  if (m.type === 'search') {
    await load(m.url);
    const t0 = performance.now();
    const r = search(m.q, m.titlesOnly);
    postMessage({ type: 'results', seq: m.seq, q: m.q, ms: Math.round(performance.now() - t0), ...r });
  }
};

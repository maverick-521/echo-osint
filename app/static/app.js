// ECHO front-end. No external libraries; works offline.
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
let job = null, jobKind = 'x', next = 0, timer = null, t0 = 0, kind = 'auto', last = null, liveT = null, lastLive = 0;
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

function show(id, on) { $(id).hidden = !on; }
function err(msg) { $('#error').textContent = msg || ''; show('#error', !!msg); }
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => t.hidden = true, 2600); }
function fmt(s) { s = Math.max(0, Math.floor(s) || 0); return Math.floor(s / 60) + 'm ' + String(s % 60).padStart(2, '0') + 's'; }
function clock(s) { s = Math.max(0, Math.floor(s) || 0); const m = Math.floor(s / 60); return m >= 60 ? Math.floor(m / 60) + ':' + String(m % 60).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0') : m + ':' + String(s % 60).padStart(2, '0'); }
/* typical run lengths (seconds) — mirrors engine budgets; used only for the quiet countdown */
const EST = {x: {fast: 200, thorough: 680, deep: 1250}, pivot: {fast: 100, thorough: 200, deep: 330}, breach: {fast: 25, thorough: 90, deep: 90}};
const CIRC = 2 * Math.PI * 52;
let runStart = 0, runMode = 'fast', tickT = null, finished = false;
function setDial(p) {
  const a = $('#dial-arc'); if (a) a.style.strokeDashoffset = (CIRC * (1 - Math.max(0, Math.min(1, p)))).toFixed(1);
  const f = $('#rail-fill'); if (f) f.style.transform = 'scaleX(' + Math.max(0, Math.min(1, p)).toFixed(3) + ')';
}
function tick() {
  if (finished || !runStart) return;
  const secs = (Date.now() - runStart) / 1000;
  const est = ((EST[jobKind] || EST.x)[runMode]) || 200;
  $('#dial-t').textContent = clock(secs);
  const left = est - secs;
  $('#dial-sub').textContent = left > 90 ? '~' + Math.round(left / 60) + ' min left' : left > 8 ? '~' + Math.ceil(left / 10) * 10 + 's left' : 'settling';
  // eases toward the estimate, never claims to be finished
  setDial(Math.min(.96, secs < est ? .9 * (secs / est) : .9 + .06 * (1 - Math.exp(-(secs - est) / est))));
}
function dialState(state) {
  const d = $('#dial'); d.classList.remove('run', 'done', 'fail', 'still');
  $('#rail').classList.remove('run', 'done');
  if (state === 'run') { d.classList.add('run'); $('#rail').classList.add('run'); }
  else if (state === 'done') { d.classList.add('done', 'still'); $('#rail').classList.add('done'); }
  else d.classList.add('fail', 'still');
}
function mode() { return document.querySelector('input[name=mode]:checked').value; }
function label(j) { return j.kind === 'x' || !j.kind ? '@' + j.target : j.target; }
const KIND_NAME = {x: 'X history', pivot: 'Multi-platform', breach: 'Leak check', auto: 'Auto'};

async function post(url, body) {
  const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}

/* ---------- segmented control ---------- */
const COPY = {
  auto: ['Who are we listening for?', '@username, user id, email, ig:name or a profile link', 'Run scan'],
  x: ['X account', '@username, numeric id or x.com link', 'Trace history'],
  pivot: ['Email, username or profile', 'name@example.com, ig:name, gh:name or a link', 'Find footprint'],
  breach: ['Email or username', 'you@example.com', 'Check for leaks'],
};
function moveThumb() {
  const b = $(`#seg button[data-kind="${kind}"]`), th = $('.seg-thumb');
  th.style.width = b.offsetWidth + 'px'; th.style.height = b.offsetHeight + 'px';
  th.style.top = '0'; th.style.bottom = 'auto';
  th.style.transform = `translate(${b.offsetLeft - 4}px, ${b.offsetTop}px)`;
}
function setKind(k, focus) {
  kind = k;
  $$('#seg button').forEach(b => b.setAttribute('aria-selected', String(b.dataset.kind === k)));
  moveThumb();
  const [lab, ph, btn] = COPY[k];
  $('#target-label').textContent = lab; $('#target').placeholder = ph; $('#run span').textContent = btn;
  show('#phone-field', k === 'breach');
  $('#target').required = k !== 'breach';
  $('.deep-opt').hidden = k === 'breach'; $('#depth').classList.toggle('two', k === 'breach');
  if (k === 'breach' && mode() === 'deep') document.querySelector('input[value=thorough]').checked = true;
  const fast = $('input[value=fast] + span small'), th = $('input[value=thorough] + span small');
  fast.textContent = k === 'breach' ? 'seconds' : '~3 min'; th.textContent = k === 'breach' ? '+ combo lists' : '~8–12 min';
  try { localStorage.setItem('echo.kind', k); } catch (e) {}
  hint(); if (focus) $('#target').focus({preventScroll: true});
}
$$('#seg button').forEach(b => b.addEventListener('click', () => setKind(b.dataset.kind, true)));
addEventListener('resize', moveThumb);
// swipe across the tab bar to change tab
(() => {
  let x0 = null; const order = ['auto', 'x', 'pivot', 'breach'];
  $('#seg').addEventListener('pointerdown', e => { x0 = e.clientX; });
  $('#seg').addEventListener('pointerup', e => {
    if (x0 === null) return; const dx = e.clientX - x0; x0 = null;
    if (Math.abs(dx) > 40) { const i = order.indexOf(kind) + (dx < 0 ? 1 : -1); if (order[i]) setKind(order[i]); }
  });
})();

/* ---------- detect hint ---------- */
let hintT = null;
async function hint() {
  const target = $('#target').value.trim(), h = $('#hint');
  if (!target) { h.textContent = kind === 'breach' ? 'Add an email, a phone number, or both.' : ''; return; }
  try {
    const r = await post('/api/detect', {target, kind});
    h.textContent = '';
    const b = document.createElement('b');
    if (r.kind) { b.textContent = KIND_NAME[r.kind]; h.append('Will run ', b, ' on ' + (r.kind === 'x' ? '@' : '') + r.target); }
    else h.textContent = r.error || '';
  } catch (x) { h.textContent = ''; }
}
$('#target').addEventListener('input', () => { clearTimeout(hintT); hintT = setTimeout(hint, 280); });

/* ---------- run ---------- */
$('#form').addEventListener('submit', async e => {
  e.preventDefault(); err();
  let target = $('#target').value.trim(); const phone = kind === 'breach' ? $('#phone').value.trim() : '';
  if (kind === 'breach' && !target && phone) target = phone;
  if (!target) { err('Enter something to listen for.'); return; }
  const btn = $('#run'); btn.classList.remove('pulse'); void btn.offsetWidth; btn.classList.add('pulse');
  await launch({target, phone, kind, mode: mode()});
});
async function launch(q) {
  try {
    const {id} = await post('/api/run', q);
    last = q; try { localStorage.setItem('echo.last', JSON.stringify(q)); } catch (e) {}
    start(id, q.kind === 'auto' ? null : q.kind);
  } catch (x) { err(x.message); }
}
$('#cancel').addEventListener('click', async () => { if (job) await post(`/api/jobs/${job}/cancel`); });

/* ---------- live status ---------- */
const AMBIENT = {
  x: ['Sampling the timeline', 'Listening to the Wayback Machine', 'Comparing display names over time', 'Following handle renames', 'Matching avatars across years'],
  pivot: ['Knocking on public profiles', 'Following cross-links', 'Reading public bios', 'Checking Gravatar and commit emails'],
  breach: ['Checking breach indexes', 'Asking XposedOrNot', 'Asking LeakCheck', 'Counting combo-list matches', 'Enriching breach details'],
};
function setLive(text) {
  const el = $('#live'); if (el.textContent === text) return;
  lastLive = Date.now();
  if (reduce) { el.textContent = text; return; }
  el.classList.add('fade'); setTimeout(() => { el.textContent = text; el.classList.remove('fade'); }, 450);
}
function liveFromLog(line) {
  const l = line.replace(/^\[\*\]\s*/, '').trim();
  if (!l || l.length > 140 || /^\s*$/.test(l)) return null;
  if (/^\[!\]|traceback|error/i.test(l)) return null;
  return l.replace(/\s+->.*$/, '').replace(/^\s+/, '');
}
function ambient() {
  const list = AMBIENT[jobKind] || AMBIENT.x;
  if (Date.now() - lastLive > 5000) setLive(list[Math.floor(Date.now() / 5000) % list.length] + '…');
}

function start(id, k) {
  job = id; jobKind = k || 'x'; next = 0; t0 = Date.now();
  $('#log').textContent = ''; $('#status').textContent = {breach: 'Checking for leaks', pivot: 'Listening across platforms', x: 'Tracing echoes'}[jobKind] || 'Listening';
  $('#live').textContent = 'Sending the first ping…'; lastLive = Date.now();
  runStart = Date.now(); runMode = mode(); finished = false;
  $('#elapsed').textContent = KIND_NAME[jobKind] || '';
  dialState('run'); setDial(0); tick(); show('#skeleton', true);
  const pg = $('#progress'); pg.classList.remove('enter'); void pg.offsetWidth; pg.classList.add('enter');
  show('#progress', true); show('#result', false);
  clearInterval(tickT); tickT = setInterval(tick, 250);
  $('#run').disabled = true; show('#cancel', true);
  clearInterval(timer); timer = setInterval(poll, 1000); poll();
  clearInterval(liveT); liveT = setInterval(ambient, 1200);
  setTimeout(() => $('#progress').scrollIntoView({behavior: reduce ? 'auto' : 'smooth', block: 'nearest'}), 60);
}

async function poll() {
  if (!job) return;
  const localSecs = (Date.now() - t0) / 1000;
  try {
    const r = await fetch(`/api/jobs/${job}?since=${next}`);
    if (!r.ok) return;
    const j = await r.json();
    if (j.kind) jobKind = j.kind;
    if (j.log && j.log.length) {
      const el = $('#log'); const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
      el.textContent += j.log.join('\n') + '\n'; next = j.next;
      if (atBottom) el.scrollTop = el.scrollHeight;
      for (let i = j.log.length - 1; i >= 0; i--) { const t = liveFromLog(j.log[i]); if (t) { setLive(t); break; } }
    }
    let secs = localSecs;
    if (j.started) { secs = ((j.ended ? j.ended * 1000 : Date.now()) - j.started * 1000) / 1000; if (!j.ended) runStart = Math.min(Date.now(), j.started * 1000); }
    if (j.mode) runMode = j.mode;
    $('#elapsed').textContent = label(j) + ' · ' + (KIND_NAME[j.kind] || '') + (j.mode ? ' · ' + j.mode : '');
    if (j.status && j.status !== 'running') {
      clearInterval(timer); clearInterval(liveT); $('#run').disabled = false; show('#cancel', false);
      finished = true; clearInterval(tickT);
      dialState(j.status === 'done' ? 'done' : 'fail'); show('#skeleton', false);
      $('#dial-t').textContent = clock(secs); $('#dial-sub').textContent = j.status === 'done' ? 'done' : j.status;
      if (j.status === 'done') setDial(1);
      $('#status').textContent = {done: 'Echo returned', error: 'No echo (see log)', cancelled: 'Cancelled'}[j.status] || j.status;
      setLive(j.status === 'done' ? 'Done in ' + fmt(secs) : 'Open “Raw log” for details');
      if (j.status === 'done') loadReport(job, j.kind);
      loadHistory();
      job = null;
    }
  } catch (e) { /* the dial keeps its own time */ }
}

/* ---------- results ---------- */
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function tone(sev) { return {high: 'tone-bad', medium: 'tone-warn', low: 'tone-ok'}[sev] || 'tone-mut'; }

async function loadReport(id, k) {
  const r = await fetch(`/api/jobs/${id}/report`);
  if (!r.ok) return;
  const {html} = await r.json();
  const rep = $('#report'); const lv = $('#leak-view'); lv.textContent = '';
  rep.innerHTML = html;
  rep.querySelectorAll('a').forEach(a => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
  $('#dl-md').href = `/api/jobs/${id}/download/md`; $('#dl-json').href = `/api/jobs/${id}/download/json`;
  const htmlChip = $('#dl-html'); if (htmlChip) { htmlChip.href = `/api/jobs/${id}/download/html`;
    fetch(`/api/jobs/${id}/download/html`, {method:'HEAD'}).then(r => { htmlChip.hidden = !r.ok; }).catch(() => { htmlChip.hidden = true; }); }
  $('#result-title').textContent = k === 'breach' ? 'Leak report' : 'Dossier';
  if (k === 'breach') {
    try {
      const d = await (await fetch(`/api/jobs/${id}/download/json`)).json();
      renderLeak(d, lv);
      if (!rep.closest('details.more')) { const det = el('details', 'more'); det.append(el('summary', null, 'Full report & links')); rep.replaceWith(det); det.append(rep); }
    } catch (e) {}
  } else {
    let ok = false;
    try { const d = await (await fetch(`/api/jobs/${id}/download/json`)).json(); ok = renderDossier(d, lv, k); } catch (e) {}
    const det = rep.closest('details.more');
    if (ok && !det) { const m = el('details', 'more'); m.append(el('summary', null, 'Full evidence & sources')); rep.replaceWith(m); m.append(rep); }
    else if (!ok && det) det.replaceWith(rep);
  }
  rep.classList.add('reveal'); [...rep.children].forEach((c, i) => c.style.animationDelay = Math.min(i * 40, 900) + 'ms');
  const res = $('#result'); show('#result', true); res.classList.remove('arrive'); void res.offsetWidth; res.classList.add('arrive');
  res.scrollIntoView({behavior: reduce ? 'auto' : 'smooth', block: 'start'});
}

/* ---------- dossier: plain-language investigator brief (hooks: .dossier .d-head .d-avatar .d-brief .d-trail) ---------- */
const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function when(s) { if (!s) return ''; const d = new Date(s); if (isNaN(d)) return String(s); return MON[d.getUTCMonth()] + ' ' + d.getUTCFullYear(); }
function num(n) { if (n == null || n === '') return ''; n = +n; return n >= 1e6 ? (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M' : n >= 1e4 ? (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'k' : n.toLocaleString(); }
function sure(c, n) { return {high: 'Strong evidence', medium: 'Likely', low: 'Possible', target: 'Strong evidence', 'username match only': 'Same username only', 'same username only': 'Same username only'}[c] || 'Seen'; }
function listJoin(a) { return a.length < 2 ? (a[0] || '') : a.slice(0, -1).join(', ') + ' and ' + a[a.length - 1]; }
const PLAT = {x: 'X', github: 'GitHub', gitlab: 'GitLab', youtube: 'YouTube', tiktok: 'TikTok', bluesky: 'Bluesky', hackernews: 'Hacker News', linktree: 'Linktree', keybase: 'Keybase', reddit: 'Reddit', instagram: 'Instagram', threads: 'Threads', telegram: 'Telegram', gravatar: 'Gravatar'};
const plat = p => PLAT[p] || (p ? p[0].toUpperCase() + p.slice(1) : '');
function bigAvatar(u) { return u ? u.replace(/_normal(\.\w+)$/, '_400x400$1').replace(/_bigger(\.\w+)$/, '_400x400$1') : ''; }
function dHead(root, o) {
  const h = el('header', 'd-head');
  if (o.banner) { const bn = el('div', 'd-banner'); bn.style.backgroundImage = `url("${o.banner}")`; h.append(bn); }
  const t = el('div', 'd-id');
  if (o.kicker) t.append(el('span', 'd-kicker', o.kicker));
  t.append(el('h3', 'd-name', o.name || o.handle));
  if (o.handle && o.handle !== o.name) t.append(el('p', 'd-handle', o.handle));
  if (o.meta && o.meta.length) t.append(el('p', 'd-meta', o.meta.filter(Boolean).join(' · ')));
  if (o.bio) t.append(el('p', 'd-bio', '“' + o.bio.trim() + '”'));
  h.append(t);
  const av = el('div', 'd-avatar' + (o.avatar ? '' : ' empty'));
  if (o.avatar) { const im = new Image(); im.alt = ''; im.referrerPolicy = 'no-referrer'; im.decoding = 'async'; im.src = o.avatar; im.onerror = () => { av.classList.add('empty'); im.remove(); }; av.append(im); }
  av.append(el('span', 'd-initial', ((o.name || o.handle || '?').replace(/^@/, '')[0] || '?').toUpperCase()));
  h.append(av); root.append(h);
}
function section(root, title) { const s = el('section', 'd-sec'); s.append(el('h4', null, title)); root.append(s); return s; }
function evLink(parent, url) {
  if (!url) return; const x = el('a', 'd-ev', 'archive'); x.href = url; x.target = '_blank'; x.rel = 'noopener noreferrer'; parent.append(' ', x);
}
function trail(root, title, rows, fmtv, withEv) {
  if (!rows || !rows.length) return;
  const s = section(root, title); const ol = el('ol', 'd-trail');
  rows.forEach((r, i) => {
    const li = el('li', i === rows.length - 1 ? 'now' : '');
    li.append(el('b', null, fmtv(r.value)));
    const span = when(r.first_seen) === when(r.last_seen) ? when(r.first_seen) : when(r.first_seen) + ' – ' + (i === rows.length - 1 ? 'now' : when(r.last_seen));
    li.append(el('span', 'd-when', span));
    const sr = el('span', 'd-sure', sure(r.confidence) + (r.sightings ? ' · seen ' + r.sightings + '×' : ''));
    if (withEv) evLink(sr, r.evidence_first);
    li.append(sr);
    ol.append(li);
  });
  s.append(ol);
}
function renderDossier(d, root, k) {
  root.textContent = '';
  const box = el('div', 'dossier'); root.append(box);
  if (d.account || d.username_history) {
    const a = d.account || {}, uh = d.username_history || [], dn = d.display_name_history || [];
    const extra = d.dossier_extra || {};
    const handle = '@' + (a.current_handle || d.query);
    dHead(box, {kicker: 'X account', name: a.display_name || handle, handle, avatar: bigAvatar(a.current_avatar), banner: a.current_banner ? a.current_banner + '/600x200' : '',
      bio: a.bio, meta: [a.created_at && 'Joined ' + when(a.created_at), a.location, a.followers != null && num(a.followers) + ' followers', a.tweets != null && num(a.tweets) + ' posts', a.protected ? 'Private' : '']});
    /* Overview */
    const b = section(box, 'Overview'); const p = [];
    const olds = uh.filter(r => (r.value || '').toLowerCase() !== (a.current_handle || '').toLowerCase());
    if (olds.length) p.push(`Since ${when(uh[0] && uh[0].first_seen) || when(a.created_at)}, this account has gone by ${uh.length} usernames. Before ${handle} it was ${listJoin(olds.map(r => '@' + r.value + ' (' + when(r.first_seen) + ')'))}.`);
    else p.push(`No earlier usernames turned up — as far as public records show, it has always been ${handle}.`);
    if (dn.length > 1) p.push(`Its display name has changed ${dn.length === 2 ? 'once' : (dn.length - 1) + ' times'}: ${listJoin(dn.map(r => '“' + r.value + '” (' + (when(r.first_seen) === when(r.last_seen) ? when(r.first_seen) : when(r.first_seen) + ' – ' + when(r.last_seen)) + ')'))}.`);
    const rel = (d.related || []).filter(r => r.confidence === 'high' || r.links_back_to_target);
    if (rel.length) p.push(`${rel.length === 1 ? 'One other X account appears' : rel.length + ' other X accounts appear'} linked to the same person: ${listJoin(rel.map(r => '@' + r.handle + (r.name ? ' (“' + r.name + '”)' : '')))}.`);
    const strong = (extra.other_profiles || []).filter(x => x.confidence === 'high');
    if (strong.length) p.push(`On other sites, ${strong.length} profile${strong.length === 1 ? '' : 's'} look strongly linked (they point back to one of this account’s usernames).`);
    p.forEach(t => b.append(el('p', null, t)));
    /* Username history */
    trail(box, 'Username history', uh, v => '@' + v);
    trail(box, `Display names (${dn.length})`, dn, v => v, true);
    /* Bio history (dated) */
    const bh = d.bio_history || (d.history || {}).bio || [];
    if (bh.length) { const s = section(box, `Bio history (${bh.length})`); const ol = el('ol', 'd-trail d-bios');
      bh.forEach((r, i) => { const last = i === bh.length - 1; const li = el('li', last ? 'now' : '');
        const span = when(r.first_seen) === when(r.last_seen) ? when(r.first_seen) : when(r.first_seen) + ' – ' + (last && a.live ? 'now' : when(r.last_seen));
        li.append(el('span', 'd-when', span));
        const q = el('q', null, (r.value || '').trim() || '(empty bio)'); if (!(r.value || '').trim()) q.className = 'd-empty'; li.append(q);
        const sr = el('span', 'd-sure', sure(r.confidence) + (r.sightings ? ' · seen ' + r.sightings + '×' : '')); evLink(sr, r.evidence_first); li.append(sr);
        ol.append(li); }); s.append(ol); }
    /* Every profile picture found (oldest first), archived copy as fallback */
    const av = (d.avatars || []).filter(x => x.value || x.live_url);
    if (av.length) { const s = section(box, `Profile pictures (${av.length})`); const g = el('div', 'd-pics');
      av.forEach((x, i) => { const f = el('figure'); const ln = el('a'); ln.target = '_blank'; ln.rel = 'noopener noreferrer';
        const arch = ((x.archived_image_captures || []).find(c => c.wayback_url) || {}).wayback_url;
        ln.href = arch || bigAvatar(x.value || x.live_url);
        const im = new Image(); im.referrerPolicy = 'no-referrer'; im.loading = 'lazy'; im.alt = '';
        im.src = bigAvatar(x.value || x.live_url);
        im.onerror = () => { if (arch && !im.dataset.f) { im.dataset.f = 1; im.src = arch; } else im.classList.add('d-gone'); };
        const span = when(x.first_seen) === when(x.last_seen) ? when(x.first_seen) : when(x.first_seen) + ' – ' + (i === av.length - 1 && a.live ? 'now' : when(x.last_seen));
        ln.append(im); f.append(ln, el('figcaption', null, span)); g.append(f); }); s.append(g); }
    /* Other profiles (related X + pivot hits) */
    const others = [];
    (d.related || []).forEach(r => others.push({title: '@' + r.handle, url: r.permalink || ('https://x.com/' + r.handle), label: sure(r.confidence),
      blurb: r.links_back_to_target ? 'Its bio mentions this account.' : r.shares_display_name ? 'Uses the same display name.' : 'Found nearby in public records.'}));
    (extra.other_profiles || []).forEach(x => others.push({title: plat(x.platform) + (x.handle ? ' · ' + x.handle : ''), url: x.url, label: x.label || sure(x.confidence),
      blurb: [x.name, x.reason].filter(Boolean).join(' — ')}));
    if (others.length) { const s = section(box, 'Other profiles'); const g = el('div', 'd-cards');
      others.forEach(r => { const c = el('a', 'd-card'); if (r.url) { c.href = r.url; c.target = '_blank'; c.rel = 'noopener noreferrer'; }
        c.append(el('b', null, r.title), el('span', 'd-sure', r.label)); if (r.blurb) c.append(el('p', null, r.blurb)); g.append(c); }); s.append(g); }
    /* Contacts listed publicly */
    const contacts = (extra.contacts || []).slice();
    if (!contacts.length && a.website) contacts.push({kind: 'website', value: a.website, from: 'X profile', url: a.website, label: 'Strong evidence'});
    if (contacts.length) { const s = section(box, 'Contacts listed publicly'); const ul = el('ul', 'd-lim');
      contacts.forEach(c => { const li = el('li'); li.append(el('b', null, (c.kind || 'contact') + ': '));
        if (c.url && /^https?:/i.test(c.url)) { const a2 = el('a', null, c.value); a2.href = c.url; a2.target = '_blank'; a2.rel = 'noopener noreferrer'; li.append(a2); }
        else li.append(document.createTextNode(c.value || ''));
        li.append(document.createTextNode(' · ' + [c.from, c.label || sure(c.confidence)].filter(Boolean).join(' · ')));
        ul.append(li); }); s.append(ul); }
    /* Breach notes */
    const notes = [];
    (extra.breach_notes || []).forEach(b => {
      if (b.count) notes.push(`${b.query} appears in ${b.count} known breach${b.count === 1 ? '' : 'es'}${b.names && b.names.length ? ': ' + listJoin(b.names) : ''}.`);
      else notes.push(`${b.query}: not found in the free breach indexes that answered.`);
    });
    if (!notes.length && (extra.handles_checked || []).length) notes.push('No free-source breach hits for the usernames checked. That is not a clean bill — private dumps aren’t covered.');
    if (notes.length) { const s = section(box, 'Breach notes'); const ul = el('ul', 'd-lim'); notes.forEach(t => ul.append(el('li', null, t))); s.append(ul); }
    /* Timeline */
    const tl = (d.timeline || []).filter(e => e.type !== 'metric' && e.type !== 'note').slice(0, 40);
    if (tl.length) { const s = section(box, 'Timeline'); const ol = el('ol', 'd-trail');
      tl.forEach(e => { const li = el('li'); let v = e.value;
        if (e.type === 'username' && v) v = '@' + String(v).replace(/^@/, '');
        if ((e.type === 'avatar' || e.type === 'banner') && typeof v === 'string' && /^https?:/i.test(v)) v = e.type === 'avatar' ? 'new profile picture' : 'new banner';
        li.append(el('b', null, (e.type || '').replace(/_/g, ' ')), el('span', 'd-when', when(e.date) || e.date || ''),
                  el('span', null, v == null || v === '' ? '(empty)' : String(v).slice(0, 120)),
                  el('span', 'd-sure', sure(e.confidence))); ol.append(li); }); s.append(ol); }
    const lim = [];
    if ((d.failed_sources || []).some(f => /About this account/i.test(f))) lim.push('X only shows the official “username changed” count to signed-in users, so that number isn’t included.');
    if (uh.length) lim.push(`Public archives are thin before ${when(uh[0].first_seen)}; anything earlier may not have been saved.`);
    lim.push('Same username on another site is not proof it’s the same person — trust “Strong evidence” first.');
    lim.push('Everything here comes from public pages and archives. Nothing private was accessed.');
    const s = section(box, 'Limits'); const ul = el('ul', 'd-lim'); lim.forEach(t => ul.append(el('li', null, t))); s.append(ul);
    return true;
  }
  if (d.profiles) {
    const found = d.profiles.filter(x => x.status === 'found'), unk = d.unknown || [], nf = d.not_found || [];
    const real = found.find(x => x.avatar && !/default_profile/.test(x.avatar));
    const name = (found.find(x => x.name) || {}).name;
    dHead(box, {kicker: d.kind === 'email' ? 'Email footprint' : 'Username footprint', name: d.value || d.query, handle: name && name !== d.value ? name : '',
      avatar: real ? bigAvatar(real.avatar) : '', meta: [`Found on ${found.length} site${found.length === 1 ? '' : 's'}`, `${found.length + unk.length + nf.length} checked`]});
    const b = section(box, 'Brief'); const strong = found.filter(x => x.confidence === 'high');
    b.append(el('p', null, found.length ? `“${d.value || d.query}” shows up on ${listJoin(found.map(x => plat(x.platform)))}.` : `No public profiles turned up for “${d.value || d.query}”.`));
    if (found.length) b.append(el('p', null, strong.length ? `${strong.length} of these link to each other, which is good evidence they’re the same person.` : 'None of them link to each other, so a shared name alone doesn’t prove it’s the same person.'));
    if (unk.length) b.append(el('p', null, `${listJoin(unk.map(x => plat(x.platform)))} couldn’t be checked — they hide profiles from logged-out visitors. You can open them yourself below.`));
    if (found.length) { const s = section(box, 'Profiles found'); const g = el('div', 'd-cards');
      found.forEach(x => { const c = el('a', 'd-card'); c.href = x.url; c.target = '_blank'; c.rel = 'noopener noreferrer';
        c.append(el('b', null, plat(x.platform)), el('span', 'd-sure', sure(x.confidence)));
        const line = [x.name, x.created && 'since ' + when(x.created), x.followers != null && num(x.followers) + ' followers'].filter(Boolean).join(' · ');
        if (line) c.append(el('p', null, line)); if (x.bio) c.append(el('p', 'd-q', x.bio)); g.append(c); }); s.append(g); }
    if ((d.linked_accounts || []).length) { const s = section(box, 'Linked accounts'); const ul = el('ul', 'd-lim');
      d.linked_accounts.forEach(x => { const li = el('li'); const a = el('a', null, x.url || x.handle || x.value || JSON.stringify(x)); if (x.url) { a.href = x.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; } li.append(a); ul.append(li); }); s.append(ul); }
    if (unk.length) { const s = section(box, 'Check by hand'); const g = el('div', 'sources');
      unk.forEach(x => { const a = el('a', 'src tone-mut', plat(x.platform)); a.href = x.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; g.append(a); }); s.append(g); }
    return true;
  }
  return false;
}

function renderLeak(d, root) {
  const n = (d.breaches || []).length, answered = (d.checks || []).some(c => c.status !== 'could not check');
  const t = n ? (d.breaches.some(b => b.severity === 'high') ? 'tone-bad' : 'tone-warn') : (answered ? 'tone-ok' : 'tone-mut');
  const v = el('div', 'verdict ' + t);
  const rings = el('div', 'v-rings'); rings.append(el('i'), el('i'), el('i'), el('b')); v.append(rings);
  const txt = el('div');
  const who = [d.email, d.phone && d.phone.e164, d.username && '@' + d.username].filter(Boolean).join(' · ');
  txt.append(el('h3', null, n ? `Found in ${n} breach${n > 1 ? 'es' : ''}` : answered ? 'No leaks found in free sources' : 'Couldn’t reach the sources'),
             el('p', null, who + (n ? '' : answered ? ' — not a guarantee; see “Check it yourself”.' : ' — try again in a minute.')));
  v.append(txt); root.append(v);
  const src = el('div', 'sources');
  for (const c of (d.checks || []).concat(d.exposure || [])) {
    const ok = c.status === 'not found' || c.status === 'no public profile';
    const s = el('span', 'src ' + (c.status === 'found' || c.status === 'public profile' ? 'tone-bad' : ok ? 'tone-ok' : 'tone-mut'), c.source.replace(/ \(.*\)$/, '') + ' · ' + c.status);
    src.append(s);
  }
  root.append(src);
  if (n) {
    const g = el('div', 'bcards');
    d.breaches.slice(0, 60).forEach((b, i) => {
      const c = el('div', 'bcard ' + tone(b.severity)); c.style.animationDelay = Math.min(i * 60, 1200) + 'ms';
      const h = el('h4'); h.append(el('span', null, b.name), el('span', 'sev', b.severity)); c.append(h);
      c.append(el('p', null, (b.date || 'date unknown') + (b.records ? ' · ' + Number(b.records).toLocaleString() + ' accounts' : '')));
      if (b.data && b.data.length) c.append(el('p', null, b.data.slice(0, 8).join(', ')));
      g.append(c);
    });
    root.append(g);
  }
  if (d.advice && d.advice.length) { const ul = el('ul', 'advice'); d.advice.forEach(a => ul.append(el('li', null, a))); root.append(el('h2', null, 'What to do'), ul); }
}

async function openPast(id) {
  job = null; clearInterval(timer); clearInterval(liveT); clearInterval(tickT); finished = true;
  const r = await fetch(`/api/jobs/${id}`); const j = await r.json();
  if (j.status === 'running') { start(id, j.kind); return; }
  last = {target: j.target, phone: j.phone || '', kind: j.kind || 'x', mode: j.mode};
  $('#log').textContent = (j.log || []).join('\n'); $('#status').textContent = 'Past echo · ' + j.status;
  $('#live').textContent = label(j); $('#elapsed').textContent = (KIND_NAME[j.kind] || '') + ' · ' + j.mode;
  dialState(j.status === 'done' ? 'done' : 'fail'); show('#skeleton', false); show('#rail', true);
  const ps = j.started && j.ended ? j.ended - j.started : 0;
  $('#dial-t').textContent = ps ? clock(ps) : '—'; $('#dial-sub').textContent = j.status === 'done' ? 'done' : j.status; setDial(j.status === 'done' ? 1 : 0);
  show('#progress', true); show('#result', false);
  if (j.status === 'done') loadReport(id, j.kind || 'x');
}

async function loadHistory() {
  const r = await fetch('/api/history'); const items = await r.json();
  const ul = $('#history'); ul.textContent = '';
  if (!items.length) { ul.append(el('li', 'empty', 'Nothing yet. Your scans will settle here.')); return; }
  items.forEach((it, i) => {
    const li = el('li'); li.style.animationDelay = Math.min(i * 35, 700) + 'ms';
    const b = el('button'); b.type = 'button';
    const st = {done: 'tone-ok', running: 'tone-warn', error: 'tone-bad', cancelled: 'tone-mut'}[it.status] || 'tone-mut';
    b.append(el('span', 'dot ' + st), el('span', 'k', KIND_NAME[it.kind || 'x']), el('span', 't', label(it)),
             el('span', 'w', it.mode + ' · ' + new Date(it.started * 1000).toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'})));
    b.addEventListener('click', () => openPast(it.id));
    li.append(b); ul.append(li);
  });
}

/* ---------- pull to refresh (touch + mouse) ---------- */
(() => {
  const sheet = $('#sheet'), ptr = $('#ptr'), lab = $('#ptr-label'), TH = 78;
  let y0 = null, dy = 0, active = false;
  const interactive = t => t.closest('input,textarea,button,a,select,label,summary,pre,#seg,.modal');
  function down(y, target) { if (scrollY > 2 || interactive(target)) return; y0 = y; dy = 0; active = false; }
  function move(y, e) {
    if (y0 === null) return;
    const raw = y - y0;
    if (raw <= 0) { if (active) reset(); return; }
    if (!active && raw < 8) return;
    active = true; if (e.cancelable) e.preventDefault();
    dy = TH * 1.6 * (1 - Math.exp(-raw / (TH * 1.6)));  // rubber band
    sheet.classList.add('dragging'); sheet.style.transform = `translateY(${dy}px)`;
    const p = Math.min(1, dy / TH); ptr.style.opacity = p; ptr.style.setProperty('--p', p);
    ptr.classList.toggle('armed', p >= 1);
    lab.textContent = p >= 1 ? (last ? 'Release to re-echo' : 'Release to refresh') : 'Pull to re-echo';
  }
  function up() {
    if (y0 === null) return; y0 = null;
    if (!active) return;
    sheet.classList.remove('dragging');
    if (dy >= TH) {
      ptr.classList.add('go'); lab.textContent = last && !job ? 'Re-echoing…' : 'Refreshing…';
      sheet.style.transform = 'translateY(56px)';
      const work = (last && !job) ? launch(last) : loadHistory();
      Promise.resolve(work).finally(() => setTimeout(reset, 700));
    } else reset();
  }
  function reset() { active = false; dy = 0; sheet.classList.remove('dragging'); sheet.style.transform = ''; ptr.style.opacity = 0; ptr.classList.remove('armed', 'go'); ptr.style.setProperty('--p', 0); }
  addEventListener('touchstart', e => down(e.touches[0].clientY, e.target), {passive: true});
  addEventListener('touchmove', e => move(e.touches[0].clientY, e), {passive: false});
  addEventListener('touchend', up); addEventListener('touchcancel', up);
  addEventListener('mousedown', e => { if (e.button === 0) down(e.clientY, e.target); });
  addEventListener('mousemove', e => { if (y0 !== null) move(e.clientY, e); });
  addEventListener('mouseup', up);
})();

/* ---------- phone / bind info ---------- */
async function info() {
  try {
    const r0 = await fetch('/api/info');
    if (r0.status === 401) { location.href = '/login'; return; }
    const i = await r0.json();
    const f = $('#foot'); f.textContent = '';
    if (i.bind === 'cloud') {
      f.append('Private · PIN-protected · works from anywhere');
    } else if (i.bind === 'tunnel') {
      f.append('Remote access on · PIN-protected');
      if (i.phone_url) { show('#phone-btn', true); $('#phone-url').textContent = i.phone_url; }
    } else if (i.bind === 'lan') {
      f.append('Phone mode on · same Wi-Fi only · paired devices only');
      if (i.phone_url) { show('#phone-btn', true); $('#phone-url').textContent = i.phone_url; }
    } else {
      const b = el('b', null, 'ECHO (phone mode)'); f.append('Local only · 127.0.0.1. For iPhone access on your Wi-Fi, start ', b, '.');
    }
  } catch (e) {}
}
$('#phone-btn').addEventListener('click', () => show('#phone-sheet', true));
$('#close-sheet').addEventListener('click', () => show('#phone-sheet', false));
$('#phone-sheet').addEventListener('click', e => { if (e.target.id === 'phone-sheet') show('#phone-sheet', false); });
$('#copy-url').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('#phone-url').textContent); toast('Link copied'); }
  catch (e) { const r = document.createRange(); r.selectNodeContents($('#phone-url')); getSelection().removeAllRanges(); getSelection().addRange(r); toast('Selected — copy it'); }
});

/* ---------- boot ---------- */
try { last = JSON.parse(localStorage.getItem('echo.last') || 'null'); } catch (e) {}
let k0 = 'auto'; try { k0 = localStorage.getItem('echo.kind') || 'auto'; } catch (e) {}
setKind(COPY[k0] ? k0 : 'auto');
requestAnimationFrame(moveThumb); document.fonts && document.fonts.ready.then(moveThumb);
loadHistory(); info();

/* Dashboard 2PC — HTML/CSS/JavaScript thuần, chỉ gọi API cùng nguồn.
 * Nguyên tắc: không bịa số liệu; lỗi mạng ≠ ABORT; dữ liệu cũ có nhãn; không tự đổi token. */
(() => {
'use strict';

const NAMES = ['coordinator', 'airline', 'hotel'];
const NODE_LABEL = { coordinator: 'Coordinator', airline: 'Airline', hotel: 'Hotel' };
const FAULT_POINTS = {
  coordinator: ['after_votes', 'after_decision', 'after_ack_airline'],
  airline: ['before_prepare', 'after_prepare', 'before_commit', 'after_commit'],
  hotel: ['before_prepare', 'after_prepare', 'before_commit', 'after_commit'],
};
const FAULT_HINT = {
  after_votes: 'Coordinator đã nhận đủ phiếu nhưng chưa ghi quyết định. Hai RM giữ PREPARED và khóa; khởi động lại Coordinator với cùng DB sẽ ghi ABORTED rồi phát lại.',
  after_decision: 'Coordinator đã ghi quyết định bền vững trước pha 2. Khởi động lại với cùng DB: giữ quyết định, phát lại đến khi đủ ACK, tồn chỉ giảm một lần.',
  after_ack_airline: 'Coordinator lỗi ngay sau khi ghi ACK của Airline. Khởi động lại sẽ phát lại phần còn thiếu cho Hotel.',
  before_prepare: 'RM nhận prepare nhưng chưa xử lý. crash: Coordinator coi là không đồng ý → ABORT. delay quá 2 giây: RPC timeout, thông điệp muộn bị tombstone chặn.',
  after_prepare: 'RM đã ghi PREPARED và khóa rồi mới lỗi. Khởi động lại giữ PREPARED, chờ Coordinator phát quyết định.',
  before_commit: 'RM nhận commit nhưng chưa áp dụng. Coordinator thử lại cho tới khi RM trả COMMITTED.',
  after_commit: 'RM đã commit nhưng mất phản hồi (mất ACK). Phát lại không được trừ tồn lần hai.',
};
const LIFE = {
  starting: ['Đang khởi động', 'warn'], running: ['Đang chạy', 'ok'], stopping: ['Đang dừng', 'warn'],
  stopped: ['Đã dừng', 'muted'], failed: ['Bị lỗi / đã thoát', 'bad'],
};
const REQ_KEY = 'htpt2pc.request.v1';
const LOAD_KEY = 'htpt2pc.loadrun.v1';

const S = {
  session: null,
  overview: null, overviewAt: null, dashDown: false, dashError: '',
  selectedTx: null, detail: null, filter: 'all',
  busy: { coordinator: false, airline: false, hotel: false },
  svcMsg: {},
  req: null, inflight: false, formMode: null, draftToken: '', bkMsg: null,
  armed: [], ftMsg: null, ftBusy: false,
  loadRun: null, loadLoop: 0, loadMsg: null, loadStarting: false,
  storageOk: true,
};

/* ---------- tiện ích DOM ---------- */
const $ = (sel, root = document) => root.querySelector(sel);
function h(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return e;
}
const setText = (n, t) => { if (n.textContent !== t) n.textContent = t; };
const setClass = (n, c) => { if (n.className !== c) n.className = c; };
const setHidden = (n, b) => { if (n.hidden !== b) n.hidden = b; };
const setDisabled = (n, b) => { if (n.disabled !== b) n.disabled = b; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const shortId = (s) => (typeof s === 'string' ? s.slice(0, 8) : '—');
function clock(x) {
  if (!x) return 'chưa có';
  const d = x instanceof Date ? x : new Date(x);
  return Number.isNaN(d.getTime()) ? 'không rõ' : d.toLocaleTimeString('vi-VN', { hour12: false });
}
function stateCls(s) {
  if (s === 'COMMITTED') return 'ok';
  if (s === 'ABORTED') return 'bad';
  if (s === 'PREPARED' || s === 'PREPARING' || s === 'ACTIVE') return 'warn';
  return 'muted';
}
const tag = (text, cls) => h('span', { class: 'tag ' + cls, text });

/* Đồng bộ danh sách theo khóa: giữ nguyên phần tử (focus, vùng chọn) khi polling. */
function syncList(parent, items, keyOf, create, update) {
  const existing = new Map();
  for (const ch of Array.from(parent.children)) existing.set(ch.dataset.key, ch);
  let prev = null;
  for (const item of items) {
    const key = String(keyOf(item));
    let node = existing.get(key);
    if (node) existing.delete(key); else { node = create(item); node.dataset.key = key; }
    update(node, item);
    const expected = prev ? prev.nextSibling : parent.firstChild;
    if (node !== expected) parent.insertBefore(node, expected);
    prev = node;
  }
  existing.forEach((n) => n.remove());
}

/* ---------- lưu trữ cục bộ (có thể thất bại) ---------- */
function storeGet(k) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : null; } catch (e) { S.storageOk = false; return null; } }
function storeSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch (e) { S.storageOk = false; return false; } }
function storeDel(k) { try { localStorage.removeItem(k); } catch (e) { S.storageOk = false; } }

/* ---------- gọi API cùng nguồn ---------- */
class NetErr extends Error { constructor(m, aborted) { super(m); this.network = true; this.aborted = !!aborted; } }
async function ensureSession(force) {
  if (S.session && !force) return S.session;
  const res = await fetch('/api/session', { cache: 'no-store' });
  const data = await res.json();
  if (!data || typeof data.session_token !== 'string') throw new NetErr('Không lấy được token phiên');
  S.session = data.session_token;
  return S.session;
}
async function api(method, path, body, timeout = 10000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    const headers = {};
    let payload;
    if (method === 'POST') {
      headers['Content-Type'] = 'application/json';
      headers['X-Session-Token'] = await ensureSession();
      payload = JSON.stringify(body || {});
    }
    const res = await fetch(path, { method, headers, body: payload, signal: ctrl.signal, cache: 'no-store' });
    let data = null;
    try { data = await res.json(); } catch (e) { /* thân không phải JSON */ }
    return { status: res.status, ok: res.ok, data };
  } catch (e) {
    throw new NetErr(e && e.message ? e.message : 'Lỗi mạng', e && e.name === 'AbortError');
  } finally { clearTimeout(timer); }
}
async function post(path, body, timeout) {
  let r = await api('POST', path, body, timeout);
  // 403 invalid_session bị từ chối TRƯỚC khi chạy thao tác: lấy token phiên mới rồi gửi lại cùng nội dung.
  if (r.status === 403 && r.data && r.data.error && r.data.error.code === 'invalid_session') {
    S.session = null;
    r = await api('POST', path, body, timeout);
  }
  return r;
}
const errText = (r) => (r && r.data && r.data.error ? `${r.data.error.code}: ${r.data.error.message}` : `HTTP ${r ? r.status : '?'}`);

/* ---------- mô hình dữ liệu ---------- */
const node = (name) => (S.overview && S.overview.nodes ? S.overview.nodes[name] : null);
function fresh(n) {
  if (!n) return { text: 'Chưa có dữ liệu', cls: 'muted' };
  if (!n.snapshot) return { text: `Không xác định${n.error ? ' (' + n.error.code + ')' : ''} — chưa từng đọc được`, cls: 'bad' };
  if (S.dashDown) return { text: `Dữ liệu cũ — mất kết nối dashboard, đọc thành công lúc ${clock(n.last_success_at)}`, cls: 'warn' };
  if (n.stale) return { text: `Dữ liệu cũ — không đọc được node (${n.error ? n.error.code : '?'}), lần đọc thành công cuối lúc ${clock(n.last_success_at)}`, cls: 'warn' };
  return { text: `Dữ liệu mới, đọc lúc ${clock(n.last_success_at)}`, cls: 'ok' };
}
const nodeFresh = (n) => !!(n && n.snapshot && !n.stale && !S.dashDown);
function coordTxs() {
  const c = node('coordinator');
  if (!c || !c.snapshot) return null;
  const first = new Map();
  for (const ev of c.snapshot.events) if (!first.has(ev.tx) || ev.seq < first.get(ev.tx)) first.set(ev.tx, ev.seq);
  return c.snapshot.transactions
    .map((t, i) => ({ ...t, order: first.has(t.tx) ? first.get(t.tx) : i }))
    .sort((a, b) => b.order - a.order);
}
function partState(name, tx) {
  const n = node(name);
  if (!n || !n.snapshot) return { text: '?', cls: 'muted' };
  const r = n.snapshot.reservations.find((x) => x.tx === tx);
  return r ? { text: r.state, cls: stateCls(r.state) } : { text: '—', cls: 'muted' };
}
const decided = (t) => t.state === 'COMMITTED' || t.state === 'ABORTED';
function txStatus(t) {
  if (!decided(t)) return { text: 'Đang xử lý', long: 'Đang xử lý — chưa có quyết định cuối.', cls: 'warn' };
  const word = t.state === 'COMMITTED' ? 'COMMIT' : 'ABORT';
  return t.complete ? { text: 'Hoàn tất', long: `Đã ${word} — đủ xác nhận (ACK 2/2).`, cls: t.state === 'COMMITTED' ? 'ok' : 'bad' }
                    : { text: 'Chờ xác nhận', long: `Đã quyết định ${word}, đang chờ xác nhận (ACK ${t.ack.length}/2).`, cls: 'warn' };
}
const FILTERS = {
  all: () => true,
  processing: (t) => !decided(t),
  commit: (t) => t.state === 'COMMITTED',
  abort: (t) => t.state === 'ABORTED',
  pending: (t) => decided(t) && !t.complete,
};

/* ---------- vòng polling: request sau bắt đầu khi request trước đã xong ---------- */
async function pollOnce() {
  try {
    const r = await api('GET', '/api/overview', null, 8000);
    if (r.ok && r.data && r.data.nodes) { S.overview = r.data; S.overviewAt = new Date(); S.dashDown = false; S.dashError = ''; }
    else { S.dashDown = true; S.dashError = errText(r); }
  } catch (e) { S.dashDown = true; S.dashError = 'không kết nối được dashboard'; }
  try { await refreshDetail(); } catch (e) { /* giữ vòng polling */ }
  try { render(); } catch (e) { console.error(e); }
}
async function pollLoop() { for (;;) { await pollOnce(); await sleep(1000); } }
async function refreshDetail() {
  const tx = S.selectedTx;
  if (!tx) return;
  try {
    const r = await api('GET', '/api/transactions/' + encodeURIComponent(tx), null, 8000);
    if (tx === S.selectedTx) S.detail = { tx, status: r.status, body: r.data, err: '' };
  } catch (e) {
    if (tx === S.selectedTx) S.detail = { tx, status: 0, body: S.detail && S.detail.tx === tx ? S.detail.body : null, err: 'Mất kết nối dashboard' };
  }
}

/* ---------- vẽ giao diện ---------- */
function render() {
  renderHeader(); renderServices(); renderInventory(); renderBooking(); renderFaults(); renderLoad(); renderTx(); renderDetail();
}
function renderHeader() {
  setText($('#last-update'), 'Cập nhật lần cuối: ' + (S.overviewAt ? clock(S.overviewAt) : 'chưa có'));
  const ps = $('#poll-state');
  setText(ps, S.dashDown ? 'Mất kết nối dashboard' : S.overview ? 'Đang cập nhật mỗi giây' : 'Đang kết nối…');
  setClass(ps, 'badge ' + (S.dashDown ? 'bad' : S.overview ? 'ok' : 'muted'));
  const lines = [];
  let bad = false;
  if (S.dashDown) { bad = true; lines.push(`Không đọc được dashboard (${S.dashError}). Số liệu bên dưới là dữ liệu cũ, lần đọc thành công cuối lúc ${clock(S.overviewAt)}. Mất kết nối không có nghĩa là giao dịch đã ABORT.`); }
  if (S.overview && NAMES.some((n) => node(n) && node(n).lifecycle && node(n).lifecycle.state === 'unmanaged')) lines.push('Chưa nối bộ quản lý tiến trình: các nút Chạy/Dừng/Khởi động lại bị khóa. Dùng python run_dashboard.py (có lifecycle.py) hoặc chạy ba dịch vụ thủ công.');
  if (!S.storageOk) lines.push('Không ghi được localStorage: nếu tải lại trang, yêu cầu chưa xác định sẽ không còn trên trình duyệt. Hãy tự ghi lại mã yêu cầu.');
  const b = $('#banner');
  setHidden(b, lines.length === 0);
  setClass(b, 'banner' + (bad ? '' : ' warn'));
  const text = lines.join('\n');
  if (b.dataset.text !== text) { b.dataset.text = text; b.replaceChildren(...lines.map((l) => h('p', { text: l }))); }
}
function lifeOf(name) {
  const n = node(name);
  return (n && n.lifecycle) || { state: 'unmanaged', managed: false };
}
function renderServices() {
  for (const card of document.querySelectorAll('.service')) {
    const name = card.dataset.name, n = node(name), lc = lifeOf(name);
    const healthOk = !!(n && n.health && n.health.ok === true && !n.stale && !S.dashDown);
    const healthBad = !!(n && (n.stale || n.error));
    let lifeText, lifeCls;
    if (!n) { lifeText = 'Chưa có dữ liệu'; lifeCls = 'muted'; }
    else if (!lc.managed && lc.state === 'failed') { lifeText = 'Bộ quản lý tiến trình bị lỗi'; lifeCls = 'bad'; }
    else if (!lc.managed) { lifeText = healthOk ? 'Phản hồi (không do dashboard quản lý)' : 'Không phản hồi'; lifeCls = healthOk ? 'ok' : 'bad'; }
    else { [lifeText, lifeCls] = LIFE[lc.state] || [String(lc.state), 'muted']; }
    setText($('[data-role=life]', card), lifeText);
    setClass($('[data-role=life]', card), 'badge ' + lifeCls);
    let health = '';
    if (n) {
      if (healthOk) health = 'Health: tốt';
      else if (healthBad) health = `Health: lỗi${n.error ? ' — ' + n.error.code : ''}` + (lc.managed && lc.state === 'running' ? ' (tiến trình còn sống nhưng health lỗi)' : '');
      if (S.dashDown) health = 'Health: không rõ (mất kết nối dashboard)';
    }
    setText($('[data-role=health]', card), health);
    const meta = [];
    if (lc.managed) { meta.push(lc.pid ? `PID ${lc.pid}` : 'không có tiến trình'); if (lc.port) meta.push(`cổng ${lc.port}`); }
    if (n && n.last_success_at) meta.push(`đọc thành công lúc ${clock(n.last_success_at)}`);
    if (lc.managed && lc.log) meta.push(`log: ${lc.log}`);
    setText($('[data-role=meta]', card), meta.join(' • '));
    const err = lc.error || (healthBad && n.error ? n.error.message : '');
    const errEl = $('[data-role=error]', card);
    setHidden(errEl, !err);
    setText(errEl, err ? String(err) : '');
    const busy = S.busy[name] || lc.state === 'starting' || lc.state === 'stopping';
    const can = !lc.managed || busy ? { start: false, stop: false, restart: false } :
      lc.state === 'running' ? { start: false, stop: true, restart: true } : { start: true, stop: false, restart: true };
    for (const btn of card.querySelectorAll('button[data-action]')) setDisabled(btn, !can[btn.dataset.action]);
    const msg = S.busy[name] ? { text: 'Đang xử lý… (chờ tối đa 10–15 giây)', cls: '' } : S.svcMsg[name] || { text: !lc.managed && n ? 'Chưa nối bộ quản lý tiến trình.' : '', cls: '' };
    setText($('[data-role=msg]', card), msg.text);
    setClass($('[data-role=msg]', card), 'note ' + (msg.cls || ''));
  }
}
function renderInventory() {
  for (const card of document.querySelectorAll('.inventory')) {
    const name = card.dataset.name, n = node(name), f = fresh(n);
    setText($('[data-role=fresh]', card), f.text);
    setClass($('[data-role=fresh]', card), 'fresh ' + f.cls);
    const body = $('[data-role=body]', card);
    let sig, build;
    if (!n || !n.snapshot) {
      sig = n ? 'unknown' : 'none';
      build = () => [h('p', { class: 'note', text: n ? 'Không xác định — chưa có snapshot, không hiển thị tồn bằng 0.' : 'Chưa có dữ liệu.' })];
    } else {
      const inv = n.snapshot.inventory.slice().sort((a, b) => (a.resource === 'DEFAULT' ? -1 : b.resource === 'DEFAULT' ? 1 : a.resource.localeCompare(b.resource)));
      const rows = inv.map((it) => {
        const held = n.snapshot.reservations.filter((r) => r.state === 'PREPARED' && r.resource === it.resource).length;
        const lock = n.snapshot.locks.find((l) => l.resource === it.resource);
        return { resource: it.resource, available: it.available, held, free: Math.max(it.available - held, 0), lock: lock ? lock.tx : null };
      });
      sig = JSON.stringify(rows);
      build = () => rows.map((r) => h('div', { class: 'inv-row' },
        h('div', {}, h('p', { class: 'res-name', text: r.resource }), h('div', { class: 'bignum', text: String(r.available) }), h('div', { class: 'meta', text: 'tồn (chưa trừ commit)' })),
        h('div', { class: 'inv-stats' },
          h('p', {}, 'Đang giữ (PREPARED): ', h('strong', { text: String(r.held) })),
          h('p', {}, 'Chưa giữ: ', h('strong', { text: String(r.free) })),
          r.lock ? h('p', {}, tag('Tạm khóa — chờ quyết định', 'warn'), ' ', h('span', { class: 'mono', text: shortId(r.lock) })) : h('p', { class: 'meta', text: 'Không có khóa' }))));
    }
    if (body.dataset.sig !== sig) { body.dataset.sig = sig; body.replaceChildren(...build()); }
  }
}

/* ----- đặt combo ----- */
function newToken() {
  const d = new Date(), p = (x, n = 2) => String(x).padStart(n, '0');
  const rnd = Math.random().toString(36).slice(2, 6);
  return `booking-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}-${rnd}`;
}
const effStatus = (req) => (req && req.status === 'sending' && !S.inflight ? 'unknown' : req ? req.status : null);
function saveReq(req) { S.req = req; if (req) storeSet(REQ_KEY, req); else storeDel(REQ_KEY); }
function setBk(text, cls) { S.bkMsg = text ? { text, cls: cls || '' } : null; }
function liveRecord(req) {
  const list = coordTxs();
  return list && req ? list.find((t) => t.token === req.token) || null : null;
}
function resultSummary(req, live) {
  const src = live || req;
  if (!src || !src.state) return null;
  const acks = Array.isArray(src.ack) ? src.ack.length : 0;
  if (src.state === 'COMMITTED') return src.complete ? { text: 'COMMITTED — đủ ACK Airline và Hotel.', cls: 'ok' } : { text: `Đã quyết định COMMIT, đang chờ xác nhận (ACK ${acks}/2).`, cls: 'warn' };
  if (src.state === 'ABORTED') return src.complete ? { text: 'ABORTED — đã phát quyết định tới cả hai bên (ACK 2/2).', cls: 'bad' } : { text: `Đã quyết định ABORT, đang chờ xác nhận (ACK ${acks}/2).`, cls: 'warn' };
  return { text: `Coordinator đang ghi trạng thái ${src.state} — chưa phải quyết định cuối.`, cls: 'warn' };
}
function renderBooking() {
  const req = S.req, st = effStatus(req), unresolved = st === 'unknown' || st === 'sending';
  const mode = unresolved ? 'locked' : 'free';
  const tokenEl = $('#bk-token'), airEl = $('#bk-airline'), hotelEl = $('#bk-hotel');
  if (S.formMode !== mode) {
    S.formMode = mode;
    if (mode === 'locked') { tokenEl.value = req.token; airEl.value = req.airline; hotelEl.value = req.hotel; }
    else { if (!S.draftToken) S.draftToken = newToken(); tokenEl.value = S.draftToken; }
  }
  const lock = S.inflight || unresolved;
  for (const el of [tokenEl, airEl, hotelEl]) setDisabled(el, lock);
  setDisabled($('#bk-submit'), lock);
  setDisabled($('#bk-newtoken'), lock);
  setDisabled($('#bk-retry'), S.inflight || !req);
  setDisabled($('#bk-check'), S.inflight || !req || st === 'done');
  setDisabled($('#bk-discard'), S.inflight || !req);
  const lockEl = $('#bk-lock');
  setHidden(lockEl, !unresolved);
  setText(lockEl, unresolved ? `Có yêu cầu chưa xác định kết quả (mã ${req.token}). Biểu mẫu bị khóa để không dùng lại token với nội dung khác. Hãy Gửi lại cùng yêu cầu hoặc Kiểm tra.` : '');
  const status = $('#bk-status');
  const m = S.bkMsg;
  setText(status, m ? m.text : S.inflight ? 'Đang gửi yêu cầu…' : '');
  setClass(status, 'status ' + (m ? m.cls : ''));
  const box = $('#bk-result');
  const live = req ? liveRecord(req) : null;
  const useLive = live && nodeFresh(node('coordinator'));
  if (req && st === 'done' && useLive && (live.state !== req.state || live.complete !== req.complete)) saveReq({ ...req, state: live.state, ack: live.ack, complete: live.complete });
  const sum = req ? resultSummary(req, useLive ? live : (st === 'done' ? req : null)) : null;
  const sig = JSON.stringify([req, useLive ? [live.state, live.complete] : null, st]);
  if (box.dataset.sig !== sig) {
    box.dataset.sig = sig;
    if (!req) box.replaceChildren();
    else box.replaceChildren(h('dl', { class: 'kv' },
      h('dt', { text: 'Mã yêu cầu' }), h('dd', { class: 'mono', text: req.token }),
      h('dt', { text: 'Nội dung' }), h('dd', { text: `Airline ${req.airline} • Hotel ${req.hotel}` }),
      h('dt', { text: 'Giao dịch' }), h('dd', {}, req.tx ? h('button', { type: 'button', class: 'linklike', onclick: () => selectTx(req.tx), text: req.tx }) : 'chưa biết'),
      h('dt', { text: 'Kết quả' }), h('dd', {}, st === 'done' || sum ? tag(sum ? sum.text : '—', sum ? sum.cls : 'muted')
        : tag('Chưa xác định kết quả — kiểm tra hoặc gửi lại cùng yêu cầu', 'warn'))));
  }
}
function readBookingForm() {
  return { token: $('#bk-token').value.trim(), airline: $('#bk-airline').value, hotel: $('#bk-hotel').value };
}
async function sendBooking(payload) {
  if (S.inflight) return;
  S.inflight = true;
  const prev = S.req;
  const sameToken = prev && prev.token === payload.token;
  const req = { ...payload, status: 'sending', tx: sameToken ? prev.tx : null, state: null, ack: [], complete: false, savedAt: new Date().toISOString() };
  saveReq(req);                         // LƯU TRƯỚC KHI GỬI
  setBk('', ''); render();
  const restore = () => saveReq(prev);
  try {
    const r = await post('/api/bookings', payload, 40000);
    const d = r.data || {};
    if (r.status === 200 && typeof d.state === 'string' && typeof d.tx === 'string') {
      if (d.state === 'COMMITTED' || d.state === 'ABORTED') {
        saveReq({ ...req, status: 'done', tx: d.tx, state: d.state, ack: d.ack || [], complete: !!d.complete });
        S.draftToken = newToken(); S.formMode = null;
        setBk(d.state === 'ABORTED' ? 'Quyết định ABORTED đã được ghi bền vững. Yêu cầu mới phải dùng mã mới.' : '', d.state === 'ABORTED' ? 'bad' : 'ok');
      } else {
        saveReq({ ...req, status: 'unknown', tx: d.tx });
        setBk(`Coordinator mới ghi ${d.state}, chưa có quyết định cuối. Kiểm tra hoặc gửi lại cùng yêu cầu.`, 'warn');
      }
    } else if (r.status === 409 && d.error && d.error.code === 'token_conflict') {
      restore(); setBk('Mã này đã được dùng cho nội dung khác — không tạo giao dịch mới. Dùng nút “Mã mới” cho yêu cầu mới.', 'bad');
    } else if (r.status === 400 || r.status === 403 || r.status === 413) {
      restore(); setBk('Yêu cầu bị từ chối trước khi thực hiện: ' + errText(r), 'bad');
    } else {
      saveReq({ ...req, status: 'unknown', tx: d.tx || req.tx || null });
      setBk('Chưa xác định kết quả — kiểm tra hoặc gửi lại cùng yêu cầu. (' + errText(r) + ')', 'warn');
    }
  } catch (e) {
    saveReq({ ...req, status: 'unknown' });
    setBk('Chưa xác định kết quả — kiểm tra hoặc gửi lại cùng yêu cầu. (Mất phản hồi: ' + (e.aborted ? 'hết thời gian chờ' : 'lỗi mạng') + ')', 'warn');
  } finally { S.inflight = false; render(); }
}
function onBookingSubmit(ev) {
  ev.preventDefault();
  if (S.inflight) return;
  const p = readBookingForm(), req = S.req, st = effStatus(req);
  if (!p.token) { setBk('Cần nhập mã yêu cầu.', 'bad'); render(); return; }
  if (req && (st === 'unknown' || st === 'sending')) { setBk('Còn yêu cầu chưa xác định kết quả. Hãy Gửi lại/Kiểm tra hoặc Hủy bản lưu trước khi đặt yêu cầu mới.', 'warn'); render(); return; }
  if (req && req.token === p.token && (req.airline !== p.airline || req.hotel !== p.hotel)) {
    setBk(`Mã ${p.token} đã dùng cho Airline ${req.airline} • Hotel ${req.hotel}. Không dùng lại token với nội dung khác — bấm “Mã mới”.`, 'bad'); render(); return;
  }
  sendBooking(p);
}
function onRetry() {
  if (S.inflight || !S.req) return;
  const { token, airline, hotel } = S.req;
  sendBooking({ token, airline, hotel });   // giữ nguyên token/payload đã lưu
}
async function onCheck() {
  const req = S.req;
  if (S.inflight || !req) return;
  setBk('Đang đọc Coordinator…', ''); render();
  try {
    const r = await api('GET', '/api/overview', null, 8000);
    const co = r.ok && r.data && r.data.nodes ? r.data.nodes.coordinator : null;
    if (!co || co.stale || !co.snapshot) { setBk('Chưa đọc được Coordinator nên chưa kết luận gì. Có thể gửi lại cùng yêu cầu.', 'warn'); }
    else {
      const t = co.snapshot.transactions.find((x) => x.token === req.token);
      if (!t) setBk('Coordinator đọc được nhưng chưa có giao dịch với mã này (begin chưa đến nơi). Gửi lại cùng yêu cầu là an toàn nhờ idempotency.', 'warn');
      else if (decided(t)) { saveReq({ ...req, status: 'done', tx: t.tx, state: t.state, ack: t.ack, complete: t.complete }); S.draftToken = newToken(); S.formMode = null; setBk(`Coordinator ghi quyết định cuối: ${t.state}.`, t.state === 'COMMITTED' ? 'ok' : 'bad'); }
      else { saveReq({ ...req, tx: t.tx }); setBk(`Coordinator đang ghi ${t.state} — chưa phải quyết định cuối. Không coi là ABORT.`, 'warn'); }
    }
  } catch (e) { setBk('Không kết nối được dashboard — chưa kết luận gì.', 'warn'); }
  render();
}
function onDiscard() {
  if (S.inflight || !S.req) return;
  if (!window.confirm('Chỉ xóa bản lưu trên trình duyệt này. Giao dịch đã gửi (nếu có) vẫn tồn tại ở backend. Tiếp tục?')) return;
  saveReq(null); S.draftToken = newToken(); S.formMode = null; setBk('Đã xóa bản lưu yêu cầu trên trình duyệt.', '');
  render();
}

/* ----- gây lỗi ----- */
function fillFaultPoints() {
  const svc = $('#ft-service').value, sel = $('#ft-point'), old = sel.value;
  sel.replaceChildren(...FAULT_POINTS[svc].map((p) => h('option', { value: p, text: p })));
  if (FAULT_POINTS[svc].includes(old)) sel.value = old;
  updateFaultHint();
}
function updateFaultHint() {
  setText($('#ft-hint'), FAULT_HINT[$('#ft-point').value] || '');
  setDisabled($('#ft-seconds'), $('#ft-mode').value !== 'delay');
}
async function onFault(ev) {
  ev.preventDefault();
  const service = $('#ft-service').value, point = $('#ft-point').value, mode = $('#ft-mode').value;
  const body = { service, point, mode };
  if (mode === 'delay') {
    const s = Number($('#ft-seconds').value);
    if (!Number.isFinite(s) || s < 0 || s > 30) { S.ftMsg = { text: 'Số giây phải trong khoảng 0–30.', cls: 'bad' }; render(); return; }
    body.seconds = s;
  }
  if (S.armed.length && !window.confirm('Đã có lệnh lỗi đang chờ. Một kịch bản chính chỉ nên arm một lỗi. Vẫn arm thêm?')) return;
  S.ftBusy = true; render();
  try {
    const r = await post('/api/faults', body, 8000);
    if (r.ok) {
      S.armed.push({ service, point, mode, seconds: body.seconds, at: new Date(), pid: lifeOf(service).pid || null });
      S.ftMsg = { text: `Đã arm ${NODE_LABEL[service]} • ${point} • ${mode}. Lỗi chỉ xảy ra một lần ở lần gọi tiếp theo.`, cls: 'ok' };
    } else S.ftMsg = { text: 'Không arm được: ' + errText(r) + (r.status === 502 || r.status === 504 ? ' (node chưa sẵn sàng hoặc không phản hồi)' : ''), cls: 'bad' };
  } catch (e) { S.ftMsg = { text: 'Không kết nối được dashboard — chưa biết lệnh arm đã tới node hay chưa.', cls: 'warn' }; }
  S.ftBusy = false; render();
}
function renderFaults() {
  setDisabled($('#ft-submit'), S.ftBusy);
  S.armed = S.armed.filter((a) => {
    const lc = lifeOf(a.service), n = node(a.service);
    if (lc.managed) return lc.state === 'running' && (lc.pid || null) === a.pid;
    return !(n && n.error);
  });
  const st = $('#ft-status');
  setText(st, S.ftMsg ? S.ftMsg.text : '');
  setClass(st, 'status ' + (S.ftMsg ? S.ftMsg.cls : ''));
  const list = $('#ft-armed');
  const sig = JSON.stringify(S.armed.map((a) => [a.service, a.point, a.mode, a.seconds, +a.at]));
  if (list.dataset.sig !== sig) {
    list.dataset.sig = sig;
    list.replaceChildren(...S.armed.map((a) => h('li', {}, tag('Đã arm', 'warn'), ` ${clock(a.at)} — ${NODE_LABEL[a.service]} • ${a.point} • ${a.mode}${a.mode === 'delay' ? ' ' + a.seconds + 's' : ''}. Dashboard không có API xem hook còn chờ hay đã chạy; hook bị xóa khi node khởi động lại.`)));
  }
}

/* ----- tải đồng thời ----- */
const loadKind = (r) => (r.result ? r.result.state : r.outcome_unknown ? 'UNKNOWN' : 'ERROR');
function inventoryNow(name) {
  const n = node(name);
  if (!nodeFresh(n)) return null;
  const it = n.snapshot.inventory.find((x) => x.resource === 'DEFAULT');
  return it ? it.available : null;
}
async function onLoad(ev) {
  ev.preventDefault();
  if (S.loadStarting || (S.loadRun && S.loadRun.state === 'running')) return;
  const count = Number($('#ld-count').value), workers = Number($('#ld-workers').value);
  if (!Number.isInteger(count) || count < 1 || count > 50 || !Number.isInteger(workers) || workers < 1 || workers > 8) {
    S.loadMsg = { text: 'Số booking phải là số nguyên 1–50 và số worker 1–8.', cls: 'bad' }; render(); return;
  }
  S.loadStarting = true;
  const before = { airline: inventoryNow('airline'), hotel: inventoryNow('hotel') };
  S.loadMsg = { text: 'Đang tạo đợt tải…', cls: '' }; render();
  try {
    const r = await post('/api/load', { count, workers }, 8000);
    if (r.status === 202 && r.data && r.data.run_id) {
      S.loadRun = { run_id: r.data.run_id, count, workers, before, state: 'running', processed: 0, results: [] };
      storeSet(LOAD_KEY, { run_id: r.data.run_id, count, workers, before });
      S.loadMsg = null;
      watchLoad();
    } else if (r.status === 409) S.loadMsg = { text: 'Đang có một đợt tải khác chạy (chỉ cho phép một đợt tại một thời điểm).', cls: 'warn' };
    else S.loadMsg = { text: 'Không chạy được tải: ' + errText(r), cls: 'bad' };
  } catch (e) { S.loadMsg = { text: 'Không kết nối được dashboard — chưa biết đợt tải đã bắt đầu hay chưa.', cls: 'warn' }; }
  S.loadStarting = false; render();
}
async function watchLoad() {
  const id = ++S.loadLoop;
  while (S.loadRun && id === S.loadLoop) {
    try {
      const r = await api('GET', '/api/load/' + encodeURIComponent(S.loadRun.run_id), null, 8000);
      if (id !== S.loadLoop) return;
      if (r.status === 404) { S.loadMsg = { text: 'Đợt tải không còn trong bộ nhớ dashboard (có thể dashboard đã khởi động lại).', cls: 'warn' }; S.loadRun = null; storeDel(LOAD_KEY); render(); return; }
      if (r.ok && r.data) { S.loadRun = { ...S.loadRun, state: r.data.state, processed: r.data.processed, results: r.data.results, count: r.data.count, workers: r.data.workers }; S.loadMsg = null; }
    } catch (e) { S.loadMsg = { text: 'Mất kết nối khi đọc tiến độ — vẫn thử lại mỗi giây.', cls: 'warn' }; }
    render();
    if (S.loadRun && S.loadRun.state === 'complete') return;
    await sleep(1000);
  }
}
function reconcile(run) {
  if (!run.before || run.before.airline == null || run.before.hotel == null) return { text: 'Không đối chiếu tồn: lúc bắt đầu chưa đọc mới được tồn của cả hai RM.', cls: 'muted' };
  const co = node('coordinator');
  if (!nodeFresh(co)) return { text: 'Chưa đối chiếu: chưa đọc mới được Coordinator.', cls: 'warn' };
  const prefix = `load:${run.run_id}:`;
  const txs = co.snapshot.transactions.filter((t) => typeof t.token === 'string' && t.token.startsWith(prefix));
  const open = txs.filter((t) => !decided(t)).length, waiting = txs.filter((t) => decided(t) && !t.complete).length;
  const byToken = new Set(txs.filter(decided).map((t) => t.token));
  const unknown = run.results.filter((r) => loadKind(r) === 'UNKNOWN' && !byToken.has(r.token)).length;
  if (open || waiting || unknown) return { text: `Chưa đối chiếu được: ${open} chưa quyết định, ${waiting} chờ ACK, ${unknown} chưa xác định.`, cls: 'warn' };
  const committed = txs.filter((t) => t.state === 'COMMITTED').length;
  const a = inventoryNow('airline'), hh = inventoryNow('hotel');
  if (a == null || hh == null) return { text: 'Chưa đối chiếu: chưa đọc mới được tồn của RM.', cls: 'warn' };
  const ok = a === run.before.airline - committed && hh === run.before.hotel - committed;
  return ok ? { text: `Khớp: ${committed} commit, tồn Airline ${run.before.airline}→${a}, Hotel ${run.before.hotel}→${hh}.`, cls: 'ok' }
            : { text: `Chưa khớp: ${committed} commit nhưng tồn Airline ${run.before.airline}→${a}, Hotel ${run.before.hotel}→${hh}. Đợi đọc lại rồi kiểm tra.`, cls: 'bad' };
}
function renderLoad() {
  const run = S.loadRun, running = !!run && run.state === 'running';
  setDisabled($('#ld-submit'), running || S.loadStarting);
  const st = $('#ld-status');
  setText(st, S.loadMsg ? S.loadMsg.text : running ? `Đang chạy: ${run.processed}/${run.count} yêu cầu (${run.workers} worker)…` : run ? `Hoàn tất ${run.processed}/${run.count} yêu cầu.` : '');
  setClass(st, 'status ' + (S.loadMsg ? S.loadMsg.cls : ''));
  const prog = $('#ld-progress');
  setHidden(prog, !run);
  if (run) { prog.max = run.count; prog.value = run.processed; }
  const counts = { COMMITTED: 0, ABORTED: 0, UNKNOWN: 0, OTHER: 0 };
  if (run) for (const r of run.results) { const k = loadKind(r); counts[k in counts ? k : 'OTHER']++; }
  const summary = $('#ld-summary');
  if (!run) setText(summary, '');
  else {
    const rec = run.state === 'complete' ? reconcile(run) : null;
    setText(summary, `COMMITTED ${counts.COMMITTED} • ABORTED ${counts.ABORTED} • chưa xác định ${counts.UNKNOWN} • lỗi khác ${counts.OTHER}` + (rec ? ' — ' + rec.text : ''));
    if (counts.UNKNOWN) summary.title = 'Kết quả chưa xác định phải kiểm tra theo token trước khi chốt tổng.';
  }
  setHidden($('#ld-wrap'), !run || run.results.length === 0);
  if (run) syncList($('#ld-body'), run.results.map((r, i) => ({ r, i })), (x) => x.r.token,
    () => h('tr', {}, h('td'), h('td', { class: 'mono' }), h('td'), h('td', { class: 'mono' }), h('td')),
    (tr, { r, i }) => {
      const k = loadKind(r), c = tr.children;
      setText(c[0], String(i + 1)); setText(c[1], 'load:…:' + r.token.split(':').pop());
      const label = k === 'UNKNOWN' ? 'Chưa xác định' : k === 'ERROR' ? 'Lỗi' : k;
      if (c[2].textContent !== label) c[2].replaceChildren(tag(label, k === 'UNKNOWN' ? 'warn' : k === 'ERROR' ? 'bad' : stateCls(k)));
      const tx = r.result ? r.result.tx : (r.tx || '');
      setText(c[3], tx ? shortId(tx) : '—');
      setText(c[4], r.result ? `${(r.result.ack || []).length}/2` : '—');
    });
}

/* ----- bảng giao dịch + chi tiết ----- */
function selectTx(tx) {
  S.selectedTx = tx; S.detail = null;
  render();
  refreshDetail().then(render);
}
function renderTx() {
  const list = coordTxs(), co = node('coordinator'), f = fresh(co);
  setText($('#tx-fresh'), 'Coordinator: ' + f.text);
  setClass($('#tx-fresh'), 'fresh ' + f.cls);
  for (const b of document.querySelectorAll('#tx-filters button')) { const on = String(b.dataset.filter === S.filter); if (b.getAttribute('aria-pressed') !== on) b.setAttribute('aria-pressed', on); }
  const rows = list ? list.filter(FILTERS[S.filter]) : [];
  const empty = $('#tx-empty');
  let msg = '';
  if (!S.overview) msg = 'Đang chờ dữ liệu đầu tiên…';
  else if (!list) msg = 'Không xác định — chưa đọc được Coordinator, không suy ra “chưa có giao dịch”.';
  else if (list.length === 0) msg = 'Chưa có giao dịch nào.';
  else if (rows.length === 0) msg = 'Không có giao dịch khớp bộ lọc.';
  setHidden(empty, !msg); setText(empty, msg);
  syncList($('#tx-body'), rows, (t) => t.tx,
    () => h('tr', {}, h('td', {}, h('button', { type: 'button', class: 'linklike' })), h('td', { class: 'mono' }), h('td'), h('td'), h('td'), h('td'), h('td')),
    (tr, t) => {
      const c = tr.children, btn = c[0].firstChild;
      setText(btn, shortId(t.tx)); btn.setAttribute('aria-label', 'Xem giao dịch ' + t.tx);
      const sel = t.tx === S.selectedTx;
      if (sel) btn.setAttribute('aria-current', 'true'); else btn.removeAttribute('aria-current');
      setClass(tr, sel ? 'sel' : '');
      setText(c[1], t.token);
      const put = (cell, text, cls) => { const cur = cell.firstChild; if (!cur || cur.textContent !== text || cur.className !== 'tag ' + cls) cell.replaceChildren(tag(text, cls)); };
      put(c[2], t.state, stateCls(t.state));
      const a = partState('airline', t.tx), hh = partState('hotel', t.tx);
      put(c[3], a.text, a.cls); put(c[4], hh.text, hh.cls);
      setText(c[5], `${t.ack.length}/2`);
      const s = txStatus(t); put(c[6], s.text, s.cls);
    });
}
function detailModel() {
  const tx = S.selectedTx, d = S.detail && S.detail.tx === tx ? S.detail : null;
  if (d && d.body && d.body.nodes) return { nodes: d.body.nodes, transaction: d.body.transaction, status: d.status, err: d.err, source: 'api' };
  if (!S.overview) return null;
  const nodes = {};
  for (const name of NAMES) {
    const n = node(name);
    const snap = n.snapshot ? JSON.parse(JSON.stringify(n.snapshot)) : null;
    if (snap) for (const key of Object.keys(snap)) if (Array.isArray(snap[key]) && key !== 'inventory') snap[key] = snap[key].filter((x) => x.tx === tx);
    nodes[name] = { ...n, snapshot: snap };
  }
  const tr = nodes.coordinator.snapshot ? nodes.coordinator.snapshot.transactions[0] || null : null;
  return { nodes, transaction: tr, status: d ? d.status : 0, err: d ? d.err : '', source: 'overview' };
}
function renderDetail() {
  const box = $('#tx-detail'), tx = S.selectedTx;
  if (!tx) {
    if (box.dataset.sig !== 'none') { box.dataset.sig = 'none'; box.replaceChildren(h('h3', { text: 'Chi tiết giao dịch' }), h('p', { class: 'note', text: 'Chọn một giao dịch trong bảng để xem payload, ACK, reservation, khóa và events từng node.' })); }
    return;
  }
  const m = detailModel();
  const sig = JSON.stringify([tx, m && m.status, m && m.err, m && m.source, m && m.transaction, m && NAMES.map((n) => { const x = m.nodes[n]; return [x.snapshot, x.stale, x.error && x.error.code, x.last_success_at]; }), S.dashDown]);
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  const parts = [h('h3', {}, 'Giao dịch ', h('span', { class: 'mono', text: tx }), ' ', h('button', { type: 'button', class: 'secondary', onclick: () => { S.selectedTx = null; S.detail = null; render(); }, text: 'Bỏ chọn' }))];
  if (!m) { parts.push(h('p', { class: 'note', text: 'Chưa có dữ liệu.' })); box.replaceChildren(...parts); return; }
  if (m.status === 404) parts.push(h('p', { class: 'status ok', text: 'Coordinator đọc được và xác nhận không có giao dịch này.' }));
  if (m.status === 502 || m.status === 504) parts.push(h('p', { class: 'status warn', text: 'Không đọc được Coordinator — không kết luận giao dịch không tồn tại. Dữ liệu node khác (nếu có) ở dưới.' }));
  if (m.err) parts.push(h('p', { class: 'status warn', text: m.err + ' — hiển thị dữ liệu cũ.' }));
  if (m.source === 'overview' && !m.err) parts.push(h('p', { class: 'meta', text: 'Chưa lấy được chi tiết từ API; đang dùng dữ liệu overview.' }));
  const t = m.transaction;
  if (t) {
    const s = txStatus(t);
    parts.push(h('dl', { class: 'kv' },
      h('dt', { text: 'Token' }), h('dd', { class: 'mono', text: t.token }),
      h('dt', { text: 'Payload' }), h('dd', { text: `Airline ${t.payload.airline} • Hotel ${t.payload.hotel}` }),
      h('dt', { text: 'Quyết định' }), h('dd', {}, tag(t.state, stateCls(t.state))),
      h('dt', { text: 'ACK' }), h('dd', { text: t.ack.length ? `${t.ack.join(', ')} (${t.ack.length}/2)` : 'chưa có (0/2)' }),
      h('dt', { text: 'Tình trạng' }), h('dd', {}, tag(s.long, s.cls))));
  }
  for (const name of NAMES) {
    const n = m.nodes[name], f = fresh(n), snap = n.snapshot;
    const kids = [h('h3', {}, NODE_LABEL[name], ' ', h('span', { class: 'fresh ' + f.cls, text: f.text }))];
    if (!snap) kids.push(h('p', { class: 'note', text: 'Không xác định / chưa có snapshot.' }));
    else {
      if (name !== 'coordinator') {
        kids.push(h('p', { class: 'line' }, 'Reservation: ', snap.reservations.length ? snap.reservations.map((r) => [tag(r.state, stateCls(r.state)), ` ${r.resource == null ? '(tombstone)' : r.resource} `]) : '—'));
        kids.push(h('p', { class: 'line' }, 'Khóa: ', snap.locks.length ? snap.locks.map((l) => `${l.resource} `) : 'không giữ khóa'));
      }
      kids.push(snap.events.length ? h('div', { class: 'tablewrap' }, h('table', { class: 'data', 'aria-label': 'Events của ' + NODE_LABEL[name] },
        h('thead', {}, h('tr', {}, h('th', { text: 'seq' }), h('th', { text: 'Trạng thái' }), h('th', { text: 'Thời điểm (UTC, chỉ để quan sát)' }))),
        h('tbody', {}, snap.events.slice().sort((a, b) => a.seq - b.seq).map((e) => h('tr', {}, h('td', { text: String(e.seq) }), h('td', {}, tag(e.state, stateCls(e.state))), h('td', { class: 'mono', text: e.at || '' })))))) : h('p', { class: 'meta', text: 'Chưa có event cho giao dịch này.' }));
    }
    parts.push(h('div', {}, ...kids));
  }
  parts.push(h('p', { class: 'hint', text: 'Events xếp theo seq trong từng node. Không suy ra thứ tự thời gian toàn cục từ timestamp khác node.' }));
  box.replaceChildren(...parts);
}

/* ---------- sự kiện ---------- */
async function onServiceClick(ev) {
  const btn = ev.target.closest('button[data-action]');
  if (!btn || btn.disabled) return;
  const name = btn.closest('.service').dataset.name, action = btn.dataset.action;
  if (S.busy[name]) return;
  S.busy[name] = true; S.svcMsg[name] = null; render();
  try {
    const r = await post(`/api/services/${name}/${action}`, {}, 30000);
    if (r.ok) {
      S.svcMsg[name] = { text: { start: 'Đã chạy', stop: 'Đã dừng', restart: 'Đã khởi động lại' }[action] + ' (cùng DB cũ).', cls: 'ok' };
      S.armed = S.armed.filter((a) => a.service !== name);
    } else {
      const code = r.data && r.data.error ? r.data.error.code : '';
      S.svcMsg[name] = { text: (code === 'port_busy' ? 'Cổng đang bị tiến trình khác chiếm; dashboard không can thiệp tiến trình ngoài. ' : '') + errText(r), cls: 'bad' };
    }
  } catch (e) { S.svcMsg[name] = { text: 'Mất phản hồi từ dashboard — kiểm tra trạng thái ở thẻ dịch vụ.', cls: 'warn' }; }
  S.busy[name] = false; render();
}
function init() {
  S.req = storeGet(REQ_KEY);
  if (S.req && S.req.status === 'sending') S.req = { ...S.req, status: 'unknown' };   // trang bị tải lại khi đang gửi
  document.addEventListener('click', (ev) => { if (ev.target.closest('.service')) onServiceClick(ev); });
  $('#bk-form').addEventListener('submit', onBookingSubmit);
  $('#bk-retry').addEventListener('click', onRetry);
  $('#bk-check').addEventListener('click', onCheck);
  $('#bk-discard').addEventListener('click', onDiscard);
  $('#bk-newtoken').addEventListener('click', () => { S.draftToken = newToken(); $('#bk-token').value = S.draftToken; setBk('', ''); render(); });
  $('#bk-token').addEventListener('input', (e) => { S.draftToken = e.target.value; });
  $('#ft-service').addEventListener('change', fillFaultPoints);
  $('#ft-point').addEventListener('change', updateFaultHint);
  $('#ft-mode').addEventListener('change', updateFaultHint);
  $('#ft-form').addEventListener('submit', onFault);
  $('#ld-form').addEventListener('submit', onLoad);
  $('#tx-filters').addEventListener('click', (ev) => { const b = ev.target.closest('button[data-filter]'); if (b) { S.filter = b.dataset.filter; render(); } });
  $('#tx-body').addEventListener('click', (ev) => { const tr = ev.target.closest('tr[data-key]'); if (tr) selectTx(tr.dataset.key); });
  window.addEventListener('storage', (ev) => { if (ev.key === REQ_KEY) { S.req = storeGet(REQ_KEY); S.formMode = null; render(); } });
  fillFaultPoints();
  const saved = storeGet(LOAD_KEY);
  if (saved && saved.run_id) { S.loadRun = { ...saved, state: 'running', processed: 0, results: [] }; watchLoad(); }
  render();
  pollLoop();
}
init();
})();

'use strict';
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const AB = ['str', 'dex', 'con', 'int', 'wis', 'cha'];
const AB_SHORT = { str: 'СИЛ', dex: 'ЛОВ', con: 'ТЕЛ', int: 'ИНТ', wis: 'МДР', cha: 'ХАР' };
const SKILL_AB = ['dex', 'int', 'str', 'wis', 'wis', 'cha', 'cha', 'int', 'dex', 'int', 'wis', 'cha', 'int', 'wis', 'int', 'dex', 'cha', 'wis'];
const mod = (n) => Math.floor((n - 10) / 2);
const sg = (n) => (n < 0 ? '' : '+') + n;
const pb = (lvl) => 2 + Math.floor((Math.max(1, lvl) - 1) / 4);

const S = { players: [], rolls: [], skills: [], invite: null, group: null, selected: new Set(), onlyHidden: false };

function store(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
function load(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } }

function toast(t) {
  const el = $('#toast'); el.textContent = t; el.classList.add('show');
  clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove('show'), 2600);
}

async function api(path, body, method = 'POST') {
  const r = await fetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  if (r.status === 401) { location.href = '/login'; throw new Error('auth'); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { toast('⚠️ ' + (data.error || r.status)); throw new Error(data.error); }
  return data;
}

/* ---------- игроки ---------- */
function hpColor(p) { return p > 50 ? 'var(--green)' : p > 25 ? '#e0a020' : 'var(--red)'; }

function lastRollOf(id) {
  for (let i = S.rolls.length - 1; i >= 0; i--) if (S.rolls[i].tg_id === id) return S.rolls[i];
  return null;
}

function renderPlayers() {
  const box = $('#players');
  $('#no-players').classList.toggle('hidden', S.players.length > 0);
  box.innerHTML = S.players.map((p) => {
    const c = p.char, pct = Math.max(0, Math.min(100, (c.hp / c.mhp) * 100));
    const last = lastRollOf(p.id);
    const sel = S.selected.has(p.id);
    return `<div class="pc ${sel ? 'sel' : ''}" data-id="${p.id}">
      <div class="pc-top">
        <input type="checkbox" class="pick" ${sel ? 'checked' : ''} title="Выбрать">
        <div class="pc-name"><b>${esc(c.name || p.first_name)}</b>
          <span>${esc(c.cls || 'без класса')} · ур. ${c.lvl} · ${esc(p.first_name)}${p.username ? ' @' + esc(p.username) : ''}</span></div>
        <div class="ac">${c.ac}<small>КД</small></div>
        <button class="ghost sm edit" title="Редактировать">✎</button>
      </div>
      <div class="hp">
        <div class="hp-bar"><div style="width:${pct}%;background:${hpColor(pct)}"></div></div>
        <span class="hp-num">${c.hp} / ${c.mhp}</span>
        <input class="hp-in" type="number" placeholder="±" min="0">
        <button class="dmg" title="Урон">🗡</button><button class="heal" title="Лечение">✚</button>
      </div>
      <div class="mods">${AB.map((a) => `<div class="${c.sv.includes(a) ? 'prof' : ''}"><b>${sg(mod(c.st[a]))}</b>${AB_SHORT[a]}</div>`).join('')}</div>
      <div class="pc-last">${last ? `Последний: <b>${last.total}</b> ${esc(last.title || last.formula)}` : 'Ещё не бросал'}</div>
    </div>`;
  }).join('');
  updateTargetHint();
}

$('#players').addEventListener('click', async (e) => {
  const card = e.target.closest('.pc'); if (!card) return;
  const id = +card.dataset.id;
  if (e.target.classList.contains('pick')) {
    e.target.checked ? S.selected.add(id) : S.selected.delete(id);
    card.classList.toggle('sel', e.target.checked); updateTargetHint(); return;
  }
  if (e.target.closest('.edit')) return openEditor(id);
  const dmg = e.target.closest('.dmg'), heal = e.target.closest('.heal');
  if (dmg || heal) {
    const n = Math.abs(parseInt(card.querySelector('.hp-in').value, 10) || 0);
    if (!n) return toast('Введи число рядом с кнопкой');
    await api(`/api/player/${id}/hp`, { delta: dmg ? -n : n });
    toast(dmg ? `🗡 −${n} хитов` : `✚ +${n} хитов`);
  }
});
$('#sel-all').addEventListener('change', (e) => {
  S.selected = e.target.checked ? new Set(S.players.map((p) => p.id)) : new Set(); renderPlayers();
});
function targets() { return S.selected.size ? [...S.selected] : S.players.map((p) => p.id); }
function updateTargetHint() {
  const n = S.selected.size;
  $('#ask-target').textContent = n ? `Выбрано игроков: ${n}` : 'У всех игроков (или отметь галочками нужных)';
}

/* ---------- лента ---------- */
function rollHtml(r) {
  const d = r.detail || {};
  const cls = ['roll', d.c > 0 ? 'crit' : '', d.c < 0 ? 'fumble' : '', r.hidden ? 'secret' : '', r.tg_id ? '' : 'gm'].join(' ');
  const t = new Date(r.ts * 1000).toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' });
  const dice = (d.r || []).map((g, i) => `d${d.s[i]}: [${g.map((v) => (v < 0 ? `<s>${-v}</s>` : v)).join(', ')}]`).join(' ');
  return `<div class="${cls}">
    <div class="tot">${r.total}</div>
    <div><span class="time">${t}</span>
      <span class="who">${r.hidden ? '🙈 ' : ''}${esc(r.who)}</span>${r.title ? ` — <span class="ttl">${esc(r.title)}</span>` : ''}
      ${d.c > 0 ? ' 💥' : d.c < 0 ? ' 💀' : ''}
      <div class="meta">${esc(r.formula)} → ${dice}${d.b ? ' ' + sg(d.b) : ''}</div></div>
  </div>`;
}
function renderFeed() {
  const list = S.onlyHidden ? S.rolls.filter((r) => r.hidden) : S.rolls;
  $('#feed').innerHTML = list.length ? list.map(rollHtml).join('') : '<p class="hint">Тут будут появляться броски игроков в реальном времени.</p>';
}
$('#only-hidden').addEventListener('change', (e) => { S.onlyHidden = e.target.checked; renderFeed(); });
$('#clear').addEventListener('click', async () => { if (confirm('Удалить историю бросков?')) await api('/api/clear'); });

/* ---------- запрос броска ---------- */
let askKind = 'k';
$('#ask-kind').addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return;
  askKind = b.dataset.k;
  [...$('#ask-kind').children].forEach((x) => x.classList.toggle('on', x === b));
  $('#ask-skill').classList.toggle('hidden', askKind !== 'k');
  $('#ask-ab').classList.toggle('hidden', !['c', 's'].includes(askKind));
  $('#ask-formula').classList.toggle('hidden', askKind !== 'f');
});
$('#ask-go').addEventListener('click', async () => {
  if (!S.players.length) return toast('В партии пока никого нет');
  const v = { k: $('#ask-skill').value, c: $('#ask-ab').value, s: $('#ask-ab').value, i: '0', f: $('#ask-formula').value.trim() }[askKind];
  if (askKind === 'f' && !v) return toast('Впиши формулу');
  const r = await api('/api/ask', { players: targets(), kind: `${askKind}:${v}`, title: $('#ask-title').value.trim(), hidden: $('#ask-hidden').checked });
  toast(`📨 Запрос отправлен: ${r.sent.length}`);
  $('#ask-title').value = '';
});

/* ---------- бросок мастера ---------- */
let gmAdv = 0;
$('#gm-dice').innerHTML = [4, 6, 8, 10, 12, 20, 100].map((n) => `<button data-n="${n}">d${n}</button>`).join('');
$('#gm-dice').addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return;
  $('#gm-formula').value = '1d' + b.dataset.n;
});
$('#gm-adv').addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return;
  gmAdv = +b.dataset.a;
  [...$('#gm-adv').children].forEach((x) => x.classList.toggle('on', x === b));
});
async function gmRoll() {
  const r = await api('/api/roll', { formula: $('#gm-formula').value, title: $('#gm-title').value, adv: gmAdv, hidden: !$('#gm-public').checked });
  $('#gm-result').innerHTML = `<span class="big">${r.total}</span> <span class="hint">${esc(r.formula)} → ${esc(r.detail.txt)}</span>`;
}
$('#gm-go').addEventListener('click', gmRoll);
$('#gm-formula').addEventListener('keydown', (e) => { if (e.key === 'Enter') gmRoll(); });

/* ---------- инициатива ---------- */
const INI = load('ini', { list: [], cur: -1, round: 1, since: 0 });
function saveIni() { store('ini', INI); renderIni(); }
function renderIni() {
  INI.list.sort((a, b) => b.v - a.v || (b.bonus || 0) - (a.bonus || 0));
  $('#ini-list').innerHTML = INI.list.map((x, i) => `<li class="${i === INI.cur ? 'cur' : ''} ${x.npc ? 'npc' : ''}"><b>${x.v}</b><span>${esc(x.name)}</span><button data-i="${i}" title="Убрать">✕</button></li>`).join('');
  $('#ini-round').textContent = INI.list.length ? `Раунд ${INI.round}` : '';
}
function iniFromRoll(r) {
  if (r.title !== 'Инициатива' || !r.tg_id || r.ts < INI.since) return;
  const ex = INI.list.find((x) => x.pid === r.tg_id);
  if (ex) ex.v = r.total; else INI.list.push({ name: r.who, v: r.total, pid: r.tg_id });
  saveIni();
}
$('#ini-add').addEventListener('click', () => {
  const name = $('#ini-name').value.trim() || 'Монстр';
  const bonus = parseInt($('#ini-bonus').value, 10) || 0;
  const v = 1 + Math.floor(Math.random() * 20) + bonus;
  INI.list.push({ name, v, bonus, npc: true });
  $('#ini-name').value = ''; saveIni();
});
$('#ini-list').addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return;
  INI.list.splice(+b.dataset.i, 1); if (INI.cur >= INI.list.length) INI.cur = -1; saveIni();
});
$('#ini-next').addEventListener('click', () => {
  if (!INI.list.length) return;
  INI.cur++; if (INI.cur >= INI.list.length) { INI.cur = 0; INI.round++; }
  saveIni(); toast(`Ход: ${INI.list[INI.cur].name}`);
});
$('#ini-clear').addEventListener('click', () => { INI.list = []; INI.cur = -1; INI.round = 1; INI.since = Date.now() / 1000; saveIni(); });
$('#ini-ask').addEventListener('click', async () => {
  INI.since = Date.now() / 1000 - 1; INI.list = INI.list.filter((x) => x.npc); INI.cur = -1; INI.round = 1; saveIni();
  const r = await api('/api/ask', { players: targets(), kind: 'i:0' });
  toast(`⚔️ Инициатива запрошена: ${r.sent.length}`);
});

/* ---------- сообщения ---------- */
$('#msg-go').addEventListener('click', async () => {
  const text = $('#msg-text').value.trim(); if (!text) return;
  const r = await api('/api/message', { players: targets(), text });
  toast(`📜 Отправлено: ${r.sent}`); $('#msg-text').value = '';
});

/* ---------- приглашение и группа ---------- */
$('#invite-btn').addEventListener('click', async () => {
  if (!S.invite) return toast('Бот ещё не запустился');
  try { await navigator.clipboard.writeText(S.invite); toast('🔗 Ссылка скопирована — отправь её друзьям'); }
  catch (e) { prompt('Ссылка для игроков:', S.invite); }
});
function renderGroup() {
  $('#group-btn').textContent = S.group ? `💬 ${S.group.title || 'Группа'}` : '💬 Группа не подключена';
}
$('#group-btn').addEventListener('click', async () => {
  if (S.group) {
    if (confirm(`Броски публикуются в «${S.group.title}». Отключить?`)) await api('/api/group/unbind');
  } else {
    const code = (S.invite || '').split('start=')[1] || '';
    prompt('Добавь бота в общий чат партии и отправь там эту команду:', `/bindgroup ${code}`);
  }
});

/* ---------- редактор персонажа ---------- */
let edId = null;
function openEditor(id) {
  const p = S.players.find((x) => x.id === id); if (!p) return;
  edId = id; const c = p.char, f = $('#ed-form');
  for (const k of ['name', 'cls', 'lvl', 'ac', 'hp', 'mhp']) f.elements[k].value = c[k];
  $('#ed-stats').innerHTML = AB.map((a) => `<label>${AB_SHORT[a]}<input type="number" min="1" max="30" name="st_${a}" value="${c.st[a]}">
    <span class="sv"><input type="checkbox" name="sv_${a}" ${c.sv.includes(a) ? 'checked' : ''}> спасбросок</span></label>`).join('');
  $('#ed-skills').innerHTML = S.skills.map((s, i) => `<label><input type="checkbox" name="sk_${i}" ${c.sk.includes(i) ? 'checked' : ''}> ${esc(s)} <small>${AB_SHORT[SKILL_AB[i]]}</small></label>`).join('');
  $('#editor').showModal();
}
$('#editor').addEventListener('close', async () => {
  if ($('#editor').returnValue !== 'save' || edId == null) return;
  const f = $('#ed-form').elements;
  const data = { name: f.name.value, cls: f.cls.value, lvl: f.lvl.value, ac: f.ac.value, hp: f.hp.value, mhp: f.mhp.value, st: {}, sv: [], sk: [] };
  AB.forEach((a) => { data.st[a] = f['st_' + a].value; if (f['sv_' + a].checked) data.sv.push(a); });
  S.skills.forEach((_, i) => { if (f['sk_' + i].checked) data.sk.push(i); });
  await api(`/api/player/${edId}`, data);
  toast('💾 Сохранено — лист в Telegram обновится сам');
});
$('#ed-del').addEventListener('click', async () => {
  if (!confirm('Убрать игрока из партии? Он сможет вернуться по ссылке-приглашению.')) return;
  await api(`/api/player/${edId}`, null, 'DELETE'); $('#editor').close();
});

/* ---------- живое соединение ---------- */
function setConn(ok) { const c = $('#conn'); c.textContent = ok ? 'онлайн' : 'нет связи'; c.classList.toggle('off', !ok); }
function connect() {
  const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
  ws.onopen = () => { setConn(true); refresh(); };
  ws.onclose = () => { setConn(false); setTimeout(connect, 2000); };
  ws.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === 'roll') { S.rolls.push(ev.roll); if (S.rolls.length > 300) S.rolls.shift(); renderFeed(); renderPlayers(); iniFromRoll(ev.roll); }
    else if (ev.type === 'players') { S.players = ev.players; renderPlayers(); }
    else if (ev.type === 'cleared') { S.rolls = []; renderFeed(); renderPlayers(); }
    else if (ev.type === 'group') { S.group = ev.group; renderGroup(); toast(ev.group ? `💬 Группа подключена: ${ev.group.title}` : 'Группа отключена'); }
  };
}
async function refresh() {
  const st = await api('/api/state', null, 'GET');
  Object.assign(S, { players: st.players, rolls: st.rolls, skills: st.skills, invite: st.invite, group: st.group });
  if (!$('#ask-skill').options.length) $('#ask-skill').innerHTML = st.skills.map((s, i) => `<option value="${i}">${esc(s)}</option>`).join('');
  renderPlayers(); renderFeed(); renderGroup(); renderIni();
}
connect();

'use strict';
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const AB = ['str', 'dex', 'con', 'int', 'wis', 'cha'];
const AB_SHORT = { str: 'СИЛ', dex: 'ЛОВ', con: 'ТЕЛ', int: 'ИНТ', wis: 'МДР', cha: 'ХАР' };
const SKILL_AB = ['dex', 'int', 'str', 'wis', 'wis', 'cha', 'cha', 'int', 'dex', 'int', 'wis', 'cha', 'int', 'wis', 'int', 'dex', 'cha', 'wis'];
const TEXTS = ['feat', 'spells', 'inv', 'notes'];
const mod = (n) => Math.floor((n - 10) / 2);
const sg = (n) => (n < 0 ? '' : '+') + n;

const S = { chars: [], rolls: [], skills: [], invite: null, selected: new Set() };

function store(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
function load(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } }

function toast(t) {
  const el = $('#toast'); el.textContent = t; el.classList.add('show');
  clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove('show'), 2800);
}

async function api(path, body, method = 'POST') {
  const r = await fetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  if (r.status === 401) { location.href = '/login'; throw new Error('auth'); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { toast('⚠️ ' + (data.error || r.status)); throw new Error(data.error); }
  return data;
}

async function copy(text, okMsg) {
  try { await navigator.clipboard.writeText(text); toast(okMsg); }
  catch (e) { prompt('Скопируй:', text); }
}

/* ---------- персонажи ---------- */
function hpColor(p) { return p > 50 ? 'var(--green)' : p > 25 ? '#e0a020' : 'var(--red)'; }
const charById = (id) => S.chars.find((c) => c.id === id);

function lastRollOf(tgId) {
  if (!tgId) return null;
  for (let i = S.rolls.length - 1; i >= 0; i--) if (S.rolls[i].tg_id === tgId) return S.rolls[i];
  return null;
}

function renderPlayers() {
  const box = $('#players');
  $('#no-players').classList.toggle('hidden', S.chars.length > 0);
  box.innerHTML = S.chars.map((ch) => {
    const c = ch.char, pct = Math.max(0, Math.min(100, (c.hp / c.mhp) * 100));
    const last = lastRollOf(ch.tg_id);
    const sel = S.selected.has(ch.id);
    const sub = [`${c.cls || 'без класса'} ${c.lvl} ур.`, c.race].filter(Boolean).join(' · ');
    const owner = ch.player
      ? `🎮 ${esc(ch.player.first_name)}${ch.player.username ? ' @' + esc(ch.player.username) : ''}`
      : '<span class="wait">⏳ ждёт игрока</span>';
    return `<div class="pc ${sel ? 'sel' : ''} ${ch.player ? '' : 'unbound'}" data-id="${ch.id}">
      <div class="pc-top">
        <input type="checkbox" class="pick" ${sel ? 'checked' : ''} title="Выбрать">
        <div class="pc-name"><b>${esc(c.name || 'Безымянный')}</b><span>${esc(sub)}</span></div>
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
      <div class="pc-owner">${owner}<span class="grow"></span>
        ${ch.player ? '<button class="ghost sm send" title="Прислать игроку карточку и лист заново">📨 В Telegram</button>' : ''}
        <button class="ghost sm link" title="Личная ссылка на этого персонажа">🔗 Ссылка</button></div>
      ${ch.player ? `<div class="pc-last">${last ? `Последний: <b>${last.total}</b> ${esc(last.title || last.formula)}` : 'Ещё не бросал'}</div>` : ''}
    </div>`;
  }).join('');
  updateTargetHint();
}

$('#players').addEventListener('click', async (e) => {
  const card = e.target.closest('.pc'); if (!card) return;
  const id = +card.dataset.id, ch = charById(id);
  if (e.target.classList.contains('pick')) {
    e.target.checked ? S.selected.add(id) : S.selected.delete(id);
    card.classList.toggle('sel', e.target.checked); updateTargetHint(); return;
  }
  if (e.target.closest('.edit')) return openEditor(id);
  if (e.target.closest('.link')) {
    if (!ch.link) return toast('Бот ещё не запустился');
    return copy(ch.link, `🔗 Ссылка на «${ch.char.name}» скопирована — отправь её игроку`);
  }
  if (e.target.closest('.send')) { await api(`/api/char/${id}/send`); return toast('📨 Отправлено игроку'); }
  const dmg = e.target.closest('.dmg'), heal = e.target.closest('.heal');
  if (dmg || heal) {
    const n = Math.abs(parseInt(card.querySelector('.hp-in').value, 10) || 0);
    if (!n) return toast('Введи число рядом с кнопкой');
    await api(`/api/char/${id}/hp`, { delta: dmg ? -n : n });
    toast(dmg ? `🗡 −${n} хитов` : `✚ +${n} хитов`);
  }
});
$('#sel-all').addEventListener('change', (e) => {
  S.selected = e.target.checked ? new Set(S.chars.map((c) => c.id)) : new Set(); renderPlayers();
});
function targets() { return S.selected.size ? [...S.selected] : S.chars.filter((c) => c.tg_id).map((c) => c.id); }
function updateTargetHint() {
  const n = S.selected.size;
  $('#ask-target').textContent = n ? `Выбрано персонажей: ${n}` : 'У всех игроков (или отметь галочками нужных)';
}
function sentToast(prefix, sent, asked) {
  const missed = asked - sent;
  toast(`${prefix}: ${sent}` + (missed > 0 ? ` (без игрока: ${missed})` : ''));
}

/* ---------- лента ---------- */
function diceHtml(d) {
  return (d.r || []).map((g, i) => `d${d.s[i]}: [${g.map((v) => (v < 0 ? `<s>${-v}</s>` : v)).join(', ')}]`).join(' ') + (d.b ? ' ' + sg(d.b) : '');
}
function rollHtml(r) {
  const d = r.detail || {};
  const cls = ['roll', d.c > 0 ? 'crit' : '', d.c < 0 ? 'fumble' : '', r.tg_id ? '' : 'gm'].join(' ');
  const t = new Date(r.ts * 1000).toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' });
  const dmg = d.d ? `<div class="meta">урон <b class="dmgv">${d.d.t}</b> · ${esc(d.d.f)} → ${diceHtml(d.d)}</div>` : '';
  return `<div class="${cls}">
    <div class="tot">${r.total}</div>
    <div><span class="time">${t}</span>
      <span class="who">${esc(r.who)}</span>${r.title ? ` — <span class="ttl">${d.d ? '⚔️ ' : ''}${esc(r.title)}</span>` : ''}
      ${d.c > 0 ? ' 💥' : d.c < 0 ? ' 💀' : ''}
      <div class="meta">${esc(r.formula)} → ${diceHtml(d)}</div>${dmg}</div>
  </div>`;
}
function renderFeed() {
  $('#feed').innerHTML = S.rolls.length ? S.rolls.map(rollHtml).join('') : '<p class="hint">Тут будут появляться броски игроков в реальном времени.</p>';
}
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
  const ids = targets();
  if (!ids.length) return toast('Ни у одного персонажа ещё нет игрока');
  const v = { k: $('#ask-skill').value, c: $('#ask-ab').value, s: $('#ask-ab').value, i: '0', f: $('#ask-formula').value.trim() }[askKind];
  if (askKind === 'f' && !v) return toast('Впиши формулу');
  const r = await api('/api/ask', { chars: ids, kind: `${askKind}:${v}`, title: $('#ask-title').value.trim() });
  sentToast('📨 Запрос отправлен', r.sent.length, ids.length);
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
  const r = await api('/api/roll', { formula: $('#gm-formula').value, title: $('#gm-title').value, adv: gmAdv });
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
  const ids = targets();
  if (!ids.length) return toast('Ни у одного персонажа ещё нет игрока');
  INI.since = Date.now() / 1000 - 1; INI.list = INI.list.filter((x) => x.npc); INI.cur = -1; INI.round = 1; saveIni();
  const r = await api('/api/ask', { chars: ids, kind: 'i:0' });
  sentToast('⚔️ Инициатива запрошена', r.sent.length, ids.length);
});

/* ---------- сообщения ---------- */
$('#msg-go').addEventListener('click', async () => {
  const text = $('#msg-text').value.trim(); if (!text) return;
  const ids = targets();
  const r = await api('/api/message', { chars: ids, text });
  sentToast('📜 Отправлено', r.sent, ids.length); $('#msg-text').value = '';
});

/* ---------- общая ссылка ---------- */
$('#invite-btn').addEventListener('click', () => {
  if (!S.invite) return toast('Бот ещё не запустился');
  copy(S.invite, '🔗 Общая ссылка скопирована: по ней игрок заведёт пустого персонажа сам');
});

/* ---------- редактор персонажа ---------- */
let edId = null;
function atkRow(a = { n: '', hit: '', dmg: '' }) {
  const d = document.createElement('div'); d.className = 'atk-row';
  d.innerHTML = `<input placeholder="Длинный меч" value="${esc(a.n)}" class="an"><input type="number" placeholder="+5" value="${a.hit === '' ? '' : a.hit}" class="ah" title="Бонус к попаданию"><input placeholder="1d8+3" value="${esc(a.dmg)}" class="ad" title="Урон"><button type="button" class="ghost" title="Убрать">✕</button>`;
  d.querySelector('button').onclick = () => d.remove();
  $('#ed-atk').append(d);
}
$('#ed-atk-add').addEventListener('click', () => atkRow());

function openEditor(id) {
  const ch = id == null ? null : charById(id);
  edId = id;
  const c = ch ? ch.char : { name: '', cls: '', race: '', bg: '', al: '', lvl: 1, hp: 10, mhp: 10, ac: 10, spd: 30, st: Object.fromEntries(AB.map((a) => [a, 10])), sv: [], sk: [], atk: [], feat: '', spells: '', inv: '', notes: '' };
  const f = $('#ed-form');
  for (const k of ['name', 'cls', 'race', 'bg', 'al', 'lvl', 'ac', 'hp', 'mhp', 'spd', ...TEXTS]) f.elements[k].value = c[k] ?? '';
  $('#ed-title').textContent = ch ? 'Персонаж' : 'Новый персонаж';
  $('#ed-del').classList.toggle('hidden', !ch);
  const lb = $('#ed-link');
  lb.classList.toggle('hidden', !ch || !ch.link);
  if (ch && ch.link) {
    lb.innerHTML = (ch.player ? `🎮 Играет: <b>${esc(ch.player.first_name)}</b>. ` : '⏳ Персонаж ждёт игрока. ')
      + `Ссылка: <code>${esc(ch.link)}</code> <button type="button" class="ghost sm" id="ed-copy">Копировать</button>`
      + (ch.player ? ' <button type="button" class="ghost sm" id="ed-unbind">Отвязать игрока</button>' : '');
    $('#ed-copy').onclick = () => copy(ch.link, '🔗 Ссылка скопирована');
    if (ch.player) $('#ed-unbind').onclick = async () => {
      if (!confirm('Отвязать игрока? Старая ссылка перестанет работать, появится новая.')) return;
      await api(`/api/char/${id}/unbind`); $('#editor').close();
    };
  }
  $('#ed-stats').innerHTML = AB.map((a) => `<label>${AB_SHORT[a]}<input type="number" min="1" max="30" name="st_${a}" value="${c.st[a]}">
    <span class="sv"><input type="checkbox" name="sv_${a}" ${c.sv.includes(a) ? 'checked' : ''}> спасбросок</span></label>`).join('');
  $('#ed-skills').innerHTML = S.skills.map((s, i) => `<label><input type="checkbox" name="sk_${i}" ${c.sk.includes(i) ? 'checked' : ''}> ${esc(s)} <small>${AB_SHORT[SKILL_AB[i]]}</small></label>`).join('');
  $('#ed-atk').innerHTML = '';
  (c.atk || []).forEach(atkRow);
  $('#editor').returnValue = '';
  $('#editor').showModal();
  $('#editor').scrollTop = 0;
}
$('#new-char').addEventListener('click', () => openEditor(null));

function editorData() {
  const f = $('#ed-form').elements;
  const data = { st: {}, sv: [], sk: [], atk: [] };
  for (const k of ['name', 'cls', 'race', 'bg', 'al', 'lvl', 'ac', 'hp', 'mhp', 'spd', ...TEXTS]) data[k] = f[k].value;
  AB.forEach((a) => { data.st[a] = f['st_' + a].value; if (f['sv_' + a].checked) data.sv.push(a); });
  S.skills.forEach((_, i) => { if (f['sk_' + i].checked) data.sk.push(i); });
  document.querySelectorAll('#ed-atk .atk-row').forEach((r) => {
    const n = r.querySelector('.an').value.trim();
    if (n) data.atk.push({ n, hit: r.querySelector('.ah').value || 0, dmg: r.querySelector('.ad').value.trim() || '1d4' });
  });
  return data;
}
$('#ed-form').addEventListener('submit', async (e) => {
  if (e.submitter?.value !== 'save') return;
  e.preventDefault();
  const data = editorData();
  try {
    if (edId == null) {
      const r = await api('/api/char', data);
      $('#editor').close();
      const link = (await api('/api/state', null, 'GET')).chars.find((c) => c.id === r.id)?.link;
      if (link) copy(link, `✨ «${data.name}» создан. Ссылка скопирована — отправь её игроку`);
      else toast(`✨ «${data.name}» создан`);
    } else {
      await api(`/api/char/${edId}`, data);
      $('#editor').close();
      toast('💾 Сохранено — лист в Telegram обновится сам');
    }
  } catch (err) { /* ошибка уже показана, редактор остаётся открытым */ }
});
$('#ed-del').addEventListener('click', async () => {
  if (!confirm('Удалить персонажа совсем?')) return;
  await api(`/api/char/${edId}`, null, 'DELETE'); $('#editor').close();
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
    else if (ev.type === 'chars') {
      const had = new Set(S.chars.filter((c) => c.player).map((c) => c.id));
      S.chars = ev.chars; renderPlayers();
      const joined = S.chars.find((c) => c.player && !had.has(c.id));
      if (joined && S.loaded) toast(`🎮 ${joined.player.first_name} забрал(а) «${joined.char.name}»`);
    }
    else if (ev.type === 'cleared') { S.rolls = []; renderFeed(); renderPlayers(); }
  };
}
async function refresh() {
  const st = await api('/api/state', null, 'GET');
  Object.assign(S, { chars: st.chars, rolls: st.rolls, skills: st.skills, invite: st.invite, loaded: true });
  if (!$('#ask-skill').options.length) $('#ask-skill').innerHTML = st.skills.map((s, i) => `<option value="${i}">${esc(s)}</option>`).join('');
  renderPlayers(); renderFeed(); renderIni();
}
connect();

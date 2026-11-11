'use strict';
/* Ширма мастера. Все расчёты делает сервер (engine.py) — здесь только показ и команды. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const AB = ['str', 'dex', 'con', 'int', 'wis', 'cha'];
const AB_SHORT = { str: 'СИЛ', dex: 'ЛОВ', con: 'ТЕЛ', int: 'ИНТ', wis: 'МДР', cha: 'ХАР' };
const TEXTS = { feat: 'Умения (текст)', spells: 'Заклинания (текст)', inv: 'Снаряжение (текст)', notes: 'Заметки и история' };
const CATS = { note: '📝 Заметка', plot: '📜 Сюжет', session: '🗓 Итоги сессии', quest: '🎯 Задание', npc: '🧑 НИП', place: '🏰 Место', loot: '💰 Добыча', mech: '⚙️ Механика' };
const sg = (n) => (n < 0 ? '' : '+') + n;
const parts = (p) => (p || []).map(([s, v]) => `${s}: ${typeof v === 'number' ? sg(v) : v}`).join('\n');
const opId = () => Math.random().toString(36).slice(2) + Date.now().toString(36);

const S = { gid: +load('gid', 0) || 0, games: [], chars: [], rolls: [], requests: [], drafts: [], combat: null, scenes: [], scene: null,
  skills: [], monsters: [], effects: [], conditions: {}, adj: {}, selected: new Set(), compact: load('compact', false), cat: null };

function store(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
function load(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } }
function toast(t) { const el = $('#toast'); el.textContent = t; el.classList.add('show'); clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove('show'), 3200); }

async function api(path, body, method = 'POST') {
  const headers = { 'Content-Type': 'application/json' };
  if (method !== 'GET') headers['X-Op'] = opId();
  const r = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (r.status === 401) { location.href = '/login'; throw new Error('auth'); }
  const data = await r.json().catch(() => ({}));
  if (r.status === 409) { toast('⚠️ ' + data.error); refresh(); throw new Error('conflict'); }
  if (!r.ok) { toast('⚠️ ' + (data.error || r.status)); throw new Error(data.error); }
  return data;
}
const G = (p) => `/api/g/${S.gid}${p}`;
async function copy(text, okMsg) { try { await navigator.clipboard.writeText(text); toast(okMsg); } catch (e) { prompt('Скопируй:', text); } }
$$('dialog [data-close]').forEach((b) => b.addEventListener('click', () => b.closest('dialog').close()));

/* ---------- игры ---------- */
function renderGames() {
  $('#game-sel').innerHTML = S.games.filter((g) => g.status === 'active' || g.id === S.gid)
    .map((g) => `<option value="${g.id}" ${g.id === S.gid ? 'selected' : ''}>${esc(g.name)}${g.status === 'archived' ? ' (архив)' : ''}</option>`).join('');
  $('#games-list').innerHTML = S.games.map((g) => `<div class="game-row ${g.id === S.gid ? 'cur' : ''}">
    <b class="grow">${esc(g.name)}</b><span class="hint">${g.status === 'archived' ? '📦 архив' : 'код ' + esc(g.lobby)}</span>
    <button class="ghost sm" data-open="${g.id}">${g.id === S.gid ? 'открыта' : 'Открыть'}</button>
    ${g.status === 'archived' ? `<button class="ghost sm" data-st="${g.id}:active">▶ Продолжить</button>` : `<button class="ghost sm" data-st="${g.id}:archived">📦 В архив</button>`}
    <button class="ghost sm" data-ren="${g.id}">✎</button></div>`).join('');
}
$('#game-sel').addEventListener('change', (e) => switchGame(+e.target.value));
$('#games-btn').addEventListener('click', () => { renderGames(); $('#games-dlg').showModal(); });
$('#games-list').addEventListener('click', async (e) => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.dataset.open) { switchGame(+b.dataset.open); $('#games-dlg').close(); }
  if (b.dataset.st) { const [id, st] = b.dataset.st.split(':'); await api(`/api/g/${id}`, { status: st }); await loadGames(); }
  if (b.dataset.ren) { const n = prompt('Название игры', S.games.find((g) => g.id === +b.dataset.ren).name); if (n) { await api(`/api/g/${b.dataset.ren}`, { name: n }); await loadGames(); } }
});
$('#game-create').addEventListener('click', async () => {
  const g = await api('/api/games', { name: $('#game-name').value.trim() || 'Новая игра' });
  $('#game-name').value = ''; await loadGames(); switchGame(g.id); $('#games-dlg').close(); toast('🎲 Игра создана: код ' + g.lobby);
});
async function loadGames() { S.games = (await api('/api/games', null, 'GET')).games; if (!S.games.find((g) => g.id === S.gid)) S.gid = S.games[0]?.id; renderGames(); }
function switchGame(id) { S.gid = id; store('gid', id); S.selected.clear(); if (S.ws) S.ws.close(); else connect(); }

/* ---------- лобби ---------- */
function renderLobby() {
  const g = S.games.find((x) => x.id === S.gid) || {};
  $('#lobby-code').textContent = g.status === 'archived' ? 'архив' : (g.lobby || '·····');
  $('#lobby-how').innerHTML = S.bot ? `Игроки открывают <a href="https://t.me/${esc(S.bot)}" target="_blank">@${esc(S.bot)}</a>, пишут код и собирают героя по шагам.` : 'Бот запускается…';
}
$('#lobby-copy').addEventListener('click', () => {
  const g = S.games.find((x) => x.id === S.gid) || {};
  copy(`🐉 Заходите в «${g.name}»!\n1. Откройте бота: ${S.lobby_link || '@' + S.bot}\n2. Если спросит код — ${g.lobby}\n3. Соберите героя по шагам или возьмите готового.`, '📋 Приглашение скопировано');
});
$('#lobby-new').addEventListener('click', async () => {
  if (!confirm('Новый код? Старый перестанет работать (персонажи останутся).')) return;
  const r = await api(G('/lobby/new')); S.lobby_link = r.lobby_link; await loadGames(); renderLobby(); toast('🔄 Новый код: ' + r.lobby);
});
function renderDrafts() {
  $('#drafts').innerHTML = (S.drafts || []).map((d) => `<div class="draft" data-id="${d.tg_id}"><span class="spin">✍️</span>
    <div class="grow"><b>${esc(d.player || 'Игрок')}</b> создаёт персонажа${d.name ? `: «${esc(d.name)}»` : ''}
    <div class="hint">${esc([d.race, d.cls].filter(Boolean).join(' · '))} — ${esc(d.step)}…</div></div><button class="ghost sm">✕</button></div>`).join('');
}
$('#drafts').addEventListener('click', async (e) => {
  const b = e.target.closest('button'); if (!b || !confirm('Сбросить черновик?')) return;
  await api(`/api/draft/${b.closest('.draft').dataset.id}`, null, 'DELETE');
});

/* ---------- партия ---------- */
const charById = (id) => S.chars.find((c) => c.id === id);
function hpColor(p) { return p > 50 ? 'var(--green)' : p > 25 ? '#e0a020' : 'var(--red)'; }
function lastRollOf(cid) { for (let i = S.rolls.length - 1; i >= 0; i--) if (S.rolls[i].char_id === cid) return S.rolls[i]; return null; }
function renderPlayers() {
  $('#start-here').classList.toggle('hidden', S.chars.length > 0);
  const fight = !!(S.combat && S.combat.on);
  $('#fight-banner').classList.toggle('hidden', !fight);
  $('#players').innerHTML = S.chars.map((ch) => {
    const c = ch.char, d = ch.d, mx = d.hp_max.val, pct = Math.max(0, Math.min(100, (c.hp / mx) * 100));
    const sel = S.selected.has(ch.id), last = lastRollOf(ch.id);
    const conds = [...(c.conds || []), ...(c.exh ? ['Истощение ' + c.exh] : [])];
    const owner = ch.player ? `🎮 ${esc(ch.player.first_name)}` : '<span class="wait">⏳ ждёт игрока</span>';
    return `<div class="pc ${sel ? 'sel' : ''} ${ch.player ? '' : 'unbound'} ${c.hp <= 0 ? 'down' : ''}" data-id="${ch.id}">
      <div class="pc-top open" title="Открыть лист">
        <div class="pc-name"><b>${esc(c.name || 'Безымянный')}</b><span>${esc([`${c.cls || 'без класса'} ${d.lvl} ур.`, c.race].filter(Boolean).join(' · '))} · ${owner}</span></div>
        <div class="ac" title="Класс доспеха: ${esc(parts(d.ac.parts))}">${d.ac.val}<small>КД</small></div></div>
      <div class="hp open"><div class="hp-bar"><div style="width:${pct}%;background:${hpColor(pct)}"></div></div>
        <span class="hp-num">❤️ ${c.hp}/${mx}${c.temp ? ` <i>+${c.temp}</i>` : ''}</span></div>
      <div class="hp-ctl"><input class="hp-in" type="number" placeholder="сколько" min="0"><button class="dmg">🗡 Урон</button><button class="heal">✚ Лечить</button></div>
      ${conds.length || c.conc || (c.effects || []).length || c.lvl_pending ? `<div class="chips">${conds.map((x) => `<span class="chip bad" title="${esc(S.conditions[x] || '')}">${esc(x)}</span>`).join('')}${c.conc ? `<span class="chip">🧠 ${esc(c.conc)}</span>` : ''}${(c.effects || []).map((x) => `<span class="chip good">✨ ${esc(x.n)}${x.rounds ? ' ' + x.rounds + 'р' : ''}</span>`).join('')}${c.lvl_pending ? '<span class="chip good">📈 ждёт /levelup</span>' : ''}</div>` : ''}
      ${c.hp <= 0 && !c.dead ? `<div class="chips"><span class="chip bad">💀 Спасброски от смерти: ${(c.death || [0, 0])[0]} ✔ / ${(c.death || [0, 0])[1]} ✘</span></div>` : ''}
      <div class="pc-foot"><label class="sel-all"><input type="checkbox" class="pick" ${sel ? 'checked' : ''}> выбрать</label><span class="grow"></span>
        <span class="hint">${last ? `🎲 ${esc(last.title || last.formula)}: <b>${last.total}</b>` : ''}</span></div>
    </div>`;
  }).join('');
  updateTargetHint();
}
$('#players').addEventListener('click', async (e) => {
  const card = e.target.closest('.pc'); if (!card) return;
  const id = +card.dataset.id;
  if (e.target.classList.contains('pick')) { e.target.checked ? S.selected.add(id) : S.selected.delete(id); card.classList.toggle('sel', e.target.checked); return updateTargetHint(); }
  if (e.target.closest('.open')) return openEditor(id);
  const dmg = e.target.closest('.dmg'), heal = e.target.closest('.heal');
  if (dmg || heal) {
    const n = Math.abs(parseInt(card.querySelector('.hp-in').value, 10) || 0);
    if (!n) { card.querySelector('.hp-in').focus(); return toast('Впиши число в поле «сколько»'); }
    const r = await api(`/api/char/${id}/op`, { op: dmg ? 'dmg' : 'heal', n });
    toast((dmg ? `🗡 −${n}` : `✚ +${n}`) + (r.log.length ? ' · ' + r.log.join('; ') : ''));
  }
});
$('#players').addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target.classList.contains('hp-in')) e.target.closest('.pc').querySelector('.dmg').click(); });

$('#sel-all').addEventListener('change', (e) => { S.selected = e.target.checked ? new Set(S.chars.map((c) => c.id)) : new Set(); renderPlayers(); });
function targets() { return S.selected.size ? [...S.selected] : S.chars.filter((c) => c.tg_id).map((c) => c.id); }
function updateTargetHint() { const n = S.selected.size; $('#ask-target').textContent = n ? `выбранные: ${n}` : 'все игроки (или отметь «выбрать» у персонажей)'; }
function sentToast(prefix, sent, asked) { const m = asked - sent; toast(`${prefix}: ${sent}` + (m > 0 ? ` (без игрока: ${m})` : '')); }

/* ---------- вкладки ---------- */
const TAB_HINT = {
  feed: 'Исследование и разговоры: проси проверки справа, броски игроков появляются в ленте. Урон и лечение применяй кнопками под броском.',
  combat: 'Драка: добавь противников, начни бой и иди по ходам — подсказка ниже скажет, что делать.',
  map: 'Необязательно: карта местности для игроков с фишками и туманом.',
  journal: 'Заметки мастера: сюжет, задания, итоги сессий. Механика (урон, отдых, бои) записывается сама.',
};
function tab(name) {
  $('#tab-hint').textContent = TAB_HINT[name] || '';
  $$('#tabs button').forEach((b) => b.classList.toggle('on', b.dataset.t === name));
  $$('.tab').forEach((t) => t.classList.toggle('hidden', t.id !== 't-' + name));
  store('tab', name);
  if (name === 'journal') loadJournal();
  if (name === 'map') drawMap();
}
$('#tabs').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) tab(b.dataset.t); });

/* ---------- лента и запросы ---------- */
function diceHtml(d) { return (d.r || []).map((g, i) => `d${d.s[i]}: [${g.map((v) => (v < 0 ? `<s>${-v}</s>` : v)).join(', ')}]`).join(' ') + (d.b ? ' ' + sg(d.b) : ''); }
function rollHtml(r) {
  const d = r.detail || {};
  const t = new Date(r.ts * 1000).toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' });
  const dmg = d.d ? `<div class="meta">урон <b class="dmgv">${d.d.t}</b> · ${esc(d.d.f)} → ${diceHtml(d.d)}</div>` : '';
  const dc = d.dc ? ` <span class="${d.ok ? 'okv' : 'badv'}">${d.ok ? '✅ успех' : '❌ провал'} (СЛ ${d.dc})</span>` : '';
  const ap = d.ap || (d.d ? { k: 'dmg', n: d.d.t } : null);
  const apHtml = ap && !d.applied ? `<div class="ap" data-rid="${r.id}">${ap.k === 'heal'
    ? `<button class="sm ap-go heal-b">✚ Вылечить ${ap.n}…</button>`
    : `<button class="sm ap-go">🗡 Нанести ${ap.n} урона…</button>${d.d ? ' <span class="hint">попал, если ' + r.total + ' ≥ КД цели</span>' : ''}${ap.dc ? ` <span class="hint">цели бросают спас ${esc(ap.save || '')} СЛ ${ap.dc}${ap.half ? ', при успехе — половина' : ''}</span>` : ''}`}</div>` : '';
  return `<div class="roll ${d.c > 0 ? 'crit' : ''} ${d.c < 0 ? 'fumble' : ''} ${r.tg_id ? '' : 'gm'} ${r.hidden ? 'secret' : ''}">
    <div class="tot">${r.total}</div><div><span class="time">${t}</span> <span class="who">${esc(r.who)}</span>${r.title ? ` — <span class="ttl">${d.d ? '⚔️ ' : ''}${esc(r.title)}</span>` : ''}
    ${d.c > 0 ? ' 💥' : d.c < 0 ? ' 💀' : ''}${r.hidden ? ' 🤫' : ''}${d.phys ? ' 🎲свои' : ''}${dc}${d.d && S.compact ? ` · урон <b class="dmgv">${d.d.t}</b>` : ''}
    <div class="meta det">${esc(r.formula)} → ${diceHtml(d)}</div>${S.compact ? '' : dmg}${apHtml}</div></div>`;
}
function applyTargets(kind) {
  const out = [];
  const cb = S.combat && S.combat.on ? S.combat.list : [];
  if (kind === 'dmg') for (const x of cb) if (x.kind === 'npc') out.push({ t: 'npc', id: x.id, n: x.name, ac: x.ac, hp: `${x.hp}/${x.mhp}` });
  for (const c of S.chars) out.push({ t: 'pc', id: c.id, n: c.char.name, ac: c.d.ac.val, hp: `${c.char.hp}/${c.d.hp_max.val}` });
  return out;
}
function openApply(box) {
  const r = S.rolls.find((x) => x.id === +box.dataset.rid); if (!r) return;
  const d = r.detail, ap = d.ap || { k: 'dmg', n: d.d.t };
  const hitRoll = d.d ? r.total : null;
  let tg = applyTargets(ap.k);
  if (!r.tg_id && !r.char_id) tg = [...tg.filter((x) => x.t === 'pc'), ...tg.filter((x) => x.t === 'npc' && !r.title.startsWith(x.n + ':'))];  // бьёт монстр — сначала герои
  if (ap.self) { const i = tg.findIndex((x) => x.t === 'pc' && x.id === ap.self); if (i > 0) tg.unshift(tg.splice(i, 1)[0]); }
  box.innerHTML = `<div class="row gap wrap"><input type="number" class="short ap-n" value="${ap.n}"> ${ap.k === 'dmg' ? '<button class="ghost sm ap-half">½ половина</button>' : ''}
    <span class="hint">${ap.k === 'heal' ? 'кого лечим?' : 'по кому?'}</span></div>
    <div class="row gap wrap">${tg.map((x) => `<button class="sm ap-tg ${x.t}" data-t="${x.t}" data-id="${x.id}">${x.t === 'npc' ? '👹' : '🧙'} ${esc(x.n)} <span class="hint">${x.hp}${ap.k === 'dmg' ? ' · КД ' + x.ac + (hitRoll != null ? (hitRoll >= x.ac ? ' ✓' : ' ✗') : '') : ''}</span></button>`).join('') || '<span class="hint">Некого</span>'}
    <button class="ghost sm ap-x">отмена</button></div>`;
}
document.addEventListener('click', async (e) => {
  const box = e.target.closest('.ap'); if (!box) return;
  if (e.target.closest('.ap-go')) return openApply(box);
  if (e.target.closest('.ap-x')) return renderFeed(), renderCombat();
  if (e.target.closest('.ap-half')) { const i = box.querySelector('.ap-n'); i.value = Math.floor(+i.value / 2); return; }
  const b = e.target.closest('.ap-tg'); if (!b) return;
  const r = S.rolls.find((x) => x.id === +box.dataset.rid); const ap = r.detail.ap || { k: 'dmg' };
  const n = Math.abs(+box.querySelector('.ap-n').value || 0); if (!n) return toast('Сколько?');
  if (b.dataset.t === 'pc') { const res = await api(`/api/char/${b.dataset.id}/op`, { op: ap.k === 'heal' ? 'heal' : 'dmg', n, type: ap.t || '' }); toast(`${ap.k === 'heal' ? '✚ +' : '🗡 −'}${n} · ${b.textContent.trim().split('  ')[0]}` + (res.log.length ? ' · ' + res.log.join('; ') : '')); }
  else { await cbOp('npc', { eid: b.dataset.id, delta: ap.k === 'heal' ? n : -n }); toast(`🗡 −${n}`); }
  r.detail.applied = true; renderFeed(); renderCombat();
});
function renderFeed() {
  $('#feed').classList.toggle('compact', S.compact);
  $('#feed').innerHTML = S.rolls.length ? S.rolls.slice().reverse().map(rollHtml).join('') : '<p class="hint">Тут будут появляться броски в реальном времени.</p>';
  $('#feed-fold').textContent = S.compact ? '▦ подробно' : '▤ компактно';
}
$('#feed').addEventListener('click', (e) => { const r = e.target.closest('.roll'); if (r && S.compact) r.classList.toggle('open'); });
$('#feed-fold').addEventListener('click', () => { S.compact = !S.compact; store('compact', S.compact); renderFeed(); });
$('#clear').addEventListener('click', async () => { if (confirm('Удалить историю бросков этой игры?')) await api(G('/clear')); });
function renderRequests() {
  const groups = {};
  for (const q of S.requests) (groups[q.grp || q.id] ||= []).push(q);
  const VIS = { gm: '🤫', all: '📢', player: '' };
  const html = Object.values(groups).slice(0, 8).map((qs) => {
    const done = qs.filter((q) => q.status === 'done').length;
    return `<div class="req"><div class="row gap"><b class="grow">${esc(qs[0].title)}${qs[0].dc ? ' · СЛ ' + qs[0].dc : ''} ${VIS[qs[0].vis] || ''}</b><span class="hint">${done}/${qs.length}</span></div>
      ${qs.map((q) => { const ch = charById(q.char_id); return `<div class="req-row"><span class="grow">${esc(ch ? ch.char.name : '?')}</span>
        ${q.status === 'done' ? `<b>${q.total ?? '✓'}</b>${q.dc && q.total != null ? (q.total >= q.dc ? ' ✅' : ' ❌') : ''}` : q.status === 'cancelled' ? '<span class="hint">отменён</span>' : q.status === 'failed' ? '<span class="hint">не доставлен</span>' : `<span class="hint">⏳ ждём</span> <button class="ghost sm" data-cancel="${q.id}">✕</button>`}</div>`; }).join('')}</div>`;
  }).join('');
  $('#requests').innerHTML = html || '<p class="hint">Запросов пока нет.</p>';
}
$('#requests').addEventListener('click', async (e) => { const b = e.target.closest('[data-cancel]'); if (b) { await api(`/api/request/${b.dataset.cancel}/cancel`); toast('❌ Запрос отменён'); } });
$('#req-toggle').addEventListener('click', () => { const el = $('#requests'); el.classList.toggle('hidden'); $('#req-toggle').textContent = el.classList.contains('hidden') ? 'развернуть' : 'свернуть'; });

/* ---------- запрос броска ---------- */
const SITUATIONS = [
  ['👀 Заметить засаду, ловушку, тайник', 'k:3'], ['🔍 Обыскать, найти улику, разгадать механизм', 'k:1'],
  ['🤫 Подкрасться, спрятаться', 'k:15'], ['🧗 Залезть, прыгнуть, выбить дверь', 'k:2'], ['🤸 Удержать равновесие, вывернуться', 'k:0'],
  ['🗝 Вскрыть замок, незаметно стащить', 'k:8'], ['💬 Убедить, договориться', 'k:16'], ['🎭 Соврать, обмануть', 'k:11'],
  ['😠 Запугать', 'k:6'], ['🧐 Понять, врёт ли собеседник', 'k:13'], ['🩹 Помочь раненому', 'k:10'], ['🐾 Выследить, найти дорогу', 'k:4'],
  ['🐴 Успокоить животное', 'k:17'], ['🎵 Выступить перед публикой', 'k:5'],
  ['📚 Вспомнить про магию и магических тварей', 'k:9'], ['📜 Вспомнить историю, королевства, легенды', 'k:7'],
  ['🌿 Вспомнить про природу, зверей, растения', 'k:12'], ['⛪ Вспомнить про богов, нежить, культы', 'k:14'],
  ['💨 Увернуться от ловушки, взрыва (спасбросок ЛОВ)', 's:dex'], ['🧪 Устоять против яда, болезни (спасбросок ТЕЛ)', 's:con'],
  ['🧠 Устоять против чар, страха (спасбросок МДР)', 's:wis'], ['💪 Устоять, когда толкают или держат (спасбросок СИЛ)', 's:str'],
  ['⚡ Инициатива — кто ходит первым в бою', 'i:0'], ['✍️ Другое — выбрать вручную…', ''],
];
let askKind = 'k', askDc = '15';
$('#ask-sit').innerHTML = SITUATIONS.map(([t, k], i) => `<option value="${i}">${t}</option>`).join('');
$('#ask-sit').addEventListener('change', () => $('#ask-custom').classList.toggle('hidden', SITUATIONS[+$('#ask-sit').value][1] !== ''));
$('#ask-kind').addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return; askKind = b.dataset.k;
  $$('#ask-kind button').forEach((x) => x.classList.toggle('on', x === b));
  $('#ask-skill').classList.toggle('hidden', askKind !== 'k'); $('#ask-ab').classList.toggle('hidden', !['c', 's'].includes(askKind)); $('#ask-formula').classList.toggle('hidden', askKind !== 'f');
});
$('#ask-dc').addEventListener('click', (e) => { const b = e.target.closest('button'); if (!b) return; askDc = b.dataset.dc; $$('#ask-dc button').forEach((x) => x.classList.toggle('on', x === b)); });
$('#ask-go').addEventListener('click', async () => {
  const ids = targets(); if (!ids.length) return toast('Ни у кого ещё нет игрока');
  let kind = SITUATIONS[+$('#ask-sit').value][1];
  if (!kind) {
    const v = { k: $('#ask-skill').value, c: $('#ask-ab').value, s: $('#ask-ab').value, f: $('#ask-formula').value.trim() }[askKind];
    if (askKind === 'f' && !v) return toast('Впиши формулу');
    kind = `${askKind}:${v}`;
  }
  const r = await api(G('/ask'), { chars: ids, kind, title: $('#ask-title').value.trim(), vis: $('#ask-secret').checked ? 'gm' : 'player', dc: kind.startsWith('i:') ? '' : askDc });
  sentToast('📨 Попросил бросить', r.sent.length, ids.length); $('#ask-title').value = '';
});
async function partyRest(kind) {
  const ids = S.selected.size ? [...S.selected] : [];
  const who = ids.length ? `выбранных (${ids.length})` : 'всю партию';
  if (!confirm(`${kind === 'long' ? '🛌 Долгий' : '☕ Короткий'} отдых для: ${who}?`)) return;
  const r = await api(G('/rest'), { kind, chars: ids });
  toast(`${kind === 'long' ? '🛌' : '☕'} Отдохнули: ${r.rested.join(', ') || 'никто'}`);
}
$('#rest-short').addEventListener('click', () => partyRest('short'));
$('#rest-long').addEventListener('click', () => partyRest('long'));

/* ---------- бросок мастера и сообщения ---------- */
let gmAdv = 0;
$('#gm-dice').innerHTML = [4, 6, 8, 10, 12, 20, 100].map((n) => `<button data-n="${n}">d${n}</button>`).join('');
$('#gm-dice').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) $('#gm-formula').value = '1d' + b.dataset.n; });
$('#gm-adv').addEventListener('click', (e) => { const b = e.target.closest('button'); if (!b) return; gmAdv = +b.dataset.a; $$('#gm-adv button').forEach((x) => x.classList.toggle('on', x === b)); });
async function gmRoll() { const r = await api(G('/roll'), { formula: $('#gm-formula').value, title: $('#gm-title').value, adv: gmAdv }); $('#gm-result').innerHTML = `<span class="big">${r.total}</span> <span class="hint">${esc(r.formula)} → ${esc(r.detail.txt)}</span>`; }
$('#gm-go').addEventListener('click', gmRoll);
$('#gm-formula').addEventListener('keydown', (e) => { if (e.key === 'Enter') gmRoll(); });
$('#msg-go').addEventListener('click', async () => {
  const text = $('#msg-text').value.trim(); if (!text) return; const ids = targets();
  const r = await api(G('/message'), { chars: ids, text }); sentToast('📜 Отправлено', r.sent, ids.length); $('#msg-text').value = '';
});

/* ---------- бой ---------- */
function renderCombat() {
  const cb = S.combat || { on: false, list: [], round: 0, turn: -1 };
  $('#cb-round').textContent = cb.on ? `Раунд ${cb.round}` : 'Бой не идёт';
  $('#cb-start').classList.toggle('hidden', cb.on);
  $('#cb-next').classList.toggle('hidden', !cb.on); $('#cb-end').classList.toggle('hidden', !cb.on); $('#cb-ask').classList.toggle('hidden', !cb.on);
  const cur = cb.on ? cb.list[cb.turn] : null;
  const npcs = cb.list.filter((x) => x.kind === 'npc').length;
  let g;
  if (!cb.on) g = `<b>Как начать бой:</b><ol><li>Добавь противников в блоке ниже (${npcs ? '✓ добавлено: ' + npcs : 'шаблон, например «Гоблин», и сколько'}).</li>
    <li>Нажми <b>⚔️ Начать бой</b> — игрокам придёт запрос инициативы, очередь выстроится сама.</li><li>Дальше просто жми <b>Следующий ход</b> и следуй подсказкам здесь.</li></ol>`;
  else if (!cur) g = '<b>Бой начался.</b> Ждём бросков инициативы от игроков (у кого «?» — ещё не бросил; можно вписать вручную). Затем жми <b>Следующий ход ▶</b>.';
  else if (cur.kind === 'npc') g = `<b>Ходит 👹 ${esc(cur.name)}</b>. Опиши, что он делает, и брось атаку: ${(cur.atk || []).map((a, j) => `<button class="sm g-atk" data-id="${cur.id}" data-i="${j}">⚔️ ${esc(a[0])}</button>`).join(' ') || '(атак нет — впиши урон вручную у цели)'}
    <br><span class="hint">Бросок ≥ КД героя — попал. Под броском появится «🗡 Нанести урон» — выбери, по кому. Затем <b>Следующий ход ▶</b>.</span>`;
  else g = `<b>Ходит 🧙 ${esc(cur.name)}</b>. Спроси игрока: «Что делаешь?». Он бросает в боте — бросок появится ниже.
    <br><span class="hint">Атака попала, если бросок ≥ КД цели (у гоблина КД 15). Нажми «🗡 Нанести» под броском и выбери противника. Затем <b>Следующий ход ▶</b>.</span>`;
  $('#cb-guide').innerHTML = g;
  $('#cb-feed').innerHTML = S.rolls.slice(-6).reverse().map(rollHtml).join('') || '<p class="hint">Пока бросков нет.</p>';
  $('#cb-list').innerHTML = cb.list.length ? cb.list.map((x, i) => {
    const ch = x.cid ? charById(x.cid) : null;
    const hp = ch ? ch.char.hp : x.hp, mx = ch ? ch.d.hp_max.val : x.mhp, ac = ch ? ch.d.ac.val : x.ac;
    const conds = ch ? (ch.char.conds || []) : (x.conds || []);
    const effs = ch ? (ch.char.effects || []) : (x.effects || []);
    return `<div class="cb-row ${i === cb.turn ? 'cur' : ''} ${x.kind} ${x.hidden ? 'hid' : ''} ${hp <= 0 ? 'down' : ''}" data-id="${x.id}">
      <input class="ini short" type="number" value="${x.init ?? ''}" placeholder="?" title="Инициатива">
      <div class="grow"><b>${esc(x.name)}</b>${x.hidden ? ' 🙈' : ''} <span class="hint">КД ${ac}${x.cr ? ' · ПО ' + x.cr : ''}</span>
        <div class="chips">${conds.map((c) => `<span class="chip bad">${esc(c)}</span>`).join('')}${effs.map((e) => `<span class="chip good" data-rme="${e.id}">✨ ${esc(e.n)}${e.rounds ? ' ' + e.rounds + 'р' : ''}</span>`).join('')}</div>
        ${x.atk && x.atk.length ? `<div class="row gap wrap">${x.atk.map((a, j) => `<button class="ghost sm atk" data-i="${j}">⚔️ ${esc(a[0])} ${a[1] ? sg(a[1]) : ''} ${esc(a[2])}</button>`).join('')}</div>` : ''}
        ${x.note ? `<div class="hint">${esc(x.note)}</div>` : ''}</div>
      <div class="cb-hp"><span>${hp}/${mx}</span><input class="hp-in" type="number" placeholder="±"><button class="dmg">🗡</button><button class="heal">✚</button></div>
      <div class="col-btns">${x.kind === 'npc' ? `<button class="ghost sm cond" title="Состояние">🩹</button><button class="ghost sm hide" title="Скрыть/показать">${x.hidden ? '👁' : '🙈'}</button>` : '<button class="ghost sm edit" title="Хиты, состояния, эффекты">🩹 Лист</button>'}<button class="ghost sm rm" title="Убрать">✕</button></div>
    </div>`;
  }).join('') : '<p class="hint">Участников пока нет. «Начать бой» добавит партию; противников — ниже.</p>';
}
async function cbOp(op, extra = {}) { const r = await api(G('/combat'), { op, ...extra }); if (r.combat) { S.combat = r.combat; renderCombat(); } return r; }
$('#cb-start').addEventListener('click', () => cbOp('start'));
$('#cb-guide').addEventListener('click', async (e) => { const b = e.target.closest('.g-atk'); if (!b) return; const r = await cbOp('npc_roll', { eid: b.dataset.id, i: +b.dataset.i }); toast(`🎲 ${r.total}` + (r.detail.d ? ` · урон ${r.detail.d.t}` : '')); });
$('#cb-next').addEventListener('click', async () => { const r = await cbOp('next'); const c = r.combat.list[r.combat.turn]; if (c) toast('Ход: ' + c.name); });
$('#cb-ask').addEventListener('click', () => cbOp('ask_init').then(() => toast('⚔️ Инициатива запрошена')));
$('#cb-end').addEventListener('click', () => { if (confirm('Закончить бой? Противники уйдут из списка.')) cbOp('end'); });
$('#cb-list').addEventListener('change', (e) => { if (e.target.classList.contains('ini')) cbOp('init', { eid: e.target.closest('.cb-row').dataset.id, value: +e.target.value || 0 }); });
$('#cb-list').addEventListener('click', async (e) => {
  const row = e.target.closest('.cb-row'); if (!row) return;
  const x = S.combat.list.find((y) => y.id === row.dataset.id);
  if (e.target.closest('.rm')) return cbOp('remove', { eid: x.id });
  if (e.target.closest('.edit')) return openEditor(x.cid, 'state');
  if (e.target.closest('.hide')) return cbOp('npc', { eid: x.id, hidden: !x.hidden });
  if (e.target.closest('.cond')) { const c = prompt('Состояния через запятую (' + Object.keys(S.conditions).join(', ') + ')', (x.conds || []).join(', ')); if (c !== null) cbOp('npc', { eid: x.id, conds: c.split(',').map((s) => s.trim()).filter(Boolean) }); return; }
  const rme = e.target.closest('[data-rme]'); if (rme && x.kind === 'npc') return cbOp('npc', { eid: x.id, rm_effect: rme.dataset.rme });
  const atk = e.target.closest('.atk');
  if (atk) { const r = await cbOp('npc_roll', { eid: x.id, i: +atk.dataset.i }); return toast(`${x.name}: ${r.total}` + (r.detail.d ? ` · урон ${r.detail.d.t}` : '')); }
  const dmg = e.target.closest('.dmg'), heal = e.target.closest('.heal');
  if (dmg || heal) {
    const n = Math.abs(parseInt(row.querySelector('.hp-in').value, 10) || 0); if (!n) return toast('Введи число');
    if (x.kind === 'pc') { const r = await api(`/api/char/${x.cid}/op`, { op: dmg ? 'dmg' : 'heal', n }); if (r.log.length) toast(r.log.join('; ')); }
    else cbOp('npc', { eid: x.id, delta: dmg ? -n : n });
  }
});
$('#npc-tpl').addEventListener('change', () => { if (!$('#npc-name').value) $('#npc-name').placeholder = $('#npc-tpl').value || 'Имя'; });
$('#npc-add').addEventListener('click', () => {
  const t = $('#npc-tpl').value;
  cbOp('add_npc', { template: t || null, name: $('#npc-name').value.trim() || t || 'Противник', count: +$('#npc-count').value || 1, hidden: $('#npc-hidden').checked,
    hp: +$('#npc-hp').value || null, ac: +$('#npc-ac').value || null, ib: +$('#npc-ib').value || 0 });
  $('#npc-name').value = '';
});

/* ---------- карта ---------- */
const MAP = { brush: 'move', drag: null, ruler: null, img: null, imgId: null, scale: 1, saveT: null };
function sceneObj() { return S.scene; }
function renderScenes() {
  $('#sc-sel').innerHTML = '<option value="0">— нет сцены —</option>' + S.scenes.map((s) => `<option value="${s.id}" ${S.scene && S.scene.id === s.id ? 'selected' : ''}>${esc(s.name)}</option>`).join('');
  const sc = sceneObj();
  for (const k of ['grid', 'ox', 'oy', 'cols', 'rows']) $('#sc-' + k).value = sc ? sc[k] : '';
  $('#sc-fog').checked = !!(sc && sc.fog_on); $('#sc-show').checked = !!(sc && sc.show);
  $('#map-info').textContent = sc ? `«${sc.name}»: ${sc.tokens.length} фишек${sc.show ? ' · видна игрокам' : ' · скрыта от игроков'}` : 'Создай сцену, чтобы начать.';
}
function saveScene(extra = {}, now = false) {
  const sc = sceneObj(); if (!sc) return;
  Object.assign(sc, extra);
  clearTimeout(MAP.saveT);
  const go = () => api(G('/scene'), { ...sc });
  if (now) return go();
  MAP.saveT = setTimeout(go, 500);
}
$('#sc-sel').addEventListener('change', async (e) => { await api(G(`/scene/${+e.target.value}/current`)); });
$('#sc-new').addEventListener('click', async () => { const n = prompt('Название сцены', 'Сцена ' + (S.scenes.length + 1)); if (!n) return; await api(G('/scene'), { name: n, current: true, party: true }); await loadScenes(); });
$('#sc-ren').addEventListener('click', () => { const sc = sceneObj(); if (!sc) return; const n = prompt('Название', sc.name); if (n) { sc.name = n; saveScene({}, true).then(loadScenes); } });
$('#sc-del').addEventListener('click', async () => { const sc = sceneObj(); if (sc && confirm('Удалить сцену?')) { await api(G(`/scene/${sc.id}`), null, 'DELETE'); S.scene = null; await loadScenes(); } });
$('#sc-img').addEventListener('change', async (e) => {
  const f = e.target.files[0]; if (!f || !sceneObj()) return toast('Сначала создай сцену');
  const fd = new FormData(); fd.append('file', f);
  const r = await fetch(G('/upload'), { method: 'POST', body: fd }).then((x) => x.json());
  if (r.error) return toast('⚠️ ' + r.error);
  saveScene({ img: r.id, w: r.w, h: r.h }, true); e.target.value = '';
});
for (const k of ['grid', 'ox', 'oy', 'cols', 'rows']) $('#sc-' + k).addEventListener('change', (e) => saveScene({ [k]: +e.target.value || 0 }));
$('#sc-fog').addEventListener('change', (e) => saveScene({ fog_on: e.target.checked }));
$('#sc-show').addEventListener('change', (e) => { saveScene({ show: e.target.checked }); toast(e.target.checked ? '🗺 Карта уйдёт игрокам' : 'Карта скрыта'); });
$('#brush').addEventListener('click', (e) => { const b = e.target.closest('button'); if (!b) return; MAP.brush = b.dataset.b; $$('#brush button').forEach((x) => x.classList.toggle('on', x === b)); });
$('#sc-fogall').addEventListener('click', () => { const sc = sceneObj(); if (!sc) return; const [c, r] = gridSize(sc); const fog = []; for (let x = 0; x < c; x++) for (let y = 0; y < r; y++) fog.push(`${x}:${y}`); saveScene({ fog, fog_on: true }); drawMap(); renderScenes(); });
$('#sc-clearfog').addEventListener('click', () => { saveScene({ fog: [] }); drawMap(); });
$('#sc-party').addEventListener('click', () => api(G('/scene'), { ...sceneObj(), party: true }));
$('#sc-tok').addEventListener('click', () => { const sc = sceneObj(); if (!sc) return; const n = prompt('Название фишки', 'Гоблин'); if (!n) return; sc.tokens.push({ id: opId(), n, x: 0, y: 0, kind: 'npc', color: '#e0574a', size: 1, hidden: false }); saveScene(); drawMap(); });
$('#sc-cbtok').addEventListener('click', () => {
  const sc = sceneObj(); if (!sc || !S.combat) return; let x = 0;
  for (const c of S.combat.list) if (c.kind === 'npc' && !sc.tokens.find((t) => t.eid === c.id)) sc.tokens.push({ id: opId(), n: c.name, x: x++, y: 3, kind: 'npc', eid: c.id, color: '#e0574a', size: 1, hidden: !!c.hidden });
  saveScene(); drawMap();
});
function gridSize(sc) { return sc.img ? [Math.floor((sc.w - sc.ox) / sc.grid), Math.floor((sc.h - sc.oy) / sc.grid)] : [sc.cols, sc.rows]; }
function drawMap() {
  const sc = sceneObj(), cv = $('#map'), ctx = cv.getContext('2d');
  if (!sc) { cv.width = 10; cv.height = 10; return; }
  const W = sc.img ? sc.w : sc.cols * sc.grid + sc.ox, H = sc.img ? sc.h : sc.rows * sc.grid + sc.oy;
  cv.width = W; cv.height = H;
  if (sc.img && MAP.imgId !== sc.img) { MAP.img = new Image(); MAP.img.onload = drawMap; MAP.img.src = `/api/file/${sc.img}`; MAP.imgId = sc.img; return; }
  if (sc.img && MAP.img && MAP.img.complete) ctx.drawImage(MAP.img, 0, 0, W, H); else { ctx.fillStyle = '#26201b'; ctx.fillRect(0, 0, W, H); }
  const g = sc.grid; ctx.strokeStyle = 'rgba(0,0,0,.35)'; ctx.lineWidth = 1;
  for (let x = sc.ox % g; x < W; x += g) { ctx.beginPath(); ctx.moveTo(x + .5, 0); ctx.lineTo(x + .5, H); ctx.stroke(); }
  for (let y = sc.oy % g; y < H; y += g) { ctx.beginPath(); ctx.moveTo(0, y + .5); ctx.lineTo(W, y + .5); ctx.stroke(); }
  const cur = S.combat && S.combat.on ? S.combat.list[S.combat.turn] : null;
  for (const t of sc.tokens) {
    const s = t.size || 1, x = sc.ox + t.x * g, y = sc.oy + t.y * g;
    ctx.globalAlpha = t.hidden ? 0.45 : 1;
    ctx.beginPath(); ctx.arc(x + s * g / 2, y + s * g / 2, s * g / 2 - 3, 0, 7); ctx.fillStyle = t.color || '#e3b45c'; ctx.fill();
    const turn = cur && ((cur.cid && cur.cid === t.cid) || cur.id === t.eid);
    ctx.lineWidth = turn ? 4 : 2; ctx.strokeStyle = turn ? '#fff' : '#140f0b'; ctx.stroke();
    ctx.fillStyle = '#140f0b'; ctx.font = `bold ${Math.max(10, g * .34)}px system-ui`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(t.n.split(/\s+/).slice(0, 2).map((w) => w[0]).join('').toUpperCase(), x + s * g / 2, y + s * g / 2);
    ctx.globalAlpha = 1;
  }
  if (sc.fog_on) { ctx.fillStyle = 'rgba(8,6,5,.55)'; for (const k of sc.fog) { const [cx, cy] = k.split(':').map(Number); ctx.fillRect(sc.ox + cx * g, sc.oy + cy * g, g, g); } }
  if (MAP.ruler) {
    const r = MAP.ruler; ctx.strokeStyle = '#e3b45c'; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(r.x0, r.y0); ctx.lineTo(r.x1, r.y1); ctx.stroke();
    const cells = Math.max(Math.abs(Math.floor((r.x1 - sc.ox) / g) - Math.floor((r.x0 - sc.ox) / g)), Math.abs(Math.floor((r.y1 - sc.oy) / g) - Math.floor((r.y0 - sc.oy) / g)));
    ctx.fillStyle = '#000a'; ctx.fillRect(r.x1 + 8, r.y1 - 26, 80, 24); ctx.fillStyle = '#e3b45c'; ctx.font = 'bold 16px system-ui'; ctx.textAlign = 'left'; ctx.fillText(`${cells * (sc.ft || 5)} фт`, r.x1 + 14, r.y1 - 14);
  }
}
function mapPos(e) { const cv = $('#map'), r = cv.getBoundingClientRect(); return { x: (e.clientX - r.left) * cv.width / r.width, y: (e.clientY - r.top) * cv.height / r.height }; }
function cellAt(sc, p) { return { cx: Math.floor((p.x - sc.ox) / sc.grid), cy: Math.floor((p.y - sc.oy) / sc.grid) }; }
$('#map').addEventListener('pointerdown', (e) => {
  const sc = sceneObj(); if (!sc) return; const p = mapPos(e), { cx, cy } = cellAt(sc, p);
  $('#map').setPointerCapture(e.pointerId);
  if (e.shiftKey) { MAP.ruler = { x0: p.x, y0: p.y, x1: p.x, y1: p.y }; return; }
  if (MAP.brush === 'move') { const t = [...sc.tokens].reverse().find((t) => cx >= t.x && cx < t.x + (t.size || 1) && cy >= t.y && cy < t.y + (t.size || 1)); if (t) MAP.drag = t; }
  else { MAP.painting = true; paint(sc, cx, cy); }
});
$('#map').addEventListener('pointermove', (e) => {
  const sc = sceneObj(); if (!sc) return; const p = mapPos(e), { cx, cy } = cellAt(sc, p);
  if (MAP.ruler) { MAP.ruler.x1 = p.x; MAP.ruler.y1 = p.y; return drawMap(); }
  if (MAP.drag && (MAP.drag.x !== cx || MAP.drag.y !== cy)) { MAP.drag.x = Math.max(0, cx); MAP.drag.y = Math.max(0, cy); drawMap(); }
  if (MAP.painting) paint(sc, cx, cy);
});
$('#map').addEventListener('pointerup', () => { if (MAP.drag || MAP.painting) saveScene(); MAP.drag = null; MAP.painting = false; MAP.ruler = null; drawMap(); });
$('#map').addEventListener('dblclick', (e) => {
  const sc = sceneObj(); if (!sc) return; const { cx, cy } = cellAt(sc, mapPos(e));
  const t = sc.tokens.find((t) => cx >= t.x && cx < t.x + (t.size || 1) && cy >= t.y && cy < t.y + (t.size || 1)); if (!t) return;
  const a = prompt(`«${t.n}»: h — скрыть/показать, 1-4 — размер, d — удалить, или новое имя`, '');
  if (a === null) return; if (a === 'h') t.hidden = !t.hidden; else if (/^[1-4]$/.test(a)) t.size = +a; else if (a === 'd') sc.tokens.splice(sc.tokens.indexOf(t), 1); else if (a.trim()) t.n = a.trim();
  saveScene(); drawMap();
});
function paint(sc, cx, cy) {
  const k = `${cx}:${cy}`, has = sc.fog.includes(k);
  if (MAP.brush === 'hide' && !has) sc.fog.push(k); if (MAP.brush === 'show' && has) sc.fog.splice(sc.fog.indexOf(k), 1);
  if (!sc.fog_on) { sc.fog_on = true; $('#sc-fog').checked = true; } drawMap();
}
async function loadScenes() { const r = await api(G('/scenes'), null, 'GET'); S.scenes = r.scenes; S.scene = r.scenes.find((s) => s.id === r.current) || null; renderScenes(); drawMap(); }

/* ---------- журнал ---------- */
$('#j-cat').innerHTML += Object.entries(CATS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('');
$('#en-cat').innerHTML = Object.entries(CATS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('');
let jT;
['j-q', 'j-cat', 'j-del'].forEach((id) => $('#' + id).addEventListener(id === 'j-q' ? 'input' : 'change', () => { clearTimeout(jT); jT = setTimeout(loadJournal, 250); }));
async function loadJournal() {
  const q = new URLSearchParams({ q: $('#j-q').value, cat: $('#j-cat').value, deleted: $('#j-del').checked ? '1' : '' });
  const r = await api(G('/journal?' + q), null, 'GET'); S.journal = r.entries;
  $('#j-list').innerHTML = r.entries.map((e) => `<div class="entry ${e.cat} ${e.deleted ? 'deleted' : ''}" data-id="${e.id}">
    <div class="row gap"><span>${CATS[e.cat] || e.cat}</span><b class="grow">${esc(e.title || 'Без названия')}</b><span title="${e.vis === 'all' ? 'видят игроки' : 'только мастер'}">${e.vis === 'all' ? '👁' : '🔒'}</span>
    ${e.deleted ? '<button class="ghost sm restore">↩️ Вернуть</button>' : ''}</div>
    ${e.body ? `<div class="ebody">${esc(e.body.slice(0, 280))}${e.body.length > 280 ? '…' : ''}</div>` : ''}
    <div class="hint">${esc(e.author)} · ${new Date(e.updated * 1000).toLocaleString('ru')}${e.session ? ' · сессия ' + e.session : ''}${e.quest ? ' · 🎯 ' + esc(e.quest) : ''}${e.char_id && charById(e.char_id) ? ' · ' + esc(charById(e.char_id).char.name) : ''}</div></div>`).join('') || '<p class="hint">Записей нет.</p>';
}
$('#j-list').addEventListener('click', async (e) => {
  const el = e.target.closest('.entry'); if (!el) return;
  if (e.target.closest('.restore')) { await api(`/api/journal/${el.dataset.id}`, { deleted: 0 }); return loadJournal(); }
  openEntry(S.journal.find((x) => x.id === +el.dataset.id));
});
$('#j-new').addEventListener('click', () => openEntry(null));
$('#j-session').addEventListener('click', async () => { const r = await api(G('/session')); toast(`🗓 Начата сессия ${r.session}`); loadJournal(); openEntry(r.entry); });
let enCur = null;
function openEntry(e) {
  enCur = e;
  $('#en-title').textContent = e ? 'Запись' : 'Новая запись';
  $('#en-t').value = e ? e.title : ''; $('#en-cat').value = e ? e.cat : 'note'; $('#en-vis').value = e ? e.vis : 'gm';
  $('#en-session').value = e ? (e.session ?? '') : (S.session || ''); $('#en-quest').value = e ? e.quest : ''; $('#en-body').value = e ? e.body : '';
  $('#en-char').innerHTML = '<option value="">—</option>' + S.chars.map((c) => `<option value="${c.id}" ${e && e.char_id === c.id ? 'selected' : ''}>${esc(c.char.name)}</option>`).join('');
  $('#en-scene').innerHTML = '<option value="">—</option>' + S.scenes.map((s) => `<option value="${s.id}" ${e && e.scene_id === s.id ? 'selected' : ''}>${esc(s.name)}</option>`).join('');
  $('#en-meta').textContent = e ? `${e.author}, создано ${new Date(e.created * 1000).toLocaleString('ru')}, изменено ${new Date(e.updated * 1000).toLocaleString('ru')}` : '';
  $('#en-del').classList.toggle('hidden', !e || !!e.deleted); $('#en-revs').innerHTML = '';
  $('#en-revs-box').classList.toggle('hidden', !e);
  $('#entry-dlg').showModal();
}
$('#en-revs-box').addEventListener('toggle', async (ev) => {
  if (!ev.target.open || !enCur) return;
  const r = await api(`/api/journal/${enCur.id}/revs`, null, 'GET');
  $('#en-revs').innerHTML = r.revs.map((v) => `<div class="rev"><div class="hint">${new Date(v.ts * 1000).toLocaleString('ru')} · ${esc(v.author)} · ${v.vis === 'all' ? '👁' : '🔒'}</div><b>${esc(v.title)}</b><div class="ebody">${esc(v.body)}</div></div>`).join('');
});
$('#en-save').addEventListener('click', async () => {
  const body = { title: $('#en-t').value, cat: $('#en-cat').value, vis: $('#en-vis').value, session: $('#en-session').value, quest: $('#en-quest').value,
    body: $('#en-body').value, char_id: $('#en-char').value, scene_id: $('#en-scene').value };
  if (enCur) await api(`/api/journal/${enCur.id}`, body); else await api(G('/journal'), body);
  $('#entry-dlg').close(); loadJournal(); toast('💾 Сохранено' + (body.vis === 'all' ? ' — игроки увидят' : ''));
});
$('#en-del').addEventListener('click', async () => { if (!enCur || !confirm('Удалить запись? Её можно будет вернуть.')) return; await api(`/api/journal/${enCur.id}`, { deleted: 1 }); $('#entry-dlg').close(); loadJournal(); });

/* ---------- новый персонаж ---------- */
$('#new-char').addEventListener('click', async () => {
  const cat = await catalog();
  $('#newc-pregen').innerHTML = cat.pregens.map((p, i) => `<option value="${i}">${esc(p)}</option>`).join('');
  $('#newc-dlg').showModal();
});
$('#newc-pg').addEventListener('click', async () => { const r = await api(G('/char'), { pregen: +$('#newc-pregen').value, name: $('#newc-name').value.trim() }); $('#newc-dlg').close(); if (r.link) copy(r.link, '✨ Создан. Ссылка для игрока скопирована'); });
$('#newc-blank').addEventListener('click', async () => { const r = await api(G('/char'), { name: $('#newc-name').value.trim() || 'Новый герой' }); $('#newc-dlg').close(); setTimeout(() => openEditor(r.id), 300); });
async function catalog() { if (!S.cat) S.cat = await api('/api/catalog', null, 'GET'); return S.cat; }

/* ---------- лист персонажа (диалог) ---------- */
let edId = null, edTab = 'state';
function openEditor(id, t = 'state') { edId = id; edTab = t; $$('#ed-tabs button').forEach((b) => b.classList.toggle('on', b.dataset.t === t)); $('#ed-link').classList.add('hidden'); renderEditor(); $('#editor').showModal(); }
$('#ed-tabs').addEventListener('click', (e) => { const b = e.target.closest('button'); if (!b) return; edTab = b.dataset.t; $$('#ed-tabs button').forEach((x) => x.classList.toggle('on', x === b)); renderEditor(); });
const v = (x) => (x == null ? '' : x);
function bd(val, p) { return `<span class="bd" title="${esc(parts(p))}">${val}</span>`; }
async function op(body) { const ch = charById(edId); const r = await api(`/api/char/${edId}/op`, { ...body, _rev: ch.rev }); if (r.log && r.log.length) toast(r.log.join('; ')); return r; }
function renderEditor() {
  const ch = charById(edId); if (!ch) return $('#editor').close();
  const c = ch.char, d = ch.d, b = $('#ed-body');
  $('#ed-title').textContent = `${c.name || 'Персонаж'} — ${c.cls || ''} ${d.lvl} ур.`;
  $('#ed-send').classList.toggle('hidden', !ch.player);
  const lb = $('#ed-link');
  if (ch.link) lb.innerHTML = (ch.player ? `🎮 Играет <b>${esc(ch.player.first_name)}</b>. ` : '⏳ Ждёт игрока. ') + `<code>${esc(ch.link)}</code> <button class="ghost sm" id="ed-copy">Копировать</button>` + (ch.player ? ' <button class="ghost sm" id="ed-unbind">Отвязать</button>' : '');
  if (edTab === 'sum') {
    b.innerHTML = `${d.warn.map((w) => `<div class="warn">⚠️ ${esc(w)}</div>`).join('')}
      <div class="stat-row"><div>КД ${bd(d.ac.val, d.ac.parts)}</div><div>Хиты ${c.hp}/${bd(d.hp_max.val, d.hp_max.parts)}</div><div>Иниц. ${bd(sg(d.init.bonus), d.init.parts)}</div>
      <div>Скорость ${bd(d.spd.val, d.spd.parts)}</div><div>Мастерство +${d.pb}</div><div>Пасс. Внимат. ${bd(d.passive.val, d.passive.parts)}</div></div>
      <h4>Характеристики и спасброски <span class="hint">— наведи на число, чтобы увидеть, из чего оно сложилось</span></h4>
      <div class="ab-grid">${AB.map((a) => `<div><b>${AB_SHORT[a]}</b> ${bd(d.ab[a].score, d.ab[a].parts)} (${sg(Math.floor((d.ab[a].score - 10) / 2))})<br><span class="hint">спас</span> ${bd(sg(d.saves[a].bonus), d.saves[a].parts)}${d.saves[a].prof ? ' ●' : ''}</div>`).join('')}</div>
      <h4>Навыки</h4><div class="sk-grid">${d.skills.map((s) => `<div class="${s.prof ? 'prof' : ''}">${s.exp ? '★' : s.prof ? '●' : '○'} ${esc(s.n)} ${bd(sg(s.bonus), s.parts)}</div>`).join('')}</div>
      <h4>Атаки</h4>${d.attacks.map((a) => `<div>⚔️ <b>${esc(a.n)}</b> ${a.hit != null ? bd(sg(a.hit), a.hit_parts) : ''} · ${esc(a.dmg)} ${esc(a.type || '')} ${a.note ? `<span class="hint">${esc(a.note)}</span>` : ''}</div>`).join('') || '<p class="hint">—</p>'}
      ${d.cast.map((cs) => `<h4>Магия: ${esc(cs.n)}</h4><div>СЛ ${bd(cs.dc, cs.dc_parts)} · атака ${bd(sg(cs.atk), cs.atk_parts)} · до ${cs.max_lvl} круга · заговоров ${cs.cantrips}${cs.known ? ' · известных ' + cs.known : ''}${cs.prepared ? ' · готовить ' + cs.prepared : ''}</div>`).join('')}
      ${Object.keys(d.slots).length ? `<div>Ячейки: ${Object.entries(d.slots).map(([k, s]) => `${k}: ${s.left}/${s.max}`).join(' · ')}</div>` : ''}
      ${d.pact ? `<div>Договор: ${d.pact.left}/${d.pact.max} (${d.pact.lvl} круг)</div>` : ''}
      ${d.spells.length ? `<div class="hint">Заклинания: ${d.spells.map(esc).join(', ')}</div>` : ''}
      ${d.features.length ? `<h4>Умения</h4>${d.features.map((f) => `<details class="feat"><summary><b>${esc(f.n)}</b> <span class="hint">${esc(f.src)}</span></summary>${esc(f.d)}</details>`).join('')}` : ''}
      ${d.resist.length ? `<div>Сопротивления: ${d.resist.map(esc).join(', ')}</div>` : ''}`;
  } else if (edTab === 'state') {
    b.innerHTML = `<div class="big-hp">❤️ ${c.hp} / ${d.hp_max.val}${c.temp ? ` <i>+${c.temp} временных</i>` : ''} <span class="hint">· КД ${d.ac.val} · скорость ${d.spd.val} фт</span></div>
      <div class="row gap wrap"><input id="st-n" type="number" class="short" placeholder="сколько"><input id="st-type" placeholder="тип (огонь…)" class="short2" title="Если указать тип, учтутся сопротивления">
        <button data-op="dmg">🗡 Нанести урон</button><button data-op="heal">✚ Вылечить</button><button class="ghost" data-op="temp">🛡 Временные хиты</button></div>
      ${c.hp <= 0 ? `<div>💀 Спасброски от смерти: ${(c.death || [0, 0])[0]} ✔ / ${(c.death || [0, 0])[1]} ✘ ${c.dead ? '☠️ мёртв' : ''}</div>` : ''}
      <h4>Состояния <span class="hint">— нажми, чтобы наложить или снять; наведи — что оно делает</span></h4><div class="chips">${Object.keys(S.conditions).map((n) => `<button class="chip ${(c.conds || []).includes(n) ? 'bad' : ''}" data-cond="${esc(n)}" title="${esc(S.conditions[n])}">${esc(n)}</button>`).join('')}</div>
      <div class="row gap">Истощение <input id="st-exh" type="number" min="0" max="6" class="short" value="${c.exh || 0}"> ${c.conc ? `🧠 Концентрация: <b>${esc(c.conc)}</b> <button class="ghost sm" data-op="conc_drop">прервать</button>` : ''}</div>
      <h4>Эффекты заклинаний <span class="hint">— Благословение, Щит веры и т.п. сами меняют броски и КД</span></h4><div>${(c.effects || []).map((e) => `<div>✨ <b>${esc(e.n)}</b> ${e.rounds ? e.rounds + ' р.' : ''} <span class="hint">${esc(e.note || '')} ${e.src ? '· ' + esc(e.src) : ''}</span> <button class="ghost sm" data-rme="${e.id}">✕</button></div>`).join('') || '<p class="hint">Нет</p>'}</div>
      <div class="row gap"><select id="st-eff">${S.effects.map((n) => `<option>${esc(n)}</option>`).join('')}</select><input id="st-rounds" type="number" class="short" placeholder="раундов"><button id="st-addeff">＋ Эффект</button></div>
      ${d.res.length || Object.keys(d.slots).length || d.pact ? '<h4>Ресурсы</h4>' : ''}
      ${d.res.map((r) => `<div class="row gap">${esc(r.n)} <b>${r.left}/${r.max}</b> <span class="hint">${r.reset === 'short' ? 'корот.' : 'долг.'} отдых</span><button class="ghost sm" data-res="${r.key}:1">−</button><button class="ghost sm" data-res="${r.key}:-1">+</button></div>`).join('')}
      ${Object.entries(d.slots).map(([k, s]) => `<div class="row gap">Ячейки ${k} <b>${s.left}/${s.max}</b><button class="ghost sm" data-slot="${k}:1">−</button><button class="ghost sm" data-slot="${k}:-1">+</button></div>`).join('')}
      ${d.pact ? `<div class="row gap">Договор <b>${d.pact.left}/${d.pact.max}</b><button class="ghost sm" data-slot="pact:1">−</button><button class="ghost sm" data-slot="pact:-1">+</button></div>` : ''}
      ${d.hd.length ? `<div class="hint">Кости хитов: ${d.hd.map((h) => `${h.die} ${h.left}/${h.max}`).join(', ')}</div>` : ''}
      <h4>Отдых</h4><div class="row gap"><button data-op="rest_short">☕ Короткий отдых</button><button data-op="rest_long">🛌 Долгий отдых</button></div>`;
  } else if (edTab === 'sheet') {
    b.innerHTML = `<div class="grid2">${[['name', 'Имя'], ['race', 'Раса'], ['bg', 'Предыстория'], ['al', 'Мировоззрение']].map(([k, t]) => `<label>${t}<input name="${k}" value="${esc(v(c[k]))}"></label>`).join('')}
      ${d.v2 ? `<label>Класс<input value="${esc(c.cls)}" disabled title="Классы меняются повышением уровня"></label>` : `<label>Класс<input name="cls" value="${esc(v(c.cls))}"></label><label>Уровень<input name="lvl" type="number" value="${c.lvl}"></label>`}
      <label>Золото<input name="gold" type="number" value="${v(c.gold)}"></label><label>Опыт<input name="xp" type="number" value="${v(c.xp)}"></label></div>
      ${d.v2 ? '' : `<div class="grid4">${[['hp', 'Хиты'], ['mhp', 'Макс. хиты'], ['ac', 'КД'], ['spd', 'Скорость']].map(([k, t]) => `<label>${t}<input name="${k}" type="number" value="${c[k]}"></label>`).join('')}</div>`}
      <h4>Характеристики ${d.v2 ? '<span class="hint">— правка запишется в разбивку как «Правка мастера»</span>' : ''}</h4>
      <div class="stats-ed">${AB.map((a) => `<label>${AB_SHORT[a]}<input type="number" min="1" max="30" name="st_${a}" value="${c.st[a]}">${d.v2 ? '' : `<span class="sv"><input type="checkbox" name="sv_${a}" ${(c.sv || []).includes(a) ? 'checked' : ''}> спас</span>`}</label>`).join('')}</div>
      <h4>Навыки <span class="hint">● владение, ★ компетентность</span></h4>
      <div class="skills-ed">${S.skills.map((s, i) => `<label><input type="checkbox" name="sk_${i}" ${(c.sk || []).includes(i) ? 'checked' : ''}><input type="checkbox" name="ex_${i}" ${(c.ex || []).includes(i) ? 'checked' : ''} title="компетентность"> ${esc(s)}</label>`).join('')}</div>
      ${d.v2 ? `<h4>Владения</h4><div class="grid2">${[['armor', 'Доспехи'], ['weapons', 'Оружие'], ['tools', 'Инструменты'], ['langs', 'Языки']].map(([k, t]) => `<label>${t}<input name="pf_${k}" value="${esc((c.profs || {})[k]?.join(', ') || '')}"></label>`).join('')}</div>` : ''}
      <h4>Свои атаки <span class="hint">(дополнительно к оружию из снаряжения)</span></h4><div id="ed-atk"></div><button type="button" class="ghost sm" id="ed-atk-add">＋ Атака</button>
      ${Object.entries(TEXTS).map(([k, t]) => `<h4>${t}</h4><textarea name="${k}" rows="3">${esc(typeof c[k] === 'string' ? c[k] : '')}</textarea>`).join('')}
      <div class="row gap end ed-foot"><button class="ghost danger" id="ed-del">Удалить персонажа</button><span class="grow"></span><button class="primary auto" id="ed-save">💾 Сохранить</button></div>`;
    (c.atk || []).forEach(atkRow);
    $('#ed-atk-add').onclick = () => atkRow();
  } else if (edTab === 'items') {
    b.innerHTML = `<div>${(c.items || []).map((it, i) => `<div class="row gap item"><button class="ghost sm" data-eq="${i}" title="Надеть/снять">${it.eq ? '✅' : '⬜'}</button><b class="grow">${esc(it.n)}</b>
      <span class="hint">${it.kind === 'weapon' ? esc(it.dmg + ' ' + (it.dtype || '')) : it.kind === 'armor' || it.kind === 'shield' ? 'КД ' + it.ac : ''} ×${it.qty ?? 1}</span><button class="ghost sm" data-rmi="${i}">✕</button></div>`).join('') || '<p class="hint">Пусто</p>'}</div>
      <div class="row gap"><input id="it-name" list="it-list" placeholder="Предмет из справочника или свой"><input id="it-qty" type="number" value="1" class="short"><button id="it-add">＋</button></div><datalist id="it-list"></datalist>
      <p class="hint">✅ — надето или в руках: только надетые доспехи и оружие участвуют в КД и атаках.</p>`;
    catalog().then((cat) => { $('#it-list').innerHTML = cat.items.map((n) => `<option value="${esc(n)}">`).join(''); });
  } else if (edTab === 'spells') {
    b.innerHTML = `<p class="hint">Известные/подготовленные. Игрок может менять их сам в боте (/spells → 📚).</p>
      <div id="sp-cur">${['known', 'prep'].map((k) => `<h4>${k === 'known' ? 'Известные / в книге' : 'Подготовленные'}</h4>${((c.sp || {})[k] || []).map((n) => `<span class="chip">${esc(n)} <a data-rms="${k}:${esc(n)}">✕</a></span>`).join(' ') || '<span class="hint">—</span>'}`).join('')}</div>
      <div class="row gap"><input id="sp-q" placeholder="🔍 найти заклинание"><select id="sp-to"><option value="known">в известные</option><option value="prep">в подготовленные</option></select></div><div id="sp-found"></div>`;
    $('#sp-q').oninput = async () => {
      const cat = await catalog(), q = $('#sp-q').value.toLowerCase();
      $('#sp-found').innerHTML = q.length < 2 ? '' : cat.spells.filter((s) => s.n.toLowerCase().includes(q) || s.d.toLowerCase().includes(q)).slice(0, 20)
        .map((s) => `<div class="row gap"><button class="ghost sm" data-adds="${esc(s.n)}">＋</button><b>${esc(s.n)}</b> <span class="hint">${s.lvl || 'заговор'} · ${esc(s.d)}</span></div>`).join('');
    };
  } else if (edTab === 'adj') {
    b.innerHTML = `<p class="hint">Ручные корректировки поверх расчёта: магический предмет, проклятие, решение мастера. Причина видна в разбивке значения.</p>
      ${(c.adj || []).map((a) => `<div class="row gap"><b>${esc(S.adj[a.field] || a.field)}</b> ${sg(a.val)} <span class="grow hint">${esc(a.reason)}</span><button class="ghost sm" data-rma="${a.id}">✕</button></div>`).join('') || '<p class="hint">Нет корректировок</p>'}
      <div class="row gap wrap"><select id="adj-f">${Object.entries(S.adj).map(([k, t]) => `<option value="${k}">${esc(t)}</option>`).join('')}</select><input id="adj-v" type="number" class="short" placeholder="+1"><input id="adj-r" placeholder="Причина (обязательно)" class="grow"><button id="adj-add">＋</button></div>`;
  } else if (edTab === 'hist') {
    b.innerHTML = '<p class="hint">Загрузка…</p>';
    api(`/api/char/${edId}/history`, null, 'GET').then((r) => {
      b.innerHTML = `<div class="row gap"><button id="ed-undo">↩️ Отменить последнее изменение</button></div>` +
        r.history.map((h) => `<div class="hist ${h.undone ? 'undone' : ''}"><span class="hint">${new Date(h.ts * 1000).toLocaleString('ru')}</span> · <b>${esc(h.actor)}</b>: ${esc(h.label)}</div>`).join('');
    });
  }
}
function atkRow(a = { n: '', hit: '', dmg: '' }) {
  const d = document.createElement('div'); d.className = 'atk-row';
  d.innerHTML = `<input placeholder="Название" value="${esc(a.n)}" class="an"><input type="number" placeholder="+5" value="${a.hit === '' ? '' : a.hit}" class="ah"><input placeholder="1d8+3" value="${esc(a.dmg)}" class="ad"><button type="button" class="ghost">✕</button>`;
  d.querySelector('button').onclick = () => d.remove(); $('#ed-atk').append(d);
}
$('#editor').addEventListener('click', async (e) => {
  const ch = charById(edId); if (!ch) return; const t = e.target;
  if (t.id === 'ed-copy') return copy(ch.link, '🔗 Скопировано');
  if (t.id === 'ed-link-btn') return $('#ed-link').classList.toggle('hidden');
  if (t.id === 'ed-send') { await api(`/api/char/${edId}/send`); return toast('📨 Лист отправлен игроку'); }
  if (t.id === 'ed-lvl') { if (confirm(`Дать «${ch.char.name}» новый уровень? Игрок выберет, что получить, в боте (/levelup).`)) { await api(`/api/char/${edId}/op`, { op: 'level_grant' }); toast('📈 Уровень выдан'); } return; }
  if (t.id === 'ed-unbind') { if (confirm('Отвязать игрока? Появится новая ссылка.')) { await api(`/api/char/${edId}/unbind`); } return; }
  if (t.id === 'ed-del') { if (confirm('Удалить персонажа совсем?')) { await api(`/api/char/${edId}`, null, 'DELETE'); $('#editor').close(); } return; }
  if (t.id === 'ed-undo') { const r = await api(`/api/char/${edId}/undo`); toast('↩️ Отменено: ' + r.undone); return; }
  if (t.id === 'ed-save') return saveSheet(ch);
  if (t.dataset.op) { const n = +($('#st-n')?.value || 0); if (['dmg', 'heal', 'temp'].includes(t.dataset.op) && !n) return toast('Введи число'); return op({ op: t.dataset.op, n, type: $('#st-type')?.value || '' }); }
  if (t.dataset.cond) return op({ op: 'cond', name: t.dataset.cond });
  if (t.dataset.rme) return op({ op: 'rm_effect', id: t.dataset.rme });
  if (t.id === 'st-addeff') return op({ op: 'effect', name: $('#st-eff').value, rounds: +$('#st-rounds').value || null });
  if (t.dataset.res) { const [k, n] = t.dataset.res.split(':'); return op({ op: 'res', key: k, n: +n }); }
  if (t.dataset.slot) { const [k, n] = t.dataset.slot.split(':'); return op({ op: 'slot', slot: k, n: +n }); }
  if (t.dataset.eq) return op({ op: 'item_eq', i: +t.dataset.eq });
  if (t.dataset.rmi) return op({ op: 'item_rm', i: +t.dataset.rmi });
  if (t.id === 'it-add') { const n = $('#it-name').value.trim(); if (n) op({ op: 'item_add', name: n, n: +$('#it-qty').value || 1 }); return; }
  if (t.dataset.adds) { const sp = JSON.parse(JSON.stringify(ch.char.sp || { known: [], prep: [] })); const k = $('#sp-to').value; if (!sp[k].includes(t.dataset.adds)) sp[k].push(t.dataset.adds); await api(`/api/char/${edId}`, { sp, _rev: ch.rev }); return toast('＋ ' + t.dataset.adds); }
  if (t.dataset.rms) { const [k, n] = t.dataset.rms.split(':'); const sp = JSON.parse(JSON.stringify(ch.char.sp)); sp[k] = sp[k].filter((x) => x !== n); await api(`/api/char/${edId}`, { sp, _rev: ch.rev }); return; }
  if (t.id === 'adj-add') return op({ op: 'adj', field: $('#adj-f').value, n: +$('#adj-v').value || 0, reason: $('#adj-r').value.trim() });
  if (t.dataset.rma) return op({ op: 'rm_adj', id: t.dataset.rma });
});
$('#editor').addEventListener('change', (e) => { if (e.target.id === 'st-exh') op({ op: 'exh', n: +e.target.value }); });
async function saveSheet(ch) {
  const b = $('#ed-body'), q = (n) => b.querySelector(`[name="${n}"]`);
  const data = { st: {}, sv: [], sk: [], ex: [], atk: [], _rev: ch.rev };
  for (const k of ['name', 'race', 'bg', 'al', 'cls', 'lvl', 'hp', 'mhp', 'ac', 'spd', 'gold', 'xp', ...Object.keys(TEXTS)]) if (q(k)) data[k] = q(k).value;
  AB.forEach((a) => { data.st[a] = q('st_' + a).value; if (q('sv_' + a)?.checked) data.sv.push(a); });
  if (ch.d.v2) delete data.sv;
  S.skills.forEach((_, i) => { if (q('sk_' + i).checked) data.sk.push(i); if (q('ex_' + i).checked) data.ex.push(i); });
  if (ch.d.v2) { data.profs = {}; for (const k of ['armor', 'weapons', 'tools', 'langs']) data.profs[k] = q('pf_' + k).value.split(',').map((s) => s.trim()).filter(Boolean); }
  $$('#ed-atk .atk-row').forEach((r) => { const n = r.querySelector('.an').value.trim(); if (n) data.atk.push({ n, hit: r.querySelector('.ah').value || 0, dmg: r.querySelector('.ad').value.trim() || '1d4' }); });
  await api(`/api/char/${edId}`, data); toast('💾 Сохранено — лист в Telegram обновится сам');
}

/* ---------- живое соединение ---------- */
function setConn(ok) { const c = $('#conn'); c.textContent = ok ? 'онлайн' : 'нет связи'; c.classList.toggle('off', !ok); }
function connect() {
  const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws?g=' + S.gid);
  S.ws = ws;
  ws.onopen = () => { setConn(true); refresh(); };
  ws.onclose = () => { setConn(false); S.ws = null; setTimeout(connect, 1500); };
  ws.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === 'roll') { S.rolls.push(ev.roll); if (S.rolls.length > 300) S.rolls.shift(); renderFeed(); renderPlayers(); renderCombat(); }
    else if (ev.type === 'chars') {
      const had = new Set(S.chars.filter((c) => c.player).map((c) => c.id)); S.chars = ev.chars; renderPlayers(); renderCombat(); renderRequests();
      const joined = S.chars.find((c) => c.player && !had.has(c.id) && c.id !== S.createdId); if (joined && S.loaded && !S.justCreated) toast(`🎮 ${joined.player.first_name} забрал(а) «${joined.char.name}»`);
      S.justCreated = false;
      if ($('#editor').open && edTab !== 'sheet') renderEditor();
    }
    else if (ev.type === 'drafts') { S.drafts = ev.drafts; renderDrafts(); }
    else if (ev.type === 'created') { toast(`🆕 ${ev.player} создал(а) героя «${ev.name}»`); S.justCreated = true; }
    else if (ev.type === 'requests') { S.requests = ev.requests; renderRequests(); }
    else if (ev.type === 'combat') { S.combat = ev.combat; renderCombat(); renderPlayers(); drawMap(); }
    else if (ev.type === 'scene') { if (ev.scene && (!S.scene || S.scene.id === ev.scene.id || !MAP.drag)) { if (!MAP.drag && !MAP.painting) { S.scene = ev.scene; renderScenes(); drawMap(); } } else if (!ev.scene) { S.scene = null; renderScenes(); drawMap(); } loadScenes(); }
    else if (ev.type === 'journal') { if (!$('#t-journal').classList.contains('hidden')) loadJournal(); }
    else if (ev.type === 'games') loadGames().then(renderLobby);
    else if (ev.type === 'cleared') { S.rolls = []; renderFeed(); renderPlayers(); }
  };
}
async function refresh() {
  if (!S.gid) await loadGames();
  const st = await api(G('/state'), null, 'GET');
  Object.assign(S, { chars: st.chars, rolls: st.rolls, requests: st.requests, skills: st.skills, lobby_link: st.lobby_link, bot: st.bot,
    drafts: st.drafts, combat: st.combat, monsters: st.monsters, effects: st.effects, conditions: st.conditions, adj: st.adj_fields,
    games: st.games, session: st.session, loaded: true });
  if (!$('#ask-skill').options.length) $('#ask-skill').innerHTML = st.skills.map((s, i) => `<option value="${i}">${esc(s)}</option>`).join('');
  $('#npc-tpl').innerHTML = '<option value="">— свой —</option>' + st.monsters.map((m) => `<option>${esc(m)}</option>`).join('');
  renderGames(); renderLobby(); renderDrafts(); renderPlayers(); renderFeed(); renderRequests(); renderCombat(); loadScenes();
  if ($('#editor').open) renderEditor();
}
(async () => { await loadGames(); tab(load('tab', 'feed')); connect(); })();

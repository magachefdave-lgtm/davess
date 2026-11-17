'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const html = fs.readFileSync(path.resolve(__dirname, '../../frontend/dist/index.html'), 'utf8');
const code = fs.readFileSync(path.resolve(__dirname, '../../frontend/dist/update_status.js'), 'utf8');

// A small DOM tree keeps the actual header markup and updater controls in the test.
// Network responses and the page clock are the only external boundaries replaced.
class Element {
  constructor(tag = 'div', attributes = {}) {
    this.tagName = tag.toUpperCase(); this.attributes = attributes; this.children = [];
    this.style = {}; this.disabled = 'disabled' in attributes; this.hidden = 'hidden' in attributes;
    this.id = attributes.id || ''; this.className = attributes.class || ''; this.isConnected = true;
  }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  insertBefore(child, before) { child.parentNode = this; this.children.splice(this.children.indexOf(before), 0, child); }
  setAttribute(key, value) { this.attributes[key] = value; }
  querySelector(selector) {
    for (const child of this.children) {
      if (child.id === selector.slice(1)) return child;
      const found = child.querySelector && child.querySelector(selector); if (found) return found;
    }
    return null;
  }
  get nextElementSibling() {
    const siblings = this.parentNode.children.filter(child => child.tagName);
    return siblings[siblings.indexOf(this) + 1] || null;
  }
  get textContent() { return this._text || this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this.children = []; this._text = String(value); }
  set innerHTML(value) { this.children = []; this._text = ''; parseInto(this, value); }
}
function parseInto(parent, markup) {
  const stack = [parent];
  for (const token of markup.match(/<!--[\s\S]*?-->|<[^>]+>|[^<]+/g) || []) {
    if (token.startsWith('<!--')) continue;
    if (token.startsWith('</')) { if (stack.length > 1) stack.pop(); continue; }
    if (!token.startsWith('<')) { stack.at(-1).appendChild({textContent: token}); continue; }
    const tag = token.match(/^<([\w-]+)/); if (!tag) continue;
    const attrs = {};
    for (const match of token.slice(tag[0].length).matchAll(/([\w-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'))?/g)) attrs[match[1]] = match[2] ?? match[3] ?? '';
    const node = stack.at(-1).appendChild(new Element(tag[1], attrs));
    if (!/\/>$/.test(token) && !/^(input|br|hr|img|meta|link)$/i.test(tag[1])) stack.push(node);
  }
}
const version = (overrides = {}) => ({mode:'ADMIN', current_version:'3.18.27', latest_version:'3.18.28',
  result:'available', checked_at:'2026-11-06T10:00:00Z', update_available:true, can_update:true,
  local:true, managed:true, busy:false, ...overrides});
const protectedStatus = (overrides = {}) => ({local:true, managed:true, mode:'ADMIN', version:'3.18.27', status:null,
  manual:{result:'available', offer_id:'fresh-offer', instance_id:'old', current_version:'3.18.27',
    available_version:'3.18.28', restart_supported:true, busy:false}, ...overrides});
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
function harness({versions = [version()], protectedData = protectedStatus(), confirm = true, opening, knownCurrent} = {}) {
  const root = new Element();
  parseInto(root, html.match(/<header>[\s\S]*?<\/header>/)[0] + '<div id="serverinfo-update"></div>');
  const calls = [], timers = new Map(), events = new Map();
  let nextTimer = 0, now = 0, authCalls = 0, confirmations = 0, opens = 0, restarted = false, reloads = 0;
  const get = id => root.querySelector('#' + id);
  if (knownCurrent) get('header-current-version').textContent = 'v' + knownCurrent;
  for (const id of ['change-panel-title','btn-exec-swap','btn-exec-sub','viewer-reason-box']) root.appendChild(new Element('div', {id}));
  const response = value => ({ok:true, status:200, json:async () => value});
  async function fetch(url, options = {}) {
    calls.push({url, options});
    if (url === '/api/system/version-status') {
      const fixture = versions.length > 1 ? versions.shift() : versions[0];
      if (fixture instanceof Error) throw fixture;
      return response(await fixture);
    }
    if (options.method === 'POST') { restarted = true; return response({operation:'new-operation', message:'preparing'}); }
    if (url === '/api/system/health') return response({mode:'ADMIN', version:'3.18.28', instance_id:'new', port:8000});
    if (url.includes('?operation=')) return response({...protectedData, manual:{result:'updated', operation:'new-operation',
      current_version:'3.18.28', new_instance_id:'new', port:8000, busy:false, restart_supported:true}});
    return response(protectedData);
  }
  const window = {
    async openServerInfo() { opens++; if (opening) await opening; },
    async askConfirm() { confirmations++; return typeof confirm === 'function' ? await confirm() : confirm; },
    addEventListener(name, handler) { events.set(name, handler); },
    location:{reload() { reloads++; get('serverinfo-update').isConnected = false; }}
  };
  const context = vm.createContext({window, document:{getElementById:get, createElement:tag => new Element(tag), querySelectorAll:()=>[],
    hidden:false, addEventListener(name, handler) { events.set(name, handler); }},
    localStorage:{getItem(){return null;}}, Date:class extends Date {static now(){return now;}},
    fetch, fetchWithAdminToken:async (...args) => { authCalls++; return fetch(...args); },
    setTimeout(handler, delay) {
      const id = ++nextTimer;
      if (restarted && delay === 1000) { now += delay; queueMicrotask(handler); }
      else timers.set(id, {handler, delay});
      return id;
    }, clearTimeout:id => timers.delete(id), SERVER_CONNECTED:true, SYSTEM_MODE:'ADMIN', APP_VER:'3.18.27', SYS_INFO:null,
    renderUnsavedBar(){}, renderAuthStatus(){}, syncMenuPops(){}
  });
  vm.runInContext(html.match(/    function applyModeUI\(\) \{[\s\S]*?\n    \}/)[0], context);
  vm.runInContext(code, context);
  return {get, calls, timers, events, window,
    get authCalls(){return authCalls;}, get confirmations(){return confirmations;}, get opens(){return opens;}, get reloads(){return reloads;},
    async tick() { const [id, timer] = timers.entries().next().value; timers.delete(id); now += timer.delay; timer.handler(); await flush(); },
    mode(mode, connected=true, appVersion='3.18.27') { context.SYSTEM_MODE=mode; context.SERVER_CONNECTED=connected; context.APP_VER=appVersion; context.applyModeUI(); },
    click:() => get('header-update').onclick()};
}

test('main header fetches public versions without an authentication prompt or manual click', async () => {
  const h = harness(); await flush();
  assert.equal(h.get('header-current-version').textContent, 'v3.18.27');
  assert.equal(h.get('header-update-label').textContent, 'v3.18.28 업데이트');
  assert.equal(h.get('header-latest-version').hidden, true);
  const request = h.calls.find(call => call.url === '/api/system/version-status');
  assert.equal(request.options.cache, 'no-store');
  assert.equal(h.authCalls, 0);
  assert.equal(h.opens, 0);
});

test('outdated local app shows one lower-row update action below its role and current version', async () => {
  const h = harness(); await flush();
  const button = h.get('header-update');
  assert.equal(button.hidden, false); assert.equal(button.disabled, false);
  const badge = h.get('header-version-badge');
  assert.ok(badge, 'the role and updater must share the same badge');
  assert.equal(badge.querySelector('#header-current-version'), h.get('header-current-version'));
  assert.equal(badge.querySelector('#header-update'), button);
  assert.notEqual(h.get('header-notifications').nextElementSibling, button);
});

for (const [name, status, visible, enabled] of [
  ['current version', version({current_version:'3.18.28', update_available:false, can_update:false, result:'up_to_date'}), false, false],
  ['remote client', version({local:false, can_update:false}), false, false],
  ['source execution', version({managed:false, can_update:false}), true, false],
  ['blocked update', version({result:'blocked', can_update:false}), true, false],
  ['busy update', version({busy:true, can_update:false}), true, false]
]) test(name + ' cannot start a header restart', async () => {
  const h = harness({versions:[status]}); await flush();
  const button = h.get('header-update');
  assert.equal(button.hidden, !visible); assert.equal(button.disabled, !enabled);
  await h.click();
  assert.equal(h.opens, 0);
  assert.equal(h.calls.filter(call => call.options.method === 'POST').length, 0);
});

for (const [result, expected] of [['checking', /확인 중/], ['check_failed', /확인 불가/], ['no_repository', /설정 없음/]]) {
  test(result + ' never presents the current version as a discovered latest version', async () => {
    const h = harness({versions:[version({latest_version:null, result, update_available:false, can_update:false})]}); await flush();
    assert.match(h.get('header-current-version').textContent, /3\.18\.27/);
    assert.match(h.get('header-latest-version').textContent, expected);
    assert.doesNotMatch(h.get('header-latest-version').textContent, /3\.18\.27|3\.18\.28/);
    assert.equal(h.get('header-update').hidden, true);
  });
}

test('checking status polls automatically, reuses one timer, and stops after leaving the page', async () => {
  const h = harness({versions:[version({latest_version:null, result:'checking', update_available:false, can_update:false}), version()]});
  await flush(); assert.equal(h.timers.size, 1);
  await h.tick();
  assert.match(h.get('header-update-label').textContent, /3\.18\.28/);
  await h.events.get('focus')(); await flush();
  assert.equal(h.timers.size, 1);
  h.events.get('pagehide')(); assert.equal(h.timers.size, 0);
  const before = h.calls.length;
  await h.events.get('focus')(); await flush();
  assert.equal(h.calls.length, before);
});

test('a finishing available worker is checked again promptly so its update button becomes usable', async () => {
  const h = harness({versions:[version({busy:true, can_update:false}), version()]}); await flush();
  assert.equal(h.get('header-update').disabled, true);
  assert.ok(h.timers.values().next().value.delay <= 2000, 'a completed check must not strand the disabled button for five minutes');
  await h.tick();
  assert.equal(h.get('header-update').disabled, false);
});

test('a failed check with a hung worker shows unknown latest and waits before retrying', async () => {
  const h = harness({versions:[version({result:'check_failed', latest_version:null, busy:true, update_available:false, can_update:false})]}); await flush();
  assert.match(h.get('header-latest-version').textContent, /확인 불가/);
  assert.ok(h.timers.values().next().value.delay >= 300000, 'a failed worker must not trigger continuous polling');
});

test('a failed public fetch keeps the known current version and never claims a latest release', async () => {
  const h = harness({versions:[version(), new Error('connection unavailable')]}); await flush();
  await h.events.get('focus')(); await flush();
  assert.match(h.get('header-current-version').textContent, /3\.18\.27/);
  assert.match(h.get('header-latest-version').textContent, /확인 불가/);
  assert.doesNotMatch(h.get('header-latest-version').textContent, /3\.18\.27|3\.18\.28/);
  assert.equal(h.get('header-update').hidden, true);
});

test('an unreachable version endpoint reports unknown versions instead of checking forever', async () => {
  const h = harness({versions:[new Error('connection unavailable')]}); await flush();
  assert.match(h.get('header-current-version').textContent, /확인 불가/);
  assert.match(h.get('header-latest-version').textContent, /확인 불가/);
});

test('header waits for fresh protected controls and reuses the existing confirmed restart and nonce reconnect', async () => {
  let finishOpening;
  const opening = new Promise(resolve => { finishOpening = resolve; });
  const h = harness({opening}); await flush();
  const clicked = h.click(); await flush();
  assert.equal(h.confirmations, 0, 'the protected offer must be loaded after opening the progress surface');
  finishOpening(); await clicked;
  assert.equal(h.authCalls, 2, 'one fresh protected GET and the existing protected restart POST');
  assert.equal(h.confirmations, 1);
  const posts = h.calls.filter(call => call.options.method === 'POST');
  assert.equal(posts.length, 1);
  assert.deepEqual(JSON.parse(posts[0].options.body), {action:'prepare_restart', offer_id:'fresh-offer', instance_id:'old'});
  assert.ok(h.calls.some(call => call.url === '/api/system/update-status?operation=new-operation'));
  assert.equal(h.reloads, 1);
});

test('cancelled confirmation and repeated header clicks submit no update and do not reset the active dialog', async () => {
  let answer;
  const confirmation = new Promise(resolve => { answer = resolve; });
  const h = harness({confirm:() => confirmation}); await flush();
  const first = h.click(); await flush();
  const originalApply = h.get('update-apply-restart');
  await h.click(); await h.events.get('focus')(); await flush();
  assert.equal(h.opens, 1); assert.equal(h.confirmations, 1);
  assert.equal(h.get('update-apply-restart'), originalApply, 'passive refresh must preserve the active confirmation and controls');
  assert.equal(h.get('header-update').disabled, true);
  answer(false); await first; await flush();
  assert.equal(h.calls.filter(call => call.options.method === 'POST').length, 0);
  assert.equal(h.get('header-update').disabled, false);
});

test('fresh protected status can withdraw an old public update offer without prompting or restarting', async () => {
  const h = harness({protectedData:protectedStatus({manual:{result:'up_to_date', restart_supported:true, busy:false}})}); await flush();
  await h.click();
  assert.equal(h.confirmations, 0);
  assert.equal(h.calls.filter(call => call.options.method === 'POST').length, 0);
});

test('a fresh protected response that is remote cannot restart the server despite an earlier local offer', async () => {
  const h = harness({protectedData:protectedStatus({local:false})}); await flush();
  await h.click();
  assert.equal(h.confirmations, 0);
  assert.equal(h.calls.filter(call => call.options.method === 'POST').length, 0);
});


test('known current version from system info survives an initial automatic check failure', async () => {
  const h = harness({knownCurrent:'3.18.27', versions:[new Error('offline')]}); await flush();
  assert.equal(h.get('header-current-version').textContent, 'v3.18.27');
  assert.match(h.get('header-latest-version').textContent, /확인 불가/);
  assert.equal(h.get('header-update').hidden, true);
});

test('system mode refresh and reconnect preserve current-version nodes and the update handler', async () => {
  const h = harness({confirm:false}); await flush();
  const current = h.get('header-current-version'), button = h.get('header-update'), handler=button.onclick;
  for (const [mode,connected,want] of [['ADMIN',true,'관리자용'],['VIEWER',true,'조회용'],['VIEWER',false,'연결 안 됨'],['ADMIN',true,'관리자용']]) {
    h.mode(mode,connected);
    assert.equal(h.get('header-current-version'),current);
    assert.equal(h.get('app-mode-badge').querySelector('#header-current-version'),current);
    assert.equal(h.get('header-mode-label').textContent,want);
    assert.equal(current.textContent,'v3.18.27');
    assert.equal(h.get('header-update'),button); assert.equal(button.onclick,handler);
  }
  await h.click(); assert.equal(h.confirmations,1);
  assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);
});

test('system info arriving after an initial version failure repairs the current-version tooltip', async () => {
  const h = harness({versions:[new Error('offline')]}); await flush();
  assert.equal(h.get('header-current-version').title,'현재 버전을 확인하지 못했습니다.');
  h.mode('VIEWER',true,'3.18.29');
  assert.equal(h.get('header-current-version').textContent,'v3.18.29');
  assert.equal(h.get('header-current-version').title,'현재 버전 v3.18.29');
  assert.equal(h.get('header-version-state-label').textContent,'최신 확인 불가');
});

test('a current release shows the latest status while a remote newer release still shows its version', async () => {
  const h = harness({versions:[version({current_version:'3.18.28',result:'up_to_date',update_available:false,can_update:false}),version({local:false,can_update:false})]});await flush();
  assert.equal(h.get('header-latest-version').hidden,false);
  assert.equal(h.get('header-version-state-label').textContent,'최신');
  assert.equal(h.get('header-version-check').hidden,false);
  assert.equal(h.get('header-update').hidden,true);
  await h.events.get('focus')();await flush();
  assert.match(h.get('header-latest-version').textContent,/최신 v3\.18\.28/);
  assert.equal(h.get('header-version-check').hidden,true);
  assert.equal(h.get('header-update').hidden,true);
});

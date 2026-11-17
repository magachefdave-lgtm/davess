'use strict';
// 3.18.0 후보 QA 결함 수정 확인(다른 파일의 시험 틀로 다루기 어려운 것만 모았다).
//  - 조회용 [연결 정보 → 자주 바꾸는 설정]: 관리자 암호·추가 백업 폴더를 감추고, 로그는 '로그 폴더 열기' + 짧은 경로
//  - 신청 창 [신청하기] 줄 고정(1366×768), 알림 배지 글자 크기
// update_status.js 는 실제 파일을 Node VM 에서 실행하고, 서버 응답·DOM 만 대역을 쓴다.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const root = path.resolve(__dirname, '../..');
const dist = path.join(root, 'frontend/dist');
const statusJs = fs.readFileSync(path.join(dist, 'update_status.js'), 'utf8');
const html = fs.readFileSync(path.join(dist, 'index.html'), 'utf8');
const commonCss = fs.readFileSync(path.join(dist, 'usability_common.css'), 'utf8');

function box() {
  const made = new Map();
  return {
    innerHTML: '', textContent: '', made,
    querySelector(sel) {
      const id = sel.replace(/^#/, '');
      if (!new RegExp(`id='${id}'`).test(this.innerHTML)) return null;
      if (!made.has(id)) made.set(id, {id, value: '', checked: false, style: {}, textContent: '', appendChild() {}});
      return made.get(id);
    },
  };
}
async function renderWith(reply) {
  const target = box();
  const posts = [];
  const ctx = vm.createContext({
    console, Promise, Date, JSON, Object, String, Array, RegExp,
    localStorage: {getItem: () => null, setItem: () => {}},
    document: {getElementById: id => (id === 'serverinfo-update' ? target : null), createElement: () => ({}), body: {insertBefore() {}, firstChild: null}},
    fetch: async (url, options = {}) => {
      if (options.method === 'POST') { posts.push(JSON.parse(options.body)); return {ok: true, status: 200, json: async () => ({message: '열었습니다.'})}; }
      return {ok: true, status: 200, json: async () => reply};
    },
  });
  ctx.window = ctx;
  vm.runInContext(statusJs, ctx, {filename: 'update_status.js'});
  await ctx.renderUpdateStatus();
  return {target, posts};
}
const STATUS = {checked_at: '2026-10-14T09:00:00+09:00', result: 'up_to_date', message: '최신입니다.',
  log_path: 'C:\\Users\\kim\\AppData\\Local\\WLR_OnAir\\VIEWER\\launcher.log'};

test('viewer connection info hides admin password and backup mirror, shows a short log path with an open button', async () => {
  const {target, posts} = await renderWith({managed: true, version: '3.18.0', status: STATUS, local: true, canOpen: true, mode: 'VIEWER',
    settings: {server_port: '8001'}});
  const page = target.innerHTML;
  assert.doesNotMatch(page, /관리자 암호|set-pass|추가 백업 폴더|set-mirror|허용할 접속 이름/);
  assert.match(page, /id='set-port'/);
  assert.match(page, /…\\VIEWER\\launcher\.log/);
  assert.match(page, /title='C:\\Users\\kim\\AppData\\Local\\WLR_OnAir\\VIEWER\\launcher\.log'/, '전체 경로는 마우스를 올리면 보인다');
  assert.match(page, /id='update-open-logs'[^>]*>로그 폴더 열기</);
  await target.made.get('update-open-logs').onclick();
  assert.deepEqual(posts, [{action: 'open_logs'}]);
  // 저장하면 보이는 칸(포트)만 보낸다 — 감춘 값은 보내지 않는다.
  target.querySelector('#set-port').value = '8011';
  await target.querySelector('#set-save').onclick();
  assert.deepEqual(posts[1], {action: 'save_settings', settings: {server_port: '8011'}});
});

test('admin connection info keeps every frequent setting and the full log path', async () => {
  const {target} = await renderWith({managed: true, version: '3.18.0', status: STATUS, local: true, canOpen: true, mode: 'ADMIN',
    settings: {admin_password_set: true, server_port: '8000', allowed_hosts: '', backup_mirror_dir: ''}});
  const page = target.innerHTML;
  for (const id of ['set-pass', 'set-pass-clear', 'set-port', 'set-hosts', 'set-mirror']) assert.match(page, new RegExp(`id='${id}'`));
  assert.match(page, /C:\\Users\\kim\\AppData\\Local\\WLR_OnAir\\VIEWER\\launcher\.log/);
  assert.doesNotMatch(page, /update-open-logs/);
});

test('even if an old server still sends them, a viewer never renders admin-only setting fields', async () => {
  const {target} = await renderWith({managed: true, version: '3.18.0', status: null, local: true, canOpen: false, mode: 'VIEWER',
    settings: {admin_password_set: false, server_port: '8001', allowed_hosts: '', backup_mirror_dir: ''}});
  assert.doesNotMatch(target.innerHTML, /set-pass|set-mirror/);
});

test('request windows keep the submit row visible on short screens', () => {
  const requestCss = fs.readFileSync(path.join(dist, 'absence_request_calendar.css'), 'utf8');
  const footer = /#absence-request-modal \.modal-body\.arc-body>\.arc-footer\{([^}]+)\}/.exec(requestCss)?.[1] || '';
  assert.match(footer, /flex:none/);
  assert.match(footer, /position:static/);
  assert.match(footer, /bottom:auto/);
  assert.match(footer, /margin:0/);
  assert.match(footer, /justify-content:flex-end/);
  assert.match(commonCss, /\.oa-modal \.oa-body \.ur-footer\{position:sticky;bottom:-22px/);
  assert.match(commonCss, /#abs-editor>div:has\(>#abs-submit-btn\)\{position:sticky/, '관리자 휴가·부재 관리의 [새 부재 등록] 줄');
  // The request footer stays outside the scrolling calendar/form layout.
  const modal = /<div class="modal" id="absence-request-modal"[\s\S]*?\n {2}<\/div>\n/.exec(html)[0];
  const last = [...modal.matchAll(/\n {6}<div[^>]*>/g)].at(-1)[0];
  assert.match(last, /class="arc-footer"/);
  assert.match(modal.slice(modal.lastIndexOf(last)), /id="btn-submit-absence-request"/);
});

test('notification badge text is at least 11px', () => {
  const rule = /\.notif-badge \{ background: var\(--holiday-text\); font: 600 (\d+(?:\.\d+)?)(px|rem)/.exec(html);
  assert.ok(rule, '배지 글꼴 규칙');
  const px = rule[2] === 'rem' ? Number(rule[1]) * 16 : Number(rule[1]);
  assert.ok(px >= 11, `배지 글자 ${px}px`);
});

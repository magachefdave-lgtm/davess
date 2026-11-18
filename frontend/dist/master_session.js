/* Master sessions use a server-issued cookie and the local launch session. */
(() => {
  'use strict';
  const get = id => document.getElementById(id);
  const panel = get('master-program-info');
  if (!panel) return;
  const modal = 'master-session-dialog';
  let master = false, active = false, status = null, revision = 0, timer = null;

  async function api(operation, body) {
    const token = await window.OnAirRequestAccess?.localSessionToken();
    if (!token) throw Error('이 PC의 OnAir 트레이에서 화면을 다시 열어 주세요.');
    const opts = {method: body ? 'POST' : 'GET', credentials:'same-origin', cache:'no-store',
      referrerPolicy:'no-referrer', headers:{'X-OnAir-Local-Session':token}};
    if (body) {
      opts.headers['Content-Type'] = 'application/json';
      opts.headers['X-OnAir-Data-Contract'] = '1';
      opts.body = JSON.stringify(body);
    }
    const response = await fetch('/api/master/' + operation, opts);
    const data = await response.json();
    if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : '관리 세션을 확인하지 못했습니다.');
    return data;
  }
  function update(data, message='') {
    status = data;
    const wasActive = active;
    active = Boolean(master && data?.active);
    get('master-device-info').textContent = data?.deviceId && !data.enrolled
      ? '등록할 PC 식별번호: ' + data.deviceId : '';
    get('master-session-open').hidden = !data || active || !(data.enrolled || data.grantPending);
    get('master-session-lock').hidden = !active;
    get('master-status').textContent = message || (active ? '관리 세션 사용 중'
      : data?.deviceId && !data.enrolled && !data.grantPending ? '서버 PC에서 이 기기를 승인하면 등록할 수 있습니다.' : '');
    window.clearTimeout(timer);
    if (active) timer = window.setTimeout(refresh, 10000);
    if (wasActive !== active) window.OnAirDinner?.onAuthorityChanged()?.catch(() => {});
  }
  async function refresh() {
    if (!master) return;
    const current = ++revision;
    try {
      const data = await api('session');
      if (current === revision) update(data);
    } catch (err) {
      if (current === revision) update(null, err.message);
    }
  }
  function configure(info, opened=false) {
    const before = master;
    master = info?.distributionRole === 'MASTER';
    panel.hidden = !master;
    if (!master) {
      if (before) { ++revision; update(null); }
      return;
    }
    if (!before || opened) refresh();
  }
  function collapse() {
    panel.open = false;
    get('master-version-detail').open = false;
  }
  get('master-version-detail').addEventListener('toggle', event => {
    if (event.currentTarget.open) refresh();
  });
  get('master-session-open').addEventListener('click', () => {
    if (!master || !status || !(status.enrolled || status.grantPending)) return;
    const enrolling = !status.enrolled;
    const fields = (enrolling ? '<label>일회용 등록 코드<input id="master-code" type="password" autocomplete="off" required></label>' : '') +
      '<label>'+(enrolling ? '새 최고 관리자 암호 (12자 이상)' : '최고 관리자 암호')+
      '<input id="master-password" type="password" maxlength="128" autocomplete="'+(enrolling?'new-password':'current-password')+'" required></label>'+
      (enrolling ? '<label>새 암호 확인<input id="master-confirm" type="password" autocomplete="new-password" required></label>' : '')+
      '<div id="master-error" role="alert"></div><button type="submit" id="master-submit" class="btn-main">'+(enrolling?'등록하고 열기':'관리 세션 열기')+'</button>';
    const dialog = OnAirUI.open(modal, '최고 관리자 인증', '<form class="master-session-form">'+fields+'</form>', {
      width:420, onClose:() => dialog.querySelector('form')?.reset()});
    const form = dialog.querySelector('form');
    form.querySelector('input').focus();
    form.onsubmit = async event => {
      event.preventDefault();
      const submit = get('master-submit'), error = get('master-error'), password = get('master-password').value;
      if (submit.disabled) return;
      if (enrolling && (password.length < 12 || password !== get('master-confirm').value)) {
        error.textContent = '12자 이상의 암호를 두 칸에 같게 입력하세요.'; return;
      }
      submit.disabled = true; error.textContent = '';
      const body = {password};
      if (enrolling) body.enrollmentCode = get('master-code').value.trim();
      try {
        await api('unlock', body);
        if (!dialog.classList.contains('active') || dialog.querySelector('form') !== form) {
          await api('lock', {}); await refresh(); return;
        }
        OnAirUI.close(modal);
        await refresh();
        if (active && typeof showToast === 'function') showToast('최고 관리자 인증이 완료되었습니다.');
      } catch (err) {
        if (dialog.classList.contains('active') && dialog.querySelector('form') === form) {
          error.textContent = err.message;
          const field = form.querySelector('#master-password');
          field.value = ''; field.focus();
        }
      } finally { submit.disabled = false; }
    };
  });
  get('master-session-lock').addEventListener('click', async () => {
    const button = get('master-session-lock');
    button.disabled = true; ++revision;
    try { await api('lock', {}); update(null); await refresh(); }
    catch (err) { await refresh(); get('master-status').textContent = err.message; }
    finally { button.disabled = false; }
  });
  window.addEventListener('focus', () => { if (master) refresh(); });
  document.addEventListener('visibilitychange', () => {
    if (!master) return;
    if (document.hidden && active) {
      ++revision; update(null);
    } else if (!document.hidden) refresh();
  });
  window.OnAirMaster = {configure, collapse, isMaster:() => master};
  if (typeof SYS_INFO !== 'undefined' && SYS_INFO) configure(SYS_INFO);
})();

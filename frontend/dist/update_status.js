/* v3.18.0 [운영] 업데이트 상태 · 이 PC 운영 동작 · 자주 쓰는 설정.
 * [연결 정보] 창 아래에 '업데이트 상태' 칸을 붙이고, 업데이트가 실패했으면 화면 위에 한 번만 안내를 띄운다.
 * 서버: GET/POST /api/system/update-status (이 PC에서만 동작, 암호를 걸었으면 암호 필요).
 */
(function () {
  "use strict";
  var URL = "/api/system/update-status";
  var VERSION_URL = "/api/system/version-status";
  var SEEN_KEY = "onair-update-notice-seen";
  var RESULT_TEXT = {
    updated: "업데이트 완료", up_to_date: "최신 버전", paused: "관리자가 업데이트 일시 정지",
    no_repository: "업데이트 설정 없음", check_failed: "업데이트 확인 실패", blocked: "업데이트 차단됨",
    start_failed: "새 버전 시작 실패", save_failed: "업데이트 보류"
  };

  function escText(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function tokenHeaders(extra) {
    var headers = Object.assign({}, extra || {});
    try { if (typeof ADMIN_TOKEN !== "undefined" && ADMIN_TOKEN) headers["X-Admin-Token"] = ADMIN_TOKEN; } catch (e) { /* no token */ }
    return headers;
  }
  // v3.18.0 실패 문장은 화면 공통 도우미로 만든다(영어 오류·숫자 코드를 그대로 보이지 않는다).
  function friendly(error, res, action, body) {
    if (typeof window.friendlyFetchError === "function") return window.friendlyFetchError(error, res, action, body);
    var d = body && body.detail;
    return typeof d === "string" && /[가-힣]/.test(d) ? d : action + " 중 오류가 났습니다. 잠시 후 다시 시도하고, 계속되면 관리자에게 알려 주세요.";
  }
  function failure(res, body) {
    var e = new Error("요청이 처리되지 않았습니다.");
    e.status = res ? res.status : 0;
    e.detail = body && body.detail;
    return e;
  }
  function seen(stamp, store) {
    try {
      if (store) localStorage.setItem(SEEN_KEY, stamp);
      return localStorage.getItem(SEEN_KEY) === stamp;
    } catch (e) { return false; }
  }
  function when(iso) {
    if (!iso) return "-";
    var d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString("ko-KR");
  }
  async function load(interactive, operation) {
    var endpoint = URL + (operation ? "?operation=" + encodeURIComponent(operation) : "");
    var res = interactive && typeof fetchWithAdminToken === "function"
      ? await fetchWithAdminToken(endpoint, {}, "업데이트 상태를 보려면 관리자 암호를 입력하세요.")
      : await fetch(endpoint, { headers: tokenHeaders() });
    if (!res.ok) {
      var body = {};
      try { body = await res.json(); } catch (e) { /* empty */ }
      if (res.status === 403) { var denied = failure(res, { detail: "업데이트 상태는 프로그램을 실행한 PC에서만 볼 수 있습니다." }); throw denied; }
      throw failure(res, body);
    }
    return res.json();
  }
  async function act(action, extra) {
    var body = JSON.stringify(Object.assign({ action: action }, extra || {}));
    var options = { method: "POST", headers: { "Content-Type": "application/json" }, body: body };
    var res = typeof fetchWithAdminToken === "function"
      ? await fetchWithAdminToken(URL, options, "이 작업을 하려면 관리자 암호를 입력하세요.")
      : await fetch(URL, Object.assign(options, { headers: tokenHeaders(options.headers) }));
    var data = {};
    try { data = await res.json(); } catch (e) { /* empty */ }
    if (!res.ok) throw failure(res, data);
    return action === "check_now" || action === "prepare_restart" ? data : data.message || "처리했습니다.";
  }

  // 헤더 조회는 공개된 버전 정보만 사용한다. 암호 확인과 재시작은 아래 기존 운영 동작에 맡긴다.
  var currentVersion = document.getElementById("header-current-version");
  var latestVersion = document.getElementById("header-latest-version");
  var headerUpdate = document.getElementById("header-update");
  var versionBadge = document.getElementById("header-version-badge");
  var updateLabel = document.getElementById("header-update-label");
  var latestLabel = document.getElementById("header-version-state-label");
  var latestCheck = document.getElementById("header-version-check");
  var headerStatus = null;
  var headerSubmitting = false;
  var headerRequest = null;
  var headerTimer = null;
  var headerStopped = false;
  function renderHeader() {
    if (!currentVersion || !latestVersion || !headerUpdate || !versionBadge || !updateLabel || !latestLabel || !latestCheck) return;
    var d = headerStatus || {};
    var known = d.current_version || (/^v(\d+\.\d+\.\d+)$/.exec(currentVersion.textContent) || [])[1];
    currentVersion.textContent = known ? "v" + known : d.result === "check_failed" ? "확인 불가" : "확인 중…";
    currentVersion.title = known ? "현재 버전 v" + known : "현재 버전을 확인하지 못했습니다.";
    if (d.mode) versionBadge.setAttribute("data-mode", d.mode);
    headerUpdate.hidden = !(d.local && d.update_available && d.latest_version);
    headerUpdate.disabled = headerSubmitting || !d.can_update || !!d.busy || !d.managed || d.result === "blocked";
    updateLabel.textContent = d.latest_version ? "v" + d.latest_version + " 업데이트" : "업데이트";
    headerUpdate.setAttribute("aria-label", (known ? "현재 v" + known + ", " : "") +
      (d.latest_version ? "최신 v" + d.latest_version + "으로 업데이트" : "업데이트"));
    headerUpdate.title = headerSubmitting || d.busy ? "업데이트 작업을 진행하고 있습니다." : d.result === "blocked"
      ? "이 버전의 업데이트가 차단되어 있습니다. 연결 정보에서 상태를 확인해 주세요."
      : !d.can_update ? "이 PC에서 업데이트 실행기로 시작한 프로그램의 상태를 연결 정보에서 확인해 주세요."
      : "새 버전을 확인한 뒤 내려받고 이 PC의 프로그램을 다시 시작합니다.";
    var current = d.result === "up_to_date" && !!d.latest_version;
    var checking = !d.result || d.result === "checking";
    latestLabel.textContent = current ? "최신" : d.latest_version ? "최신 v" + d.latest_version
      : d.result === "no_repository" ? "업데이트 설정 없음" : checking ? "확인 중…" : "최신 확인 불가";
    latestVersion.hidden = !headerUpdate.hidden;
    latestCheck.hidden = !current;
    latestVersion.title = d.latest_version ? "최신 게시 버전 v" + d.latest_version +
      (d.local === false && d.update_available ? " · 업데이트는 프로그램을 실행한 PC에서 진행하세요." : "") : latestLabel.textContent;
    versionBadge.setAttribute("data-state", current ? "current" : checking ? "checking"
      : !d.latest_version && d.result !== "no_repository" ? "failed" : "available");
  }
  function scheduleHeader(delay) {
    if (headerTimer !== null) clearTimeout(headerTimer);
    headerTimer = null;
    if (!headerStopped) headerTimer = setTimeout(refreshHeader, delay);
  }
  function refreshHeader() {
    if (headerStopped || !currentVersion || !latestVersion || !headerUpdate) return Promise.resolve();
    if (headerSubmitting) return Promise.resolve();
    if (headerRequest) return headerRequest;
    headerRequest = (async function () {
      try {
        var res = await fetch(VERSION_URL, { cache: "no-store" });
        if (!res.ok) throw failure(res, {});
        var data = await res.json();
        if (!headerStopped) { headerStatus = data; renderHeader(); }
      } catch (e) {
        if (!headerStopped) {
          headerStatus = { current_version: headerStatus && headerStatus.current_version, result: "check_failed" };
          renderHeader();
        }
      } finally {
        headerRequest = null;
        var finishing = headerStatus && headerStatus.busy && ["available", "up_to_date"].indexOf(headerStatus.result) >= 0;
        scheduleHeader(headerStatus && (headerStatus.result === "checking" || finishing) ? 1500 : 300000);
      }
    })();
    return headerRequest;
  }
  async function applyFromHeader() {
    if (!headerStatus || headerSubmitting || headerUpdate.disabled || headerUpdate.hidden) return;
    headerSubmitting = true;
    renderHeader();
    try {
      if (typeof window.openServerInfo !== "function") return;
      // 이 클릭 때 보호된 상태를 다시 읽어 권한·새 버전 제안을 확인하고 기존 확인창/재연결을 그대로 쓴다.
      await window.openServerInfo();
      var box = document.getElementById("serverinfo-update");
      var apply = box && box.querySelector("#update-apply-restart");
      if (apply && !apply.disabled && typeof apply.onclick === "function") await apply.onclick();
    } finally {
      headerSubmitting = false;
      renderHeader();
      refreshHeader();
    }
  }

  // ---------------------------------------------------------------- 배너(실패 시 한 번)
  function showBanner(status) {
    if (!status || !status.notice || !status.checked_at || seen(status.checked_at)) return;
    if (document.getElementById("update-notice-banner")) return;
    var bar = document.createElement("div");
    bar.id = "update-notice-banner";
    bar.setAttribute("role", "status");
    bar.style.cssText = "position:sticky;top:0;z-index:900;display:flex;gap:10px;align-items:center;" +
      "padding:8px 14px;background:var(--warn-bg,#fff7e0);color:var(--warn-text,#7a4b00);" +
      "border-bottom:1px solid var(--warn-border,#e2c26a);font-size:13px;";
    bar.innerHTML = "<span style='flex:1'>⚠️ <b>" + escText(RESULT_TEXT[status.result] || "업데이트 안내") +
      "</b> — " + escText(status.message) + " 지금 화면은 그대로 쓸 수 있습니다. 자세한 내용은 [연결 정보]에 있습니다.</span>";
    var info = document.createElement("button");
    info.className = "btn-sm";
    info.textContent = "연결 정보";
    info.onclick = function () { if (typeof openServerInfo === "function") openServerInfo(); };
    var close = document.createElement("button");
    close.className = "btn-sm";
    close.textContent = "닫기";
    close.setAttribute("aria-label", "업데이트 안내 닫기");
    close.onclick = function () { seen(status.checked_at, true); bar.remove(); };
    bar.appendChild(info);
    bar.appendChild(close);
    document.body.insertBefore(bar, document.body.firstChild);
  }

  // ---------------------------------------------------------------- [연결 정보] 업데이트 상태
  function row(label, html, warn) {
    return "<tr><th style='width:130px;text-align:left;background:var(--surface-2,#f1f5f9);padding:7px 10px;border:1px solid var(--border);'>" +
      label + "</th><td style='padding:7px 10px;border:1px solid var(--border);" + (warn ? "color:var(--holiday-text);font-weight:800;" : "") +
      "'>" + html + "</td></tr>";
  }
  // 긴 경로는 뒤쪽 두 단계만 보여 준다(전체 경로는 마우스를 올리면 보인다). 예: …\\VIEWER\\launcher.log
  function shortPath(path) {
    var parts = String(path || "").split(/[\\/]+/).filter(Boolean);
    if (parts.length <= 2) return String(path || "");
    var sep = String(path).indexOf("\\") >= 0 ? "\\" : "/";
    return "…" + sep + parts.slice(-2).join(sep);
  }
  function render(box, d) {
    var s = d.status;
    // 3.18.0 QA: 조회용에는 관리자 암호·추가 백업 폴더가 쓰이지 않는다(서버도 저장을 거절한다).
    var viewer = d.mode === "VIEWER";
    var html = "<div style='margin-top:14px;font-weight:800;font-size:13px;'>업데이트 상태</div>" +
      "<table style='width:100%;border-collapse:collapse;font-size:12px;margin-top:6px;'>" +
      row("지금 버전", escText(d.version));
    if (!d.managed) {
      html += row("마지막 확인", "업데이트 실행기 없이 시작한 프로그램입니다(자동 업데이트 안 함).");
    } else if (!s) {
      html += row("마지막 확인", "아직 기록이 없습니다. 프로그램을 다시 시작하면 확인합니다.");
    } else {
      html += row("마지막 확인", escText(when(s.checked_at))) +
        row("결과", "<b>" + escText(RESULT_TEXT[s.result] || s.result) + "</b> — " + escText(s.message), !!s.notice) +
        (s.available_version ? row("받은 새 버전", escText(s.available_version)) : "") +
        (s.log_path ? (viewer
          ? row("로그 파일", "<span id='update-log-short' title='" + escText(s.log_path) + "' style='font-family:monospace;'>" + escText(shortPath(s.log_path)) + "</span>" +
              (d.canOpen && d.managed ? " <button type='button' class='btn-sm' id='update-open-logs' style='margin-left:6px;'>로그 폴더 열기</button>" : ""))
          : row("로그 파일", "<span style='font-family:monospace;word-break:break-all;'>" + escText(s.log_path) + "</span>")) : "");
    }
    html += "</table><div id='update-actions' style='display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;'></div>" +
      "<div id='update-action-result' role='status' aria-live='polite' style='font-size:12px;margin-top:6px;'></div>";
    if (d.settings) {
      var st = d.settings;
      var has = function (key) { return Object.prototype.hasOwnProperty.call(st, key) && !(viewer && (key === "admin_password_set" || key === "backup_mirror_dir")); };
      html += "<details id='update-settings' style='margin-top:10px;font-size:12px;'><summary style='cursor:pointer;font-weight:800;'>자주 바꾸는 설정 (이 PC)</summary>" +
        "<div style='display:grid;grid-template-columns:150px 1fr;gap:6px 8px;align-items:center;margin-top:8px;'>" +
        (has("admin_password_set") ? "<label for='set-pass'>관리자 암호</label><span><input id='set-pass' type='password' autocomplete='new-password' placeholder='" +
        (st.admin_password_set ? "설정됨 — 바꿀 때만 입력" : "설정 안 됨 — 정하려면 입력") + "' style='width:100%'>" +
        (st.admin_password_set ? "<label style='font-weight:400'><input id='set-pass-clear' type='checkbox'> 암호 지우기</label>" : "") + "</span>" : "") +
        (has("server_port") ? "<label for='set-port'>포트</label><input id='set-port' inputmode='numeric' value='" + escText(st.server_port) + "'>" : "") +
        (has("allowed_hosts") ? "<label for='set-hosts'>허용할 접속 이름</label><span><input id='set-hosts' placeholder='' value='" + escText(st.allowed_hosts) + "' style='width:100%'><small style='color:var(--text-muted,#64748b)'>보통 비워 둡니다. 다른 이름(별칭)으로 접속할 때만 그 이름을 쉼표로 구분해 적습니다.</small></span>" : "") +
        (has("backup_mirror_dir") ? "<label for='set-mirror'>추가 백업 폴더</label><span><input id='set-mirror' placeholder='' value='" + escText(st.backup_mirror_dir) + "' style='width:100%'><small style='color:var(--text-muted,#64748b)'>전체 경로를 적습니다(USB·다른 드라이브 권장). 비우면 사용하지 않습니다.</small></span>" : "") +
        "</div><div style='display:flex;gap:6px;margin-top:8px;'><button class='btn-main' id='set-save'>설정 저장</button></div>" +
        "<div style='color:var(--text-muted,#64748b);margin-top:4px;'>저장하면 원래 파일 사본을 남기고, <b>프로그램을 다시 시작해야 적용됩니다</b>.</div></details>";
    }
    box.innerHTML = html;
    var actions = box.querySelector("#update-actions");
    var out = box.querySelector("#update-action-result");
    function button(label, action, extra, confirmText) {
      var b = document.createElement("button");
      b.className = "btn-sm";
      b.textContent = label;
      b.onclick = async function () {
        if (confirmText) {
          var go = typeof window.askConfirm === "function"
            ? await window.askConfirm({ title: label, message: confirmText, ok: label })
            : window.confirm(confirmText);
          if (!go) return;
        }
        out.style.color = "";
        out.textContent = "처리 중…";
        try { out.textContent = await act(action, typeof extra === "function" ? extra() : extra); }
        catch (e) { out.style.color = "var(--holiday-text)"; out.textContent = friendly(e, null, label); }
      };
      actions.appendChild(b);
      return b;
    }
    var inlineLogs = box.querySelector("#update-open-logs");
    if (inlineLogs) {
      inlineLogs.onclick = async function () {
        out.style.color = "";
        out.textContent = "처리 중…";
        try { out.textContent = await act("open_logs"); }
        catch (e) { out.style.color = "var(--holiday-text)"; out.textContent = friendly(e, null, "로그 폴더 열기"); }
      };
    } else if (d.canOpen && d.managed) button("로그 폴더 열기", "open_logs");
    if (d.canOpen) button("설정 파일 열기 (메모장)", "open_config");
    if (d.local && s && (s.result === "blocked" || s.result === "start_failed")) {
      button("차단된 업데이트 다시 허용", "reset_blocked", null,
        "전에 시작하지 못해 막아 둔 새 버전을 다음 실행 때 다시 시도합니다.\n\n문제가 해결되었을 때만 누르세요. 다시 허용할까요?");
    }
    if (d.local && d.managed && d.manual) {
      var manual = d.manual;
      var check = button("최신 버전 확인", "check_now");
      var apply = button("업데이트 후 다시 시작", "prepare_restart");
      apply.id = "update-apply-restart";
      apply.className = "btn-main";
      apply.style.fontSize = "12px";
      var available = document.createElement("div");
      available.style.cssText = "font-size:12px;margin-top:8px;line-height:1.6;overflow-wrap:anywhere";
      available.setAttribute("role", "status");
      available.setAttribute("aria-live", "polite");
      actions.parentNode.insertBefore(available, out);
      var epoch = {};
      box._liveCheckEpoch = epoch;
      var submitting = false;
      function describe(value) {
        manual = value;
        available.textContent = (value.available_version ? "게시 버전: " + value.available_version + " · " : "") +
          (value.message || "") + (value.checked_at ? " · 확인: " + when(value.checked_at) : "") + (value.total_bytes ? " (" + Math.floor(100 * value.received_bytes / value.total_bytes) + "%)" : "");
        check.disabled = submitting || !!value.busy;
        apply.disabled = submitting || !!value.busy || !value.restart_supported || value.result !== "available" || !value.offer_id;
        if (!value.restart_supported) apply.title = "업데이트 실행 파일로 시작한 프로그램에서 사용할 수 있습니다.";
      }
      describe(manual);
      async function follow(operation, oldInstance, restarting) {
        var deadline = Date.now() + (restarting ? 540000 : 18000);
        var lastFailure = false;
        while (Date.now() < deadline) {
          await new Promise(function (resolve) { setTimeout(resolve, 1000); });
          if (!box.isConnected || box._liveCheckEpoch !== epoch) return;
          try {
            var fresh = await load(false, restarting ? operation : null);
            var value = fresh.manual;
            if (!value) throw new Error("업데이트 상태를 기다립니다.");
            if (restarting && value.operation !== operation) continue;
            describe(value);
            if (restarting && (value.result === "updated" || value.result === "rolled_back")) {
              var response = await fetch("/api/system/health", { cache: "no-store", headers: tokenHeaders() });
              if (response.status === 401) throw failure(response, {});
              if (!response.ok) continue;
              var health = await response.json();
              if (health.mode === d.mode && health.version === value.current_version &&
                  health.instance_id === value.new_instance_id && health.instance_id !== oldInstance &&
                  health.port === value.port) {
                out.textContent = value.result === "rolled_back" ? "이전 버전으로 복귀했습니다. 화면을 다시 연결합니다." : "업데이트를 완료했습니다. 화면을 다시 연결합니다.";
                window.location.reload();
                return;
              }
            } else if (["failed", "cancelled", "check_failed"].indexOf(value.result) >= 0 || (!restarting && !value.busy)) {
              out.textContent = value.message || "";
              return;
            }
            lastFailure = false;
          } catch (e) {
            if (restarting && e.status === 401) {
              manual = Object.assign({}, manual, { result: "authentication_required", busy: false, offer_id: null });
              describe(manual);
              out.textContent = "관리자 암호가 변경되었거나 인증이 만료되었습니다. 연결 정보를 다시 열어 인증한 뒤 업데이트 결과를 확인해 주세요.";
              return;
            }
            if (!restarting) { out.textContent = friendly(e, null, "최신 버전 확인"); return; }
            lastFailure = true;
            out.textContent = "프로그램이 다시 시작되기를 기다리고 있습니다…";
          }
        }
        out.textContent = lastFailure ? "재연결을 확인하지 못했습니다. 잠시 후 프로그램을 실행하고 다시 접속해 주세요." : "작업이 아직 끝나지 않았습니다. 연결 정보를 다시 열어 상태를 확인해 주세요.";
      }
      if (manual.busy) follow(manual.operation, manual.instance_id, manual.result !== "checking");
      check.onclick = async function () {
        if (submitting || check.disabled) return;
        submitting = true; describe(manual);
        try {
          var response = await act("check_now");
          await follow(response.operation, manual.instance_id, false);
        } catch (e) { out.textContent = friendly(e, null, "최신 버전 확인"); }
        finally { submitting = false; describe(manual); }
      };
      apply.onclick = async function () {
        if (submitting || apply.disabled) return;
        submitting = true; describe(manual);
        try {
          var message = "작성 중인 내용을 먼저 저장해 주세요. 새 버전을 내려받고 확인한 뒤 이 PC의 프로그램을 종료하고 다시 시작합니다. 계속할까요?";
          var go = typeof window.askConfirm === "function"
            ? await window.askConfirm({ title: "업데이트 후 다시 시작", message: message, ok: "업데이트 후 다시 시작" })
            : window.confirm(message);
          if (!go) return;
          var oldInstance = manual.instance_id;
          var response = await act("prepare_restart", { offer_id: manual.offer_id, instance_id: oldInstance });
          out.textContent = "새 버전을 준비하고 있습니다…";
          await follow(response.operation, oldInstance, true);
        } catch (e) { out.textContent = friendly(e, null, "업데이트 후 다시 시작"); }
        finally { submitting = false; describe(manual); }
      };
    }
    var save = box.querySelector("#set-save");
    if (save) {
      save.onclick = async function () {
        var settings = {};
        var passBox = box.querySelector("#set-pass");
        var pass = passBox ? passBox.value : "";
        var clear = box.querySelector("#set-pass-clear");
        if (clear && clear.checked) settings.admin_password = "";
        else if (pass) settings.admin_password = pass;
        var fields = { server_port: "#set-port", allowed_hosts: "#set-hosts", backup_mirror_dir: "#set-mirror" };
        Object.keys(fields).forEach(function (k) {
          var input = box.querySelector(fields[k]);
          if (!input) return;                       // 이 PC(조회용 등)에서 보여 주지 않는 설정은 보내지 않는다
          var v = input.value.trim();
          if (v !== String(d.settings[k] || "")) settings[k] = v;
        });
        if (!Object.keys(settings).length) { out.textContent = "바뀐 값이 없습니다."; return; }
        out.style.color = "";
        out.textContent = "저장 중…";
        try { out.textContent = await act("save_settings", { settings: settings }); }
        catch (e) { out.style.color = "var(--holiday-text)"; out.textContent = friendly(e, null, "설정 저장"); }
      };
    }
  }
  async function renderInto() {
    var box = document.getElementById("serverinfo-update");
    if (!box) return;
    box.textContent = "업데이트 상태를 불러오는 중…";
    try { render(box, await load(true)); }
    catch (e) { box.textContent = "업데이트 상태: " + friendly(e, null, "업데이트 상태 불러오기"); }
  }

  if (typeof window.openServerInfo === "function") {
    var original = window.openServerInfo;
    window.openServerInfo = async function () {
      var result = await original.apply(this, arguments);
      await renderInto();
      return result;
    };
  }
  window.renderUpdateStatus = renderInto;
  if (currentVersion && latestVersion && headerUpdate) {
    headerUpdate.onclick = applyFromHeader;
    renderHeader();
    refreshHeader();
    if (typeof window.addEventListener === "function") {
      window.addEventListener("focus", refreshHeader);
      window.addEventListener("pagehide", function () {
        headerStopped = true;
        if (headerTimer !== null) clearTimeout(headerTimer);
        headerTimer = null;
      });
      window.addEventListener("pageshow", function () { if (headerStopped) { headerStopped = false; refreshHeader(); } });
    }
    if (typeof document.addEventListener === "function") {
      document.addEventListener("visibilitychange", function () { if (!document.hidden) refreshHeader(); });
    }
  }
  load(false).then(function (d) { showBanner(d.status); }).catch(function () { /* 다른 PC에서 접속한 경우 등: 조용히 넘어간다 */ });
})();

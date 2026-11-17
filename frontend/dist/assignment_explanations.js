/* Presentation of facts collected by the existing fixed-order assignment engine. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels = {'weekday|F':'평일 여','weekend|M':'주말 남','weekend|F':'주말 여','holiday|M':'공휴일 남','holiday|F':'공휴일 여','major|M':'명절 남','major|F':'명절 여','preserved|M':'보완일 남','preserved|F':'보완일 여'};
  function basisText(basis) {
    if (!basis) return '인정된 이전 배정 기록이 없어 첫 순번부터 시작';
    if (basis.kind === 'record') return `${basis.date} ${basis.name} ${basis.substitute ? '순번을 소비한 대체 근무' : '근무'} 기록에서 이어집니다`;
    if (basis.kind === 'retired') return `${basis.date} ${basis.name}의 정규 차례를 반영했습니다 (현재 명단에서 제외됨)`;
    if (basis.kind === 'year-tail') return `${basis.year}년 추가 공휴일 보완에서 소비한 ${basis.consumed}개 순번을 반영했습니다`;
    if (basis.kind === 'year-anchor') return `${basis.year}년 공휴일 보완에서 확정한 시작 기준을 사용합니다`;
    if (basis.kind === 'holiday-credit') return '추가 공휴일이 된 날의 기존 평일 순번 소비를 반영했습니다';
    return '이전 배정의 순번 소비 기록을 반영했습니다';
  }
  function renderStart(row, meta, previousPos, previousName, context) {
    const cell = $('rot-start-' + row.prefix + '-next');
    if (!cell || !meta) return;
    const changed = meta.pos !== (context.initialIndexes[row.key] || 0);
    const reason = changed ? `시작 기준 직접 변경 · ${previousName || '이전 배정 없음'} 다음 차례` : basisText(context.basis[row.key]);
    cell.innerHTML = `<div class="oa-assignment-next"><span class="oa-assignment-person">${escape(meta.name)}</span><span>${meta.paired ? '순번 ' + meta.cycle + ' · ' : ''}${meta.ordinal}번</span></div><p class="oa-assignment-basis">${escape(reason)}</p>`;
  }
  function rowHtml(row) {
    return `<tr><td>${escape(row.date)}</td><td>${escape(labels[row.key] || row.key)}</td><td>${row.name ? `<strong class="oa-assignment-person">${escape(row.name)}</strong>` : '<strong class="oa-assignment-empty">미배정</strong>'}</td><td><span>${escape(row.reason)}</span>${(row.skipped || []).length ? `<ul class="oa-assignment-skipped">${row.skipped.map(item => `<li><b>${escape(item.name)}</b> · ${escape(item.reason)}</li>`).join('')}</ul>` : ''}</td></tr>`;
  }
  function table(rows) {
    return '<table class="oa-assignment-table"><thead><tr><th>배정일</th><th>구분</th><th>실제 배정 대상</th><th>선정 근거 · 건너뛴 이유</th></tr></thead><tbody>' + rows.map(rowHtml).join('') + '</tbody></table>';
  }
  function explanationHtml(rows) {
    if (!Array.isArray(rows) || !rows.length) return '';
    const first = new Map();
    rows.forEach(row => { if (!first.has(row.key)) first.set(row.key, row); });
    const exceptions = rows.filter(row => !row.name || row.fallback || row.skipped?.length);
    return '<div class="oa-assignment-heading"><h3>이번 자동 배정의 대상과 근거</h3><span>계산 결과 · 아직 저장 전</span></div>' +
      '<p class="oa-assignment-caption">구분별 첫 배정 대상입니다. 이름은 실제 계산 결과이며, 건너뛴 사람과 이유를 함께 표시합니다.</p>' +
      table([...first.values()]) +
      (exceptions.length ? `<details class="oa-assignment-details"><summary>건너뜀·순번 밖·미배정 ${exceptions.length}건 확인</summary>${table(exceptions)}</details>` : '') +
      `<details class="oa-assignment-details"><summary>전체 배정 근거 ${rows.length}건 보기</summary>${table(rows)}</details>`;
  }
  const previousPreview = window.openAssignPreview;
  if (typeof previousPreview === 'function') window.openAssignPreview = function(before, scope, label, info) {
    const result = previousPreview.apply(this, arguments);
    const modal = $('assign-preview-modal');
    if (modal) {
      let box = $('oa-assignment-explanations');
      if (!box) {
        box = document.createElement('section'); box.id = 'oa-assignment-explanations';
        const groups = $('oa-preview-groups') || modal.querySelector('.table-scroll');
        if (groups) groups.parentNode.insertBefore(box, groups);
      }
      box.innerHTML = explanationHtml(info?.assignmentReasons);
      box.hidden = !box.innerHTML;
    }
    return result;
  };
  window.OnAirAssignment = {renderStart, basisText, explanationHtml};
})();

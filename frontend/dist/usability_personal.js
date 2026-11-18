/* OnAir personal schedules: classic script, shared data remain the existing source of truth. */
(() => {
  'use strict';
  const UI = () => window.OnAirUI;
  const escape = value => UI().escape(value);
  const day = key => UI().dateLabel(key);
  const ids = {agenda:'oa-personal-agenda',absence:'oa-personal-absence',education:'oa-personal-education',picker:'oa-personal-picker',search:'oa-personal-search',vacation:'oa-personal-vacation',swap:'oa-personal-swap'};
  const get = id => document.getElementById(id);
  // Storage is named, not passed: with blocked site data even reading window.localStorage throws.
  const storageGet = (kind,key) => { try {return window[kind].getItem(key)||'';} catch {return '';} };
  const storageSet = (kind,key,value) => { try {value ? window[kind].setItem(key,value) : window[kind].removeItem(key);} catch {} };
  const utc = key => {const [y,m,d]=String(key).split('-').map(Number);return Date.UTC(y,m-1,d);};
  const validDate = value => {const key=normalizeDate(value);if(!/^\d{4}-\d{2}-\d{2}$/.test(key))return '';const t=new Date(utc(key));return t.toISOString().slice(0,10)===key?key:'';};
  const addDay = key => new Date(utc(key)+86400000).toISOString().slice(0,10);
  const diffDays = (a,b) => Math.round((utc(b)-utc(a))/86400000);
  const state = (start,end) => end < todayStr ? 'past' : start > todayStr ? 'upcoming' : 'ongoing';
  const labels = {past:'지난 일정',upcoming:'예정',ongoing:'진행 중'};
  const button = (action,text,attrs='',cls='') => `<button type="button" class="${cls}" data-oa-action="${action}" ${attrs}>${escape(text)}</button>`;
  const attrs = values => Object.entries(values).map(([key,value])=>`data-${key}="${escape(value)}"`).join(' ');
  const footer = id => `<div class="op-footer">${button('close','닫기',attrs({modal:id}),'btn-main')}</div>`;
  const wrap = body => `<div class="oa-personal">${body}</div>`;
  const intervalText = intervals => `이전 ${intervals.previous===null?'없음':intervals.previous+'일'} · 다음 ${intervals.next===null?'없음':intervals.next+'일'}`;
  // Swap requests are accepted only by a server-connected viewer; the admin app edits schedules directly.
  const REQUEST_ONLY_MESSAGE='조회용 WLR OnAir가 켜져 있고 연결되어 있을 때만 신청할 수 있습니다.';
  const canRequestChange = () => typeof SERVER_CONNECTED!=='undefined'&&!!SERVER_CONNECTED&&typeof SYSTEM_MODE!=='undefined'&&SYSTEM_MODE==='VIEWER';
  const ICS_HINT='Outlook에서 파일을 열면 일정이 추가됩니다. 바뀐 일정은 다시 내려받아 가져오세요.';
  // Pending requests are owned by the requests sidecar; personal views only read them.
  const pendingRequests=(name,date)=>{try{return window.OnAirRequests?.pendingFor?.(name,date)||[];}catch{return [];}};
  let pickerName = '';
  let swapSelection = null;
  let installed = false;
  let legacyPending = '';
  let restoreSelectionPending = false;
  function migrateLegacyName(){if(!legacyPending||!memberList.length)return;if(memberList.some(m=>m.name===legacyPending))storageSet('localStorage','sd_my_name_remember','1');else{if(myName===legacyPending)myName='';storageSet('localStorage','sd_my_name','');}legacyPending='';}
  function restorePersonalSelection() {
    if(!restoreSelectionPending||!dataLoaded)return;
    restoreSelectionPending=false;
    // Wait for the roster, and restore once without replacing a search already entered.
    if(myName&&memberList.some(m=>m.name===myName)&&!selectedQuickMember&&!get('member-search-input')?.value.trim())selectMemberDuty(myName);
  }
  const originalFullDuty = typeof openMyDutyModal==='function' ? openMyDutyModal : null;

  function searchNames(query) {
    const term=String(query||'').trim();
    const exact=memberList.filter(m=>m.name===term);
    return (exact.length?exact:memberList.filter(m=>!term||String(m.name).includes(term))).slice().sort((a,b)=>a.name.localeCompare(b.name,'ko'));
  }
  function absenceSections(name) {
    const result={ongoing:[],upcoming:[],past:[]};
    absences.filter(a=>a.name===name).forEach(a=>{const start=validDate(a.start),end=validDate(a.end||a.start);if(start&&end&&end>=start)result[state(start,end)].push(a);});
    Object.entries(result).forEach(([kind,rows])=>rows.sort((a,b)=>kind==='past'?normalizeDate(b.end||b.start).localeCompare(normalizeDate(a.end||a.start)):normalizeDate(a.start).localeCompare(normalizeDate(b.start))));
    return result;
  }
  function educationFields(education) {
    const fields=[['title','교육명'],['location','장소'],['provider','교육기관'],['preparation','준비물'],['content','교육 내용'],['notes','안내사항']];
    return fields.flatMap(([key,label])=>{const value=education&&typeof education[key]==='string'?education[key].trim():'';return value?[{key,label,value}]:[];});
  }
  function agendaRows(name,filter='all') {
    const rows=[];
    if(filter!=='absence')Object.keys(scheduleMap).forEach(key=>{
      if(!validDate(key)||noDutyMap[key])return;
      const workers=scheduleMap[key]||[],me=workers.find(w=>w.name===name);if(!me)return;
      const kind=typeof dutyDayKind==='function'?dutyDayKind(key):'weekday';
      rows.push({kind:'duty',type:'당직',title:`${({weekday:'평일',weekend:'주말',holiday:'공휴일',major:'명절'})[kind]||''} 당직`,start:key,end:key,state:state(key,key),mates:workers.filter(w=>w.name!==name).map(w=>w.name),me,memo:commentMap[key]||''});
    });
    if(filter!=='duty')Object.values(absenceSections(name)).flat().forEach(record=>{const start=validDate(record.start),end=validDate(record.end||record.start);rows.push({kind:'absence',type:record.type||'부재',title:educationFields(record.education).find(f=>f.key==='title')?.value||record.type||'부재',start,end,state:state(start,end),record});});
    const order={ongoing:0,upcoming:1,past:2};
    return rows.sort((a,b)=>order[a.state]-order[b.state]||(a.state==='past'?b.start.localeCompare(a.start):a.start.localeCompare(b.start))||a.kind.localeCompare(b.kind));
  }
  function period(start,end) {return `${escape(day(start))}${end!==start?' ~ '+escape(day(end)):''} · ${diffDays(start,end)+1}일`;}
  function timing(row) {
    if(row.state==='past')return '<div class="op-sub">지난 일정</div>';
    return row.kind!=='duty'&&row.state==='ongoing'?`<div class="op-sub">${escape(day(row.end))}까지 · 진행 중</div>`:'';
  }
  function countdown(row) {
    const days=diffDays(todayStr,row.start);
    if(days<0||row.state==='past')return '';
    const label=days===0?'D-DAY':`D-${String(days).padStart(2,'0')}`;
    const description=days===0?(row.kind==='duty'?'오늘 당직':'오늘 시작'):`${days}일 뒤 ${row.kind==='duty'?'당직':'시작'}`;
    return `<span class="op-dday${days===0?' today':''}" aria-label="${escape(description)}">${label}</span>`;
  }
  function absenceCard(a) {
    const start=validDate(a.start),end=validDate(a.end||a.start),metadata=educationFields(a.education);
    const row={start,end,state:state(start,end),kind:'absence'};
    const action=metadata.length&&a.id!=null&&String(a.id)!==''?button('education','상세 보기',attrs({'absence-id':a.id,name:a.name,date:start})):'';
    return `<div class="op-row"><span class="op-pill">${escape(a.type||'부재')}</span><div class="op-grow"><div class="op-period">${period(start,end)}</div>${timing(row)}${metadata.find(f=>f.key==='title')?`<div class="op-main">${escape(metadata.find(f=>f.key==='title').value)}</div>`:''}</div>${countdown(row)}${action?`<div class="op-actions">${action}</div>`:''}</div>`;
  }
  function todayVacationReturnFocus(modalId) {
    const trigger=document.activeElement?.closest?.('#today-vacation-container [data-oa-action]');
    if(!trigger)return undefined;
    const action=trigger.dataset.oaAction,name=trigger.dataset.name;
    return () => queueMicrotask(() => {
      if((modalId&&get(modalId)?.classList.contains('active'))||document.querySelector('.modal.active')||trigger.isConnected)return;
      const list=get('today-vacation-container'),buttons=[...(list?.querySelectorAll('[data-oa-action]')||[])].filter(node=>node.getClientRects().length);
      const same=buttons.find(node=>node.dataset.oaAction===action&&node.dataset.name===name);
      (same||buttons.find(node=>node.dataset.oaAction==='all-vacation')||buttons[0]||get('week-vacation-title'))?.focus({preventScroll:true});
    });
  }
  function openAbsence(name,dateKey=todayStr) {
    const restoreTodayFocus=todayVacationReturnFocus(ids.absence);
    const weeklyTrigger=document.activeElement?.closest?.('.abs-row[data-oa-person]');
    const weeklyReturn=weeklyTrigger ? {name:weeklyTrigger.dataset.oaPerson,date:weeklyTrigger.dataset.dateKey,container:weeklyTrigger.closest('.abs-list')?.id} : null;
    const restoreWeeklyFocus=weeklyReturn ? () => queueMicrotask(() => {
      if(get(ids.absence)?.classList.contains('active'))return;
      const list=get(weeklyReturn.container);
      const row=[...(list?.querySelectorAll('.abs-row')||[])].find(node=>node.dataset.oaPerson===weeklyReturn.name&&node.dataset.dateKey===weeklyReturn.date&&node.getClientRects().length);
      const kind=weeklyReturn.container==='week-dispatch-container'?'dispatch':'vacation';
      (row||list?.querySelector('.abs-more')||get(`week-${kind}-title`))?.focus({preventScroll:true});
    }) : undefined;
    const sections=absenceSections(name);
    const active=sections.ongoing.length+sections.upcoming.length;
    const body=`<div class="op-sub">${escape(day(validDate(dateKey)||todayStr))} 선택 · ${escape(day(todayStr))} 기준</div>`+
      (sections.ongoing.length?'<div class="op-section">진행 중</div>'+sections.ongoing.map(absenceCard).join(''):'')+
      (sections.upcoming.length?'<div class="op-section">예정된 일정</div>'+sections.upcoming.map(absenceCard).join(''):'')+
      (!active?'<p class="op-sub">진행 중이거나 예정된 일정이 없습니다.</p>':'')+
      (sections.past.length?`<details><summary>지난 일정 ${sections.past.length}건 보기</summary>${sections.past.map(absenceCard).join('')}</details>`:'')+
      (!active&&!sections.past.length?'<p class="op-sub">등록된 일정이 없습니다.</p>':'')+footer(ids.absence);
    UI().open(ids.absence,`${name} · 휴가·교육·부재 일정`,wrap(body),{width:620,onClose:restoreWeeklyFocus||restoreTodayFocus});
  }
  function openEducation(record) {
    if(!record)return;
    const start=validDate(record.start),end=validDate(record.end||record.start),fields=educationFields(record.education);
    const title=fields.find(f=>f.key==='title')?.value;
    UI().open(ids.education,`${record.name} · ${record.type||'교육'} 일정`,wrap(`<div class="op-toolbar"><span class="op-pill">${escape(record.type||'교육')}</span><span class="op-pill live">${escape(labels[state(start,end)])}</span></div>${title?`<div class="op-main">${escape(title)}</div>`:''}<div class="op-period">${period(start,end)}</div><dl class="op-details">${fields.filter(f=>f.key!=='title').map(f=>`<dt>${escape(f.label)}</dt><dd>${escape(f.value)}</dd>`).join('')}</dl>${!fields.length?'<p class="op-sub">등록된 교육 안내가 없습니다.</p>':''}${footer(ids.education)}`),{width:600});
  }
  function agendaCard(row,name) {
    const rowAttrs=attrs({name,date:row.start});
    const action=row.kind==='duty'?button('date','날짜 보기',rowAttrs)+(name===myName&&canRequestChange()&&row.start>=todayStr&&!closedMonths.includes(row.start.slice(0,7))?button('swap','맞교환 후보',rowAttrs):''):button('absence','상세 보기',rowAttrs);
    const description=row.kind==='duty'?`${row.mates.length?'함께 근무 '+row.mates.join(', '):''}${row.me.isSubstitute?' · 대체':''}`:'';
    const pending=row.kind==='duty'&&row.state!=='past'?pendingRequests(name,row.start):[];
    const pendingBadge=pending.length?`<span class="op-pill pending" title="${escape(pending.map(p=>p.text).join('\n'))}">신청 대기</span>`:'';
    return `<div class="op-row"><div class="op-date">${escape(day(row.start))}${row.end!==row.start?`<small>~ ${escape(day(row.end))}</small>`:''}</div><span class="op-pill ${row.kind==='duty'?'duty':''}">${escape(row.type)}</span><div class="op-grow"><div class="op-main">${escape(row.title)}${pendingBadge}</div>${description?`<div class="op-sub">${escape(description)}</div>`:''}${timing(row)}${row.memo?`<div class="op-sub">${escape(row.memo)}</div>`:''}</div>${countdown(row)}<div class="op-actions">${action}</div></div>`;
  }
  function openAgenda(name=myName,filter='all') {
    if(!name)return openPicker();
    const rows=agendaRows(name,filter),current=rows.filter(r=>r.state!=='past'),past=rows.filter(r=>r.state==='past');
    const tabs=[['all','전체'],['duty','당직'],['absence','휴가·교육']].map(([key,label])=>button('agenda-filter',label,attrs({name,filter:key}),`op-tab${filter===key?' active':''}`)).join('');
    UI().open(ids.agenda,`${name} · ${name===myName?'내 일정':'개인 일정'}`,wrap(`<div class="op-sub">당직·휴가·교육을 함께 확인합니다.</div><div class="op-toolbar">${tabs}<span class="op-total">진행·예정 ${current.length}건</span></div>${current.map(row=>agendaCard(row,name)).join('')}${!current.length?'<p class="op-sub">진행 중이거나 예정된 일정이 없습니다.</p>':''}${past.length?`<details><summary>지난 일정 ${past.length}건 보기</summary>${past.map(row=>agendaCard(row,name)).join('')}</details>`:''}${!rows.length?'<p class="op-sub">등록된 일정이 없습니다.</p>':''}<p id="oa-ics-hint" class="op-sub op-ics-hint" hidden></p><div class="op-footer"><label class="op-check" title="끄면 오늘과 앞으로의 일정만 담습니다"><input type="checkbox" id="oa-ics-past"> 지난 일정 포함</label>${button('ics','ICS 내려받기',attrs({name}))}${name===myName&&canRequestChange()?button('my-requests','내 신청 내역',attrs({name})):''}${name===myName?button('full-duty','전체 당직표',attrs({name})):''}${button('close','닫기',attrs({modal:ids.agenda}),'btn-main')}</div>`),{width:760});
  }
  function openFullDuty() {UI().close(ids.agenda);if(originalFullDuty)originalFullDuty();}
  function openPicker() {
    pickerName=memberList.some(m=>m.name===myName)?myName:'';
    UI().open(ids.picker,'내 이름 선택',wrap(`<div class="op-sub">이름을 검색하고 선택하세요.</div><input id="oa-personal-name-search" class="op-search" type="search" autocomplete="off" placeholder="이름 검색" aria-label="내 이름 검색"><div id="oa-personal-name-count" class="op-sub"></div><div id="oa-personal-name-list" class="op-name-list"></div><div class="op-note"><label><input type="checkbox" id="oa-personal-name-remember" ${storageGet('localStorage','sd_my_name_remember')==='1'?'checked':''}> 이 PC에 내 이름 기억</label><div class="op-sub">공용 PC에서는 선택하지 마세요. 기본은 이번 실행에만 적용됩니다.</div></div><div class="op-footer">${myName?button('clear-name','내 이름 해제'):''}${button('close','취소',attrs({modal:ids.picker}))}${button('apply-name','선택한 이름 사용','','btn-main')}</div>`),{width:480});
    updatePickerList('');get('oa-personal-name-search')?.focus();
  }
  function updatePickerList(query) {
    const rows=searchNames(query),count=get('oa-personal-name-count'),list=get('oa-personal-name-list');
    if(count)count.textContent=`검색 결과 ${rows.length}명`;
    if(list)list.innerHTML=rows.length?rows.map(m=>button('pick-name',m.name,attrs({name:m.name}),`op-name${m.name===pickerName?' selected':''}`)).join(''):'<p class="op-sub">일치하는 이름이 없습니다.</p>';
    const apply=document.querySelector('#'+ids.picker+' [data-oa-action="apply-name"]');if(apply)apply.disabled=!pickerName;
  }
  function applyPicker() {if(!memberList.some(m=>m.name===pickerName))return;setMyName(pickerName,{remember:!!get('oa-personal-name-remember')?.checked});UI().close(ids.picker);showToast(`${pickerName} 님으로 설정했습니다.`);}
  function vacationLayout(width,height,count) {
    const cols=Math.max(1,Math.floor(Math.max(width,1)/52)),rows=Math.max(1,Math.floor((Math.max(height,1)+3)/25)),slots=cols*rows;
    const visible=count>slots?Math.max(0,slots-1):count;
    return {cols,rows:Math.max(1,Math.min(rows,Math.ceil(Math.min(count,slots)/cols)||1)),slots,visible,hidden:count-visible,font:12};
  }
  // Try a single column first. Add columns when that would make the rows
  // too short. A long name can occupy consecutive slots in the same row.
  function todayNameLayout(list, people) {
    const width = list.clientWidth - 2;
    const height = list.clientHeight;
    if (width <= 0 || height <= 0 || (people.length > 2 && people.length > 3 * Math.ceil(height / 12))) return null;
    const context = document.createElement('canvas').getContext?.('2d');
    const family = context ? getComputedStyle(list).fontFamily : '';
    if (people.length === 2) {
      const tile = Math.min(108, width);
      let font = 16;
      while (context && font > 12) {
        context.font = '700 ' + font + 'px ' + family;
        if (people.every(({name}) => context.measureText(name).width <= tile - 2)) break;
        font--;
      }
      const rowGap = Math.min(3, height / 3);
      return {lines:[[tile], [tile]], font, rowHeight:Math.min(36, (height - rowGap) / 2),
        rowGap, columnGap:0, tracking:-0.25};
    }
    if (!context) return null;
    let best = null;
    for (const font of [15, 14, 13, 12]) {
      const tracking = font === 12 ? -0.55 : -0.25;
      const columnGap = font === 12 ? 1.5 : font === 13 ? 2.5 : 4;
      const rowGap = font === 12 ? 1 : font === 13 ? 2 : 4;
      context.font = '700 ' + font + 'px ' + family;
      const widths = people.map(({name}) =>
        context.measureText(name).width + Array.from(name).length * tracking + 2);
      for (let cols = 1; cols <= Math.min(3, people.length); cols++) {
        const cellWidth = (width - (cols - 1) * columnGap) / cols;
        const lines = [[]];
        let used = 0, wideNames = 0;
        for (const labelWidth of widths) {
          const span = Math.max(1, Math.ceil((labelWidth + columnGap) / (cellWidth + columnGap)));
          if (span > cols) break;
          if (used + span > cols) {
            lines.push([]);
            used = 0;
          }
          lines[lines.length - 1].push(span * cellWidth + (span - 1) * columnGap);
          used += span;
          if (span > 1) wideNames++;
        }
        if (lines.reduce((n, line) => n + line.length, 0) !== people.length) continue;
        const rowHeight = Math.min(cols === 1 ? 36 : 33,
          (height - (lines.length - 1) * rowGap) / lines.length);
        if (rowHeight < font * 1.06) continue;
        const comfort = rowHeight >= 22 && font >= 13 ? 3 : rowHeight >= 17 ? 2 : 1;
        if (!best || comfort > best.comfort || (comfort === best.comfort &&
            (cols < best.cols || (cols === best.cols && wideNames < best.wideNames)))) {
          best = {lines, font, rowHeight, rowGap, columnGap, tracking, cols, wideNames, comfort};
        }
      }
    }
    return best;
  }

  function todayAbsenceButton(row, fittedName, width) {
    const kind = row.kind === 'short' ? 'short' : 'vacation';
    const label = row.name + ' · ' + (kind === 'short' ? '부재' : '휴가') + ' · ' + row.types.join(' · ');
    const size = width == null ? '' : ' style="width:' + width + 'px"';
    const name = fittedName ? '<span class="today-vacation-name">' + escape(row.name) + '</span>' : escape(row.name);
    return '<button type="button" class="today-vacation-person kind-' + kind + '" data-oa-action="absence" ' +
      attrs({name:row.name, date:todayStr}) + ' title="' + escape(label) + '" aria-label="' +
      escape(label + ' · 개인 일정') + '"' + size + '>' + name + '</button>';
  }

  function fitTodayVacations() {
    const list = get('today-vacation-container');
    if (!list) return;
    const restoreFocus = todayVacationReturnFocus();
    const people = todayVacationRows();
    const count = get('today-vacation-count');
    if (count) count.textContent = people.length + '명';
    list.dataset.single = String(people.length === 1);
    list.dataset.compact = 'true';
    list.parentElement?.classList.toggle('has-multiple-absences', people.length >= 2);
    const layout = people.length >= 2 ? todayNameLayout(list, people) : null;
    list.dataset.layout = layout ? (people.length === 2 ? 'pair' : 'stack') : 'grid';
    if (layout) {
      list.style.setProperty('--vac-label-size', layout.font + 'px');
      list.style.setProperty('--vac-name-spacing', layout.tracking + 'px');
      list.style.setProperty('--vac-row-height', layout.rowHeight + 'px');
      list.style.setProperty('--vac-row-gap', layout.rowGap + 'px');
      list.style.setProperty('--vac-col-gap', layout.columnGap + 'px');
      let index = 0;
      list.innerHTML = layout.lines.map(line => '<div class="today-absence-row">' + line.map(width =>
        todayAbsenceButton(people[index++], people.length === 2, width)
      ).join('') + '</div>').join('');
    } else {
      // Keep the existing list dialog reachable when the available area cannot fit every full name.
      const grid = vacationLayout(list.clientWidth, list.clientHeight, people.length);
      list.style.setProperty('--vac-cols', String(grid.cols));
      list.style.setProperty('--vac-rows', String(grid.rows));
      list.style.setProperty('--vac-gap', '3px');
      list.innerHTML = people.length ? people.slice(0, grid.visible)
        .map(p => todayAbsenceButton(p, people.length === 1)).join('') +
        (grid.hidden ? button('all-vacation','외 ' + grid.hidden + '명','','today-vacation-person op-more') : '')
        : '<div class="today-vacation-empty">금일 휴가·부재자가 없습니다.</div>';
    }
    restoreFocus?.();
  }
  function openAllVacations() {
    const restoreTodayFocus=todayVacationReturnFocus(ids.vacation);
    const rows=todayVacationRows();
    UI().open(ids.vacation,`금일 휴가·부재 · ${rows.length}명`,wrap(`<div class="op-sub">${escape(day(todayStr))} · 이름을 누르면 개인 일정을 봅니다.</div>${rows.map(row=>`<div class="op-row"><div class="op-grow"><div class="op-main">${escape(row.name)}</div><div class="op-sub">${row.kind==='short'?'부재':'휴가'} · ${escape(row.types.join(' · '))}</div></div>${button('absence','일정 보기',attrs({name:row.name,date:todayStr}))}</div>`).join('')}${footer(ids.vacation)}`),{width:540,onClose:restoreTodayFocus});
  }
  function swapEligibility(name,source,partner,target) {
    const me=memberList.find(m=>m.name===name),other=memberList.find(m=>m.name===partner);
    if(!validDate(source)||!validDate(target)||source===target||source<todayStr||target<todayStr||!me||!other||name===partner||me.gender!==other.gender)return false;
    if(noDutyMap[source]||noDutyMap[target]||closedMonths.includes(source.slice(0,7))||closedMonths.includes(target.slice(0,7)))return false;
    if(majorDutyBlockStart(source)||majorDutyBlockStart(target)||isMajorMainDay(source)||isMajorMainDay(target))return false;
    if(isOffDayKey(source)!==isOffDayKey(target)||isAbsent(name,target)||isAbsent(partner,source))return false;
    const a=(scheduleMap[source]||[]).map(w=>w.name),b=(scheduleMap[target]||[]).map(w=>w.name);
    return a.includes(name)&&b.includes(partner)&&!a.includes(partner)&&!b.includes(name);
  }
  function swappedOverrides(name,source,partner,target) {return {[source]:(scheduleMap[source]||[]).map(w=>w.name===name?partner:w.name),[target]:(scheduleMap[target]||[]).map(w=>w.name===partner?name:w.name)};}
  function postSwapIntervals(name,source,partner,target) {
    const overrides=swappedOverrides(name,source,partner,target);
    const interval=(person,received)=>{const keys=Object.keys(scheduleMap).filter(k=>validDate(k)&&!noDutyMap[k]&&(overrides[k]||(scheduleMap[k]||[]).map(w=>w.name)).includes(person)).sort();const previous=keys.filter(k=>k<received).at(-1),next=keys.find(k=>k>received);return {previous:previous?diffDays(previous,received):null,next:next?diffDays(received,next):null};};
    return {mine:interval(name,target),partner:interval(partner,source)};
  }
  function swapCandidates(name,source,windowDays=30) {
    const span=[30,60].includes(Number(windowDays))?Number(windowDays):60;
    if(!validDate(source))return [];
    const out=[];
    Object.keys(scheduleMap).sort().filter(k=>validDate(k)&&k>=todayStr&&Math.abs(diffDays(source,k))<=span).forEach(date=>(scheduleMap[date]||[]).forEach(worker=>{
      if(!swapEligibility(name,source,worker.name,date))return;
      const overrides=swappedOverrides(name,source,worker.name,date),risks=[{name,dateKey:date},{name:worker.name,dateKey:source}].flatMap(check=>{const run=consecutiveRun(check.name,check.dateKey,overrides);return run&&run.len>=7?[{name:check.name,...run}]:[];});
      out.push({name:worker.name,date,intervals:postSwapIntervals(name,source,worker.name,date),risks});
    }));
    return out;
  }
  function openSwap(name,source,windowDays=30) {
    const candidates=swapCandidates(name,source,windowDays);
    // Always hold the freshly computed candidate object: a {name,date} stub has no risks/intervals.
    swapSelection=candidates.find(c=>c.name===swapSelection?.name&&c.date===swapSelection?.date)||candidates[0]||null;
    const selected=c=>swapSelection?.name===c.name&&swapSelection?.date===c.date;
    const table=candidates.length?`<div class="op-table-scroll"><table class="op-table"><thead><tr><th>상대</th><th>바꿀 날짜</th><th>내 근무 간격</th><th>상대 근무 간격</th><th>선택</th></tr></thead><tbody>${candidates.map(c=>`<tr class="${selected(c)?'selected':''}"><td><b>${escape(c.name)}</b>${c.risks?.length?'<div class="op-risk">7일 이상 연속 근무 확인 필요</div>':''}</td><td>${escape(day(c.date))}</td><td>${escape(intervalText(c.intervals.mine))}</td><td>${escape(intervalText(c.intervals.partner))}</td><td>${button('choose-swap',selected(c)?'선택됨':'선택',attrs({name,source,partner:c.name,target:c.date,window:windowDays}))}</td></tr>`).join('')}</tbody></table></div>`:'<p class="op-sub">현재 일정과 조건에 맞는 후보가 없습니다.</p>';
    UI().open(ids.swap,'맞교환 후보 비교',wrap(`<div class="op-note"><b>${escape(name)}</b> · 내 당직 <b>${escape(day(source))}</b></div><div class="op-toolbar">${[30,60].map(span=>button('swap-window',span+'일',attrs({name,date:source,window:span}),`op-tab${span===Number(windowDays)?' active':''}`)).join('')}<span class="op-total">비교 가능한 후보 ${candidates.length}건</span></div><div class="op-sub">근무 간격은 양쪽 당직을 교환한 후의 이전·다음 당직 날짜 간격입니다.</div>${table}${swapSelection?`<div class="op-section">선택한 교환</div><div class="op-note"><b>${escape(name)} ${escape(day(source))}</b> ↔ <b>${escape(swapSelection.name)} ${escape(day(swapSelection.date))}</b>${(swapSelection.risks||[]).map(r=>`<div class="op-risk">${escape(r.name)} · ${escape(day(r.start))} ~ ${escape(day(r.end))} · ${r.len}일 연속 근무</div>`).join('')}</div>`:''}<p class="op-sub">상대방과 협의한 뒤 신청해 주세요. 신청·승인 시 최신 일정으로 다시 확인합니다.</p><div class="op-footer">${button('close','취소',attrs({modal:ids.swap}))}${swapSelection?button('prefill-swap','이 후보로 신청 작성',attrs({name,source,partner:swapSelection.name,target:swapSelection.date}),'btn-main'):''}</div>`),{width:820});
  }
  function selectSwapCandidate(name,source,partner,target) {
    if(!canRequestChange()){showToast(REQUEST_ONLY_MESSAGE,true);return false;}
    if(!swapEligibility(name,source,partner,target)){showToast('일정이 변경되어 이 후보를 선택할 수 없습니다.',true);return false;}
    UI().close(ids.swap);UI().close(ids.agenda);
    const seed={requester_name:name,target_date:source,request_type:'SWAP',swap_date:target,swap_worker:partner};
    if(window.OnAirRequests?.openChange){window.OnAirRequests.openChange(seed);return true;}
    goToDate(source);const radio=document.querySelector('input[name="change_type"][value="SWAP"]');if(radio)radio.checked=true;toggleChangeTypeUI('SWAP');
    get('swap-my-worker').value=name;get('swap-target-year').value=String(Number(target.slice(0,4)));get('swap-target-month').value=String(Number(target.slice(5,7)));updateSwapDateDropdown();get('swap-target-date').value=target;updateSwapWorkerDropdown();get('swap-target-worker').value=partner;
    if(get('swap-target-date').value!==target||get('swap-target-worker').value!==partner){showToast('현재 신청서에서 선택할 수 없는 후보입니다. 일정을 다시 확인하세요.',true);return false;}return true;
  }
  const icsEscape = value => String(value??'').replace(/\\/g,'\\\\').replace(/\r\n|\r|\n/g,'\\n').replace(/;/g,'\\;').replace(/,/g,'\\,').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g,'');
  function stableUID(text) {let hash=2166136261;for(const char of text){hash^=char.codePointAt(0);hash=Math.imul(hash,16777619);}return `onair-${(hash>>>0).toString(16)}@personal.local`;}
  function foldLine(line) {let part='',size=0;const lines=[];for(const char of line){const length=new TextEncoder().encode(char).length;if(size+length>75){lines.push(part);part=' ';size=1;}part+=char;size+=length;}lines.push(part);return lines.join('\r\n');}
  function buildICS(name=myName,options={}) {
    const includePast=!!options.includePast;
    const lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//OnAir//Personal Schedule//KO','CALSCALE:GREGORIAN','METHOD:PUBLISH'];
    const stamp=new Date().toISOString().replace(/[-:]/g,'').replace(/\.\d{3}/,'');
    const seen=new Set();
    agendaRows(name).filter(row=>includePast||row.end>=todayStr).forEach(row=>{
      const identity=row.kind==='duty'?`duty|${name}|${row.start}`:`absence|${name}|${row.record.id!=null?'id:'+row.record.id:[row.type,row.start,row.end].join('|')}`;
      const uid=stableUID(identity);if(seen.has(uid))return;seen.add(uid);
      const fields=row.kind==='absence'?educationFields(row.record.education):[];
      const description=row.kind==='duty'?[row.mates.length?'함께 근무: '+row.mates.join(', '):'',row.me.isSubstitute?'대체 근무':'',row.memo].filter(Boolean).join('\n'):fields.map(f=>f.label+': '+f.value).join('\n');
      lines.push('BEGIN:VEVENT','UID:'+uid,'DTSTAMP:'+stamp,'DTSTART;VALUE=DATE:'+row.start.replace(/-/g,''),'DTEND;VALUE=DATE:'+addDay(row.end).replace(/-/g,''),'SUMMARY:'+icsEscape(name+' · '+row.title));
      if(description)lines.push('DESCRIPTION:'+icsEscape(description));
      const location=fields.find(f=>f.key==='location');if(location)lines.push('LOCATION:'+icsEscape(location.value));
      lines.push('END:VEVENT');
    });
    lines.push('END:VCALENDAR');return lines.map(foldLine).join('\r\n')+'\r\n';
  }
  function downloadICS(name=myName,options={}) {
    if(!name)return openPicker();
    const blob=new Blob([buildICS(name,options)],{type:'text/calendar;charset=utf-8'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=`OnAir_${name.replace(/[\\/:*?"<>|]/g,'_')}_일정.ics`;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    const hint=get('oa-ics-hint');if(hint){hint.textContent=ICS_HINT;hint.hidden=false;}
    showToast(ICS_HINT);
  }
  function fitNameLabel(name,width,measure) {
    const full=String(name||''),available=Number(width)-4;
    if(available<=0||typeof measure!=='function'||measure(full)<=available)return full;
    const chars=Array.from(full);
    while(chars.length&&measure(chars.join('')+'…')>available)chars.pop();
    return chars.length?chars.join('')+'…':'…';
  }
  function fitWeeklyNames() {
    const canvas=document.createElement('canvas'),context=canvas.getContext?.('2d');
    if(!context)return;
    document.querySelectorAll('#week-vacation-container .abs-row-name,#week-dispatch-container .abs-row-name').forEach(node=>{
      const full=node.dataset.oaPerson||node.textContent.trim(),style=getComputedStyle(node);
      context.font=style.font||`${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
      const top=node.closest('.abs-row-top'),type=top?.querySelector('.abs-row-type');
      const width=top ? top.clientWidth-(type?.getBoundingClientRect().width||0)-(parseFloat(getComputedStyle(top).columnGap)||0) : node.clientWidth;
      node.textContent=fitNameLabel(full,width,value=>context.measureText(value).width);
      node.setAttribute('title',full);
    });
  }
  function enhanceNames() {
    const todayWorkers=noDutyMap[todayStr]?[]:scheduleMap[todayStr]||[];
    document.querySelectorAll('#today-person-container .today-person:not(.noduty-card)').forEach((node,index)=>{if(todayWorkers[index]){node.dataset.oaPerson=todayWorkers[index].name;node.setAttribute('tabindex','0');node.setAttribute('role','button');node.setAttribute('aria-label',todayWorkers[index].name+' 개인 일정 보기');}});
    document.querySelectorAll('#week-vacation-container .abs-row-name,#week-dispatch-container .abs-row-name').forEach(node=>{const full=node.dataset.oaPerson||node.textContent.trim();node.dataset.oaPerson=full;node.textContent=full;node.removeAttribute('tabindex');node.removeAttribute('role');node.removeAttribute('aria-label');});
    document.querySelectorAll('#calendar-tbody .worker-tag[data-worker-name]').forEach(node=>{node.setAttribute('tabindex','0');node.setAttribute('role','button');node.setAttribute('aria-label',node.dataset.workerName+' 개인 일정 보기');});
    fitWeeklyNames();
    const line=get('my-next-duty');if(line&&myName){line.onclick=()=>openAgenda(myName);line.setAttribute('tabindex','0');line.setAttribute('role','button');line.setAttribute('aria-label',myName+' 내 일정 보기');}
  }
  function injectStyle() {
    if(get('oa-personal-style'))return;
    const node=document.createElement('style');node.id='oa-personal-style';node.textContent=`
      .oa-personal{font-size:14px;line-height:1.6;min-width:0}.oa-personal button{font:inherit;font-size:12px;min-height:32px;padding:4px 12px;white-space:nowrap}.oa-personal .op-sub{font-size:12px;color:var(--text-muted);font-weight:400}.oa-personal .op-main{font-size:14px;font-weight:600;color:var(--text-main);overflow-wrap:anywhere}.oa-personal .op-toolbar{display:flex;align-items:center;gap:8px;margin:12px 0 16px;flex-wrap:wrap}.oa-personal .op-tab{border-radius:8px;background:var(--surface);color:var(--text-sub)}.oa-personal .op-tab.active{background:var(--primary-light);color:var(--primary);font-weight:700}.oa-personal .op-total{margin-left:auto;font-size:12px;color:var(--text-muted)}.oa-personal .op-section{font-size:12px;font-weight:600;color:var(--text-muted);margin:20px 0 7px}.oa-personal .op-row{display:flex;align-items:center;gap:14px;padding:12px 0;border-bottom:1px solid var(--border)}.oa-personal .op-grow{flex:1;min-width:0}.oa-personal .op-pill{display:inline-block;font-size:11px;line-height:22px;padding:0 8px;border-radius:6px;background:var(--vacation-bg);color:var(--vacation-text);white-space:nowrap}.oa-personal .op-pill.duty{background:var(--male-bg);color:var(--male-text)}.oa-personal .op-pill.live{background:var(--primary-light);color:var(--primary)}.oa-personal .op-period{font-size:14px;color:var(--text-main);font-weight:500}.oa-personal .op-date{min-width:78px;font-size:14px;font-weight:600}.oa-personal .op-date small{display:block;font-size:12px;color:var(--text-muted);font-weight:400}.oa-personal .op-actions{display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end}.oa-personal .op-footer{display:flex;justify-content:flex-end;align-items:center;gap:8px;border-top:1px solid var(--border);padding-top:16px;margin-top:18px;flex-wrap:wrap}.oa-personal .op-search{width:100%;padding:10px 12px;margin:12px 0;border:1px solid var(--border-strong);background:var(--card);color:var(--text-main);border-radius:9px;box-sizing:border-box;font:inherit}.oa-personal .op-name-list{max-height:260px;overflow:auto}.oa-personal .op-name{width:100%;display:flex;justify-content:space-between;align-items:center;border-radius:0;min-height:43px;text-align:left;background:transparent;border-bottom:1px solid var(--border);font-size:14px}.oa-personal .op-name.selected{background:var(--primary-light);color:var(--primary)}.oa-personal .op-note{background:var(--surface);border-radius:8px;padding:10px 12px;font-size:12px;margin-top:14px}.oa-personal details{margin-top:14px;border-top:1px solid var(--border);padding-top:12px}.oa-personal summary{font-size:12px;color:var(--text-muted);cursor:pointer}.oa-personal .op-details{display:grid;grid-template-columns:88px 1fr;gap:0;margin:16px 0}.oa-personal .op-details dt,.oa-personal .op-details dd{padding:11px 0;margin:0;border-bottom:1px solid var(--border);font-size:14px;overflow-wrap:anywhere;white-space:pre-wrap}.oa-personal .op-details dt{font-size:12px;color:var(--text-muted)}.oa-personal .op-table-scroll{overflow:auto}.oa-personal .op-table{width:100%;border-collapse:collapse;font-size:12px}.oa-personal .op-table th{text-align:left;font-size:12px;font-weight:600;color:var(--text-muted);padding:10px 8px;background:var(--surface)}.oa-personal .op-table td{padding:13px 8px;border-bottom:1px solid var(--border)}.oa-personal .op-table tr.selected{background:var(--primary-light)}.oa-personal .op-risk{color:var(--holiday-text);font-size:11px;margin-top:4px}.oa-personal .op-pill.pending{margin-left:6px;background:var(--primary-light);color:var(--primary);font-weight:700;vertical-align:1px}.oa-personal .op-check{display:inline-flex;align-items:center;gap:5px;font-size:12px;color:var(--text-sub);margin-right:auto}.oa-personal .op-check input{width:15px;height:15px;margin:0}.oa-personal .op-ics-hint{margin:12px 0 0;padding:8px 10px;border-radius:8px;background:var(--surface)}
      .oa-personal .op-dday{flex:0 0 auto;min-width:64px;height:30px;padding:0 10px;display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;border-radius:8px;margin-right:4px;font-size:14px;line-height:1.2;font-weight:700;letter-spacing:.1px;color:var(--primary);background:var(--primary-light);font-variant-numeric:tabular-nums;white-space:nowrap}.oa-personal .op-dday.today{color:var(--card);background:var(--primary)}.oa-personal .op-row>.op-pill{flex-shrink:0}.oa-personal .op-grow .op-sub{overflow-wrap:anywhere}
      #today-vacation-container .today-vacation-person{font-family:inherit;font-size:12px!important;font-weight:800;line-height:1.25;display:flex;align-items:center;justify-content:center;width:100%;height:100%;margin:0;min-height:0;padding:1px 2px;border:0;text-align:center;cursor:pointer;white-space:nowrap}#today-vacation-container[data-single="true"] .today-vacation-person{grid-column:1/-1;justify-self:center;align-self:center;width:calc(100% - 8px);min-width:0;max-width:132px;height:48px;max-height:calc(100% - 8px);box-sizing:border-box;padding:6px;border-radius:8px;font-size:18px!important;font-weight:700}#today-vacation-container .op-more{background:var(--surface);color:var(--primary)}
      #today-vacation-container[data-single="true"] .today-vacation-name{display:block;min-width:0;max-width:100%;font:inherit;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left}
      @media(max-width:1439px){#today-vacation-container[data-single="true"] .today-vacation-person{height:44px;font-size:16px!important}}
      .today-vacation-panel.has-multiple-absences { gap:1px; }
      .has-multiple-absences .today-vacation-heading { flex-wrap:nowrap; line-height:1.05; gap:2px; }
      .has-multiple-absences #today-vacation-count { font-size:10px; line-height:1; white-space:nowrap; }
      #today-vacation-container:is([data-layout="pair"],[data-layout="stack"]) {
        display:flex; flex-direction:column; justify-content:center; gap:var(--vac-row-gap);
      }
      .today-absence-row {
        flex:none; display:flex; justify-content:center; gap:var(--vac-col-gap); height:var(--vac-row-height);
      }
      #today-vacation-container .today-absence-row .today-vacation-person {
        flex:none; height:100%; box-sizing:border-box; padding:1px 0 0; border:0; border-radius:5px;
        font-size:var(--vac-label-size)!important; font-weight:700; letter-spacing:var(--vac-name-spacing); line-height:1.06;
      }
      #today-vacation-container[data-layout="pair"] .today-vacation-name { font:inherit; text-align:center; }
      #today-vacation-container[data-layout="pair"] .today-vacation-person {
        border-radius:7px; border:1px solid var(--vacation-border);
      }
      #today-vacation-container[data-layout="pair"] .kind-short { border-color:var(--short-absence-border); }
      #today-vacation-container .today-absence-row button:focus-visible { outline:2px solid var(--primary); outline-offset:-2px; }
      #week-vacation-container .abs-row,#week-dispatch-container .abs-row{display:flex;flex-direction:column;justify-content:center;min-width:0;min-height:0;gap:0;font-family:inherit;font-size:inherit;line-height:normal;text-align:center;letter-spacing:inherit;transform:none}
      #week-vacation-container .abs-row-top,#week-dispatch-container .abs-row-top{display:flex;flex-wrap:nowrap;align-items:center;justify-content:center;gap:5px;width:100%;min-width:0}
      #week-vacation-container .abs-row-name,#week-dispatch-container .abs-row-name{display:block;flex:0 1 auto;min-width:0;width:auto;text-align:center;cursor:pointer;white-space:nowrap;overflow:hidden}
      #week-vacation-container .abs-row-type,#week-dispatch-container .abs-row-type{display:block;flex:0 0 auto;max-width:55%;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:10.5px;color:var(--text-sub)}
      #week-vacation-container .abs-row-range,#week-dispatch-container .abs-row-range{display:block;width:100%;margin-top:2px;text-align:center;line-height:1.3;white-space:normal;overflow:visible;overflow-wrap:normal}
      .abs-range-date{display:inline-block;white-space:pre}
      #week-vacation-container{grid-auto-rows:max-content;align-content:start}
      #week-vacation-container[data-weekly-fit="compact"]{gap:1px}
      #week-vacation-container[data-weekly-fit="compact"] .abs-row{padding:0 1px}
      #week-vacation-container[data-weekly-fit="compact"] .abs-row-top{gap:2px}
      #week-vacation-container[data-weekly-fit="compact"] .abs-row-top,
      #week-vacation-container[data-weekly-fit="compact"] .abs-row-name,
      #week-vacation-container[data-weekly-fit="compact"] .abs-row-type{line-height:1.05}
      #week-vacation-container[data-weekly-fit="compact"] .abs-row-name{font-size:11px}
      #week-vacation-container[data-weekly-fit="compact"] .abs-row-type{font-size:10px}
      #week-vacation-container[data-weekly-fit="compact"] .abs-row-range{font-size:10px;line-height:1.05;margin-top:0}
      .abs-range-compact{display:none}.abs-list:not([data-density="a"]) .abs-range-full{display:none}.abs-list:not([data-density="a"]) .abs-range-compact{display:inline}
      .info-desk-col .week-absence-title{min-height:0;padding:0;border:0;border-radius:0;background:transparent;box-shadow:none;font-family:inherit;line-height:normal;transform:none;text-decoration:underline dotted;text-underline-offset:3px;text-decoration-color:var(--text-muted)}
      .info-desk-col .week-absence-title:hover{color:var(--primary);background:transparent;transform:none}
      .abs-list .abs-row:hover{box-shadow:inset 0 0 0 1px var(--primary);transform:none}
      .abs-list .abs-row:focus-visible,.abs-list .abs-more:focus-visible{outline-offset:-2px}
      .abs-list .abs-more{min-height:0;line-height:1.3;font-family:inherit!important;font-size:10.5px;letter-spacing:inherit;cursor:pointer;box-shadow:none;transform:none}
      .abs-list .abs-more:hover{background:var(--primary-light);color:var(--primary);transform:none}
      [data-oa-person],[data-worker-name]{cursor:pointer}[data-oa-person]:focus-visible,[data-worker-name]:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
      @media(max-width:600px){.oa-personal .op-row{flex-wrap:wrap;gap:8px}.oa-personal .op-actions{width:100%}.oa-personal .op-date{min-width:70px}}
    `;document.head.appendChild(node);
  }
  function handleAction(node) {
    const d=node.dataset;
    switch(d.oaAction){
      case 'close':UI().close(d.modal);break;
      case 'absence':openAbsence(d.name,d.date);break;
      case 'education':{
        // API refreshes replace and reorder this array while the list can remain open.
        const record=d.absenceId?absences.find(a=>a.id!=null&&String(a.id)===d.absenceId&&a.name===d.name):null;
        if(record)openEducation(record);
        else{showToast('교육 일정이 삭제되었거나 변경되었습니다. 최신 목록을 확인하세요.');openAbsence(d.name,d.date);}
        break;
      }
      case 'agenda-filter':openAgenda(d.name,d.filter);break;
      case 'date':UI().close(ids.agenda);goToDate(d.date);break;
      case 'full-duty':openFullDuty();break;
      case 'ics':downloadICS(d.name,{includePast:!!get('oa-ics-past')?.checked});break;
      case 'my-requests':UI().close(ids.agenda);if(window.OnAirRequests?.openMyRequests)window.OnAirRequests.openMyRequests(d.name);else openMyRequestsModal();break;
      case 'pick-name':pickerName=d.name;updatePickerList(get('oa-personal-name-search')?.value||'');break;
      case 'apply-name':applyPicker();break;
      case 'clear-name':setMyName('');UI().close(ids.picker);break;
      case 'search-choice':UI().close(ids.search);selectMemberDuty(d.name);break;
      case 'all-vacation':openAllVacations();break;
      case 'swap':if(!canRequestChange()){showToast(REQUEST_ONLY_MESSAGE,true);break;}swapSelection=null;openSwap(d.name,d.date);break;
      case 'swap-window':openSwap(d.name,d.date,Number(d.window));break;
      case 'choose-swap':swapSelection=swapCandidates(d.name,d.source,Number(d.window)).find(c=>c.name===d.partner&&c.date===d.target)||null;openSwap(d.name,d.source,Number(d.window));break;
      case 'prefill-swap':selectSwapCandidate(d.name,d.source,d.partner,d.target);break;
    }
  }
  function handleName(node) {node.focus?.({preventScroll:true});const date=node.closest('[data-date-key]')?.dataset.dateKey||todayStr;if(node.matches('.v-mini-chip,.abs-row'))openAbsence(node.dataset.person||node.dataset.oaPerson,date);else openAgenda(node.dataset.workerName||node.dataset.oaPerson);}
  function install() {
    if(installed)return;installed=true;
    setMyName=function(name,options={}){
      const next=String(name||'');if(next&&!memberList.some(m=>m.name===next))return false;
      legacyPending='';restoreSelectionPending=false;myName=next;const remember=!!(typeof options==='boolean'?options:options.remember);
      storageSet('sessionStorage','sd_my_name_session',next);storageSet('localStorage','sd_my_name',remember?next:'');storageSet('localStorage','sd_my_name_remember',remember&&next?'1':'0');
      renderMyNameUI();selectMemberDuty(next);return true;
    };
    const rememberMarker=storageGet('localStorage','sd_my_name_remember'),localName=storageGet('localStorage','sd_my_name');legacyPending=!rememberMarker&&localName?localName:'';myName=storageGet('sessionStorage','sd_my_name_session')||((rememberMarker==='1'||legacyPending)?localName:'');migrateLegacyName();
    restoreSelectionPending=!!myName;
    promptMyName=openPicker;openPersonAbsenceModal=openAbsence;openMyDutyModal=()=>openAgenda(myName);
    const renderName=renderMyNameUI;renderMyNameUI=function(){migrateLegacyName();restorePersonalSelection();const result=renderName.apply(this,arguments);enhanceNames();return result;};
    const renderToday=renderTodayCard;renderTodayCard=function(){const result=renderToday.apply(this,arguments);enhanceNames();return result;};
    const renderWeek=renderWeekAbsenceBoard;renderWeekAbsenceBoard=function(){const result=renderWeek.apply(this,arguments);enhanceNames();return result;};
    const renderCal=renderCalendar;renderCalendar=function(){const result=renderCal.apply(this,arguments);enhanceNames();return result;};
    window.fitTodayVacations=fitTodayVacations;window.renderTodayVacations=fitTodayVacations;
    executeMemberSearch=function(){
      const query=get('member-search-input')?.value.trim()||'',rows=query?searchNames(query):[];
      selectedQuickMember=rows.length===1?rows[0].name:'';updateMemberSelectionUI();
      return {query,rows};
    };
    handleMemberSearchKeydown=function(event){
      if(event.key!=='Enter'||event.isComposing||event.keyCode===229)return;
      event.preventDefault();
      const {query,rows}=executeMemberSearch();
      if(!query||rows.length===1)return;
      UI().open(ids.search,'이름 검색 결과',wrap(`<div class="op-sub">“${escape(query)}” · 검색 결과 ${rows.length}명</div>${rows.length?rows.map(m=>button('search-choice',m.name,attrs({name:m.name}),'op-name')).join(''):'<p class="op-sub">일치하는 이름이 없습니다. 검색어를 확인하세요.</p>'}${footer(ids.search)}`),{width:480});
    };
    document.addEventListener('click',event=>{const action=event.target.closest?.('[data-oa-action]');if(action){event.preventDefault();event.stopPropagation();handleAction(action);return;}const node=event.target.closest?.('.v-mini-chip[data-person],.worker-tag[data-worker-name],.today-person[data-oa-person],.abs-row[data-oa-person]');if(node){event.preventDefault();event.stopPropagation();handleName(node);}},true);
    document.addEventListener('input',event=>{if(event.target.id==='oa-personal-name-search')updatePickerList(event.target.value);});
    document.addEventListener('keydown',event=>{if(event.key!=='Enter'&&event.key!==' ')return;const node=event.target.closest?.('.worker-tag[data-worker-name],.today-person[data-oa-person],.abs-row[data-oa-person]');if(node){event.preventDefault();event.stopPropagation();handleName(node);}else if(event.target.id==='my-next-duty'){event.preventDefault();openAgenda(myName);}else if(event.target.id==='oa-personal-name-search'&&event.key==='Enter'){const names=searchNames(event.target.value);if(names.length===1){pickerName=names[0].name;updatePickerList(event.target.value);}}},true);
    const ready = () => {
      injectStyle();
      enhanceNames();
      fitTodayVacations();
      const list = get('today-vacation-container');
      if (list && typeof ResizeObserver === 'function') {
        new ResizeObserver(fitTodayVacations).observe(list);
      }
      const search = get('member-search-input');
      if (search) {
        search.placeholder = '이름 검색 · Enter로 결과';
        search.title = '이름이 한 명으로 좁혀지면 달력의 근무일을 강조합니다. 여러 명 또는 일치하는 이름이 없으면 Enter로 검색 결과를 확인하세요.';
      }
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready);
    else ready();
    window.addEventListener('resize', () => requestAnimationFrame(() => {
      if (typeof ResizeObserver !== 'function') fitTodayVacations();
      enhanceNames();
    }));
  }
  window.OnAirPersonal={openAgenda,openAbsence,openEducation,openPicker,searchNames,agendaRows,absenceSections,educationFields,vacationLayout,fitNameLabel,fitTodayVacations,openAllVacations,swapEligibility,swapCandidates,postSwapIntervals,openSwap,selectSwapCandidate,buildICS,downloadICS,enhanceNames,install};
  install();
})();

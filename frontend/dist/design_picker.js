(() => {
  const displayDesigns = [
    ['auto', '자동', 'linear-gradient(135deg,#20a182 50%,#a63c44 50%)'],
    ['original', '기본 디자인', '#20a182'],
    ['frost', '서리 유리', '#91cbe5'],
    ['paper', '설원 격자', '#d6dfe9'],
    ['print', '눈마을 목판화', '#be9b66'],
    ['photo', '옐로스톤 겨울 파노라마', '#638594'],
    ['christmas', '크리스마스 엽서', 'linear-gradient(135deg,#265443 50%,#a63c44 50%)']
  ];
  let preference = 'auto';
  function validPreference(value) {
    return displayDesigns.some(([id]) => id === value) ? value : 'auto';
  }
  try { preference = validPreference(localStorage.getItem('wlr_design')); } catch (e) {}
  function applyDesign(now) {
    const seasonal = now.getMonth() >= 10 ? 'christmas' : 'original';
    const design = preference === 'auto' ? seasonal : preference;
    const root = document.documentElement;
    if (design === 'original') delete root.dataset.winter;
    else root.dataset.winter = design;
    return seasonal;
  }
  applyDesign(new Date());
  document.addEventListener('DOMContentLoaded', function setupDesignPicker() {
    const toggle = document.getElementById('design-toggle');
    const menu = document.getElementById('design-menu');
    let nextDayTimer;
    for (const [id, name, swatch] of displayDesigns) {
      const item = document.createElement('button');
      item.type = 'button';
      item.setAttribute('role', 'menuitemradio');
      item.tabIndex = -1;
      item.dataset.design = id;
      item.style.setProperty('--sample', swatch);
      item.textContent = name;
      menu.appendChild(item);
    }

    function reflectSelection() {
      const now = new Date();
      const seasonal = applyDesign(now);
      menu.querySelector('[data-design="auto"]').textContent =
        '자동 · ' + displayDesigns.find(([id]) => id === seasonal)[1];
      for (const item of menu.children) {
        const checked = item.dataset.design === preference;
        item.setAttribute('aria-checked', String(checked));
        if (checked) toggle.title = '현재 디자인 · ' + item.textContent;
      }
      clearTimeout(nextDayTimer);
      if (preference === 'auto') {
        const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
        nextDayTimer = setTimeout(reflectSelection, midnight - now);
      }
    }
    function closeMenu(returnFocus) {
      menu.hidden = true;
      toggle.setAttribute('aria-expanded', 'false');
      if (returnFocus) toggle.focus();
    }
    function openMenu() {
      reflectSelection();
      menu.hidden = false;
      toggle.setAttribute('aria-expanded', 'true');
      menu.querySelector('[aria-checked="true"]').focus();
    }

    reflectSelection();
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) reflectSelection();
    });
    window.addEventListener('storage', event => {
      if (event.storageArea === localStorage && (event.key === 'wlr_design' || event.key === null)) {
        preference = validPreference(event.newValue);
        reflectSelection();
      }
    });
    toggle.addEventListener('click', () => menu.hidden ? openMenu() : closeMenu(true));
    toggle.addEventListener('keydown', event => {
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      event.preventDefault();
      openMenu();
    });
    menu.addEventListener('click', event => {
      const item = event.target.closest('[data-design]');
      if (!item) return;
      preference = item.dataset.design;
      try { localStorage.setItem('wlr_design', preference); } catch (e) {}
      reflectSelection();
      closeMenu(true);
    });
    menu.addEventListener('keydown', event => {
      if (event.key === 'Tab') {
        setTimeout(() => closeMenu(false), 0);
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        closeMenu(true);
        return;
      }
      const items = Array.from(menu.children);
      const index = items.indexOf(document.activeElement);
      const next = { ArrowDown: (index + 1) % items.length, ArrowUp: (index + items.length - 1) % items.length,
                     Home: 0, End: items.length - 1 }[event.key];
      if (typeof next === 'number') {
        event.preventDefault();
        items[next].focus();
      }
    });
    const controls = toggle.parentElement;
    controls.addEventListener('focusout', event => {
      if (!menu.hidden && !controls.contains(event.relatedTarget)) closeMenu(false);
    });
    document.addEventListener('pointerdown', event => {
      if (!menu.hidden && !menu.contains(event.target) && !toggle.contains(event.target)) closeMenu(false);
    });
  });
})();

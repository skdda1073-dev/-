/** Display state only: existing feature controllers own their inputs and data. */
(() => {
    'use strict';
    window.AppModules = window.AppModules || {};
    let initialized = false, active = 'journey', toolOpen = true, expanded = false;
    let tabs = [], resizeTimer, dirtyProvider = () => false, guarding = false;
    let resultToken = null, resultAvailable = false, resultOpen = false, detailView = false, navigation = 0;
    const menuListeners = new Set();
    const byId = id => document.getElementById(id);
    const mobile = () => window.matchMedia('(max-width: 799px)').matches;
    const wide = () => window.matchMedia('(min-width: 1280px)').matches && !expanded;
    const keyOf = tab => tab.id.replace('transport-tab-', '');
    function resizeMap() {
        window.AppModules.map?.resizeMap();
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => { window.AppModules.map?.resizeMap(); layoutDetail(); }, 260);
    }
    function layoutDetail() {
        const stage = byId('map-stage'), detail = byId('route-detail'), shell = byId('app-shell');
        const visible = active === 'journey' && resultAvailable && resultOpen && detailView;
        const rect = stage.getBoundingClientRect();
        let docked = wide() && resultOpen && active === 'journey' && rect.width >= 640 && rect.height >= 480;
        const content = byId('route-detail-content'), scroll = content.scrollTop;
        const focused = detail.contains(document.activeElement) ? document.activeElement : null;
        function place(inMap) {
            const host = byId(inMap ? 'route-detail-map' : 'route-detail-inline');
            if (detail.parentElement !== host) host.appendChild(detail);
            shell.classList.toggle('detail-docked', inMap);
        }
        place(docked);
        detail.hidden = !visible;
        if (docked && visible) {
            const panel = detail.getBoundingClientRect(), tools = stage.querySelector('.map-tools')?.getBoundingClientRect();
            const overlaps = tools && tools.width > 0 && tools.height > 0 && panel.left < tools.right && panel.right > tools.left && panel.top < tools.bottom && panel.bottom > tools.top;
            if (panel.left < rect.left + 180 || panel.right > rect.right - 72 || panel.top < rect.top + 48 || panel.bottom > rect.bottom - 56 || overlaps) {
                docked = false;
                place(false);
            }
        }
        shell.classList.toggle('result-detail-view', visible);
        byId('results-list').hidden = !visible || docked;
        content.scrollTop = scroll;
        if (focused && visible && document.activeElement !== focused) focused.focus({ preventScroll: true });
        if (visible && !docked && byId('result-container').contains(document.activeElement)) byId('route-detail-heading').focus({ preventScroll: true });
        const selected = window.AppModules.renderer?.getSelectedIndex();
        byId('result-container').querySelectorAll('.route-select').forEach(button => {
            button.setAttribute('aria-expanded', String(visible && Number(button.getAttribute('data-journey-index')) === selected));
        });
    }
    function syncSelection() {
        const target = byId('taxi-selection'), state = window.AppState;
        if (!target || !state) return;
        const campus = state.getSelectedCampus() === 'knu' ? '강원대학교' : '한림대학교';
        const origin = state.getOriginMode() === 'building' ? (byId('building-select')?.value || '교내 건물 미선택') : '현재 위치';
        target.textContent = `${campus} · ${origin} → ${state.getDestination().name || '도착지 미선택'}`;
    }
    function render() {
        const shell = byId('app-shell'), sidebar = byId('app-sidebar');
        const resultsVisible = active === 'journey' && resultAvailable && resultOpen;
        const toolVisible = toolOpen && (!resultsVisible || wide());
        shell.classList.toggle('menu-expanded', expanded);
        shell.classList.toggle('tool-closed', !toolVisible && !resultsVisible);
        shell.classList.toggle('search-closed', !toolVisible);
        shell.classList.toggle('results-visible', resultsVisible);
        shell.classList.toggle('results-replace', resultsVisible && !wide());
        sidebar.inert = mobile() && !expanded;
        sidebar.setAttribute('aria-hidden', String(sidebar.inert));
        byId('sidebar-backdrop').hidden = !mobile() || !expanded;
        const toggle = byId('sidebar-toggle');
        toggle.setAttribute('aria-expanded', String(expanded));
        toggle.setAttribute('aria-label', expanded ? '메뉴 접기' : '메뉴 펼치기');
        toggle.title = expanded ? '메뉴 접기' : '메뉴 펼치기';
        tabs.forEach(tab => {
            const current = keyOf(tab) === active, selected = current && toolVisible;
            tab.setAttribute('aria-selected', String(selected));
            tab.tabIndex = current ? 0 : -1;
            byId(tab.getAttribute('aria-controls')).hidden = !selected;
        });
        const planner = byId('planner-panel');
        planner.classList.toggle('is-collapsed', !toolVisible);
        planner.setAttribute('aria-hidden', String(!toolVisible));
        planner.inert = !toolVisible;
        byId('planner-body').hidden = !toolVisible;
        byId('panel-toggle').hidden = !toolVisible;
        byId('panel-toggle').setAttribute('aria-expanded', String(toolVisible));
        const heading = byId('tool-heading');
        heading.parentElement.parentElement.hidden = !toolVisible;
        const tab = tabs.find(item => keyOf(item) === active);
        heading.textContent = active === 'journey' ? '어디로 갈까요?' : tab.getAttribute('aria-label');
        byId('tool-reopen').hidden = toolVisible || resultsVisible;
        byId('results-section').hidden = !resultsVisible;
        byId('results-reopen').hidden = !resultAvailable || resultsVisible || active !== 'journey' || toolVisible;
        byId('results-show').hidden = !resultAvailable;
        layoutDetail();
        syncSelection();
    }
    function select(name, options = {}) {
        if (!initialized) init();
        if (!tabs.some(tab => keyOf(tab) === name)) return false;
        menuListeners.forEach(listener => listener(name, options));
        navigation++;
        resultOpen = false;
        detailView = false;
        active = name;
        toolOpen = true;
        if (mobile()) expanded = false;
        render();
        resizeMap();
        if (options.focus || mobile()) byId('tool-heading').focus({ preventScroll: true });
        return true;
    }
    function closeTool() {
        navigation++;
        toolOpen = false;
        render();
        resizeMap();
        (mobile() ? byId('tool-reopen') : byId('transport-tab-' + active)).focus({ preventScroll: true });
    }
    function openTool() { select(active, { focus: true }); }
    function toggleMenu() {
        expanded = !expanded;
        render();
        resizeMap();
        if (expanded && mobile()) byId('transport-tab-' + active).focus({ preventScroll: true });
    }
    function openJourney(target) {
        select('journey', { focus: true });
        const input = target ? byId(target) : byId('origin-' + (window.AppState?.getOriginMode() || 'gps'));
        input?.focus({ preventScroll: true });
    }
    function beginResults(payload = {}) {
        resultToken = { navigation };
        resultAvailable = true; resultOpen = active === 'journey'; detailView = false;
        updateResultSummary(resultToken, payload);
        byId('results-section').dataset.state = 'loading';
        render(); resizeMap();
        if (canPresent(resultToken)) byId('results-heading').focus({ preventScroll: true });
        return resultToken;
    }
    function updateResultSummary(token, payload) {
        if (token !== resultToken) return;
        const campus = payload.campus === 'knu' ? '강원대학교 · ' : payload.campus === 'hallym' ? '한림대학교 · ' : '';
        byId('results-summary').textContent = `${campus}${payload.location || '현재 위치'} → ${payload.dest_name || '주변 정류장'}`;
    }
    function canPresent(token = resultToken) {
        return Boolean(token && token === resultToken && token.navigation === navigation && active === 'journey' && resultOpen);
    }
    function invalidateResults() {
        resultToken = null; resultAvailable = false; resultOpen = false; detailView = false;
        byId('results-summary').textContent = '';
        byId('results-section').dataset.state = 'idle';
        render(); resizeMap();
    }
    function showResults() {
        if (!resultAvailable || active !== 'journey') return;
        resultOpen = true;
        if (resultToken) resultToken.navigation = navigation;
        render(); resizeMap();
        const renderer = window.AppModules.renderer, index = renderer?.getSelectedIndex();
        if (Number.isInteger(index)) renderer.selectJourney(index);
        byId(detailView ? 'route-detail-heading' : 'results-heading').focus({ preventScroll: true });
    }
    function closeResults() {
        navigation++; resultOpen = false; detailView = false;
        render(); resizeMap();
        (toolOpen ? byId('results-show') : byId('results-reopen')).focus({ preventScroll: true });
    }
    function editSearch() {
        navigation++; resultOpen = false; toolOpen = true; detailView = false;
        render(); resizeMap(); byId('tool-heading').focus({ preventScroll: true });
    }
    function showDetail(options = {}) {
        if (!canPresent()) return;
        detailView = true; render();
        if (options.focus || mobile()) byId('route-detail-heading').focus({ preventScroll: true });
    }
    function closeDetail(options = {}) {
        detailView = false; render();
        if (options.returnFocus === false) return;
        const selected = window.AppModules.renderer?.getSelectedButton();
        const candidates = [selected, byId('results-heading'), byId('tool-heading'), byId('sidebar-toggle')];
        const target = candidates.find(element => {
            if (!element?.isConnected || !element.getClientRects().length) return false;
            const rect = element.getBoundingClientRect();
            if (rect.bottom <= 0 || rect.right <= 0 || rect.top >= (window.innerHeight || Infinity) || rect.left >= (window.innerWidth || Infinity)) return false;
            for (let parent = element.parentElement; parent; parent = parent.parentElement) {
                if (parent.hidden || parent.inert) return false;
                const style = typeof window.getComputedStyle === 'function' ? window.getComputedStyle(parent) : null;
                if (style?.visibility === 'hidden' || style?.display === 'none') return false;
                if (['results-scroll', 'results-section', 'planner-panel'].includes(parent.id)) {
                    const clip = parent.getBoundingClientRect();
                    if (rect.bottom <= clip.top || rect.top >= clip.bottom || rect.right <= clip.left || rect.left >= clip.right) return false;
                }
            }
            return true;
        });
        target?.focus({ preventScroll: true });
    }
    function showMap() {
        detailView = false;
        if (!wide()) { navigation++; resultOpen = false; toolOpen = false; }
        render();
        resizeMap();
    }
    function hasUnsavedData() { return Boolean(dirtyProvider()); }
    function beforeUnload(event) {
        if (!hasUnsavedData()) return;
        event.preventDefault();
        event.returnValue = '';
    }
    function refreshUnloadGuard() {
        const needed = hasUnsavedData();
        if (needed === guarding) return;
        if (needed) window.addEventListener('beforeunload', beforeUnload);
        else window.removeEventListener('beforeunload', beforeUnload);
        guarding = needed;
    }
    function setDirtyProvider(provider) { dirtyProvider = provider; refreshUnloadGuard(); }
    function reload() {
        if (hasUnsavedData() && !window.confirm('새로고침하면 작성 중인 내용과 시연방·대화가 사라집니다. 새로고침할까요?')) return;
        window.removeEventListener('beforeunload', beforeUnload);
        guarding = false;
        try { window.location.reload(); }
        catch (error) { refreshUnloadGuard(); throw error; }
    }
    function init() {
        if (initialized || !byId('app-shell')) return;
        initialized = true;
        tabs = [...byId('app-menu').querySelectorAll('[role="tab"]')];
        tabs.forEach((tab, index) => {
            const label = tab.querySelector('span').textContent.trim();
            tab.setAttribute('aria-label', label); tab.title = label;
            tab.addEventListener('click', () => select(keyOf(tab)));
            tab.addEventListener('keydown', event => {
                let next;
                if (event.key === 'ArrowDown' || event.key === 'ArrowRight') next = (index + 1) % tabs.length;
                if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
                if (event.key === 'Home') next = 0;
                if (event.key === 'End') next = tabs.length - 1;
                if (next === undefined) return;
                event.preventDefault(); select(keyOf(tabs[next]));
                if (!mobile()) tabs[next].focus();
            });
        });
        byId('sidebar-toggle').addEventListener('click', toggleMenu);
        byId('sidebar-backdrop').addEventListener('click', () => {
            expanded = false; render(); resizeMap(); byId('sidebar-toggle').focus();
        });
        byId('tool-reopen').addEventListener('click', openTool);
        byId('app-reload').addEventListener('click', reload);
        byId('taxi-edit-selection').addEventListener('click', () => openJourney());
        byId('results-show').addEventListener('click', showResults);
        byId('results-reopen').addEventListener('click', showResults);
        byId('results-close').addEventListener('click', closeResults);
        byId('results-edit').addEventListener('click', editSearch);
        byId('results-map').addEventListener('click', () => window.AppModules.ui?.focusMap());
        byId('results-list').addEventListener('click', () => closeDetail());
        byId('route-detail-close').addEventListener('click', () => closeDetail());
        byId('route-detail').addEventListener('keydown', event => {
            if (event.key === 'Escape' && detailView) { event.preventDefault(); event.stopPropagation(); closeDetail(); }
        });
        if (typeof window.ResizeObserver === 'function') new window.ResizeObserver(layoutDetail).observe(byId('map-stage'));
        byId('skip-search').addEventListener('click', event => { event.preventDefault(); openJourney('tab-hallym'); });
        document.addEventListener('input', refreshUnloadGuard);
        document.addEventListener('change', refreshUnloadGuard);
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && expanded) {
                expanded = false; render(); resizeMap(); byId('sidebar-toggle').focus();
            }
            if (event.key === 'Tab' && mobile() && expanded) {
                const controls = [byId('sidebar-toggle'), byId('transport-tab-' + active)];
                const first = controls[0], last = controls.at(-1);
                if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
                else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
            }
        });
        window.addEventListener('resize', () => {
            const focusedMenu = byId('app-sidebar').contains(document.activeElement);
            const focusedTool = byId('planner-panel').contains(document.activeElement);
            render();
            if (focusedMenu && mobile() && !expanded) byId('sidebar-toggle').focus();
            if (focusedTool && byId('planner-body').hidden && resultOpen) byId('results-heading').focus({ preventScroll: true });
            resizeMap();
        });
        render(); refreshUnloadGuard();
    }
    window.AppModules.shell = { init, select, closeTool, openTool, openJourney, syncSelection,
        beginResults, updateResultSummary, canPresent, invalidateResults, showResults, closeResults, editSearch, showDetail, closeDetail, showMap,
        getNavigation: () => navigation,
        onMenuSelect: listener => { menuListeners.add(listener); return () => menuListeners.delete(listener); },
        setDirtyProvider, refreshUnloadGuard, hasUnsavedData,
        getState: () => ({ active, toolOpen, expanded, resultAvailable, resultOpen, detailView }) };
})();

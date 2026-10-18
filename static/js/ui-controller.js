/** Planner sheet, quick actions, and the explicit search action. */
(function() {
    let initialized = false;

    function sync() {
        const button = document.getElementById('route-submit');
        const label = document.getElementById('route-submit-label');
        const input = document.getElementById('dest-input');
        if (!label || (button && button.disabled)) return;
        label.textContent = destName || (input && input.value.trim()) ? '경로 찾기' : '가까운 정류장 찾기';
    }

    function setPlannerExpanded(expanded) {
        const panel = document.getElementById('planner-panel');
        const body = document.getElementById('planner-body');
        const toggle = document.getElementById('panel-toggle');
        if (panel) panel.classList.toggle('is-collapsed', !expanded);
        if (body) body.hidden = !expanded;
        if (toggle) {
            toggle.setAttribute('aria-expanded', String(expanded));
            toggle.setAttribute('aria-label', expanded ? '검색 패널 접고 지도 크게 보기' : '검색 패널 펼치기');
            const icon = toggle.querySelector('svg use');
            if (icon) icon.setAttribute('href', expanded ? '#icon-chevron-down' : '#icon-chevron-up');
        }
        if (window.AppModules.map) {
            window.AppModules.map.resizeMap();
            setTimeout(function() { window.AppModules.map.resizeMap(); }, 220);
        }
    }

    function togglePlanner() {
        const panel = document.getElementById('planner-panel');
        if (panel) setPlannerExpanded(panel.classList.contains('is-collapsed'));
    }

    function focusDestination() {
        setPlannerExpanded(true);
        if (destName) window.clearDestination({ focus: false });
        const input = document.getElementById('dest-input');
        if (input) input.focus();
    }

    function focusBuildings() {
        setPlannerExpanded(true);
        window.selectOriginMode('building', { focus: false });
        const select = document.getElementById('building-select');
        if (select) select.focus();
    }

    function focusMap() {
        const mobile = typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 799px)').matches;
        if (mobile) setPlannerExpanded(false);
        const ready = window.AppModules.map && window.AppModules.map.isReady();
        const target = document.getElementById(ready ? 'map' : 'map-status');
        if (target) {
            target.tabIndex = -1;
            target.focus({ preventScroll: true });
        }
        if (window.AppModules.map) window.AppModules.map.resizeMap();
    }

    function searchJourney(event) {
        if (event && typeof event.preventDefault === 'function') event.preventDefault();
        const button = document.getElementById('route-submit');
        if (button && button.disabled) return Promise.resolve();
        setPlannerExpanded(true);
        const input = document.getElementById('dest-input');
        if (!destName && input && input.value.trim()) {
            window.AppModules.search.setSearchStatus('검색 결과에서 목적지를 선택해 주세요.', true);
            input.focus();
            return Promise.resolve();
        }
        window.AppModules.search.cancelSearch();
        return window.AppState.getOriginMode() === 'building' ? window.findRoutes() : window.findRoutesByGPS();
    }

    function init() {
        if (initialized) return;
        initialized = true;
        sync();
        const submit = document.getElementById('route-submit');
        if (submit && !submit.hasAttribute('onclick')) submit.addEventListener('click', searchJourney);
        const form = submit && submit.closest('form');
        if (form) form.addEventListener('submit', searchJourney);
    }

    window.AppModules.ui = { init, sync, setPlannerExpanded, togglePlanner,
        focusDestination, focusBuildings, focusMap, searchJourney };
    window.selectOriginMode = window.AppModules.search.selectOriginMode;
    window.searchJourney = searchJourney;
    window.focusDestination = focusDestination;
    window.focusBuildings = focusBuildings;
    window.focusMap = focusMap;
    window.togglePlanner = togglePlanner;
})();

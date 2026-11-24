/** Planner sheet, quick actions, and the explicit search action. */
(function() {
    let initialized = false;
    let localOriginMode = 'gps';
    let positionIntent = null;
    const byId = id => document.getElementById(id);

    function schoolJourney() { return window.AppState.getJourneyScope() === 'national'; }
    function towardCampus() { return schoolJourney() && window.AppState.getJourneyDirection() === 'to_campus'; }

    function updateJourneyForm() {
        const schoolMode = schoolJourney();
        const toCampus = towardCampus();
        const campus = byId('journey-campus-section');
        const place = byId('journey-place-section');
        if (!campus || !place) return;
        if (toCampus && place.nextElementSibling !== campus) campus.before(place);
        if (!toCampus && campus.nextElementSibling !== place) place.before(campus);
        byId('journey-scope-local').setAttribute('aria-pressed', String(!schoolMode));
        byId('journey-scope-national').setAttribute('aria-pressed', String(schoolMode));
        byId('national-from-campus').setAttribute('aria-pressed', String(!toCampus));
        byId('national-to-campus').setAttribute('aria-pressed', String(toCampus));
        for (const id of ['national-direction', 'national-place-tools', 'national-departure-field']) byId(id).hidden = !schoolMode;
        byId('journey-origin-options').hidden = schoolMode;
        byId('journey-campus-label').textContent = toCampus ? '도착 캠퍼스' : '출발 캠퍼스';
        byId('journey-building-label').textContent = toCampus ? '도착 건물' : '출발 건물';
        byId('journey-place-label').textContent = toCampus ? '출발할 장소' : '도착할 장소';
        byId('autocomplete-list').setAttribute('aria-label', toCampus ? '출발지 검색 결과' : '목적지 검색 결과');
        const clearText = toCampus ? '출발지 다시 선택' : '목적지 다시 선택';
        const clearButton = byId('journey-clear-place');
        clearButton.setAttribute('aria-label', clearText);
        clearButton.querySelector('span').textContent = clearText;
    }

    function sync() {
        if (window.AppModules.shell) window.AppModules.shell.syncSelection();
        const button = document.getElementById('route-submit');
        const label = document.getElementById('route-submit-label');
        const input = document.getElementById('dest-input');
        if (!label || (button && button.disabled)) return;
        label.textContent = schoolJourney() ? '통학 경로 찾기'
            : destName || (input && input.value.trim()) ? '경로 찾기' : '가까운 정류장 찾기';
    }

    function resetJourneyInputs() {
        positionIntent = null;
        window.AppModules.search.cancelSearch();
        window.AppModules.api.cancelPendingRequests();
    }

    function setJourneyScope(value) {
        const state = window.AppState;
        if (value === state.getJourneyScope()) return;
        if (value === 'national') localOriginMode = state.getOriginMode();
        state.setJourneyScope(value);
        resetJourneyInputs();
        updateJourneyForm();
        window.AppModules.search.selectOriginMode(schoolJourney() ? 'building' : localOriginMode, { reset: false, focus: false });
        window.AppModules.search.setSearchStatus(schoolJourney() ? '' : '목적지 없이 가까운 정류장만 찾아도 좋아요.');
    }

    function setJourneyDirection(value) {
        const state = window.AppState;
        if (value === state.getJourneyDirection()) return;
        state.setJourneyDirection(value);
        resetJourneyInputs();
        updateJourneyForm();
        window.AppModules.search.selectOriginMode('building', { reset: false, focus: false });
        window.AppModules.search.setSearchStatus('');
    }

    async function choosePosition() {
        resetJourneyInputs();
        const { search, gps, shell } = window.AppModules;
        const intent = {}, navigation = shell?.getNavigation();
        const selection = () => JSON.stringify([window.AppState.getDestination(), byId('dest-input').value]);
        const requestedSelection = selection();
        positionIntent = intent;
        search.setSearchStatus('현재 위치를 확인하고 있어요…');
        const position = await gps.getPosition(true);
        if (positionIntent !== intent || !schoolJourney() || navigation !== shell?.getNavigation() ||
                requestedSelection !== selection()) return;
        positionIntent = null;
        search.selectOriginMode('building', { reset: false, focus: false });
        if (position) search.selectDestination('현재 위치', position.lat, position.lon);
        else search.setSearchStatus('현재 위치를 확인하지 못했어요. 주소를 검색하거나 지도에서 선택해 주세요.', true);
    }

    function chooseMapLocation() {
        resetJourneyInputs();
        const { mapJourney, search, shell } = window.AppModules;
        if (!mapJourney?.armSchoolPlace(towardCampus() ? 'origin' : 'destination', point => {
            shell?.openJourney();
            search.selectDestination(point.name, point.lat, point.lon);
        })) search.setSearchStatus('지도를 연결하지 못했어요. 주소나 장소를 검색해 주세요.', true);
    }

    function searchSchoolJourney() {
        const building = byId('building-select'), input = byId('dest-input');
        const { search, api } = window.AppModules;
        if (!building?.value) {
            building?.setAttribute('aria-invalid', 'true');
            search.setSearchStatus('교내 건물을 선택해 주세요.', true);
            building?.focus();
            return;
        }
        const place = window.AppState.getDestination();
        if (!place.name) {
            search.setSearchStatus(towardCampus() ? '출발할 장소를 선택해 주세요.' : '도착할 장소를 선택해 주세요.', true);
            input?.focus();
            return;
        }
        building.removeAttribute('aria-invalid');
        const school = { name: building.value, campus: window.AppState.getSelectedCampus(), building: building.value };
        const [origin, destination] = towardCampus() ? [place, school] : [school, place];
        const departure = byId('national-departure').value;
        window.AppState.setLastSearchMode('national');
        return api.requestNationalRoute(origin, destination, { departure });
    }

    function setPlannerExpanded(expanded) {
        if (window.AppModules.shell) {
            if (expanded) window.AppModules.shell.openTool();
            else window.AppModules.shell.closeTool();
            return;
        }
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
        if (window.AppModules.shell) window.AppModules.shell.openJourney();
        else setPlannerExpanded(true);
        if (destName) window.clearDestination({ focus: false });
        const input = document.getElementById('dest-input');
        if (input) input.focus();
    }

    function focusBuildings() {
        if (window.AppModules.shell) window.AppModules.shell.openJourney();
        else setPlannerExpanded(true);
        window.selectOriginMode('building', { focus: false });
        const select = document.getElementById('building-select');
        if (select) select.focus();
    }

    function focusMap() {
        if (window.AppModules.shell) window.AppModules.shell.showMap();
        const mobile = typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 799px)').matches;
        if (mobile && !window.AppModules.shell) setPlannerExpanded(false);
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
        if (window.AppModules.shell) window.AppModules.shell.select('journey');
        else setPlannerExpanded(true);
        if (schoolJourney()) {
            window.AppModules.search.cancelSearch();
            return searchSchoolJourney();
        }
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
        updateJourneyForm();
        sync();
        byId('journey-scope-local')?.addEventListener('click', () => setJourneyScope('local'));
        byId('journey-scope-national')?.addEventListener('click', () => setJourneyScope('national'));
        byId('national-from-campus')?.addEventListener('click', () => setJourneyDirection('from_campus'));
        byId('national-to-campus')?.addEventListener('click', () => setJourneyDirection('to_campus'));
        byId('national-current-place')?.addEventListener('click', choosePosition);
        byId('national-map-place')?.addEventListener('click', chooseMapLocation);
        byId('national-departure')?.addEventListener('input', resetJourneyInputs);
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

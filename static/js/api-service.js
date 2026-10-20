/** Route requests, cancellation, and delivery to the independent map/result modules. */
(function() {
    let latestRequest = 0;
    let routeController = null;
    let taxiController = null;
    let taxiVersion = 0;
    let taxiContext = null;

    function setRouteLoading(visible, message) {
        const loading = document.getElementById('loading');
        const text = document.getElementById('loading-text');
        const submit = document.getElementById('route-submit');
        const label = document.getElementById('route-submit-label');
        const results = document.getElementById('results-section');
        if (loading) {
            loading.hidden = !visible;
            loading.style.display = visible ? 'flex' : 'none';
        }
        if (text && visible) text.textContent = message || '정류장과 이동 경로를 확인하고 있어요…';
        if (submit) {
            submit.disabled = visible;
            submit.setAttribute('aria-busy', String(visible));
            submit.classList.toggle('is-loading', visible);
        }
        if (label && visible) label.textContent = '찾는 중…';
        if (results) results.setAttribute('aria-busy', String(visible));
        if (visible) {
            const initial = document.getElementById('initial-state');
            if (initial) initial.hidden = true;
        } else if (window.AppModules.ui) {
            window.AppModules.ui.sync();
        }
    }

    function clearResults() {
        const container = document.getElementById('result-container');
        const initial = document.getElementById('initial-state');
        const count = document.getElementById('result-count');
        const heading = document.getElementById('results-heading');
        if (container) container.replaceChildren();
        if (initial) initial.hidden = false;
        if (count) {
            count.textContent = '';
            count.hidden = true;
        }
        if (heading) heading.textContent = '나의 이동';
        if (window.AppModules.map) window.AppModules.map.clearStationMarkers();
    }

    function cancelPendingRequests(options) {
        options = options || {};
        latestRequest += 1;
        if (routeController) routeController.abort();
        routeController = null;
        taxiVersion += 1;
        if (taxiController) taxiController.abort();
        taxiController = null;
        taxiContext = null;
        if (window.AppModules.taxi) window.AppModules.taxi.reset();
        if (options.cancelLocation !== false && window.AppModules.gps) {
            window.AppModules.gps.cancelPendingLocation();
        }
        setRouteLoading(false);
        if (options.clearResults !== false) clearResults();
    }

    function drawRouteResult(data) {
        try {
            if (window.AppModules.map) window.AppModules.map.drawStationMarkersOnMap(data);
        } catch (error) {
            console.error('지도 결과 표시 실패:', error);
        }
        const renderer = window.renderResults || (window.AppModules.renderer && window.AppModules.renderer.renderResults);
        if (typeof renderer === 'function') {
            renderer(data);
            return;
        }
        const container = document.getElementById('result-container');
        if (container) {
            const message = document.createElement('div');
            message.className = 'result-card status-error';
            message.textContent = data && (data.error || data.notice)
                ? String(data.error || data.notice) : '결과 화면을 불러오지 못했어요. 새로고침 후 다시 시도해 주세요.';
            container.replaceChildren(message);
        }
    }

    function stateSnapshot(mode) {
        const building = document.getElementById('building-select');
        const position = window.AppState.getCurrentPosition();
        return JSON.stringify({
            mode: mode,
            selectedMode: window.AppState.getOriginMode(),
            campus: selectedCampus,
            building: mode === 'building' && building ? building.value : null,
            position: mode === 'gps' && position ? [position.lat, position.lon] : null,
            destination: window.AppState.getDestination()
        });
    }

    async function requestTaxiEstimate(context) {
        if (!context || !context.isCurrent()) return;
        taxiVersion += 1;
        const version = taxiVersion;
        if (taxiController) taxiController.abort();
        const controller = typeof AbortController === 'function' ? new AbortController() : null;
        taxiController = controller;
        const current = function() { return version === taxiVersion && context.isCurrent(); };
        const taxi = window.AppModules.taxi;
        if (taxi) taxi.loading(context);
        let timeout;
        try {
            const response = await Promise.race([
                fetch('/api/taxi/estimate', {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(context.payload),
                    ...(controller ? { signal: controller.signal } : {})
                }),
                new Promise(function(resolve, reject) {
                    timeout = setTimeout(function() {
                        reject(new Error('택시 조회가 늦어지고 있어요. 잠시 후 갱신해 주세요.'));
                        if (controller) controller.abort();
                    }, 15000);
                })
            ]);
            const data = await response.json();
            if (!current()) return;
            if (!response.ok) throw new Error(data && typeof data.error === 'string'
                ? data.error : '택시 정보를 불러오지 못했어요.');
            if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('택시 응답을 확인할 수 없어요.');
            if (taxi) taxi.render(data, context);
        } catch (error) {
            if (!current() || error.name === 'AbortError') return;
            if (taxi) taxi.failed(error instanceof TypeError || error instanceof SyntaxError
                ? '택시 서버에 연결하지 못했어요. 잠시 후 갱신해 주세요.' : error.message, context);
        } finally {
            clearTimeout(timeout);
            if (version === taxiVersion) taxiController = null;
        }
    }

    function refreshTaxi() {
        return requestTaxiEstimate(taxiContext);
    }

    async function requestRoute(payload, mode) {
        cancelPendingRequests();
        const requestId = latestRequest;
        const snapshot = stateSnapshot(mode);
        const controller = typeof AbortController === 'function' ? new AbortController() : null;
        routeController = controller;
        const isCurrent = function() {
            return requestId === latestRequest && snapshot === stateSnapshot(mode);
        };
        if (payload.dest_name && window.AppState.isValidPosition(payload.dest_lat, payload.dest_lon)) {
            taxiContext = { payload: { ...payload }, mode: mode, isCurrent: isCurrent };
            // Independent requests: transit failure and completion do not affect the taxi card.
            requestTaxiEstimate(taxiContext);
        }
        setRouteLoading(true);
        let timeout;
        try {
            const response = await Promise.race([
                fetch('/get_route_info', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                    ...(controller ? { signal: controller.signal } : {})
                }),
                new Promise(function(resolve, reject) {
                    timeout = setTimeout(function() {
                        reject(new Error('경로 조회가 오래 걸리고 있어요. 잠시 후 다시 찾아보세요.'));
                        if (controller) controller.abort();
                    }, 45000);
                })
            ]);
            let data;
            try {
                data = await response.json();
            } catch (error) {
                throw new Error('서버 응답을 읽지 못했어요. 잠시 후 다시 시도해 주세요.');
            }
            if (!isCurrent()) return;
            if (!response.ok && !(data && data.error)) {
                data = { error: '경로를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.' };
            }
            drawRouteResult(data);
        } catch (error) {
            if (!isCurrent() || error.name === 'AbortError') return;
            drawRouteResult({
                error: error instanceof TypeError
                    ? '서버에 연결하지 못했어요. 연결 상태를 확인하고 다시 찾아보세요.'
                    : error.message || '경로를 불러오지 못했어요. 다시 시도해 주세요.'
            });
        } finally {
            clearTimeout(timeout);
            if (requestId === latestRequest) {
                routeController = null;
                setRouteLoading(false);
            }
        }
    }

    function requestBusByCoords(lat, lon) {
        if (!window.AppState.isValidPosition(lat, lon)) {
            cancelPendingRequests();
            drawRouteResult({ notice: '현재 위치를 확인하지 못했어요. 다시 확인하거나 교내 건물을 선택해 주세요.' });
            return Promise.resolve();
        }
        const previous = window.AppState.getCurrentPosition();
        if (!previous || previous.lat !== Number(lat) || previous.lon !== Number(lon)) {
            window.AppState.setCurrentPosition(lat, lon);
        }
        window.AppState.setOriginMode('gps');
        window.AppState.setLastSearchMode('gps');
        const destination = window.AppState.getDestination();
        const status = document.getElementById('origin-status');
        if (status) {
            status.textContent = '현재 위치에서 출발해요.';
            status.classList.remove('status-error');
        }
        return requestRoute({
            lat: Number(lat), lon: Number(lon), location: '내 실시간 위치', campus: selectedCampus,
            dest_lat: destination.lat, dest_lon: destination.lon, dest_name: destination.name
        }, 'gps');
    }

    function findRoutes() {
        if (window.selectOriginMode) window.selectOriginMode('building', { focus: false });
        window.AppState.setOriginMode('building');
        window.AppState.setLastSearchMode('building');
        const select = document.getElementById('building-select');
        const building = select ? select.value : '';
        const status = document.getElementById('origin-status');
        if (!building) {
            cancelPendingRequests();
            if (status) {
                status.textContent = '출발 건물을 먼저 선택해 주세요.';
                status.classList.add('status-error');
            }
            if (select) {
                select.setAttribute('aria-invalid', 'true');
                select.focus();
            }
            return Promise.resolve();
        }
        if (select) select.removeAttribute('aria-invalid');
        if (status) {
            status.textContent = building + '에서 출발해요.';
            status.classList.remove('status-error');
        }
        const destination = window.AppState.getDestination();
        return requestRoute({
            location: building, campus: selectedCampus,
            dest_lat: destination.lat, dest_lon: destination.lon, dest_name: destination.name
        }, 'building');
    }

    window.AppModules.api = { requestBusByCoords, findRoutes, cancelPendingRequests, setRouteLoading, drawRouteResult, refreshTaxi };
    window.requestBusByCoords = requestBusByCoords;
    window.findRoutes = findRoutes;
    window.cancelPendingRequests = cancelPendingRequests;
    window.setRouteLoading = setRouteLoading;
})();


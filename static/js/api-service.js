/** Route requests, cancellation, and delivery to the independent map/result modules. */
(function() {
    let latestRequest = 0;
    let routeController = null;
    let taxiController = null;
    let taxiVersion = 0;
    let taxiContext = null;
    let taxiIntentVersion = 0;
    let taxiPending = false;
    let resultToken = null;

    function beginRouteResults(payload) {
        resultToken = window.AppModules.shell?.beginResults(payload) || null;
        return resultToken;
    }

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
            window.AppModules.renderer?.renderResults({ loading: true, message });
            const initial = document.getElementById('initial-state');
            if (initial) initial.hidden = true;
        } else if (window.AppModules.ui) {
            window.AppModules.ui.sync();
        }
    }

    function clearResults() {
        resultToken = null;
        window.AppModules.shell?.invalidateResults();
        window.AppModules.renderer?.resetResults();
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

    function cancelTaxiRequests() {
        taxiVersion += 1;
        taxiIntentVersion += 1;
        if (taxiController) taxiController.abort();
        taxiController = null;
        taxiContext = null;
        taxiPending = false;
        if (window.AppModules.taxi) window.AppModules.taxi.reset();
    }

    function cancelPendingRequests(options) {
        options = options || {};
        latestRequest += 1;
        if (routeController) routeController.abort();
        routeController = null;
        if (options.resetTaxi !== false) cancelTaxiRequests();
        if (options.cancelLocation !== false && window.AppModules.gps) {
            window.AppModules.gps.cancelPendingLocation();
        }
        setRouteLoading(false);
        if (options.clearResults !== false) clearResults();
    }

    function drawRouteResult(data, token = resultToken) {
        const present = !window.AppModules.shell || window.AppModules.shell.canPresent(token);
        try {
            if (present && window.AppModules.map && !(data && Array.isArray(data.journeys))) window.AppModules.map.drawStationMarkersOnMap(data);
        } catch (error) {
            console.error('지도 결과 표시 실패:', error);
        }
        const renderer = window.renderResults || (window.AppModules.renderer && window.AppModules.renderer.renderResults);
        if (typeof renderer === 'function') {
            renderer(data, { present });
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

    function stateSnapshot(mode, ignorePosition) {
        const building = document.getElementById('building-select');
        const position = window.AppState.getCurrentPosition();
        return JSON.stringify({
            mode: mode,
            selectedMode: window.AppState.getOriginMode(),
            campus: selectedCampus,
            building: mode === 'building' && building ? building.value : null,
            position: !ignorePosition && mode === 'gps' && position ? [position.lat, position.lon] : null,
            destination: window.AppState.getDestination()
        });
    }

    async function requestTaxiEstimate(context) {
        if (!window.AppModules.taxi || !context || !context.isCurrent()) return;
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
            const result = await Promise.race([
                fetch('/api/taxi/estimate', {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(context.payload),
                    ...(controller ? { signal: controller.signal } : {})
                }).then(async function(response) {
                    return { response: response, data: await response.json() };
                }),
                new Promise(function(resolve, reject) {
                    timeout = setTimeout(function() {
                        reject(new Error('택시 조회가 늦어지고 있어요. 잠시 후 갱신해 주세요.'));
                        if (controller) controller.abort();
                    }, 15000);
                })
            ]);
            const response = result.response, data = result.data;
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

    async function requestTaxiFromSelection() {
        const taxi = window.AppModules.taxi;
        if (!taxi) return;
        if (taxiPending && taxiContext && taxiContext.isCurrent()) return;
        cancelTaxiRequests();
        const intent = taxiIntentVersion;
        taxiPending = true;
        const mode = window.AppState.getOriginMode();
        const selection = stateSnapshot(mode, true);
        const intentIsCurrent = function() {
            return intent === taxiIntentVersion && selection === stateSnapshot(mode, true);
        };
        try {
            const destination = window.AppState.getDestination();
            if (!destination || typeof destination.name !== 'string' || !destination.name.trim()
                || !window.AppState.isValidPosition(destination.lat, destination.lon)) {
                taxi.prompt('길찾기에서 도착할 장소를 검색하고 검색 결과를 선택해 주세요.');
                if (window.AppModules.search && typeof window.AppModules.search.setSearchStatus === 'function') {
                    window.AppModules.search.setSearchStatus('검색 결과에서 목적지를 선택해 주세요.', true);
                }
                const input = document.getElementById('dest-input');
                if (window.AppModules.shell) window.AppModules.shell.openJourney('dest-input');
                else if (input) input.focus();
                return;
            }
            const payload = { campus: selectedCampus, dest_name: destination.name,
                dest_lat: Number(destination.lat), dest_lon: Number(destination.lon) };
            if (mode === 'building') {
                const select = document.getElementById('building-select');
                const building = select ? select.value : '';
                if (!building) {
                    taxi.prompt('길찾기에서 출발 건물을 먼저 선택해 주세요.');
                    if (select) select.setAttribute('aria-invalid', 'true');
                    if (window.AppModules.shell) window.AppModules.shell.openJourney('building-select');
                    else if (select) select.focus();
                    return;
                }
                if (select) select.removeAttribute('aria-invalid');
                payload.location = building;
                // The server resolves campus building coordinates; no placeholder lat/lon is sent.
            } else if (mode === 'gps') {
                payload.location = '내 실시간 위치';
                taxiContext = { payload: payload, mode: mode, isCurrent: intentIsCurrent };
                const gps = window.AppModules.gps;
                if (!gps || typeof gps.getPosition !== 'function') {
                    taxi.failed('현재 위치를 확인할 수 없어요. 교내 건물을 선택해 주세요.', taxiContext);
                    return;
                }
                taxi.loading(taxiContext, '현재 위치를 확인하고 있어요…');
                // This method is reached only from the user's estimate/refresh button.
                const position = await gps.getPosition(false);
                if (!intentIsCurrent()) return;
                if (!position || !window.AppState.isValidPosition(position.lat, position.lon)) {
                    taxi.failed('현재 위치를 확인하지 못했어요. 위치 권한을 확인하거나 교내 건물을 선택해 주세요.', taxiContext);
                    return;
                }
                payload.lat = Number(position.lat);
                payload.lon = Number(position.lon);
            } else {
                taxi.prompt('길찾기에서 현재 위치 또는 출발 건물을 선택해 주세요.');
                return;
            }
            if (!intentIsCurrent()) return;
            const snapshot = stateSnapshot(mode);
            taxiContext = { payload: payload, mode: mode, isCurrent: function() {
                return intent === taxiIntentVersion && snapshot === stateSnapshot(mode);
            } };
            return await requestTaxiEstimate(taxiContext);
        } catch (error) {
            if (!intentIsCurrent() || error.name === 'AbortError') return;
            taxi.failed('택시 조회를 시작하지 못했어요. 출발지와 목적지를 확인한 뒤 다시 조회해 주세요.', taxiContext);
        } finally {
            if (intent === taxiIntentVersion) taxiPending = false;
        }
    }

    function refreshTaxi() {
        return requestTaxiFromSelection();
    }

    async function requestRoute(payload, mode, pendingToken) {
        cancelPendingRequests({ resetTaxi: !(taxiContext && taxiContext.isCurrent()), clearResults: !pendingToken, cancelLocation: !pendingToken });
        const token = pendingToken || beginRouteResults(payload);
        resultToken = token;
        window.AppModules.shell?.updateResultSummary(token, payload);
        const requestId = latestRequest;
        const snapshot = stateSnapshot(mode);
        const controller = typeof AbortController === 'function' ? new AbortController() : null;
        routeController = controller;
        const isCurrent = function() {
            return requestId === latestRequest && snapshot === stateSnapshot(mode);
        };
        // A transit search does not make a separate taxi provider request.
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
            if (!data || (typeof data !== 'object') || (!Array.isArray(data) && !data.error && !data.notice && !Array.isArray(data.journeys))) {
                throw new Error('서버 응답 형식을 확인할 수 없어요. 잠시 후 다시 조회해 주세요.');
            }
            if (!response.ok && !(data && data.error)) {
                data = { error: '경로를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.' };
            }
            drawRouteResult(data, token);
        } catch (error) {
            if (!isCurrent() || error.name === 'AbortError') return;
            drawRouteResult({
                error: error instanceof TypeError
                    ? '서버에 연결하지 못했어요. 연결 상태를 확인하고 다시 찾아보세요.'
                    : error.message || '경로를 불러오지 못했어요. 다시 시도해 주세요.'
            }, token);
        } finally {
            clearTimeout(timeout);
            if (requestId === latestRequest) {
                routeController = null;
                setRouteLoading(false);
            }
        }
    }

    function requestBusByCoords(lat, lon, pendingToken) {
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
        }, 'gps', pendingToken);
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

    window.AppModules.api = { requestBusByCoords, findRoutes, cancelPendingRequests, setRouteLoading, drawRouteResult,
        requestTaxiFromSelection, refreshTaxi, beginRouteResults };
    window.requestBusByCoords = requestBusByCoords;
    window.findRoutes = findRoutes;
    window.cancelPendingRequests = cancelPendingRequests;
    window.setRouteLoading = setRouteLoading;
})();


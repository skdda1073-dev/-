/** Explicit, cancellable one-shot location lookup. Loading the page never starts GPS. */
(function() {
    let locationVersion = 0;
    let pendingResolve = null;

    function setLocationStatus(message, failed) {
        const status = document.getElementById('origin-status');
        if (status) {
            status.textContent = message;
            status.classList.toggle('status-error', Boolean(failed));
        }
        const announcement = document.getElementById('app-status');
        if (announcement) announcement.textContent = message;
    }

    function cancelPendingLocation() {
        locationVersion += 1;
        if (pendingResolve) pendingResolve(null);
        pendingResolve = null;
    }

    function locate(force) {
        cancelPendingLocation();
        const version = locationVersion;
        const cached = window.AppState.getCurrentPosition();
        if (!force && cached && Date.now() - cached.capturedAt < 60000) {
            return Promise.resolve(cached);
        }
        if (!navigator.geolocation || window.isSecureContext === false) {
            setLocationStatus('현재 위치를 사용할 수 없는 환경이에요. 교내 건물을 선택해 주세요.', true);
            return Promise.resolve(null);
        }

        return new Promise(function(resolve) {
            pendingResolve = resolve;
            function finish(position) {
                if (version !== locationVersion) return;
                pendingResolve = null;
                resolve(position);
            }
            function onError(error) {
                if (version !== locationVersion) return;
                const messages = {
                    1: '위치 권한이 꺼져 있어요. 브라우저 주소창의 위치 권한을 허용하거나 교내 건물을 선택해 주세요.',
                    2: '현재 위치 신호를 찾지 못했어요. 다시 시도하거나 교내 건물을 선택해 주세요.',
                    3: '위치 확인 시간이 초과됐어요. 다시 시도하거나 교내 건물을 선택해 주세요.'
                };
                setLocationStatus(messages[error && error.code] || '현재 위치를 확인하지 못했어요. 교내 건물을 선택해 주세요.', true);
                finish(null);
            }
            try {
                navigator.geolocation.getCurrentPosition(function(position) {
                    if (version !== locationVersion) return;
                    const coords = position && position.coords;
                    if (!coords || !window.AppState.setCurrentPosition(coords.latitude, coords.longitude)) {
                        onError({ code: 2 });
                        return;
                    }
                    setLocationStatus('현재 위치에서 출발해요.', false);
                    finish(window.AppState.getCurrentPosition());
                }, onError, { enableHighAccuracy: false, timeout: 10000, maximumAge: force ? 0 : 60000 });
            } catch (error) {
                onError(error);
            }
        });
    }

    function beginLocation() {
        if (window.selectOriginMode) window.selectOriginMode('gps', { focus: false });
        window.AppModules.api.cancelPendingRequests();
        setLocationStatus('현재 위치를 확인하고 있어요…', false);
        window.AppModules.api.setRouteLoading(true, '현재 위치를 확인하고 있어요…');
    }

    async function findRoutesByGPS() {
        beginLocation();
        const lookup = locate(false);
        const version = locationVersion;
        const position = await lookup;
        if (version !== locationVersion) return;
        if (!position) {
            window.AppModules.api.setRouteLoading(false);
            const initial = document.getElementById('initial-state');
            if (initial) initial.hidden = false;
            return;
        }
        if (window.AppModules.map) window.AppModules.map.recenterPosition(position.lat, position.lon);
        return window.requestBusByCoords(position.lat, position.lon);
    }

    async function findMyLocation() {
        beginLocation();
        const lookup = locate(true);
        const version = locationVersion;
        const position = await lookup;
        if (version !== locationVersion) return;
        window.AppModules.api.setRouteLoading(false);
        const initial = document.getElementById('initial-state');
        if (initial) initial.hidden = false;
        if (!position) return;
        if (window.AppModules.map) window.AppModules.map.recenterPosition(position.lat, position.lon, true);
    }

    window.AppModules.gps = { findMyLocation, findRoutesByGPS, cancelPendingLocation };
    window.findMyLocation = findMyLocation;
    window.findRoutesByGPS = findRoutesByGPS;
})();


/**
 * api-service.js
 * 경로 요청, 최신 요청 보장, 지도·결과 화면 전달을 담당합니다.
 */

let latestRouteRequest = 0;
let routeAbortController = null;

function setRouteLoading(visible) {
    const loading = document.getElementById('loading');
    if (!loading) return;
    loading.style.display = visible ? 'block' : 'none';
    if (visible) loading.textContent = '🚌 정류장 순서와 경로를 확인하고 있습니다...';
}

function drawRouteResult(data) {
    // 지도 SDK가 실패해도 결과 카드와 오류 안내는 표시합니다.
    try {
        const map = window.AppModules.map;
        if (map && typeof map.drawStationMarkersOnMap === 'function') map.drawStationMarkersOnMap(data);
    } catch (error) {
        console.error('지도 결과 표시 실패:', error);
    }
    window.AppModules.renderer.renderResults(data);
}

function cancelPendingRequests(options = {}) {
    latestRouteRequest += 1;
    if (routeAbortController) routeAbortController.abort();
    routeAbortController = null;
    setRouteLoading(false);
    if (options.clearResults !== false) {
        const container = document.getElementById('result-container');
        if (container) container.replaceChildren();
        try {
            if (window.AppModules.map) window.AppModules.map.clearStationMarkers();
        } catch (error) {
            console.error('이전 지도 결과 제거 실패:', error);
        }
    }
}

function routeStateSnapshot(mode) {
    const destination = window.AppState.getDestination();
    const building = document.getElementById('building-select');
    return JSON.stringify({
        mode: window.AppState.getLastSearchMode(),
        campus: window.AppState.getSelectedCampus(),
        building: mode === 'building' && building ? building.value : null,
        // GPS 감시의 미세한 위치 갱신은 진행 중 조회를 무효화하지 않습니다.
        destination: { name: destination.name, lat: destination.lat, lon: destination.lon }
    });
}

async function requestRoute(payload, mode) {
    cancelPendingRequests();
    const requestId = latestRouteRequest;
    const snapshot = routeStateSnapshot(mode);
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    routeAbortController = controller;
    setRouteLoading(true);
    const container = document.getElementById('result-container');
    if (container) container.replaceChildren();

    const isCurrent = () => requestId === latestRouteRequest && snapshot === routeStateSnapshot(mode);
    try {
        const response = await fetch('/get_route_info', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            ...(controller ? { signal: controller.signal } : {})
        });
        let data;
        try {
            data = await response.json();
        } catch (error) {
            throw new Error('서버의 경로 응답을 읽을 수 없습니다. 잠시 후 다시 조회해 주세요.');
        }
        if (!isCurrent()) return;
        if (!response.ok && !(data && data.error)) {
            data = { error: `경로 조회에 실패했습니다. (HTTP ${response.status})` };
        }
        drawRouteResult(data);
    } catch (error) {
        if (!isCurrent() || error.name === 'AbortError') return;
        console.error('get_route_info 에러:', error);
        drawRouteResult({ error: error.message || '데이터 통신에 실패했습니다. 잠시 후 다시 조회해 주세요.' });
    } finally {
        if (requestId === latestRouteRequest) {
            routeAbortController = null;
            setRouteLoading(false);
        }
    }
}

function requestBusByCoords(lat, lon) {
    window.AppState.setLastSearchMode('gps');
    window.AppState.setCurrentPosition(lat, lon);
    const destination = window.AppState.getDestination();
    return requestRoute({
        lat: lat,
        lon: lon,
        location: '내 실시간 위치',
        dest_lat: destination.lat,
        dest_lon: destination.lon,
        dest_name: destination.name
    }, 'gps');
}

function findRoutes() {
    const buildingSelect = document.getElementById('building-select');
    const building = buildingSelect ? buildingSelect.value : '';
    if (!building) {
        cancelPendingRequests({ clearResults: true });
        drawRouteResult({ notice: '출발 건물을 먼저 선택해 주세요.' });
        return;
    }
    window.AppState.setLastSearchMode('building');
    const destination = window.AppState.getDestination();
    return requestRoute({
        location: building,
        campus: window.AppState.getSelectedCampus(),
        dest_lat: destination.lat,
        dest_lon: destination.lon,
        dest_name: destination.name
    }, 'building');
}

window.AppModules.api = {
    requestBusByCoords: requestBusByCoords,
    findRoutes: findRoutes,
    cancelPendingRequests: cancelPendingRequests
};
window.requestBusByCoords = requestBusByCoords;
window.findRoutes = findRoutes;


/**
 * api-service.js
 * 백엔드 라우트 정보 및 버스 실시간 데이터 비동기 요청 모듈
 */

function requestBusByCoords(lat, lon) {
    window.AppState.setLastSearchMode('gps');
    window.AppState.setCurrentPosition(lat, lon);
    
    const loadingEl = document.getElementById('loading');
    const destination = window.AppState.getDestination();
    loadingEl.style.display = 'block';
    loadingEl.innerText = '🚌 정보를 불러오고 있습니다...';
    
    fetch('/get_route_info', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            lat: lat,
            lon: lon,
            location: '내 실시간 위치',
            dest_lat: destination.lat,
            dest_lon: destination.lon,
            dest_name: destination.name
        })
    })
    .then(response => response.json())
    .then(data => {
        if (Array.isArray(data)) {
            window.AppModules.map.drawStationMarkersOnMap(data);
        }
        window.AppModules.renderer.renderResults(data);
    })
    .catch(error => {
        console.error('get_route_info 에러:', error);
        loadingEl.style.display = 'none';
        alert('데이터 통신에 실패했습니다.');
    });
}

function findRoutes() {
    const buildingSelectEl = document.getElementById('building-select');
    const building = buildingSelectEl ? buildingSelectEl.value : '';
    if (!building) {
        alert('출발 건물을 먼저 선택해주세요!');
        return;
    }

    window.AppState.setLastSearchMode('building');
    const loadingEl = document.getElementById('loading');
    const resultContainer = document.getElementById('result-container');
    const destination = window.AppState.getDestination();

    loadingEl.style.display = 'block';
    loadingEl.innerText = '🚌 정보를 불러오고 있습니다...';
    resultContainer.innerHTML = '';

    fetch('/get_route_info', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            location: building,
            campus: window.AppState.getSelectedCampus(),
            dest_lat: destination.lat,
            dest_lon: destination.lon,
            dest_name: destination.name
        })
    })
    .then(response => response.json())
    .then(data => {
        if (Array.isArray(data)) {
            window.AppModules.map.drawStationMarkersOnMap(data);
        }
        window.AppModules.renderer.renderResults(data);
    })
    .catch(error => {
        console.error('get_route_info 에러:', error);
        loadingEl.style.display = 'none';
        alert('데이터 통신에 실패했습니다.');
    });
}

window.AppModules.api = {
    requestBusByCoords: requestBusByCoords,
    findRoutes: findRoutes
};

// index.html의 기존 인라인 호출과의 호환성을 유지합니다.
window.requestBusByCoords = requestBusByCoords;
window.findRoutes = findRoutes;


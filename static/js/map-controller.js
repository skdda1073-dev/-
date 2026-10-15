/**
 * map-controller.js
 * 네이버 지도와 주변 정류장·경로 후보 마커를 표시합니다.
 */

let mapInstance = null;
let startMarker = null;
let stationMarkers = [];
let journeyLines = [];

function mapSdkAvailable() {
    return Boolean(window.naver && window.naver.maps && typeof window.naver.maps.LatLng === 'function');
}

function mapCoordinate(point) {
    if (!point || point.lat === null || point.lon === null || point.lat === undefined || point.lon === undefined) return null;
    const lat = Number(point.lat);
    const lon = Number(point.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
    return new window.naver.maps.LatLng(lat, lon);
}

function mapSafeText(value) {
    return String(value ?? '').replace(/[&<>"']/g, character => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[character]));
}

function initMap() {
    if (!mapSdkAvailable() || typeof window.naver.maps.Map !== 'function') {
        console.error('네이버 지도 SDK를 불러오지 못했습니다. 경로 결과는 계속 조회할 수 있습니다.');
        const mapElement = document.getElementById('map');
        if (mapElement) mapElement.textContent = '지도를 불러오지 못했습니다. 아래 경로 안내는 이용할 수 있습니다.';
        return;
    }
    try {
        const center = window.AppState.getCampusCenter(window.AppState.getSelectedCampus());
        mapInstance = new window.naver.maps.Map('map', { center: mapCoordinate(center), zoom: 16 });
        startMarker = new window.naver.maps.Marker({ position: mapCoordinate(center), map: mapInstance });
    } catch (error) {
        mapInstance = null;
        startMarker = null;
        console.error('네이버 지도 초기화 실패:', error);
        const mapElement = document.getElementById('map');
        if (mapElement) mapElement.textContent = '지도를 불러오지 못했습니다. 아래 경로 안내는 이용할 수 있습니다.';
    }
}

function panToCampus(campus) {
    clearStationMarkers();
    if (!mapInstance || !startMarker || !mapSdkAvailable()) return;
    const position = mapCoordinate(window.AppState.getCampusCenter(campus));
    if (!position) return;
    mapInstance.panTo(position);
    startMarker.setPosition(position);
}

function clearStationMarkers() {
    stationMarkers.forEach(marker => marker.setMap(null));
    journeyLines.forEach(line => line.setMap(null));
    stationMarkers = [];
    journeyLines = [];
}

function addRouteMapMarker(point, label, color, bounds) {
    const position = mapCoordinate(point);
    if (!position) return;
    bounds.extend(position);
    const marker = new window.naver.maps.Marker({
        position: position,
        map: mapInstance,
        title: label,
        icon: {
            content: `<div style="background:${color};color:white;padding:6px 10px;border-radius:14px;font-size:12px;font-weight:800;box-shadow:0 3px 8px rgba(0,0,0,0.25);white-space:nowrap;border:2px solid white;">${mapSafeText(label)}</div>`,
            anchor: new window.naver.maps.Point(40, 15)
        }
    });
    stationMarkers.push(marker);
}

function drawStationMarkersOnMap(data) {
    clearStationMarkers();
    if (!mapInstance || !mapSdkAvailable()) return;
    const bounds = new window.naver.maps.LatLngBounds();
    let hasPoints = false;
    const includeOrigin = point => {
        const position = mapCoordinate(point);
        if (!position) return;
        if (startMarker) startMarker.setPosition(position);
        bounds.extend(position);
        hasPoints = true;
    };

    if (data && !Array.isArray(data)) {
        includeOrigin(data.origin);
        if (mapCoordinate(data.destination)) {
            addRouteMapMarker(data.destination, `🎯 ${data.destination.name || '목적지'}`, '#805AD5', bounds);
            hasPoints = true;
        }
        const journey = !data.error && data.mode === 'journeys' && Array.isArray(data.journeys) ? data.journeys[0] : null;
        const legs = journey && Array.isArray(journey.legs) ? journey.legs : [];
        legs.forEach((leg, index) => {
            if (mapCoordinate(leg.board)) {
                addRouteMapMarker(leg.board, `${index === 0 ? '승차' : '환승 승차'}: ${leg.board.name || ''} · ${leg.route_no || ''}번`, '#005AAB', bounds);
                hasPoints = true;
            }
            if (mapCoordinate(leg.alight)) {
                addRouteMapMarker(leg.alight, `${index === legs.length - 1 ? '하차' : '환승 하차'}: ${leg.alight.name || ''}`, '#03A34A', bounds);
                hasPoints = true;
            }
            const path = Array.isArray(leg.path) ? leg.path.map(mapCoordinate).filter(Boolean) : [];
            path.forEach(position => bounds.extend(position));
            if (path.length > 1 && typeof window.naver.maps.Polyline === 'function') {
                journeyLines.push(new window.naver.maps.Polyline({
                    map: mapInstance,
                    path: path,
                    strokeColor: index === 0 ? '#005AAB' : '#805AD5',
                    strokeOpacity: 0.7,
                    strokeWeight: 4,
                    strokeStyle: 'shortdash'
                }));
                // 정류장 좌표를 잇는 선이며 실제 도로 주행 궤적은 아닙니다.
                const middle = leg.path[Math.floor(leg.path.length / 2)];
                addRouteMapMarker(middle, `${leg.route_no || ''}번 정류장 연결선`, '#4A5568', bounds);
            }
        });
    } else if (Array.isArray(data)) {
        if (data[0]) includeOrigin({ lat: data[0].target_lat, lon: data[0].target_lon });
        data.forEach(station => {
            const point = { lat: station.station_lat, lon: station.station_lon };
            if (!mapCoordinate(point)) return;
            const walk = typeof station.walk_min === 'number' && Number.isFinite(station.walk_min) ? ` · 도보 약 ${Math.ceil(station.walk_min)}분` : '';
            addRouteMapMarker(point, `🚏 ${station.station_name || '정류장'}${walk}`, '#03A34A', bounds);
            hasPoints = true;
        });
    }
    if (hasPoints) mapInstance.fitBounds(bounds, { top: 45, right: 45, bottom: 45, left: 45 });
}

window.AppModules.map = {
    initMap: initMap,
    panToCampus: panToCampus,
    clearStationMarkers: clearStationMarkers,
    drawStationMarkersOnMap: drawStationMarkersOnMap
};
window.initMap = initMap;
window.panToCampus = panToCampus;
window.clearStationMarkers = clearStationMarkers;
window.drawStationMarkersOnMap = drawStationMarkersOnMap;


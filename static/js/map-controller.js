/**
 * map-controller.js
 * 네이버 지도 초기화 및 마커 렌더링 모듈
 */

let mapInstance = null;
let startMarker = null;
let stationMarkers = [];

function initMap() {
    const center = window.AppState.getCampusCenter(window.AppState.getSelectedCampus());
    mapInstance = new naver.maps.Map('map', {
        center: new naver.maps.LatLng(center.lat, center.lon),
        zoom: 16
    });

    startMarker = new naver.maps.Marker({
        position: new naver.maps.LatLng(center.lat, center.lon),
        map: mapInstance
    });
}

function panToCampus(campus) {
    if (!mapInstance || !startMarker) return;
    const center = window.AppState.getCampusCenter(campus);
    const newLoc = new naver.maps.LatLng(center.lat, center.lon);
    mapInstance.panTo(newLoc);
    startMarker.setPosition(newLoc);
    clearStationMarkers();
}

function clearStationMarkers() {
    stationMarkers.forEach(m => m.setMap(null));
    stationMarkers = [];
}

function drawStationMarkersOnMap(stations) {
    clearStationMarkers();
    if (!Array.isArray(stations) || stations.length === 0) return;

    const bounds = new naver.maps.LatLngBounds();
    if (stations[0].target_lat) {
        const startPos = new naver.maps.LatLng(stations[0].target_lat, stations[0].target_lon);
        startMarker.setPosition(startPos);
        bounds.extend(startPos);
    }

    // ★ 목적지가 선택된 경우: 기존 주변 정류장 마커를 지우고, 가야 할 추천 정류장만 지도에 표시
    let targetStations = stations;
    const destination = window.AppState.getDestination();
    if (destination.name) {
        const recList = stations.filter(s => s.has_recommended_bus);
        if (recList.length > 0) {
            targetStations = [recList[0]];
        }
    }

    targetStations.forEach((station) => {
        if (!station.station_lat || !station.station_lon) return;
        const pos = new naver.maps.LatLng(station.station_lat, station.station_lon);
        bounds.extend(pos);

        const isDest = Boolean(destination.name);
        const bgColor = isDest ? '#005AAB' : '#03c75a';
        const badgePrefix = isDest ? '🎯 승차정류장:' : '🚏';

        const marker = new naver.maps.Marker({
            position: pos,
            map: mapInstance,
            icon: {
                content: `<div style="background:${bgColor}; color:white; padding:6px 10px; border-radius:14px; font-size:12px; font-weight:800; box-shadow:0 3px 8px rgba(0,0,0,0.25); white-space:nowrap; border:2px solid white;">
                            ${badgePrefix} ${station.station_name} (도보 ${station.walk_min}분)
                          </div>`,
                anchor: new naver.maps.Point(40, 15)
            }
        });
        stationMarkers.push(marker);
    });

    mapInstance.fitBounds(bounds, { top: 40, right: 40, bottom: 40, left: 40 });
}

window.AppModules.map = {
    initMap: initMap,
    panToCampus: panToCampus,
    clearStationMarkers: clearStationMarkers,
    drawStationMarkersOnMap: drawStationMarkersOnMap
};

// index.html의 기존 전역 호출과의 호환성을 유지합니다.
window.initMap = initMap;
window.panToCampus = panToCampus;
window.clearStationMarkers = clearStationMarkers;
window.drawStationMarkersOnMap = drawStationMarkersOnMap;


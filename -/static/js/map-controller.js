/**
 * map-controller.js
 * 네이버 지도 초기화 및 마커 렌더링 모듈
 */

function initMap() {
    const center = campusCenter[selectedCampus] || campusCenter.hallym;
    map = new naver.maps.Map('map', {
        center: new naver.maps.LatLng(center.lat, center.lon),
        zoom: 16
    });

    startMarker = new naver.maps.Marker({
        position: new naver.maps.LatLng(center.lat, center.lon),
        map: map
    });
}

function panToCampus(campus) {
    if (!map || !startMarker) return;
    const center = campusCenter[campus];
    const newLoc = new naver.maps.LatLng(center.lat, center.lon);
    map.panTo(newLoc);
    startMarker.setPosition(newLoc);
    clearStationMarkers();
}

function clearStationMarkers() {
    if (!stationMarkers) return;
    stationMarkers.forEach(m => m.setMap(null));
    stationMarkers = [];
}

function drawStationMarkersOnMap(stations) {
    clearStationMarkers();
    if (!stations || stations.length === 0) return;

    const bounds = new naver.maps.LatLngBounds();
    if (stations[0].target_lat) {
        const startPos = new naver.maps.LatLng(stations[0].target_lat, stations[0].target_lon);
        startMarker.setPosition(startPos);
        bounds.extend(startPos);
    }

    // ★ 목적지가 선택된 경우: 기존 주변 정류장 마커를 지우고, 가야 할 추천 정류장만 지도에 표시
    let targetStations = stations;
    if (destName) {
        const recList = stations.filter(s => s.has_recommended_bus);
        if (recList.length > 0) {
            targetStations = [recList[0]];
        }
    }

    targetStations.forEach((station) => {
        if (!station.station_lat || !station.station_lon) return;
        const pos = new naver.maps.LatLng(station.station_lat, station.station_lon);
        bounds.extend(pos);

        const isDest = Boolean(destName);
        const bgColor = isDest ? '#005AAB' : '#03c75a';
        const badgePrefix = isDest ? '🎯 승차정류장:' : '🚏';

        const marker = new naver.maps.Marker({
            position: pos,
            map: map,
            icon: {
                content: `<div style="background:${bgColor}; color:white; padding:6px 10px; border-radius:14px; font-size:12px; font-weight:800; box-shadow:0 3px 8px rgba(0,0,0,0.25); white-space:nowrap; border:2px solid white;">
                            ${badgePrefix} ${station.station_name} (도보 ${station.walk_min}분)
                          </div>`,
                anchor: new naver.maps.Point(40, 15)
            }
        });
        stationMarkers.push(marker);
    });

    map.fitBounds(bounds, { top: 40, right: 40, bottom: 40, left: 40 });
}

window.initMap = initMap;
window.panToCampus = panToCampus;
window.clearStationMarkers = clearStationMarkers;
window.drawStationMarkersOnMap = drawStationMarkersOnMap;


/** Real NAVER map markers. Stop coordinates are never presented as a fabricated road route. */
(function() {
    let mapReady = false;
    let authenticationFailed = false;
    let readinessTimer = null;
    let sdkRequested = false;
    let latestEntries = [];

    function announce(message) {
        const status = document.getElementById('app-status');
        if (status) status.textContent = message;
    }

    function setMapStatus(message, visible) {
        const status = document.getElementById('map-status');
        if (status) {
            const text = status.querySelector('[data-map-status-text]') || status.querySelector('p');
            if (text && message) text.textContent = message;
            else if (message) status.textContent = message;
            status.hidden = !visible;
            status.style.display = visible ? '' : 'none';
        }
        const element = document.getElementById('map');
        if (element) element.classList.toggle('is-ready', !visible);
    }

    function markReady() {
        if (authenticationFailed) return;
        mapReady = true;
        clearTimeout(readinessTimer);
        setMapStatus('', false);
    }

    const previousAuthFailure = window.navermap_authFailure;
    window.navermap_authFailure = function() {
        authenticationFailed = true;
        mapReady = false;
        clearTimeout(readinessTimer);
        setMapStatus('지도를 연결하지 못했어요. 검색 결과의 정류장과 이동 안내를 확인해 주세요.', true);
        if (typeof previousAuthFailure === 'function') previousAuthFailure();
    };

    function loadMapSDK(element) {
        const source = element.getAttribute('data-sdk-src');
        if (sdkRequested || !source) return;
        sdkRequested = true;
        const script = document.createElement('script');
        script.id = 'naver-map-sdk';
        script.async = true;
        script.src = source;
        // The NAVER callback and this load fallback may both run; initMap is idempotent.
        script.addEventListener('load', initMap, { once: true });
        script.addEventListener('error', function() {
            setMapStatus('지도를 불러오지 못했어요. 출발지와 목적지를 검색해 이동 안내를 확인해 주세요.', true);
        }, { once: true });
        document.head.appendChild(script);
    }

    function initMap() {
        if (map) return !authenticationFailed;
        const element = document.getElementById('map');
        if (!element) return false;
        if (authenticationFailed || !window.naver || !window.naver.maps || typeof window.naver.maps.Map !== 'function') {
            setMapStatus('지도 연결을 기다리고 있어요. 출발지와 목적지를 선택해 이동 정보를 확인해 보세요.', true);
            if (!authenticationFailed) loadMapSDK(element);
            return false;
        }
        try {
            const center = campusCenter[selectedCampus] || campusCenter.hallym;
            map = new naver.maps.Map(element, {
                center: new naver.maps.LatLng(center.lat, center.lon),
                zoom: 15,
                zoomControl: false
            });
            if (naver.maps.Event && typeof naver.maps.Event.addListener === 'function') {
                naver.maps.Event.addListener(map, 'tilesloaded', markReady);
                readinessTimer = setTimeout(function() {
                    if (!mapReady) setMapStatus('지도를 불러오는 데 시간이 걸리고 있어요. 검색 결과는 계속 확인할 수 있어요.', true);
                }, 12000);
            } else {
                markReady();
            }
            window.addEventListener('resize', resizeMap);
            if (latestEntries.length) drawEntries(latestEntries);
            return true;
        } catch (error) {
            clearTimeout(readinessTimer);
            map = null;
            mapReady = false;
            setMapStatus('지도를 불러오지 못했어요. 검색 결과의 정류장과 이동 안내를 확인해 주세요.', true);
            console.error('지도 초기화 실패:', error);
            return false;
        }
    }

    function removeStationMarkers() {
        stationMarkers.forEach(function(marker) {
            if (marker && typeof marker.setMap === 'function') marker.setMap(null);
        });
        stationMarkers = [];
        startMarker = null;
    }

    function clearStationMarkers() {
        latestEntries = [];
        removeStationMarkers();
    }

    function markerEntry(lat, lon, label, kind) {
        if (!window.AppState.isValidPosition(lat, lon)) return null;
        return { lat: Number(lat), lon: Number(lon), label: String(label || '정류장'), kind: kind || 'station' };
    }

    function drawEntries(entries) {
        const valid = entries.filter(Boolean);
        latestEntries = valid;
        if (!map || authenticationFailed) return false;
        removeStationMarkers();
        if (!valid.length) return false;
        const bounds = new naver.maps.LatLngBounds();
        valid.forEach(function(entry) {
            const position = new naver.maps.LatLng(entry.lat, entry.lon);
            const label = document.createElement('div');
            label.className = 'station-marker station-marker--' + entry.kind;
            label.setAttribute('title', entry.label);
            label.setAttribute('aria-label', entry.label);
            const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            icon.setAttribute('class', 'icon');
            icon.setAttribute('aria-hidden', 'true');
            const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
            use.setAttribute('href', entry.kind === 'origin' ? '#icon-location'
                : entry.kind === 'destination' ? '#icon-pin' : '#icon-bus');
            icon.appendChild(use);
            label.appendChild(icon);
            const marker = new naver.maps.Marker({
                position: position,
                map: map,
                title: entry.label,
                icon: { content: label.outerHTML, anchor: new naver.maps.Point(18, 20) }
            });
            stationMarkers.push(marker);
            if (entry.kind === 'origin') startMarker = marker;
            bounds.extend(position);
        });
        if (valid.length === 1) {
            map.panTo(new naver.maps.LatLng(valid[0].lat, valid[0].lon));
        } else {
            const padding = { top: 100, right: 45, bottom: 85, left: 45 };
            const element = document.getElementById('map');
            const panel = document.getElementById('planner-panel');
            if (element && panel && window.matchMedia && window.matchMedia('(min-width: 800px)').matches) {
                const mapRect = element.getBoundingClientRect();
                const panelRect = panel.getBoundingClientRect();
                padding.left = Math.min(mapRect.width * 0.6, panelRect.right - mapRect.left + 35);
            } else if (panel && panel.classList.contains('is-collapsed')) {
                padding.bottom = 170;
            }
            map.fitBounds(bounds, padding);
        }
        return true;
    }

    function journeyEntries(response, index) {
        const entries = [];
        if (response.origin) entries.push(markerEntry(response.origin.lat, response.origin.lon,
            '출발 · ' + (response.origin.name || '출발지'), 'origin'));
        const journey = Array.isArray(response.journeys) ? response.journeys[index] : null;
        const legs = journey && Array.isArray(journey.legs) ? journey.legs : [];
        legs.forEach(function(leg, legIndex) {
            if (!leg || typeof leg !== 'object') return;
            const previous = legIndex > 0 ? legs[legIndex - 1] : null;
            const board = leg.board;
            const alight = leg.alight;
            if (board) {
                const transfer = previous && previous.alight &&
                    Number(previous.alight.lat) === Number(board.lat) &&
                    Number(previous.alight.lon) === Number(board.lon);
                if (!transfer) entries.push(markerEntry(board.lat, board.lon,
                    '승차 · ' + (board.name || '승차 정류장'), 'station'));
            }
            if (alight) {
                const next = legs[legIndex + 1];
                const transfer = next && next.board &&
                    Number(next.board.lat) === Number(alight.lat) &&
                    Number(next.board.lon) === Number(alight.lon);
                entries.push(markerEntry(alight.lat, alight.lon,
                    (transfer ? '환승 · ' : '하차 · ') + (alight.name || '하차 정류장'), 'station'));
            }
        });
        if (response.destination) entries.push(markerEntry(response.destination.lat, response.destination.lon,
            '도착 · ' + (response.destination.name || '목적지'), 'destination'));
        return entries;
    }

    function drawStationMarkersOnMap(data) {
        clearStationMarkers();
        if (authenticationFailed || !data || data.error || data.notice) return false;
        if (!Array.isArray(data) && Array.isArray(data.journeys)) {
            return drawEntries(journeyEntries(data, 0));
        }
        if (!Array.isArray(data) || !data.length) return false;
        const first = data[0] || {};
        const entries = [markerEntry(first.target_lat, first.target_lon, '출발 · ' + (first.start_name || '출발지'), 'origin')];
        let stations = data;
        if (destName) {
            const recommended = data.find(function(station) { return station && station.has_recommended_bus; });
            if (recommended) stations = [recommended];
        }
        stations.forEach(function(station) {
            if (!station) return;
            const walk = station.walk_min !== null && Number.isFinite(Number(station.walk_min))
                ? ' · 도보 ' + Math.ceil(Number(station.walk_min)) + '분' : '';
            entries.push(markerEntry(station.station_lat, station.station_lon,
                (station.station_name || '정류장') + walk, 'station'));
        });
        return drawEntries(entries);
    }

    function showJourneyOnMap(response, index) {
        if (!response || !Array.isArray(response.journeys) || !Number.isInteger(index) || !response.journeys[index]) return false;
        const entries = journeyEntries(response, index);
        if (!map || authenticationFailed) {
            if (!authenticationFailed) drawEntries(entries);
            announce('지도를 연결하지 못했어요. 경로 카드의 승차·하차 정류장을 확인해 주세요.');
            return false;
        }
        if (window.focusMap) window.focusMap();
        const shown = drawEntries(entries);
        if (shown) announce((index + 1) + '번째 경로의 출발지, 승차·하차 정류장, 목적지를 지도에 표시했어요.');
        return shown;
    }

    function recenterPosition(lat, lon, withAnnouncement) {
        if (!window.AppState.isValidPosition(lat, lon)) return false;
        const entries = [markerEntry(lat, lon, '내 위치', 'origin')];
        if (!map || authenticationFailed) {
            if (!authenticationFailed) drawEntries(entries);
            if (withAnnouncement) announce('현재 위치를 확인했어요. 지도 연결 후 위치를 표시할 수 있어요.');
            return false;
        }
        const shown = drawEntries(entries);
        if (withAnnouncement && shown) announce('지도를 현재 위치로 이동했어요.');
        return shown;
    }

    function panToCampus(campus) {
        clearStationMarkers();
        if (!map || authenticationFailed || !campusCenter[campus]) return;
        const center = campusCenter[campus];
        map.panTo(new naver.maps.LatLng(center.lat, center.lon));
    }

    function resizeMap() {
        if (!map || authenticationFailed) return;
        const element = document.getElementById('map');
        if (typeof map.autoResize === 'function') map.autoResize();
        else if (element && typeof map.setSize === 'function' && typeof naver.maps.Size === 'function') {
            map.setSize(new naver.maps.Size(element.clientWidth, element.clientHeight));
        }
    }

    window.AppModules.map = {
        init: initMap, initMap, panToCampus, clearStationMarkers, drawStationMarkersOnMap,
        showJourneyOnMap, recenterPosition, resizeMap, isReady: function() { return mapReady; }
    };
    window.initMap = initMap;
    window.panToCampus = panToCampus;
    window.clearStationMarkers = clearStationMarkers;
    window.drawStationMarkersOnMap = drawStationMarkersOnMap;
    window.showJourneyOnMap = showJourneyOnMap;
})();


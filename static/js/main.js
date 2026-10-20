/** Shared browser state and application startup. No location permission is requested here. */
var selectedCampus = 'hallym';
var originMode = 'gps';
var lastSearchMode = null;
var currentLat = null;
var currentLon = null;
var currentPositionAt = 0;
var destLat = null;
var destLon = null;
var destName = '';
var searchTimer = null;
var map = null;
var startMarker = null;
var stationMarkers = [];

var campusCenter = {
    hallym: { lat: 37.8863, lon: 127.7381 },
    knu: { lat: 37.8691, lon: 127.7442 }
};
var buildingData = window.BUILDING_DATA || { hallym: [], knu: [] };

window.AppModules = window.AppModules || {};
window.AppState = {
    getSelectedCampus: function() { return selectedCampus; },
    setSelectedCampus: function(value) {
        if (value === 'hallym' || value === 'knu') selectedCampus = value;
    },
    getOriginMode: function() { return originMode; },
    setOriginMode: function(value) {
        if (value === 'gps' || value === 'building') originMode = value;
    },
    getLastSearchMode: function() { return lastSearchMode; },
    setLastSearchMode: function(value) { lastSearchMode = value; },
    isValidPosition: function(lat, lon) {
        const numeric = function(value) {
            return (typeof value === 'number' || (typeof value === 'string' && value.trim() !== '')) &&
                Number.isFinite(Number(value));
        };
        return numeric(lat) && numeric(lon) && Math.abs(Number(lat)) <= 90 && Math.abs(Number(lon)) <= 180;
    },
    setCurrentPosition: function(lat, lon) {
        if (!this.isValidPosition(lat, lon)) return false;
        currentLat = Number(lat);
        currentLon = Number(lon);
        currentPositionAt = Date.now();
        return true;
    },
    getCurrentPosition: function() {
        return this.isValidPosition(currentLat, currentLon)
            ? { lat: currentLat, lon: currentLon, capturedAt: currentPositionAt } : null;
    },
    getDestination: function() { return { name: destName, lat: destLat, lon: destLon }; },
    setDestination: function(name, lat, lon) {
        if (typeof name !== 'string' || !name.trim() || !this.isValidPosition(lat, lon)) return false;
        destName = name.trim();
        destLat = Number(lat);
        destLon = Number(lon);
        return true;
    },
    clearDestination: function() {
        destName = '';
        destLat = null;
        destLon = null;
    }
};

(function() {
    let initialized = false;
    function initializeApplication() {
        if (initialized) return;
        initialized = true;
        const campusData = document.getElementById('campus-building-data');
        if (campusData) {
            try {
                const parsed = JSON.parse(campusData.textContent);
                if (parsed && typeof parsed === 'object') buildingData = parsed;
            } catch (error) {
                console.error('교내 건물 목록을 읽지 못했습니다:', error);
            }
        }
        ['search', 'ui', 'map'].forEach(function(name) {
            const module = window.AppModules[name];
            if (!module || typeof module.init !== 'function') return;
            try {
                module.init();
            } catch (error) {
                console.error(name + ' 화면 초기화 실패:', error);
                const status = document.getElementById('app-status');
                if (status) status.textContent = '화면 일부를 불러오지 못했어요. 새로고침 후 다시 시도해 주세요.';
            }
        });
    }
    if (document.readyState !== 'complete') {
        document.addEventListener('DOMContentLoaded', initializeApplication, { once: true });
    } else {
        setTimeout(initializeApplication, 0);
    }
})();


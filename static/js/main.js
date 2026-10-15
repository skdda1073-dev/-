/**
 * main.js
 * 애플리케이션 상태 소유, 모듈 연결 검증 및 초기화만 담당합니다.
 */

const appState = {
    selectedCampus: 'hallym',
    lastSearchMode: null,
    currentPosition: { lat: null, lon: null },
    destination: { name: '', lat: null, lon: null },
    searchTimer: null,
    campusCenter: {
        hallym: { lat: 37.8863, lon: 127.7381 },
        knu: { lat: 37.8691, lon: 127.7442 }
    },
    buildingData: window.BUILDING_DATA || { hallym: [], knu: [] }
};

window.AppModules = window.AppModules || {};
window.AppState = {
    getSelectedCampus: () => appState.selectedCampus,
    setSelectedCampus: (campus) => { appState.selectedCampus = campus; },
    getLastSearchMode: () => appState.lastSearchMode,
    setLastSearchMode: (mode) => { appState.lastSearchMode = mode; },
    getCurrentPosition: () => ({ ...appState.currentPosition }),
    setCurrentPosition: (lat, lon) => { appState.currentPosition = { lat, lon }; },
    getDestination: () => ({ ...appState.destination }),
    setDestination: (name, lat, lon) => { appState.destination = { name, lat, lon }; },
    clearDestination: () => { appState.destination = { name: '', lat: null, lon: null }; },
    getSearchTimer: () => appState.searchTimer,
    setSearchTimer: (timer) => { appState.searchTimer = timer; },
    getCampusCenter: (campus) => appState.campusCenter[campus] || appState.campusCenter.hallym,
    getBuildingNames: (campus) => appState.buildingData[campus] || []
};

const requiredModules = ['map', 'gps', 'search', 'api', 'renderer'];

window.addEventListener('load', function() {
    const missingModules = requiredModules.filter((moduleName) => !window.AppModules[moduleName]);
    if (missingModules.length > 0) {
        console.error('필수 애플리케이션 모듈이 연결되지 않았습니다:', missingModules.join(', '));
        return;
    }

    window.AppModules.map.initMap();
    window.AppModules.search.updateBuildingSelect('hallym');
    window.AppModules.gps.findMyLocation();
});


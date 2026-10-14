/**
 * main.js
 * 애플리케이션 진입점 및 전역 상태 초기화 모듈
 */

// ==========================================
// 🌐 전역 상태 변수 관리
// ==========================================
var selectedCampus = 'hallym';
var lastSearchMode = null;
var currentLat = null;
var currentLon = null;
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

// index.html에서 인라인으로 주입된 window.BUILDING_DATA 바인딩
var buildingData = window.BUILDING_DATA || { hallym: [], knu: [] };

// ==========================================
// 🚀 앱 초기화 실행
// ==========================================
window.onload = function() {
    // 1. 네이버 지도 초기화
    if (typeof initMap === 'function') {
        initMap();
    }

    // 2. 초기 한림대학교 건물 드롭다운 목록 구성
    if (typeof updateBuildingSelect === 'function') {
        updateBuildingSelect('hallym');
    }

    // 3. 브라우저 실시간 위치 추적 시작
    if (typeof findMyLocation === 'function') {
        findMyLocation();
    }
};


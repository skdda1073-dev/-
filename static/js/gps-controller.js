/**
 * gps-controller.js
 * 실시간 GPS 위치 추적 및 위치 권한 처리 모듈
 */

function findMyLocation() {
    if (navigator.geolocation) {
        navigator.geolocation.watchPosition(function(position) {
            window.AppState.setCurrentPosition(
                position.coords.latitude,
                position.coords.longitude
            );
        }, function(error) {}, { 
            enableHighAccuracy: false,
            maximumAge: 60000,
            timeout: 10000 
        });
    }
}

function findRoutesByGPS() {
    const loadingEl = document.getElementById('loading');
    const resultContainer = document.getElementById('result-container');

    loadingEl.style.display = 'block';
    loadingEl.innerText = '📍 실시간 위치를 파악 중입니다...';
    resultContainer.innerHTML = '';

    const currentPosition = window.AppState.getCurrentPosition();
    if (currentPosition.lat && currentPosition.lon) {
        window.AppModules.api.requestBusByCoords(currentPosition.lat, currentPosition.lon);
        return;
    }

    navigator.geolocation.getCurrentPosition(
        function(position) {
            window.AppModules.api.requestBusByCoords(position.coords.latitude, position.coords.longitude);
        },
        function(error) {
            if (error.code === 1) {
                loadingEl.style.display = 'none';
                alert("브라우저 위치 권한이 거부되어 있습니다. 사파리 상단 [설정 -> 웹사이트 -> 위치]에서 허용으로 바꿔주세요!");
                return;
            }
            navigator.geolocation.getCurrentPosition(
                function(pos2) {
                    window.AppModules.api.requestBusByCoords(pos2.coords.latitude, pos2.coords.longitude);
                },
                function(err2) {
                    loadingEl.style.display = 'none';
                    alert("현재 Wi-Fi 위치 신호를 잡지 못했습니다. 아래의 '선택한 건물에서 가까운 정류장'을 이용해주세요.");
                },
                { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 }
            );
        },
        { enableHighAccuracy: true, timeout: 4000, maximumAge: 60000 }
    );
}

window.AppModules.gps = {
    findMyLocation: findMyLocation,
    findRoutesByGPS: findRoutesByGPS
};

// index.html의 기존 인라인 호출과의 호환성을 유지합니다.
window.findMyLocation = findMyLocation;
window.findRoutesByGPS = findRoutesByGPS;


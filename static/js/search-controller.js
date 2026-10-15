/**
 * search-controller.js
 * 학교 캠퍼스 전환, 출발 건물 드롭다운 및 도착지 자동완성 검색 제어 모듈
 */

let poiRequestId = 0;
let poiAbortController = null;

function cancelPoiSearch() {
    clearTimeout(window.AppState.getSearchTimer());
    poiRequestId += 1;
    if (poiAbortController) poiAbortController.abort();
    poiAbortController = null;
    const list = document.getElementById('autocomplete-list');
    if (list) {
        list.replaceChildren();
        list.style.display = 'none';
    }
}

function switchCampus(campus) {
    cancelPoiSearch();
    window.AppModules.api.cancelPendingRequests({ clearResults: true });
    window.AppState.setSelectedCampus(campus);
    document.getElementById('tab-hallym').classList.toggle('active', campus === 'hallym');
    document.getElementById('tab-knu').classList.toggle('active', campus === 'knu');
    updateBuildingSelect(campus);

    window.AppModules.map.panToCampus(campus);
}

function updateBuildingSelect(campus) {
    const selectEl = document.getElementById('building-select');
    if (!selectEl) return;
    const schoolName = campus === 'hallym' ? '한림대' : '강원대';
    selectEl.innerHTML = `<option value="">[${schoolName}] 출발 건물을 선택하세요</option>`;
    
    window.AppState.getBuildingNames(campus).forEach(name => {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name;
        selectEl.appendChild(opt);
    });
}

function onSearchDestination(keyword) {
    cancelPoiSearch();
    const listEl = document.getElementById('autocomplete-list');
    const query = String(keyword || '').trim();
    if (!query || !listEl) return;
    const requestId = poiRequestId;

    const searchTimer = setTimeout(() => {
        poiAbortController = new AbortController();
        fetch(`/search_poi?q=${encodeURIComponent(query)}`, { signal: poiAbortController.signal })
        .then(res => {
            if (!res.ok) throw new Error('장소 검색 응답 오류');
            return res.json();
        })
        .then(items => {
            if (requestId !== poiRequestId) return;
            listEl.replaceChildren();
            if (!Array.isArray(items) || items.length === 0) return;
            items.forEach(item => {
                if (!item || item.lat === null || item.lon === null || item.lat === undefined || item.lon === undefined ||
                    String(item.lat).trim() === '' || String(item.lon).trim() === '' ||
                    !Number.isFinite(Number(item.lat)) || !Number.isFinite(Number(item.lon)) ||
                    Math.abs(Number(item.lat)) > 90 || Math.abs(Number(item.lon)) > 180) return;
                const option = document.createElement('div');
                option.className = 'autocomplete-item';
                const title = document.createElement('div');
                title.textContent = `📍 ${item.name}`;
                const address = document.createElement('div');
                address.textContent = item.sub || '';
                option.append(title, address);
                option.addEventListener('click', () => selectDestination(item.name, Number(item.lat), Number(item.lon)));
                listEl.appendChild(option);
            });
            listEl.style.display = listEl.childElementCount ? 'block' : 'none';
        })
        .catch(err => {
            if (err.name === 'AbortError' || requestId !== poiRequestId) return;
            console.error('POI 검색 실패:', err);
        });
    }, 250);
    window.AppState.setSearchTimer(searchTimer);
}

function selectDestination(name, lat, lon) {
    cancelPoiSearch();
    window.AppModules.api.cancelPendingRequests({ clearResults: true });
    window.AppState.setDestination(name, lat, lon);
    document.getElementById('autocomplete-list').style.display = 'none';
    document.getElementById('dest-input').value = '';
    document.getElementById('dest-input').style.display = 'none';
    
    const tagEl = document.getElementById('dest-selected-tag');
    document.getElementById('dest-selected-text').innerText = `🎯 도착지: ${name}`;
    tagEl.style.display = 'flex';

    const building = document.getElementById('building-select').value;
    const currentPosition = window.AppState.getCurrentPosition();
    if (window.AppState.getLastSearchMode() === 'gps' && currentPosition.lat && currentPosition.lon) {
        window.AppModules.api.requestBusByCoords(currentPosition.lat, currentPosition.lon);
    } else if (building) {
        window.AppModules.api.findRoutes();
    }
}

function clearDestination() {
    cancelPoiSearch();
    window.AppModules.api.cancelPendingRequests({ clearResults: true });
    window.AppState.clearDestination();
    document.getElementById('dest-selected-tag').style.display = 'none';
    document.getElementById('dest-input').style.display = 'block';

    const building = document.getElementById('building-select').value;
    const currentPosition = window.AppState.getCurrentPosition();
    if (window.AppState.getLastSearchMode() === 'gps' && currentPosition.lat && currentPosition.lon) {
        window.AppModules.api.requestBusByCoords(currentPosition.lat, currentPosition.lon);
    } else if (building) {
        window.AppModules.api.findRoutes();
    }
}

window.AppModules.search = {
    switchCampus: switchCampus,
    updateBuildingSelect: updateBuildingSelect,
    onSearchDestination: onSearchDestination,
    selectDestination: selectDestination,
    clearDestination: clearDestination
};

// index.html과 자동완성 항목의 기존 인라인 호출과의 호환성을 유지합니다.
window.switchCampus = switchCampus;
window.updateBuildingSelect = updateBuildingSelect;
window.onSearchDestination = onSearchDestination;
window.selectDestination = selectDestination;
window.clearDestination = clearDestination;


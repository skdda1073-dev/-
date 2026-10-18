/**
 * search-controller.js
 * 학교 캠퍼스 전환, 출발 건물 드롭다운 및 도착지 자동완성 검색 제어 모듈
 */

function switchCampus(campus) {
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
    clearTimeout(window.AppState.getSearchTimer());
    const listEl = document.getElementById('autocomplete-list');
    if (!keyword || keyword.trim().length < 1) {
        listEl.style.display = 'none';
        return;
    }

    const searchTimer = setTimeout(() => {
        fetch(`/search_poi?q=${encodeURIComponent(keyword.trim())}`)
        .then(res => res.json())
        .then(items => {
            if (!items || items.length === 0) {
                listEl.style.display = 'none';
                return;
            }
            let html = '';
            items.forEach(item => {
                const safeName = item.name.replace(/'/g, "\\'");
                html += `
                <div class="autocomplete-item" onclick="selectDestination('${safeName}', ${item.lat}, ${item.lon})">
                    <div style="font-size: 14px; font-weight: 700; color: #1A202C;">📍 ${item.name}</div>
                    <div style="font-size: 12px; color: #718096; margin-top: 2px;">${item.sub}</div>
                </div>`;
            });
            listEl.innerHTML = html;
            listEl.style.display = 'block';
        })
        .catch(err => {
            console.error('POI 검색 실패:', err);
        });
    }, 250);
    window.AppState.setSearchTimer(searchTimer);
}

function selectDestination(name, lat, lon) {
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


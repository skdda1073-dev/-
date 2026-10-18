/**
 * search-controller.js
 * 학교 캠퍼스 전환, 출발 건물 드롭다운 및 도착지 자동완성 검색 제어 모듈
 */

function switchCampus(campus) {
    selectedCampus = campus;
    document.getElementById('tab-hallym').classList.toggle('active', campus === 'hallym');
    document.getElementById('tab-knu').classList.toggle('active', campus === 'knu');
    updateBuildingSelect(campus);

    if (typeof panToCampus === 'function') {
        panToCampus(campus);
    }
}

function updateBuildingSelect(campus) {
    const selectEl = document.getElementById('building-select');
    if (!selectEl) return;
    const schoolName = campus === 'hallym' ? '한림대' : '강원대';
    selectEl.innerHTML = `<option value="">[${schoolName}] 출발 건물을 선택하세요</option>`;
    
    if (buildingData && buildingData[campus]) {
        buildingData[campus].forEach(name => {
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            selectEl.appendChild(opt);
        });
    }
}

function onSearchDestination(keyword) {
    clearTimeout(searchTimer);
    const listEl = document.getElementById('autocomplete-list');
    if (!keyword || keyword.trim().length < 1) {
        listEl.style.display = 'none';
        return;
    }

    searchTimer = setTimeout(() => {
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
}

function selectDestination(name, lat, lon) {
    destName = name;
    destLat = lat;
    destLon = lon;
    document.getElementById('autocomplete-list').style.display = 'none';
    document.getElementById('dest-input').value = '';
    document.getElementById('dest-input').style.display = 'none';
    
    const tagEl = document.getElementById('dest-selected-tag');
    document.getElementById('dest-selected-text').innerText = `🎯 도착지: ${name}`;
    tagEl.style.display = 'flex';

    const building = document.getElementById('building-select').value;
    if (lastSearchMode === 'gps' && currentLat && currentLon) {
        requestBusByCoords(currentLat, currentLon);
    } else if (building) {
        findRoutes();
    }
}

function clearDestination() {
    destName = '';
    destLat = null;
    destLon = null;
    document.getElementById('dest-selected-tag').style.display = 'none';
    document.getElementById('dest-input').style.display = 'block';

    const building = document.getElementById('building-select').value;
    if (lastSearchMode === 'gps' && currentLat && currentLon) {
        requestBusByCoords(currentLat, currentLon);
    } else if (building) {
        findRoutes();
    }
}

window.switchCampus = switchCampus;
window.updateBuildingSelect = updateBuildingSelect;
window.onSearchDestination = onSearchDestination;
window.selectDestination = selectDestination;
window.clearDestination = clearDestination;


// 출발역→도착역 ITX-청춘 시간표. 기존 버스 길찾기 스크립트와 독립적으로 동작합니다.
(() => {
    'use strict';

    const root = document.getElementById('itx-timetable');
    if (!root) return;

    const originSelect = root.querySelector('#itx-origin');
    const destSelect = root.querySelector('#itx-destination');
    const dateInput = root.querySelector('#itx-date');
    const loadButton = root.querySelector('#itx-load');
    const status = root.querySelector('#itx-status');
    const warning = root.querySelector('#itx-warning');
    const tableWrap = root.querySelector('#itx-table-wrap');
    const tableBody = root.querySelector('#itx-table-body');

    let requestVersion = 0;
    let availableStations = [];
    let stationWarning = '';

    const display = (value) => value === null || value === undefined || String(value).trim() === ''
        ? '—' : String(value);

    function addCell(row, value) {
        const cell = document.createElement('td');
        cell.textContent = display(value); // 외부 API 문자열을 HTML로 삽입하지 않음
        row.appendChild(cell);
    }

    function addTrainRow(train) {
        const row = document.createElement('tr');
        addCell(row, train.train_no);
        addCell(row, `${train.origin}역`);
        addCell(row, train.departure_time);
        addCell(row, `${train.destination}역`);
        addCell(row, train.arrival_time);
        const fare = Number(train.fare);
        addCell(row, train.fare !== null && train.fare !== undefined && Number.isFinite(fare)
            ? `${fare.toLocaleString('ko-KR')}원` : '—');
        tableBody.appendChild(row);
    }

    function makeOptions(select, stations, selectedName) {
        select.replaceChildren();
        for (const station of stations) {
            const option = document.createElement('option');
            option.value = station;
            option.textContent = `${station}역`;
            select.appendChild(option);
        }
        if (stations.includes(selectedName)) select.value = selectedName;
    }

    function updateDestinationOptions() {
        const origin = originSelect.value;
        const previous = destSelect.value;
        const destinations = availableStations.filter(name => name !== origin);
        const preferred = destinations.includes(previous) ? previous
            : destinations.includes('마석') ? '마석'
            : destinations.includes('용산') ? '용산' : destinations[0] || '';
        makeOptions(destSelect, destinations, preferred);
    }

    async function loadTimetable() {
        const currentRequest = ++requestVersion;
        const origin = originSelect.value;
        const destination = destSelect.value;
        const date = dateInput.value.replace(/-/g, '');

        if (!origin || !destination || !/^\d{8}$/.test(date)) {
            status.textContent = '출발역, 도착역, 조회 날짜를 확인해 주세요.';
            return;
        }
        if (origin === destination) {
            status.textContent = '출발역과 도착역을 다르게 골라 주세요.';
            return;
        }

        loadButton.disabled = true;
        status.textContent = `${origin}역 → ${destination}역 ITX 시간표를 조회하고 있어요.`;
        tableWrap.hidden = true;
        tableBody.replaceChildren();
        warning.hidden = true;
        warning.textContent = '';

        const params = new URLSearchParams({origin, destination, date});
        try {
            const response = await fetch(`/api/itx/route?${params}`, {
                headers: {Accept: 'application/json'},
            });
            const data = await response.json().catch(() => null);
            if (currentRequest !== requestVersion) return;
            if (!response.ok) {
                if (response.status === 404) {
                    throw new Error('ITX 구간조회 API가 없어요. app.py에 Blueprint를 등록했는지 확인해 주세요.');
                }
                throw new Error(data?.error || `열차 시간표 조회에 실패했어요. (HTTP ${response.status})`);
            }
            if (!data || !Array.isArray(data.trains)) {
                throw new Error('서버가 예상과 다른 시간표 형식을 반환했어요.');
            }

            const count = data.trains.length;
            status.textContent = `${dateInput.value} · ${origin}역 → ${destination}역 · 직통 ITX-청춘 ${count}건`;
            if (count) {
                data.trains.forEach(addTrainRow);
                tableWrap.hidden = false;
            } else {
                status.textContent += ' · 조회된 열차가 없어요. 해당 날짜에 미운행·미정차 또는 데이터 미제공일 수 있어요.';
            }
            const notices = [stationWarning, data.scope_warning].filter(Boolean);
            if (notices.length) {
                warning.textContent = notices.join(' ');
                warning.hidden = false;
            }
        } catch (error) {
            if (currentRequest !== requestVersion) return;
            status.textContent = error instanceof Error ? error.message : '시간표 조회 중 오류가 발생했어요.';
        } finally {
            if (currentRequest === requestVersion) loadButton.disabled = false;
        }
    }

    async function loadStationOptions() {
        status.textContent = 'TAGO에서 역 코드를 확인하고 있어요.';
        try {
            const response = await fetch('/api/itx/stations', {headers: {Accept: 'application/json'}});
            const data = await response.json().catch(() => null);
            if (!response.ok || !data || !Array.isArray(data.stations)) {
                throw new Error(data?.error || '역 목록을 불러오지 못했어요.');
            }
            availableStations = data.stations.map(item => item.name);
            stationWarning = data.warning || '';
            if (!availableStations.includes('마석')) {
                stationWarning += ' 마석역의 TAGO 역 코드를 아직 찾지 못했습니다. TAGO 역 목록 조회 결과를 확인해 주세요.';
            }
            if (availableStations.length < 2) {
                throw new Error('조회 가능한 역이 2곳 미만입니다. TAGO 역 목록 API를 확인해 주세요.');
            }
            makeOptions(originSelect, availableStations, '춘천');
            updateDestinationOptions();
            originSelect.disabled = false;
            destSelect.disabled = false;
            loadButton.disabled = false;
            if (data.date) dateInput.value = data.date;
            if (stationWarning) {
                warning.textContent = stationWarning.trim();
                warning.hidden = false;
            }
            status.textContent = "출발역·도착역·날짜를 확인하고 시간표 조회를 눌러 주세요.";
        } catch (error) {
            status.textContent = error instanceof Error ? error.message : '역 목록 조회 실패';
        }
    }

    loadButton.addEventListener('click', loadTimetable);
    originSelect.addEventListener('change', () => {
        updateDestinationOptions();
        status.textContent = "시간표 조회 버튼을 눌러 주세요.";
        tableWrap.hidden = true;
    });
    destSelect.addEventListener('change', () => { status.textContent = "시간표 조회 버튼을 눌러 주세요."; tableWrap.hidden = true; });
    dateInput.addEventListener('change', () => { status.textContent = "시간표 조회 버튼을 눌러 주세요."; tableWrap.hidden = true; });
    // 역 목록 API는 ITX 탭을 열었을 때만 호출합니다.
    const panel = document.getElementById('transport-panel-subway');
    let started = false;
    function startIfVisible() {
        if (started || (panel && panel.hidden)) return;
        started = true;
        loadStationOptions();
    }
    if (panel) {
        new MutationObserver(startIfVisible).observe(panel, {
            attributes: true,
            attributeFilter: ['hidden'],
        });
    }
    startIfVisible();
})();

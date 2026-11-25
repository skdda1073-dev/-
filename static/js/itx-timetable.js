// 출발역→도착역 ITX-청춘 시간표. 기존 버스 길찾기 스크립트와 독립적으로 동작합니다.
(() => {
    'use strict';

    const root = document.getElementById('itx-timetable');
    if (!root) return;

    const originSelect = root.querySelector('#itx-origin');
    const destSelect = root.querySelector('#itx-destination');
    const dateInput = root.querySelector('#itx-date');
    const timeInput = root.querySelector('#itx-time');
    const swapButton = root.querySelector('#itx-swap');
    const loadButton = root.querySelector('#itx-load');
    const status = root.querySelector('#itx-status');
    const warning = root.querySelector('#itx-warning');
    const results = root.querySelector('#itx-results');
    const resultsHeading = root.querySelector('#itx-results-heading');
    const resultMeta = root.querySelector('#itx-result-meta');
    const trainList = root.querySelector('#itx-train-list');
    const panel = document.getElementById('transport-panel-subway');

    let requestVersion = 0;
    let requestController = null;
    let queryBusy = false;
    let stationLoading = false;
    let stationReady = false;
    let availableStations = [];
    let stationWarning = '';

    function node(tag, value, className) {
        const element = document.createElement(tag);
        if (value !== undefined) element.textContent = String(value);
        if (className) element.className = className;
        return element;
    }

    function announce(message, state = '') {
        status.textContent = message;
        status.setAttribute('data-state', state);
    }

    function updateButton() {
        loadButton.disabled = stationLoading || queryBusy;
        loadButton.textContent = stationLoading ? '역 목록 확인 중…' : queryBusy ? '시간표 조회 중…'
            : stationReady ? '시간표 조회' : '역 목록 다시 불러오기';
        swapButton.disabled = !stationReady || originSelect.value === destSelect.value
            || !availableStations.includes(originSelect.value) || !availableStations.includes(destSelect.value);
        root.setAttribute('aria-busy', String(stationLoading || queryBusy));
    }

    function clearResults() {
        results.hidden = true;
        resultsHeading.textContent = '';
        resultMeta.textContent = '';
        trainList.replaceChildren();
        warning.hidden = true;
        warning.textContent = '';
    }

    function invalidate() {
        requestVersion += 1;
        if (requestController) requestController.abort();
        requestController = null;
        queryBusy = false;
        clearResults();
        updateButton();
    }

    function selectionChanged() {
        invalidate();
        announce('');
    }

    function addTrain(train) {
        const item = node('li', undefined, 'itx-train');
        const article = node('article');
        article.setAttribute('aria-label', 'ITX-청춘 ' + train.train_no);
        const times = node('div', undefined, 'itx-train-times');
        for (const [label, value] of [['출발', train.departure_time], ['도착', train.arrival_time]]) {
            const group = node('div', undefined, 'itx-time-group');
            const time = node('time', value, 'itx-time');
            time.setAttribute('datetime', value);
            group.append(node('span', label, 'itx-time-label'), time);
            times.appendChild(group);
        }
        const fare = Number(train.fare);
        const fareText = train.fare !== null && train.fare !== undefined && String(train.fare).trim() !== ''
            && Number.isSafeInteger(fare) && fare >= 0 ? `성인 ${fare.toLocaleString('ko-KR')}원` : '요금 미제공';
        const details = node('div', undefined, 'itx-train-details');
        details.append(node('span', '열차 ' + train.train_no), node('span', fareText));
        article.append(times, details);
        item.appendChild(article);
        trainList.appendChild(item);
    }

    function makeOptions(select, stations, selectedName) {
        select.replaceChildren();
        for (const station of stations) {
            const option = document.createElement('option');
            option.value = station;
            option.textContent = station;
            select.appendChild(option);
        }
        if (stations.includes(selectedName)) select.value = selectedName;
    }

    function updateDestinationOptions(previous = destSelect.value) {
        const origin = originSelect.value;
        const destinations = availableStations.filter(name => name !== origin);
        const preferred = destinations.includes(previous) ? previous
            : destinations.includes('마석') ? '마석'
            : destinations.includes('용산') ? '용산' : destinations[0] || '';
        makeOptions(destSelect, destinations, preferred);
    }

    function validTime(value) {
        return typeof value === 'string' && /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value);
    }

    async function loadTimetable() {
        if (loadButton.disabled) return;
        if (!stationReady) { loadStationOptions(); return; }
        invalidate();
        const currentRequest = requestVersion;
        const selected = {
            origin: originSelect.value,
            destination: destSelect.value,
            date: dateInput.value.replace(/-/g, ''),
            displayDate: dateInput.value,
            fromTime: timeInput.value || '00:00',
        };
        const { origin, destination, date, fromTime } = selected;

        if (!origin || !destination || !/^\d{8}$/.test(date) || !validTime(fromTime)) {
            announce('선택한 역과 날짜·시각을 확인해 주세요.', 'error');
            return;
        }
        if (origin === destination) {
            announce('출발역과 도착역을 다르게 골라 주세요.', 'error');
            return;
        }

        queryBusy = true;
        updateButton();
        announce('시간표 조회 중…');
        const controller = new AbortController();
        requestController = controller;
        const shell = window.AppModules?.shell;
        const navigation = shell?.getNavigation();
        const params = new URLSearchParams({origin, destination, date});
        try {
            const response = await fetch(`/api/itx/route?${params}`, {
                headers: {Accept: 'application/json'},
                signal: controller.signal,
            });
            const data = await response.json().catch(() => null);
            if (currentRequest !== requestVersion) return;
            if (!response.ok) {
                throw new Error(response.status >= 500 || response.status === 404 ? '열차 시간표에 연결하지 못했어요. 잠시 후 다시 조회해 주세요.'
                    : data?.error || '열차 시간표 조회에 실패했어요. 선택한 구간과 날짜를 확인해 주세요.');
            }
            if (!data || !Array.isArray(data.trains) || data.date !== date || data.origin !== origin || data.destination !== destination
                || data.trains.some(train => !train || train.origin !== origin || train.destination !== destination
                    || typeof train.train_no !== 'string' || !train.train_no.trim()
                    || !validTime(train.departure_time) || !validTime(train.arrival_time))) {
                throw new Error('조회 결과의 구간과 시각을 확인할 수 없어요. 다시 조회해 주세요.');
            }

            const trains = data.trains.filter(train => train.departure_time >= fromTime);
            const count = trains.length;
            const timeLabel = fromTime === '00:00' ? '' : ` · ${fromTime} 이후`;
            resultsHeading.textContent = `${origin}역 → ${destination}역`;
            resultMeta.textContent = `${selected.displayDate}${timeLabel} · 직통 ITX-청춘 ${count}편 · 계획 시간표`;
            if (count) {
                trains.forEach(addTrain);
                announce('', 'ok');
            } else {
                const message = data.trains.length ? `${fromTime} 이후 출발하는 열차가 없어요.`
                    : '조회된 열차가 없어요. 해당 날짜에 미운행·미정차 또는 데이터 미제공일 수 있어요.';
                trainList.appendChild(node('li', message, 'itx-empty'));
                announce(message, 'empty');
            }
            results.hidden = false;
            const notices = [stationWarning, data.scope_warning].filter(value => typeof value === 'string' && value.trim());
            if (notices.length) {
                warning.textContent = notices.join(' ');
                warning.hidden = false;
            }
            if ((!panel || !panel.hidden) && (!shell || shell.getNavigation() === navigation)) {
                resultsHeading.focus({ preventScroll: true });
                resultsHeading.scrollIntoView({ block: 'start', inline: 'nearest', behavior: 'auto' });
            }
        } catch (error) {
            if (currentRequest !== requestVersion || error?.name === 'AbortError') return;
            announce(error instanceof Error ? error.message : '시간표 조회 중 오류가 발생했어요. 다시 조회해 주세요.', 'error');
        } finally {
            if (currentRequest === requestVersion) {
                queryBusy = false;
                requestController = null;
                updateButton();
            }
        }
    }

    async function loadStationOptions() {
        if (stationLoading) return;
        stationLoading = true;
        stationReady = false;
        updateButton();
        announce('역 목록 확인 중…');
        try {
            const response = await fetch('/api/itx/stations', {headers: {Accept: 'application/json'}});
            const data = await response.json().catch(() => null);
            if (!response.ok || !data || !Array.isArray(data.stations)) {
                throw new Error('역 목록을 불러오지 못했어요. 역 목록 다시 불러오기를 눌러 주세요.');
            }
            availableStations = [...new Set(data.stations.filter(item => item && typeof item.name === 'string' && item.name.trim()).map(item => item.name))];
            stationWarning = typeof data.warning === 'string' ? data.warning : '';
            if (!availableStations.includes('마석')) {
                stationWarning += ' 마석역 시간표는 현재 조회할 수 없어요.';
            }
            if (availableStations.length < 2) {
                throw new Error('조회 가능한 역이 충분하지 않아요. 잠시 후 역 목록을 다시 불러와 주세요.');
            }
            makeOptions(originSelect, availableStations, '춘천');
            updateDestinationOptions();
            originSelect.disabled = false;
            destSelect.disabled = false;
            stationReady = true;
            if (!dateInput.value && /^\d{4}-\d{2}-\d{2}$/.test(data.date || '')) dateInput.value = data.date;
            if (stationWarning) {
                warning.textContent = stationWarning.trim();
                warning.hidden = false;
            }
            announce('');
        } catch (error) {
            announce(error instanceof Error ? error.message : '역 목록을 불러오지 못했어요. 다시 불러오기를 눌러 주세요.', 'error');
        } finally {
            stationLoading = false;
            updateButton();
        }
    }

    loadButton.addEventListener('click', loadTimetable);
    swapButton.addEventListener('click', () => {
        if (swapButton.disabled) return;
        const previousOrigin = originSelect.value;
        originSelect.value = destSelect.value;
        updateDestinationOptions(previousOrigin);
        selectionChanged();
    });
    originSelect.addEventListener('change', () => {
        updateDestinationOptions();
        selectionChanged();
    });
    destSelect.addEventListener('change', selectionChanged);
    dateInput.addEventListener('input', selectionChanged);
    dateInput.addEventListener('change', selectionChanged);
    timeInput.addEventListener('input', selectionChanged);
    timeInput.addEventListener('change', selectionChanged);
    // 역 목록 API는 ITX 탭을 열었을 때만 호출합니다.
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

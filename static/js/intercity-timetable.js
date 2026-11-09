/* 버스타고 도착지와 오늘 출발 시간표 조회. 화면 문자열은 모두 텍스트로 삽입합니다. */
(() => {
    'use strict';
    const root = document.getElementById('intercity-timetable');
    const panel = document.getElementById('transport-panel-intercity');
    if (!root || !panel) return;
    const byId = id => root.querySelector('#intercity-' + id);
    const origin = byId('origin'), destination = byId('destination');
    const originFilter = byId('origin-filter'), destinationFilter = byId('destination-filter');
    const originHint = byId('origin-hint'), destinationHint = byId('destination-hint');
    const destinationHeading = byId('destination-heading'), destinationRetry = byId('destinations-retry');
    const destinationWarnings = byId('destinations-warnings');
    const includeStops = byId('include-stops'), additionalOption = byId('additional-option');
    const MAX_CANDIDATES = 30;
    const date = byId('date'), submit = byId('load'), swap = byId('swap');
    const status = byId('status'), retry = byId('retry'), results = byId('results');
    const trips = byId('trips'), warnings = byId('warnings');
    let terminals = [], ready = false, started = false, initBusy = false, queryBusy = false;
    let requestVersion = 0, queryController = null, initController = null, initVersion = 0;
    let destinations = [], additionalStops = [], destinationOriginId = '', destinationState = 'idle';
    let destinationVersion = 0, destinationController = null;
    const timeFormat = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
    const dayFormat = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit' });

    function node(tag, text, className) {
        const element = document.createElement(tag);
        if (text !== undefined) element.textContent = text;
        if (className) element.className = className;
        return element;
    }
    function announce(text, state = 'info') {
        status.textContent = text;
        status.dataset.state = state;
    }
    function clearResults() {
        results.hidden = true;
        trips.replaceChildren();
        warnings.replaceChildren();
        warnings.hidden = true;
        byId('result-meta').textContent = '';
    }
    function setWarnings(values) {
        warnings.replaceChildren(...values.filter(value => typeof value === 'string' && value.trim()).map(value => node('li', value)));
        warnings.hidden = !warnings.children.length;
    }
    function visibleDestinations() {
        return (includeStops.checked ? [...destinations, ...additionalStops] : [...destinations])
            .sort((a, b) => a.name.localeCompare(b.name, 'ko-KR') || a.city.localeCompare(b.city, 'ko-KR') || a.id.localeCompare(b.id));
    }
    function selectedDestination() {
        return destinationState === 'ready' && destinationOriginId === origin.value
            ? visibleDestinations().find(item => item.id === destination.value && item.id !== origin.value) : null;
    }
    function validSelection() {
        return ready && /^\d{4}-\d{2}-\d{2}$/.test(date.value) && origin.value && selectedDestination();
    }
    function updateButtons() {
        const destinationBusy = destinationState === 'loading';
        destination.disabled = destinationFilter.disabled = !ready || destinationState !== 'ready' || initBusy || !visibleDestinations().length;
        includeStops.disabled = !ready || destinationState !== 'ready' || initBusy || !additionalStops.length;
        additionalOption.hidden = destinationState !== 'ready' || !additionalStops.length;
        submit.disabled = !validSelection() || queryBusy || initBusy || destinationBusy;
        swap.disabled = !ready || !selectedDestination() || !terminals.some(item => item.id === destination.value) || initBusy || destinationBusy;
        root.setAttribute('aria-busy', String(initBusy || queryBusy || destinationBusy));
    }
    function invalidate() {
        requestVersion += 1;
        if (queryController) queryController.abort();
        queryController = null;
        queryBusy = false;
        clearResults();
        updateButtons();
    }
    function fillOrigin(wanted) {
        const query = originFilter.value.trim().toLocaleLowerCase('ko-KR');
        const selected = terminals.find(item => item.id === wanted);
        const rank = item => {
            const name = item.name.toLocaleLowerCase('ko-KR');
            return name === query ? 0 : name.startsWith(query) ? 1 : 2;
        };
        // A blank search keeps only the explicit selection, never the nationwide catalogue.
        const matches = query
            ? terminals.filter(item => (item.name + ' ' + item.city).toLocaleLowerCase('ko-KR').includes(query))
                .sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name, 'ko-KR') || a.id.localeCompare(b.id))
            : selected ? [selected] : [];
        const items = matches.slice(0, MAX_CANDIDATES);
        if (selected && matches.some(item => item.id === selected.id) && !items.some(item => item.id === selected.id)) {
            items[items.length - 1] = selected;
        }
        const placeholderText = !query && !items.length ? '터미널 이름을 먼저 검색하세요'
            : items.length ? '터미널을 선택하세요' : '검색에 맞는 터미널이 없어요';
        const placeholder = node('option', placeholderText);
        placeholder.value = '';
        origin.replaceChildren(placeholder, ...items.map(item => {
            const option = node('option', item.name + (item.city ? ' · ' + item.city : ''));
            option.value = item.id;
            return option;
        }));
        origin.value = items.some(item => item.id === wanted) ? wanted : '';
        if (originHint) {
            originHint.textContent = !query
                ? selected ? `선택: ${selected.name} · 다른 터미널은 이름을 검색하세요.` : '터미널 이름을 검색한 뒤 아래 목록에서 선택하세요.'
                : `검색 결과 ${matches.length.toLocaleString('ko-KR')}곳` + (matches.length > MAX_CANDIDATES
                    ? ` 중 ${MAX_CANDIDATES}곳을 표시해요. 이름을 더 구체적으로 입력하세요.`
                    : matches.length ? ' · 아래 목록에서 선택하세요.' : ' · 터미널 이름이나 지역명을 바꿔 검색해 주세요.');
        }
    }
    function fillDestinations(wanted = '') {
        const query = destinationFilter.value.trim().toLocaleLowerCase('ko-KR');
        const available = visibleDestinations();
        const nameCounts = new Map();
        available.forEach(item => nameCounts.set(item.name, (nameCounts.get(item.name) || 0) + 1));
        const matches = available.filter(item => !query || (item.name + ' ' + item.city).toLocaleLowerCase('ko-KR').includes(query));
        const placeholderText = destinationState === 'loading' ? '출발지의 도착지 목록을 불러오는 중이에요'
            : destinationState === 'error' ? '도착지 목록을 다시 불러와 주세요'
                : destinationState === 'idle' ? '출발 터미널을 먼저 선택하세요'
                    : !available.length ? '표시할 도착지 목록이 없어요'
                        : !matches.length ? '검색에 맞는 도착지가 없어요' : '도착 터미널을 선택하세요';
        const placeholder = node('option', placeholderText);
        placeholder.value = '';
        // The server classifies terminals/airports and other stops. Names never determine eligibility here.
        destination.replaceChildren(placeholder, ...matches.map(item => {
            const option = node('option', item.name + (nameCounts.get(item.name) > 1 && item.city ? ' · ' + item.city : ''));
            option.value = item.id;
            return option;
        }));
        destination.value = matches.some(item => item.id === wanted && item.id !== origin.value) ? wanted : '';
        if (destinationHint) {
            destinationHint.textContent = destinationState === 'loading' ? '선택한 출발지의 도착지를 확인하고 있어요.'
                : destinationState === 'error' ? '아래 다시 불러오기 버튼으로 도착지 목록을 확인해 주세요.'
                    : destinationState === 'idle' ? '출발 터미널을 선택하면 도착지 목록이 표시돼요.'
                        : !available.length ? additionalStops.length
                            ? '기본 목록에 표시할 도착지가 없어요. 다른 하차지도 함께 볼 수 있어요.'
                            : '도착지 목록을 확인하지 못했어요. 공식 예매처에서 확인해 주세요.'
                            : query ? `검색 결과 ${matches.length.toLocaleString('ko-KR')}곳 / 전체 ${available.length.toLocaleString('ko-KR')}곳`
                                : `도착지 ${available.length.toLocaleString('ko-KR')}곳 · 목록에서 선택하세요.`;
        }
    }
    function resetDestinations() {
        destinationVersion += 1;
        if (destinationController) destinationController.abort();
        destinationController = null;
        destinations = [];
        additionalStops = [];
        destinationOriginId = '';
        destinationState = 'idle';
        destinationFilter.value = '';
        destinationRetry.hidden = true;
        destinationWarnings.replaceChildren();
        destinationWarnings.hidden = true;
        destinationHeading.textContent = '도착 터미널';
        fillDestinations();
    }
    async function jsonRequest(url, controller) {
        const response = await fetch(url, { headers: { Accept: 'application/json' }, signal: controller.signal });
        const body = await response.json().catch(() => null);
        if (!response.ok) {
            const error = new Error(body && typeof body.error === 'string' ? body.error : '시간표 제공 서비스에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.');
            error.body = body;
            throw error;
        }
        if (!body || typeof body !== 'object') throw new Error('시간표 제공 서비스의 응답을 확인할 수 없어요.');
        return body;
    }
    function queryPrompt() {
        if (origin.value && origin.value === destination.value) announce('출발 터미널과 도착 터미널을 다르게 선택해 주세요.');
        else if (!origin.value) announce('출발 터미널 이름을 검색해 선택해 주세요.');
        else if (destinationState === 'loading') announce('선택한 출발지의 도착지를 불러오고 있어요.');
        else if (destinationState === 'error') return;
        else if (destinationState === 'empty') announce('이 출발지의 도착지를 확인하지 못했어요. 실제 운행 여부는 아래 공식 예매처에서 확인해 주세요.', 'empty');
        else if (!visibleDestinations().length && additionalStops.length) announce('다른 하차지도 보기를 선택하면 추가 도착지를 확인할 수 있어요.');
        else if (!destination.value) announce('표시된 도착지 목록에서 터미널을 선택해 주세요. 오늘 운행은 시간표 조회로 확인할 수 있어요.');
        else announce('출발·도착 터미널을 선택하고 오늘 시간표 조회를 눌러 주세요.');
    }
    async function loadDestinations({ wanted = '', force = false } = {}) {
        const from = terminals.find(item => item.id === origin.value);
        if (!ready || !from) {
            resetDestinations(); updateButtons(); queryPrompt(); return;
        }
        if (!force && destinationOriginId === from.id && ['loading', 'ready', 'empty'].includes(destinationState)) return;
        invalidate();
        resetDestinations();
        const version = destinationVersion;
        const selectedOrigin = from.id;
        destinationOriginId = selectedOrigin;
        destinationState = 'loading';
        destinationHeading.textContent = `${from.name} 출발 도착지`;
        fillDestinations();
        announce(`${from.name} 출발 도착지를 불러오고 있어요.`);
        updateButtons();
        const controller = new AbortController();
        destinationController = controller;
        try {
            const data = await jsonRequest('/api/intercity/destinations?' + new URLSearchParams({ departure_id: selectedOrigin }), controller);
            if (version !== destinationVersion || origin.value !== selectedOrigin) return;
            if (!['ok', 'empty'].includes(data.status) || data.departure?.id !== selectedOrigin
                || !Array.isArray(data.destinations) || !Array.isArray(data.additional_stops)) {
                throw new Error('도착지 목록의 응답 형식을 확인할 수 없어요. 다시 불러와 주세요.');
            }
            const entries = [...data.destinations, ...data.additional_stops], ids = new Set();
            if (entries.some(item => !item || typeof item.name !== 'string' || !item.name.trim()
                || typeof item.id !== 'string' || !item.id || item.id === selectedOrigin || ids.has(item.id) || !ids.add(item.id))) {
                throw new Error('도착지 목록의 응답 형식을 확인할 수 없어요. 다시 불러와 주세요.');
            }
            const normalized = item => ({ id: item.id, name: item.name, city: typeof item.city === 'string' ? item.city : '' });
            destinations = data.destinations.map(normalized);
            additionalStops = data.additional_stops.map(normalized);
            if (/^\d{4}-\d{2}-\d{2}$/.test(data.today || '')) date.value = data.today;
            destinationState = entries.length ? 'ready' : 'empty';
            const notes = Array.isArray(data.warnings) ? data.warnings : [];
            destinationWarnings.replaceChildren(...notes.filter(value => typeof value === 'string' && value.trim()).map(value => node('li', value)));
            destinationWarnings.hidden = !destinationWarnings.children.length;
            fillDestinations(wanted);
            if (wanted && !destination.value && entries.length) announce('현재 도착지 목록에 이전 출발지가 없어 자동 선택하지 않았어요. 도착지를 다시 선택해 주세요.');
            else queryPrompt();
        } catch (error) {
            if (version !== destinationVersion || origin.value !== selectedOrigin || error.name === 'AbortError') return;
            destinations = [];
            additionalStops = [];
            destinationState = 'error';
            destinationRetry.hidden = false;
            fillDestinations();
            announce(error instanceof Error ? error.message : '도착지 목록을 불러오지 못했어요. 다시 시도해 주세요.', 'error');
        } finally {
            if (version === destinationVersion && origin.value === selectedOrigin) {
                destinationController = null;
                updateButtons();
            }
        }
    }
    async function initialize() {
        if (initBusy) return;
        const version = ++initVersion;
        initBusy = true;
        ready = false;
        includeStops.checked = false;
        resetDestinations();
        invalidate();
        origin.disabled = destination.disabled = originFilter.disabled = destinationFilter.disabled = true;
        retry.hidden = true;
        announce('오늘 조회 날짜와 터미널 목록을 확인하고 있어요.');
        if (initController) initController.abort();
        const controller = new AbortController();
        initController = controller;
        try {
            const config = await jsonRequest('/api/intercity/config', controller);
            if (version !== initVersion) return;
            if (!/^\d{4}-\d{2}-\d{2}$/.test(config.today || '') || config.date_policy !== 'today_only') throw new Error('조회 가능한 날짜를 확인하지 못했어요.');
            date.value = config.today;
            if (!config.configured) throw new Error('시외버스 시간표 연결 준비 중입니다. 지금은 아래 공식 예매처에서 확인해 주세요.');
            const data = await jsonRequest('/api/intercity/terminals', controller);
            if (version !== initVersion) return;
            if (!Array.isArray(data.terminals)) throw new Error('터미널 목록의 응답 형식을 확인할 수 없어요.');
            const seen = new Set();
            terminals = data.terminals.filter(item => item && typeof item.id === 'string' && item.id && typeof item.name === 'string' && item.name && !seen.has(item.id) && seen.add(item.id))
                .map(item => ({ id: item.id, name: item.name, city: typeof item.city === 'string' ? item.city : '' }))
                .sort((a, b) => a.name.localeCompare(b.name, 'ko-KR') || a.id.localeCompare(b.id));
            if (terminals.length < 2) throw new Error('조회할 수 있는 터미널 목록이 충분하지 않아요. 공식 예매처에서 확인해 주세요.');
            if (/^\d{4}-\d{2}-\d{2}$/.test(data.today || '')) date.value = data.today;
            const preferred = terminals.find(item => item.name === '춘천');
            originFilter.value = destinationFilter.value = '';
            fillOrigin(origin.value || preferred?.id || '');
            origin.disabled = originFilter.disabled = false;
            ready = true;
            queryPrompt();
        } catch (error) {
            if (version !== initVersion || error.name === 'AbortError') return;
            announce(error instanceof Error ? error.message : '터미널 목록을 불러오지 못했어요.', 'error');
            retry.hidden = false;
        } finally {
            if (version === initVersion) {
                initBusy = false;
                initController = null;
                updateButtons();
            }
        }
        if (ready && version === initVersion && origin.value) await loadDestinations();
    }
    function displayTime(value, queryDate) {
        if (!value || !Number.isFinite(Date.parse(value))) return '미제공';
        const at = new Date(value), day = dayFormat.format(at);
        return (day !== queryDate ? day.slice(5).replace('-', '/') + ' ' : '') + timeFormat.format(at);
    }
    function renderTrip(item, queryDate) {
        const li = node('li'), article = node('article', undefined, 'intercity-trip');
        const heading = node('h4', undefined, 'intercity-trip-time');
        const departure = node('span', displayTime(item.departure_at, queryDate));
        departure.append(node('small', ' 출발'));
        const arrival = node('span', displayTime(item.arrival_at, queryDate));
        arrival.append(node('small', item.arrival_estimated === true ? ' 도착 예상' : ' 도착 예정'));
        heading.append(departure, node('span', '→'), arrival);
        const detail = node('dl', undefined, 'intercity-trip-details');
        const duration = typeof item.duration_minutes === 'number' && Number.isFinite(item.duration_minutes) && item.duration_minutes >= 0 ? item.duration_minutes.toLocaleString('ko-KR') + '분' : '미제공';
        const fare = typeof item.fare_krw === 'number' && Number.isFinite(item.fare_krw) && item.fare_krw >= 0 ? item.fare_krw.toLocaleString('ko-KR') + '원' : '미제공';
        [['버스 등급', typeof item.grade === 'string' && item.grade ? item.grade : '미제공'], ['소요시간', duration], ['성인 요금', fare]].forEach(([label, value]) => {
            const group = node('div');
            group.append(node('dt', label), node('dd', value));
            detail.append(group);
        });
        article.append(heading, detail);
        li.append(article);
        return li;
    }
    async function loadTimetable(event) {
        event.preventDefault();
        if (!validSelection() || queryBusy || initBusy) { queryPrompt(); return; }
        invalidate();
        const version = requestVersion;
        const selected = { departure_id: origin.value, arrival_id: destination.value, date: date.value };
        const from = terminals.find(item => item.id === selected.departure_id);
        const to = selectedDestination();
        queryBusy = true;
        updateButtons();
        announce(`${from.name} → ${to.name} · 오늘 배차정보를 조회하고 있어요.`);
        const controller = new AbortController();
        queryController = controller;
        try {
            const data = await jsonRequest('/api/intercity/timetable?' + new URLSearchParams(selected), controller);
            if (version !== requestVersion) return;
            if (!Array.isArray(data.trips) || !['ok', 'empty', 'partial'].includes(data.status) || data.date !== selected.date || data.trips.some(item => !item || !Number.isFinite(Date.parse(item.departure_at)))) {
                throw new Error('시간표의 응답 형식을 확인할 수 없어요. 다시 조회해 주세요.');
            }
            const ordered = [...data.trips].sort((a, b) => Date.parse(a.departure_at) - Date.parse(b.departure_at));
            const notes = Array.isArray(data.warnings) ? [...data.warnings] : [];
            if (data.status === 'partial') notes.unshift('일부 운행정보만 확인됐어요. 공식 예매처에서 전체 시간표를 함께 확인해 주세요.');
            setWarnings(notes);
            if (!ordered.length) {
                announce('이 구간의 오늘 배차 조회 결과가 없어요. 데이터 미제공이나 배차 갱신 지연일 수 있으니 아래 공식 예매처에서 확인해 주세요.', 'empty');
                return;
            }
            byId('results-heading').textContent = `${from.name} → ${to.name}`;
            byId('result-meta').textContent = `${selected.date} · ${ordered.length}건 · 한국 시간` + (data.fetched_at && Number.isFinite(Date.parse(data.fetched_at)) ? ' · 조회 ' + timeFormat.format(new Date(data.fetched_at)) : '') + (data.from_cache ? ' (최근 조회 결과)' : '');
            trips.replaceChildren(...ordered.map(item => renderTrip(item, selected.date)));
            results.hidden = false;
            announce(data.status === 'partial' ? `${ordered.length}건을 확인했어요. 일부 정보가 누락되어 참고 사항을 확인해 주세요.` : `오늘 배차정보 ${ordered.length}건을 출발 시간순으로 표시했어요.`, data.status);
        } catch (error) {
            if (version !== requestVersion || error.name === 'AbortError') return;
            if (/^\d{4}-\d{2}-\d{2}$/.test(error.body?.today || '') && error.body.today !== date.value) {
                date.value = error.body.today;
                announce('한국 시간 기준 날짜가 바뀌었어요. 오늘 시간표 조회를 다시 눌러 주세요.', 'error');
            } else {
                announce(error instanceof Error ? error.message : '시간표 조회에 실패했어요. 다시 시도해 주세요.', 'error');
            }
        } finally {
            if (version === requestVersion) {
                queryBusy = false;
                queryController = null;
                updateButtons();
            }
        }
    }
    function selectionChanged() { invalidate(); queryPrompt(); }
    async function originChanged() {
        invalidate();
        if (!origin.value) { resetDestinations(); updateButtons(); queryPrompt(); }
        else if (destinationOriginId !== origin.value) await loadDestinations();
        else queryPrompt();
    }
    origin.addEventListener('change', async () => { fillOrigin(origin.value); await originChanged(); });
    originFilter.addEventListener('input', async () => { fillOrigin(origin.value); await originChanged(); });
    destination.addEventListener('change', () => { fillDestinations(destination.value); selectionChanged(); });
    destinationFilter.addEventListener('input', () => { fillDestinations(destination.value); selectionChanged(); });
    includeStops.addEventListener('change', () => { fillDestinations(destination.value); selectionChanged(); });
    swap.addEventListener('click', async () => {
        if (!selectedDestination() || !terminals.some(item => item.id === destination.value)) return;
        const previous = origin.value;
        originFilter.value = destinationFilter.value = '';
        fillOrigin(destination.value);
        await loadDestinations({ wanted: previous, force: true });
    });
    byId('form').addEventListener('submit', loadTimetable);
    retry.addEventListener('click', initialize);
    destinationRetry.addEventListener('click', () => loadDestinations({ force: true }));
    function startIfVisible() {
        if (panel.hidden || started) return;
        started = true;
        initialize();
    }
    new MutationObserver(startIfVisible).observe(panel, { attributes: true, attributeFilter: ['hidden'] });
    startIfVisible();
})();

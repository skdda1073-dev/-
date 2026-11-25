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
    const MAX_CANDIDATES = 30;
    const date = byId('date'), time = byId('time'), submit = byId('load'), swap = byId('swap');
    const status = byId('status'), retry = byId('retry'), results = byId('results');
    const trips = byId('trips'), warnings = byId('warnings'), notes = byId('notes');
    const pickers = ['origin-picker', 'destination-picker', 'booking'].map(byId);
    let terminals = [], ready = false, started = false, initBusy = false, queryBusy = false;
    let requestVersion = 0, queryController = null, initController = null, initVersion = 0;
    let destinations = [], selectedDestinationId = '', destinationOriginId = '', destinationState = 'idle';
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
    function updateNotes() {
        notes.hidden = warnings.hidden && destinationWarnings.hidden;
        if (notes.hidden) notes.open = false;
    }
    function finishPicking(kind, value) {
        const picker = byId(kind + '-picker');
        if (value && picker.open) {
            picker.open = false;
            byId(kind + '-toggle').focus();
        }
    }
    function clearResults() {
        results.hidden = true;
        trips.replaceChildren();
        warnings.replaceChildren();
        warnings.hidden = true;
        updateNotes();
        byId('result-meta').textContent = '';
    }
    function setWarnings(values) {
        warnings.replaceChildren(...values.filter(value => typeof value === 'string' && value.trim()).map(value => node('li', value)));
        warnings.hidden = !warnings.children.length;
        updateNotes();
    }
    function selectedDestination() {
        return destinationState === 'ready' && destinationOriginId === origin.value
            ? destinations.find(item => item.id === selectedDestinationId) : null;
    }
    function validSelection() {
        return ready && /^\d{4}-\d{2}-\d{2}$/.test(date.value) && /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(time.value || '00:00')
            && origin.value && selectedDestination();
    }
    function updateButtons() {
        const destinationBusy = destinationState === 'loading';
        destinationFilter.disabled = !ready || destinationState !== 'ready' || initBusy;
        submit.disabled = !validSelection() || queryBusy || initBusy || destinationBusy;
        swap.disabled = !ready || !selectedDestination() || !terminals.some(item => item.id === selectedDestinationId) || initBusy || destinationBusy;
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
        const placeholderText = !query && !items.length ? '이름 검색' : items.length ? '선택' : '검색 결과 없음';
        const placeholder = node('option', placeholderText);
        placeholder.value = '';
        origin.replaceChildren(placeholder, ...items.map(item => {
            const option = node('option', item.name + (item.city ? ' · ' + item.city : ''));
            option.value = item.id;
            return option;
        }));
        origin.value = items.some(item => item.id === wanted) ? wanted : '';
        byId('origin-name').textContent = origin.value ? selected.name : '선택';
        originHint.textContent = query ? `검색 결과 ${matches.length.toLocaleString('ko-KR')}곳` +
            (matches.length > MAX_CANDIDATES ? ` · 상위 ${MAX_CANDIDATES}곳` : '') : '';
    }
    function terminalSearchText(value) {
        return value.normalize('NFC').toLocaleLowerCase('ko-KR').replace(/\s/g, '')
            .replace(/(?:(?:시외|고속|종합)?(?:버스)?터미널)$/, '');
    }
    function fillDestinations(wanted = selectedDestinationId) {
        const query = terminalSearchText(destinationFilter.value);
        const nameCounts = new Map();
        destinations.forEach(item => nameCounts.set(item.name, (nameCounts.get(item.name) || 0) + 1));
        const matches = destinations.filter(item => !query || terminalSearchText(item.name).includes(query)
            || terminalSearchText(item.city).includes(query));
        const selected = destinations.find(item => item.id === wanted);
        selectedDestinationId = selected?.id || '';
        byId('destination-name').textContent = selected?.name || (destinationState === 'loading' ? '확인 중' : '선택');
        destination.replaceChildren(...matches.map(item => {
            const entry = node('li');
            const button = node('button', item.name);
            button.setAttribute('type', 'button');
            button.setAttribute('data-terminal-id', item.id);
            if (nameCounts.get(item.name) > 1 && item.city) button.setAttribute('aria-label', `${item.name} · ${item.city}`);
            if (item.id === selectedDestinationId) button.setAttribute('aria-current', 'true');
            button.addEventListener('click', () => chooseDestination(item.id));
            entry.append(button);
            return entry;
        }));
        destinationHint.textContent = destinationState === 'idle' ? '출발지를 먼저 선택하세요.'
            : destinationState === 'loading' ? '불러오는 중…'
                : destinationState === 'error' ? '도착지 조회 실패'
                    : !matches.length ? '검색 결과가 없어요.' : `도착지 ${matches.length.toLocaleString('ko-KR')}곳`;
    }
    function chooseDestination(id) {
        if (destinationOriginId !== origin.value || destinationState !== 'ready' || !destinations.some(item => item.id === id)) return;
        fillDestinations(id);
        finishPicking('destination', id);
        return loadTimetable({ replacePending: true });
    }
    function resetDestinations() {
        destinationVersion += 1;
        if (destinationController) destinationController.abort();
        destinationController = null;
        destinations = [];
        selectedDestinationId = '';
        destinationOriginId = '';
        destinationState = 'idle';
        destinationFilter.value = '';
        destinationRetry.hidden = true;
        destinationWarnings.replaceChildren();
        destinationWarnings.hidden = true;
        updateNotes();
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
        if (destinationState === 'loading') announce('도착지 조회 중…');
        else if (destinationState === 'error') return;
        else if (destinationState === 'empty') announce('도착지를 확인하지 못했어요. 예매처에서 확인해 주세요.', 'empty');
        else announce('');
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
        queryPrompt();
        updateButtons();
        const controller = new AbortController();
        destinationController = controller;
        try {
            const data = await jsonRequest('/api/intercity/destinations?' + new URLSearchParams({ departure_id: selectedOrigin, scope: 'terminals' }), controller);
            if (version !== destinationVersion || origin.value !== selectedOrigin) return;
            if (!['ok', 'empty'].includes(data.status) || data.departure?.id !== selectedOrigin
                || data.display_scope !== 'terminals' || !Array.isArray(data.destinations)) {
                throw new Error('도착지 목록의 응답 형식을 확인할 수 없어요. 다시 불러와 주세요.');
            }
            const entries = data.destinations, ids = new Set();
            if (entries.some(item => !item || typeof item.name !== 'string' || !item.name.trim()
                || typeof item.id !== 'string' || !item.id || item.id === selectedOrigin || ids.has(item.id) || !ids.add(item.id))) {
                throw new Error('도착지 목록의 응답 형식을 확인할 수 없어요. 다시 불러와 주세요.');
            }
            const normalized = item => ({ id: item.id, name: item.name, city: typeof item.city === 'string' ? item.city : '' });
            destinations = entries.map(normalized).sort((a, b) => a.name.localeCompare(b.name, 'ko-KR')
                || a.city.localeCompare(b.city, 'ko-KR') || a.id.localeCompare(b.id));
            if (/^\d{4}-\d{2}-\d{2}$/.test(data.today || '')) date.value = data.today;
            destinationState = entries.length ? 'ready' : 'empty';
            const notes = Array.isArray(data.warnings) ? data.warnings : [];
            destinationWarnings.replaceChildren(...notes.filter(value => typeof value === 'string' && value.trim()).map(value => node('li', value)));
            destinationWarnings.hidden = !destinationWarnings.children.length;
            updateNotes();
            fillDestinations(wanted);
            if (wanted && !selectedDestinationId && entries.length) announce('이전 출발지가 목록에 없어요. 도착지를 선택하세요.');
            else queryPrompt();
        } catch (error) {
            if (version !== destinationVersion || origin.value !== selectedOrigin || error.name === 'AbortError') return;
            destinations = [];
            selectedDestinationId = '';
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
        resetDestinations();
        invalidate();
        origin.disabled = originFilter.disabled = destinationFilter.disabled = true;
        retry.hidden = true;
        announce('터미널 조회 중…');
        if (initController) initController.abort();
        const controller = new AbortController();
        initController = controller;
        try {
            const config = await jsonRequest('/api/intercity/config', controller);
            if (version !== initVersion) return;
            if (!/^\d{4}-\d{2}-\d{2}$/.test(config.today || '') || config.date_policy !== 'today_only') throw new Error('조회 가능한 날짜를 확인하지 못했어요.');
            date.value = config.today;
            if (!config.configured) throw new Error('시간표 연결 준비 중이에요. 공식 예매처에서 확인해 주세요.');
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
        departure.append(node('small', ' 출발', 'sr-only'));
        const arrival = node('span', displayTime(item.arrival_at, queryDate));
        arrival.append(item.arrival_estimated === true ? node('small', ' 도착 예상') : node('small', ' 도착 예정', 'sr-only'));
        heading.append(departure, node('span', '→'), arrival);
        const detail = node('dl', undefined, 'intercity-trip-details');
        const duration = typeof item.duration_minutes === 'number' && Number.isFinite(item.duration_minutes) && item.duration_minutes >= 0 ? item.duration_minutes.toLocaleString('ko-KR') + '분' : '미제공';
        const fare = typeof item.fare_krw === 'number' && Number.isFinite(item.fare_krw) && item.fare_krw >= 0 ? item.fare_krw.toLocaleString('ko-KR') + '원' : '미제공';
        [['버스 등급', typeof item.grade === 'string' && item.grade ? item.grade : '미제공'], ['소요시간', duration], ['성인 요금', fare]].forEach(([label, value]) => {
            const group = node('div');
            group.append(node('dt', label, 'sr-only'), node('dd', value));
            detail.append(group);
        });
        article.append(heading, detail);
        li.append(article);
        return li;
    }
    async function loadTimetable({ replacePending = false } = {}) {
        if (!validSelection() || (queryBusy && !replacePending) || initBusy) { queryPrompt(); return; }
        invalidate();
        const version = requestVersion;
        const selected = { departure_id: origin.value, arrival_id: selectedDestinationId, date: date.value };
        const fromTime = time.value || '00:00';
        const cutoff = Date.parse(`${selected.date}T${fromTime}:00+09:00`);
        const from = terminals.find(item => item.id === selected.departure_id);
        const to = selectedDestination();
        queryBusy = true;
        updateButtons();
        announce('시간표 조회 중…');
        const controller = new AbortController();
        queryController = controller;
        try {
            const data = await jsonRequest('/api/intercity/timetable?' + new URLSearchParams(selected), controller);
            if (version !== requestVersion) return;
            if (!Array.isArray(data.trips) || !['ok', 'empty', 'partial'].includes(data.status) || data.date !== selected.date || data.trips.some(item => !item || !Number.isFinite(Date.parse(item.departure_at)))) {
                throw new Error('시간표의 응답 형식을 확인할 수 없어요. 다시 조회해 주세요.');
            }
            const ordered = data.trips.filter(item => Date.parse(item.departure_at) >= cutoff)
                .sort((a, b) => Date.parse(a.departure_at) - Date.parse(b.departure_at));
            const notes = Array.isArray(data.warnings) ? [...data.warnings] : [];
            if (data.status === 'partial') notes.unshift('일부 운행정보만 확인됐어요. 예매처에서 전체 시간표를 확인하세요.');
            setWarnings(notes);
            if (!ordered.length) {
                announce(data.trips.length ? `${fromTime} 이후 출발편이 없어요.`
                    : '조회 결과가 없어요. 예매처에서 확인해 주세요.', 'empty');
                return;
            }
            byId('results-heading').textContent = `${from.name} → ${to.name}`;
            const timeLabel = fromTime === '00:00' ? '' : `${fromTime} 이후 · `;
            byId('result-meta').textContent = `${timeLabel}${ordered.length}편` + (data.fetched_at && Number.isFinite(Date.parse(data.fetched_at)) ? ' · ' + timeFormat.format(new Date(data.fetched_at)) + ' 조회' : '') + (data.from_cache ? ' (최근 조회 결과)' : '');
            trips.replaceChildren(...ordered.map(item => renderTrip(item, selected.date)));
            results.hidden = false;
            announce(data.status === 'partial' ? '일부 시간표만 확인됐어요.' : '', data.status);
        } catch (error) {
            if (version !== requestVersion || error.name === 'AbortError') return;
            if (/^\d{4}-\d{2}-\d{2}$/.test(error.body?.today || '') && error.body.today !== date.value) {
                date.value = error.body.today;
                announce('날짜가 바뀌었어요. 다시 조회해 주세요.', 'error');
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
    origin.addEventListener('change', async () => {
        fillOrigin(origin.value);
        finishPicking('origin', origin.value);
        await originChanged();
    });
    originFilter.addEventListener('input', async () => { fillOrigin(origin.value); await originChanged(); });
    destinationFilter.addEventListener('input', () => fillDestinations());
    destinationFilter.addEventListener('keydown', event => {
        if (event.isComposing || (event.key !== 'Enter' && event.key !== 'ArrowDown')) return;
        event.preventDefault();
        const first = destination.querySelector('button');
        if (event.key === 'Enter' && destination.children.length === 1) first?.click();
        else first?.focus();
    });
    time.addEventListener('input', selectionChanged);
    time.addEventListener('change', selectionChanged);
    swap.addEventListener('click', async () => {
        if (swap.disabled) return;
        const previous = origin.value;
        originFilter.value = destinationFilter.value = '';
        fillOrigin(selectedDestinationId);
        await loadDestinations({ wanted: previous, force: true });
    });
    byId('form').addEventListener('submit', event => {
        event.preventDefault();
        return loadTimetable();
    });
    root.addEventListener('click', event => {
        for (const picker of pickers) if (picker.open && !picker.contains(event.target)) picker.open = false;
    });
    root.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        const open = pickers.find(picker => picker.open);
        if (!open) return;
        event.preventDefault();
        event.stopPropagation();
        open.open = false;
        open.querySelector('summary').focus();
    });
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

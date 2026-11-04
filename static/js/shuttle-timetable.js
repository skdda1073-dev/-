/** Local shuttle timetable only. Does not access map, GPS or journey state. */
(() => {
    'use strict';
    const root = document.getElementById('shuttle-timetable');
    if (!root) return;
    const panel = document.getElementById('transport-panel-shuttle');
    const el = name => root.querySelector('#shuttle-' + name);
    const route = el('route'), direction = el('direction'), at = el('at');
    const board = el('board'), alight = el('alight'), query = el('query');
    let catalog = [], started = false, fullVersion = 0, segmentVersion = 0;
    let fullController, segmentController, fullAt = '';

    function node(tag, text, className) {
        const item = document.createElement(tag);
        item.textContent = text;
        if (className) item.className = className;
        return item;
    }
    function status(target, text, state = 'ok') {
        target.textContent = text;
        target.dataset.state = state;
    }
    function options(select, values, previous = select.value) {
        select.replaceChildren(...values.map(item => {
            const option = node('option', item.label);
            option.value = item.value;
            return option;
        }));
        if (values.some(item => item.value === previous)) select.value = previous;
        select.disabled = values.length === 0;
    }
    function setDirections() {
        options(direction, catalog.filter(item => item.route_name === route.value)
            .map(item => ({ value: item.direction, label: item.direction })));
    }
    function koreanNow() {
        const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul',
            year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
            .formatToParts(new Date());
        const values = Object.fromEntries(parts.map(item => [item.type, item.value]));
        return `${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}`;
    }
    function queryAt() {
        if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(at.value) || !at.checkValidity()) {
            throw new Error('조회 기준 날짜·시각을 확인해 주세요. 한국 시간으로 입력해 주세요.');
        }
        return at.value + ':00+09:00';
    }
    function warnings(target, data) {
        const messages = [...data.warnings];
        if (data.calendar_status !== 'verified') messages.unshift('조회 날짜의 운행 조건 미확인 · 시간표 참고값입니다.');
        if (data.profile_status?.status === 'error') messages.unshift('공식 보강정보를 적용하지 못했습니다. 원본 시간표만 표시합니다.');
        if (data.conditions?.eligibility === null) messages.push('탑승 대상은 미확인입니다.');
        if (data.conditions?.fare === null) messages.push('요금은 미확인입니다.');
        target.replaceChildren(...[...new Set(messages)].map(text => node('li', text)));
    }
    const operation = value => ({ verified: '확인된 운행 조건에 해당', not_operating: '조회일 미운행 조건에 해당',
        unknown: '조회일 운행 조건 미확인' })[value] || '조회일 운행 조건 미확인';
    function summary(data, count) {
        const messages = {
            no_connection: '해당 노선·방면·정류장 순서에서 연결되는 구간이 없습니다.',
            unmatched: '선택한 정류장이 시간표에 없습니다. 노선·방면을 다시 선택해 주세요.',
            ambiguous_stop: '같은 이름의 정류장이 여러 개입니다. 정류장 ID를 구분해 선택해 주세요.',
            not_operating: '조회일은 해당 시간표의 미운행 조건에 해당합니다.',
            no_departures_remaining: '조회 기준 시각 이후 남은 시간표 후보가 없습니다. 다음 운행일은 추정하지 않습니다.',
        };
        const text = messages[data.status] || `시간표 참고 후보 ${count}건`;
        return data.calendar_status === 'verified' ? text : text + ' 조회 날짜의 운행 조건은 미확인입니다.';
    }
    async function fetchData(params, signal) {
        let response;
        try {
            response = await fetch('/shuttle_timetable?' + params, { signal, headers: { Accept: 'application/json' } });
        } catch (error) {
            if (signal.aborted) throw error;
            throw new Error('시간표 조회 오류 · 서버에 연결하지 못했습니다. 연결 상태를 확인하고 다시 시도해 주세요.');
        }
        const data = await response.json().catch(() => null);
        if (!response.ok) throw new Error(`시간표 조회 오류 (HTTP ${response.status}). 다시 시도해 주세요.`);
        const states = ['ok', 'unknown_calendar', 'no_connection', 'unmatched', 'ambiguous_stop', 'not_operating', 'no_departures_remaining'];
        if (!data || data.mode !== 'shuttle_timetable' || !states.includes(data.status)
                || !['verified', 'unknown'].includes(data.calendar_status)
                || typeof data.query_at !== 'string' || !Array.isArray(data.warnings)
                || !data.warnings.every(item => typeof item === 'string')
                || !Array.isArray(data.routes) || !data.routes.every(item => item && typeof item.route_name === 'string' && typeof item.direction === 'string')
                || !Array.isArray(data.stops) || !data.stops.every(item => item && typeof item.id === 'string' && typeof item.name === 'string')
                || !Array.isArray(data.trips) || !data.trips.every(item => item && typeof item.id === 'string'
                    && typeof item.route_name === 'string' && typeof item.direction === 'string' && Array.isArray(item.events)
                    && item.events.every(event => event && typeof event.stop_id === 'string' && typeof event.name === 'string'
                        && (event.time === null || typeof event.time === 'string')))
                || !Array.isArray(data.timetable_candidates) || !data.timetable_candidates.every(item => item && typeof item.trip_id === 'string'
                    && typeof item.board?.name === 'string' && typeof item.alight?.name === 'string'
                    && typeof item.scheduled_stop_at === 'string')) {
            throw new Error('시간표 응답 형식을 확인할 수 없습니다. 다시 시도해 주세요.');
        }
        return data;
    }
    function clearSegment() {
        ++segmentVersion;
        segmentController?.abort();
        el('candidates').replaceChildren();
        el('segment-warnings').replaceChildren();
        status(el('segment-status'), '타는 정류장과 내리는 정류장을 선택한 뒤 구간을 조회해 주세요.');
        query.disabled = board.disabled || alight.disabled || !fullAt;
    }
    function renderFull(data) {
        const trips = data.trips.filter(item => item.route_name === route.value && item.direction === direction.value);
        const stopIds = new Set(trips.flatMap(item => item.events.map(event => event.stop_id)));
        const stops = data.stops.filter(item => stopIds.has(item.id));
        const nameCounts = new Map();
        stops.forEach(item => nameCounts.set(item.name, (nameCounts.get(item.name) || 0) + 1));
        const choices = stops.map(item => ({ value: item.id,
            label: nameCounts.get(item.name) > 1 ? `${item.name} · ID ${item.id}` : item.name }));
        options(board, choices);
        options(alight, choices, choices.some(item => item.value === alight.value) ? alight.value : choices.at(-1)?.value);
        el('trips').replaceChildren(...trips.map((trip, index) => {
            const article = node('article', '', 'shuttle-trip');
            article.dataset.tripId = trip.id;
            article.append(node('h4', `${index + 1}회차 · ${trip.route_name} · ${trip.direction}`),
                node('p', operation(trip.operation_status_at_query_date), 'shuttle-note'));
            if (!trip.routing_usable) article.append(node('p', '정류장 연결이 확인되지 않아 구간 후보 계산에서 제외되는 회차입니다.', 'shuttle-note'));
            const list = document.createElement('ol');
            trip.events.forEach(event => {
                const label = event.time_role === 'departure' && trip.sequence_basis === 'official_route_endpoints'
                    ? '예정 출발' : '시간표 시각';
                list.append(node('li', `${event.name} · ${label} ${event.time ?? '미확인'}`));
            });
            article.append(list);
            return article;
        }));
        if (!trips.length) el('trips').append(node('p', '해당 노선·방면의 시간표가 없습니다.', 'shuttle-note'));
        const source = data.source || {}, profile = source.official_profile;
        const coverage = data.conditions?.calendar_coverage;
        el('source').textContent = `조회 기준: ${data.query_at} (한국 시간) · 원본: ${source.filename || '출처 미확인'}`
            + (profile?.checked_on ? ` · 프로필 확인일: ${profile.checked_on}` : ' · 프로필 확인일 미확인')
            + (coverage ? ` · 운행 달력 확인 범위: ${coverage.start} ~ ${coverage.end}` : ' · 운행 달력 확인 범위 미확인');
        warnings(el('warnings'), data);
        status(el('status'), `전체 시간표 ${trips.length}회차 · ` + (data.calendar_status === 'verified'
            ? '조회일 운행 조건은 각 회차에서 확인하세요.' : '조회일 운행 조건 미확인.'));
        clearSegment();
    }
    async function loadFull() {
        const version = ++fullVersion;
        fullController?.abort();
        fullController = new AbortController();
        fullAt = '';
        clearSegment();
        board.disabled = alight.disabled = query.disabled = true;
        el('trips').replaceChildren();
        el('warnings').replaceChildren();
        el('source').textContent = '';
        status(el('status'), '전체 시간표를 불러오고 있어요.', 'loading');
        root.setAttribute('aria-busy', 'true');
        try {
            const requestedAt = queryAt();
            const params = new URLSearchParams({ at: requestedAt });
            if (catalog.length) { params.set('route', route.value); params.set('direction', direction.value); }
            const data = await fetchData(params, fullController.signal);
            if (version !== fullVersion) return;
            if (!catalog.length) {
                catalog = data.routes;
                options(route, [...new Set(catalog.map(item => item.route_name))].map(value => ({ value, label: value })));
                setDirections();
            }
            fullAt = requestedAt;
            renderFull(data);
        } catch (error) {
            if (version !== fullVersion) return;
            status(el('status'), error instanceof Error ? error.message : '시간표를 불러오지 못했습니다.', 'error');
        } finally {
            if (version === fullVersion) root.setAttribute('aria-busy', 'false');
        }
    }
    function localTime(value) {
        if (value === null || value === undefined) return '미확인';
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? '미확인' : new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul',
            year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(date);
    }
    async function loadSegment(event) {
        event.preventDefault();
        if (!fullAt || query.disabled) return;
        const version = ++segmentVersion;
        segmentController?.abort();
        segmentController = new AbortController();
        query.disabled = true;
        el('candidates').replaceChildren();
        el('segment-warnings').replaceChildren();
        status(el('segment-status'), '구간 후보를 조회하고 있어요.', 'loading');
        try {
            if (fullAt !== queryAt()) throw new Error('날짜·시각을 변경했습니다. 전체 시간표를 먼저 다시 조회해 주세요.');
            if (!board.value || !alight.value || board.value === alight.value) throw new Error('서로 다른 타는 정류장과 내리는 정류장을 선택해 주세요.');
            const params = new URLSearchParams({ route: route.value, direction: direction.value,
                board: board.value, alight: alight.value, at: fullAt });
            const data = await fetchData(params, segmentController.signal);
            if (version !== segmentVersion) return;
            warnings(el('segment-warnings'), data);
            status(el('segment-status'), summary(data, data.timetable_candidates.length));
            el('candidates').replaceChildren(...data.timetable_candidates.map(item => {
                const article = node('article', '', 'shuttle-candidate');
                article.dataset.tripId = item.trip_id;
                article.append(node('h4', `${item.board.name} → ${item.alight.name}`),
                    node('p', (item.scheduled_departure_at ? '예정 출발: ' + localTime(item.scheduled_departure_at)
                        : '타는 정류장 시간표 시각: ' + localTime(item.scheduled_stop_at))),
                    node('p', item.scheduled_arrival_at ? '예정 도착: ' + localTime(item.scheduled_arrival_at)
                        : '내리는 정류장 시간표 시각: ' + localTime(item.scheduled_alight_stop_at)),
                    node('p', item.ride_min == null ? '승차 소요시간: 미확인' : `시간표 기준 승차 소요시간: ${item.ride_min}분`),
                    node('p', item.service_date_confirmed ? '확인된 운행 조건에 해당 · 실제 탑승 보장 아님' : '운행 조건 미확인 · 시간표 참고값', 'shuttle-note'));
                return article;
            }));
        } catch (error) {
            if (version !== segmentVersion) return;
            status(el('segment-status'), error instanceof Error ? error.message : '구간 조회 오류가 발생했습니다.', 'error');
        } finally {
            if (version === segmentVersion) query.disabled = !fullAt || board.disabled || alight.disabled;
        }
    }
    route.addEventListener('change', () => { setDirections(); loadFull(); });
    direction.addEventListener('change', loadFull);
    at.addEventListener('change', loadFull);
    board.addEventListener('change', clearSegment);
    alight.addEventListener('change', clearSegment);
    el('refresh').addEventListener('click', loadFull);
    el('segment-form').addEventListener('submit', loadSegment);
    function startIfVisible() {
        if (started || panel.hidden) return;
        started = true;
        at.value = koreanNow();
        loadFull();
    }
    new MutationObserver(startIfVisible).observe(panel, { attributes: true, attributeFilter: ['hidden'] });
    startIfVisible();
})();

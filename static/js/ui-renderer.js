/** Results presentation: keep unknown waits unknown; map and requests stay in their controllers. */
(function () {
    'use strict';

    const boundContainers = new WeakSet();
    let lastJourneyResponse = null;

    function text(value) {
        if (typeof value === 'string') return value;
        if (typeof value === 'number' && Number.isFinite(value)) return String(value);
        return '';
    }

    function escapeHtml(value) {
        return text(value).replace(/[&<>"']/g, character => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[character]));
    }

    function record(value) {
        return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    }

    function number(value) {
        if (typeof value !== 'number' && typeof value !== 'string') return null;
        if (typeof value === 'string' && !value.trim()) return null;
        const parsed = Number(value);
        return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
    }

    function minutes(value) {
        const parsed = number(value);
        if (parsed === null) return '시간 미확인';
        if (parsed > 0 && parsed < 1) return '1분 미만';
        return `${Math.ceil(parsed)}분`;
    }

    function distance(value) {
        const parsed = number(value);
        if (parsed === null) return '';
        return parsed >= 1000 ? `${(parsed / 1000).toFixed(1)}km` : `${Math.round(parsed)}m`;
    }

    function icon(name) {
        return `<svg class="icon" aria-hidden="true" focusable="false"><use href="#icon-${name}"></use></svg>`;
    }

    function friendlyMessage(value) {
        const message = text(value) || text(record(value).message);
        if (/PUBLIC_DATA_KEY/.test(message)) return '실시간 버스 도착정보를 확인할 수 없어 대기 시간과 전체 도착 시간은 표시하지 않아요.';
        if (/TMAP_APP_KEY|TMAP_KEY_MISSING/.test(message)) return '도보·장소 정보를 연결하지 못했어요. 잠시 후 다시 시도해 주세요.';
        return message.replace(/CSV/g, '노선 데이터');
    }

    function warningList(values) {
        const entries = Array.isArray(values) ? values : (values ? [values] : []);
        return [...new Set(entries.map(friendlyMessage).map(value => value.trim()).filter(Boolean))];
    }

    function warningsMarkup(values, className) {
        const entries = warningList(values);
        return entries.length ? `<ul class="${className}">${entries.map(value => `<li>${escapeHtml(value)}</li>`).join('')}</ul>` : '';
    }

    function formatKoreanTime(date) {
        if (!date || typeof date.getTime !== 'function' || !Number.isFinite(date.getTime())) return '--:--';
        const hours = date.getHours();
        return `${hours >= 12 ? '오후' : '오전'} ${hours % 12 || 12}:${String(date.getMinutes()).padStart(2, '0')}`;
    }

    function format24Time(date) {
        if (!date || typeof date.getTime !== 'function' || !Number.isFinite(date.getTime())) return '--:--';
        return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
    }

    function setResultsState(heading, count) {
        const section = document.getElementById('results-section');
        const initial = document.getElementById('initial-state');
        const headingElement = document.getElementById('results-heading');
        const countElement = document.getElementById('result-count');
        if (section) { section.hidden = false; section.style.display = ''; }
        if (initial) initial.hidden = true;
        if (headingElement) headingElement.textContent = heading;
        if (countElement) {
            countElement.textContent = Number.isInteger(count) ? `${count}개` : '';
            countElement.hidden = !Number.isInteger(count);
        }
    }

    function stateCard(kind, title, message) {
        const iconName = kind === 'error' ? 'warning' : kind === 'notice' ? 'pin' : 'search';
        return `<div class="result-card result-message result-message--${kind}" role="${kind === 'error' ? 'alert' : 'status'}">
            <span class="result-message-icon">${icon(iconName)}</span><h3>${escapeHtml(title)}</h3><p>${escapeHtml(message)}</p></div>`;
    }

    function resultsNotes(values) {
        const items = warningsMarkup(values, 'results-note-list');
        return items ? `<details class="results-notes"><summary>${icon('warning')}<span>경로 정보 안내</span>${icon('chevron-down')}</summary>${items}</details>` : '';
    }

    function routeBadge(leg, alternate) {
        const route = text(leg.route_no).trim() || '번호 미확인';
        return `<span class="route-badge${alternate ? ' route-badge--alternate' : ''}">${icon('bus')}<span>${escapeHtml(route)}</span></span>`;
    }

    function routeTiming(journey, legs) {
        const access = record(journey.access_walk);
        const egress = record(journey.egress_walk);
        let walk = number(journey.walk_min);
        let ride = number(journey.ride_min);
        if (walk === null && number(access.minutes) !== null && number(egress.minutes) !== null) walk = number(access.minutes) + number(egress.minutes);
        if (ride === null && legs.every(leg => number(leg.ride_min) !== null)) ride = legs.reduce((sum, leg) => sum + number(leg.ride_min), 0);
        const directionUnknown = journey.total_unavailable_reason === 'direction_unverified' || legs.some(leg => leg.direction_verified !== true || leg.arrival_reference_only === true);
        const waitUnknown = directionUnknown || number(journey.wait_min) === null || legs.some(leg => number(leg.wait_min) === null);
        const complete = number(journey.total_min) !== null && !waitUnknown && !journey.total_unavailable_reason && journey.has_live_departure === true;
        return { access, egress, walk, ride, directionUnknown, waitUnknown, complete,
            duration: complete ? number(journey.total_min) : (walk !== null && ride !== null ? walk + ride : null) };
    }

    function timeHeadline(timing) {
        const label = timing.complete ? '총 소요 추정' : timing.duration === null ? '소요 시간' : '이동 구간 추정';
        const duration = timing.duration === null
            ? '<strong class="route-duration-unknown">시간 미확인</strong>'
            : `<span class="route-duration-prefix">약</span><strong>${timing.duration > 0 && timing.duration < 1 ? '1분 미만' : Math.ceil(timing.duration)}</strong>${timing.duration > 0 && timing.duration < 1 ? '' : '<span class="route-duration-unit">분</span>'}`;
        const detail = timing.complete ? '도보·버스·대기 포함' : '대기·환승 여유 제외 · 총 소요 미확인';
        return `<span class="route-duration-label">${label}</span><span class="route-duration">${duration}</span><span class="route-duration-note">${detail}</span>`;
    }

    function walkStep(walk, title, destination, finalStep) {
        const info = record(walk);
        const duration = number(info.minutes);
        const metres = distance(info.distance_m);
        const detail = duration === null ? '도보 시간 미확인' : `도보 약 ${minutes(duration)}${metres ? ` · ${metres}` : ''}`;
        return `<li class="journey-step journey-step--walk${finalStep ? ' journey-step--last' : ''}">
            <span class="journey-step-icon">${icon(finalStep ? 'pin' : 'walk')}</span><div class="journey-step-content"><span class="journey-step-label">${escapeHtml(title)}</span><strong>${escapeHtml(destination)}</strong><p>${escapeHtml(detail)}</p></div></li>`;
    }

    function busStep(leg, index) {
        const board = record(leg.board);
        const alight = record(leg.alight);
        const verified = leg.direction_verified === true && leg.arrival_reference_only !== true;
        const wait = verified ? number(leg.wait_min) : null;
        const arrival = verified ? number(leg.arrival_min) : null;
        const stopCount = number(leg.stop_count);
        const ride = number(leg.ride_min);
        const facts = [];
        if (stopCount !== null) facts.push(`${Math.round(stopCount)}개 정류장`);
        facts.push(ride === null ? '탑승 시간 미확인' : `탑승 약 ${minutes(ride)}`);
        const waitText = wait === null ? '대기 시간 미확인' : `승차 대기 약 ${minutes(wait)}`;
        const arrivalText = arrival === null ? '' : ` · 조회 시점 기준 약 ${minutes(arrival)} 후 도착`;
        const routeId = leg.route_number_verified === false && text(leg.route_id)
            ? `<p class="journey-route-id">노선 ID ${escapeHtml(leg.route_id)} · 노선번호 확인 필요</p>` : '';
        return `<li class="journey-step journey-step--bus${index > 0 ? ' journey-step--alternate' : ''}">
            <span class="journey-step-icon">${icon('bus')}</span><div class="journey-step-content">
                <div class="journey-leg-heading">${routeBadge(leg, index > 0)}<span>${index > 0 ? '환승' : '승차'}</span></div>
                <strong>${escapeHtml(text(board.name) || '승차 정류장')}<span class="journey-stop-arrow">→</span>${escapeHtml(text(alight.name) || '하차 정류장')}</strong>
                <p>${escapeHtml(facts.join(' · '))}</p><p class="journey-direction">${escapeHtml(text(leg.direction) || '운행 방향 정보 미확인')}</p>
                <p class="journey-wait${wait === null ? ' journey-wait--unknown' : ''}">${escapeHtml(waitText + arrivalText)}</p>${routeId}</div></li>`;
    }

    function journeyTimeline(journey, legs, response, timing) {
        const origin = record(response.origin);
        const destination = record(response.destination);
        const board = record(legs[0].board);
        let html = walkStep(timing.access, text(origin.name) || '출발지에서 이동', text(board.name) || '승차 정류장', false);
        legs.forEach((leg, index) => {
            if (index > 0) {
                const buffer = number(journey.transfer_buffer_min);
                const bufferText = buffer === null ? '환승 여유 시간을 확인하세요' : `환승 여유 총 ${minutes(buffer)} 반영`;
                html += `<li class="journey-step journey-step--transfer"><span class="journey-step-icon">${icon('swap')}</span><div class="journey-step-content"><strong>같은 정류장에서 환승</strong><p>${escapeHtml(bufferText)}</p></div></li>`;
            }
            html += busStep(leg, index);
        });
        html += walkStep(timing.egress, '하차 후 도보', text(destination.name) || '목적지', true);
        return `<ol class="journey-timeline">${html}</ol>`;
    }

    function journeyCard(journey, index, mapIndex, response) {
        const legs = journey.legs.map(record);
        const timing = routeTiming(journey, legs);
        const board = record(legs[0].board);
        const accessMinutes = number(timing.access.minutes);
        const accessDistance = distance(timing.access.distance_m);
        const accessText = accessMinutes === null ? '도보 시간 미확인' : `도보 약 ${minutes(accessMinutes)}${accessDistance ? ` · ${accessDistance}` : ''}`;
        const transfers = Math.max(0, legs.length - 1);
        const transferLabel = transfers ? `환승 ${transfers}회` : '환승 없음';
        const chain = legs.map((leg, legIndex) => `${legIndex ? `<span class="route-chain-arrow">${icon('chevron-down')}</span>` : ''}${routeBadge(leg, legIndex > 0)}`).join('');
        const notes = [];
        if (timing.directionUnknown) notes.push('운행 방향 확인 필요');
        if (timing.waitUnknown) notes.push('대기 시간 미확인');
        if (legs.some(leg => leg.route_number_verified === false)) notes.push('노선번호 확인 필요');
        if (!notes.length) notes.push('도착 예상은 운행 상황에 따라 달라질 수 있어요');
        const warnings = warningList(journey.warnings);
        if (timing.directionUnknown) warnings.unshift('정류장 순서를 바탕으로 찾은 경로 후보예요. 상·하행과 종점·회차에서 계속 탈 수 있는지는 승차 전에 확인해 주세요.');
        return `<article class="result-card route-card${index === 0 ? ' route-card--first' : ''}" aria-labelledby="route-title-${index}">
            <div class="route-card-top"><span class="route-rank">경로 ${String(index + 1).padStart(2, '0')}</span><button class="route-map-button" type="button" data-journey-index="${mapIndex}" aria-label="경로 ${index + 1} 지도에서 보기">${icon('map')}<span>지도에서 보기</span>${icon('arrow-up-right')}</button></div>
            <div class="route-overview"><h3 id="route-title-${index}">${timeHeadline(timing)}</h3><span class="route-transfer">${transferLabel}</span></div>
            <div class="route-chain"><span class="route-chain-walk">${icon('walk')}<span>${timing.walk === null ? '도보 미확인' : `총 도보 ${minutes(timing.walk)}`}</span></span><span class="route-chain-arrow">${icon('chevron-down')}</span>${chain}</div>
            <div class="boarding-guide"><span class="boarding-guide-icon">${icon('walk')}</span><div><span class="boarding-guide-label">승차 정류장까지</span><strong>${escapeHtml(text(board.name) || '승차 정류장 미확인')}</strong><p>${escapeHtml(accessText)}</p></div></div>
            <p class="route-note${timing.directionUnknown || timing.waitUnknown ? ' route-note--warning' : ''}">${icon(timing.directionUnknown || timing.waitUnknown ? 'warning' : 'clock')}<span>${escapeHtml(notes.join(' · '))}</span></p>
            <details class="route-details"><summary><span>경로 자세히</span>${icon('chevron-down')}</summary>${journeyTimeline(journey, legs, response, timing)}${warningsMarkup(warnings, 'route-warnings')}</details></article>`;
    }

    function nearbyBus(bus) {
        const route = text(bus.route).trim() || '번호 미확인';
        const arrival = number(bus.min_left);
        const previousStops = number(bus.prev_cnt);
        const status = ['relax', 'hurry', 'miss'].includes(bus.status_type) ? bus.status_type : 'unknown';
        const arrivalText = arrival === null ? '도착 미확인' : arrival === 0 ? '곧 도착 예상' : `약 ${minutes(arrival)} 후`;
        const statusText = status === 'hurry' ? '승차 여유 부족' : status === 'miss' ? '다음 배차 확인' : '';
        const guide = friendlyMessage(bus.guide_msg);
        return `<div class="bus-row"><div class="bus-row-main"><span class="route-badge route-badge--nearby">${icon('bus')}<span>${escapeHtml(route)}</span></span><div class="bus-arrival${arrival === null || status === 'miss' ? ' bus-arrival--muted' : ''}"><strong>${escapeHtml(arrivalText)}</strong>${previousStops === null ? '' : `<span>${Math.round(previousStops)}개 정류장 전</span>`}</div></div>
            ${text(bus.destination) ? `<p class="bus-destination">${escapeHtml(bus.destination)}</p>` : ''}
            ${statusText || guide ? `<p class="bus-guide bus-guide--${status}">${statusText ? `<span>${statusText}</span>` : ''}${escapeHtml(guide)}</p>` : ''}</div>`;
    }

    function stationCard(station, index) {
        const buses = Array.isArray(station.buses) ? station.buses.filter(value => value && typeof value === 'object' && !Array.isArray(value)).map(record) : [];
        const walk = number(station.walk_min);
        const metres = distance(station.walk_dist);
        const stationNumber = text(station.station_no);
        const walkText = walk === null ? '도보 미확인' : `도보 약 ${minutes(walk)}`;
        const meta = [stationNumber ? `정류장 ${stationNumber}` : '', metres].filter(Boolean).join(' · ');
        let busList = buses.slice(0, 3).map(nearbyBus).join('');
        if (buses.length > 3) busList += `<details class="station-more"><summary><span>버스 ${buses.length - 3}개 더 보기</span>${icon('chevron-down')}</summary>${buses.slice(3).map(nearbyBus).join('')}</details>`;
        if (!buses.length) busList = `<div class="station-no-arrivals">${icon('clock')}<div><strong>${station.arrival_error ? '도착정보를 확인할 수 없어요' : '현재 표시할 도착정보가 없어요'}</strong><p>정류장 안내에서 다음 배차를 확인해 주세요.</p></div></div>`;
        return `<article class="result-card station-card" aria-labelledby="station-title-${index}">
            <div class="station-header"><div><span class="station-kicker">가까운 정류장 ${String(index + 1).padStart(2, '0')}</span><h3 id="station-title-${index}" class="station-name">${escapeHtml(text(station.station_name) || '이름 미확인 정류장')}</h3>${meta ? `<p class="station-meta">${escapeHtml(meta)}</p>` : ''}</div><span class="station-walk">${icon('walk')}${escapeHtml(walkText)}</span></div>
            <p class="station-direction">${icon('arrow-up-right')}<span>${escapeHtml(text(station.direction) || '승차 방향은 정류장 안내를 확인해 주세요.')}</span></p><div class="bus-list">${busList}</div>
            ${station.arrival_error && buses.length ? `<p class="station-arrival-warning">${icon('warning')}일부 도착정보를 확인하지 못했어요.</p>` : ''}</article>`;
    }

    function bindMapActions(container) {
        if (boundContainers.has(container) || typeof container.addEventListener !== 'function') return;
        container.addEventListener('click', event => {
            const target = event.target && typeof event.target.closest === 'function' ? event.target.closest('[data-journey-index]') : null;
            if (!target || !container.contains(target) || !lastJourneyResponse) return;
            const index = Number(target.getAttribute('data-journey-index'));
            if (!Number.isInteger(index) || index < 0 || index >= lastJourneyResponse.journeys.length) return;
            if (typeof window.showJourneyOnMap === 'function') {
                const shown = window.showJourneyOnMap(lastJourneyResponse, index);
                if (shown && typeof window.focusMap === 'function') window.focusMap();
            }
        });
        boundContainers.add(container);
    }

    function renderResults(data) {
        const container = document.getElementById('result-container');
        if (!container) return;
        bindMapActions(container);
        const payload = record(data);
        const loading = document.getElementById('loading');
        container.hidden = false;
        lastJourneyResponse = null;
        if (payload.loading === true) {
            setResultsState('경로를 찾고 있어요', null);
            container.innerHTML = '';
            container.setAttribute('aria-busy', 'true');
            if (loading) {
                loading.hidden = false;
                loading.style.display = 'flex';
                const loadingText = document.getElementById('loading-text');
                if (loadingText) loadingText.textContent = friendlyMessage(payload.message || payload.notice) || '이동할 수 있는 경로를 살펴보고 있어요';
            } else container.innerHTML = stateCard('notice', '경로를 찾고 있어요', '잠시만 기다려 주세요.');
            return;
        }
        if (loading) { loading.hidden = true; loading.style.display = 'none'; }
        container.setAttribute('aria-busy', 'false');
        if (payload.error) {
            setResultsState('다시 확인해 주세요', null);
            container.innerHTML = stateCard('error', '정보를 불러오지 못했어요', friendlyMessage(payload.error) || '잠시 후 다시 시도해 주세요.');
            return;
        }
        if (payload.notice) {
            setResultsState('출발 준비', null);
            container.innerHTML = stateCard('notice', '여기부터 확인해 주세요', friendlyMessage(payload.notice));
            return;
        }
        if (payload.mode === 'journeys' || Array.isArray(payload.journeys)) {
            const journeys = Array.isArray(payload.journeys) ? payload.journeys : [];
            const candidates = journeys.map((value, index) => ({ value: record(value), index })).filter(item => Array.isArray(item.value.legs) && item.value.legs.length && item.value.legs.every(leg => leg && typeof leg === 'object' && !Array.isArray(leg)));
            setResultsState('이동 경로', candidates.length);
            if (!candidates.length) {
                container.innerHTML = stateCard('empty', '검색 범위 안에서 경로를 찾지 못했어요', '출발지나 목적지를 바꿔 다시 찾아보세요. 가까운 정류장도 확인할 수 있어요.') + resultsNotes(payload.warnings);
                return;
            }
            lastJourneyResponse = payload;
            container.innerHTML = candidates.map((item, index) => journeyCard(item.value, index, item.index, payload)).join('') + resultsNotes(payload.warnings);
            return;
        }
        const stations = Array.isArray(data) ? data.filter(value => value && typeof value === 'object' && !Array.isArray(value)).map(record) : [];
        setResultsState('가까운 정류장', stations.length);
        if (!stations.length) {
            container.innerHTML = stateCard('empty', '주변 정류장을 찾지 못했어요', '출발 건물을 바꾸거나 내 위치를 다시 확인해 주세요.');
            return;
        }
        container.innerHTML = `<p class="results-caption">${icon('clock')}<span>도착정보는 조회 시점 기준이며 운행 상황에 따라 달라질 수 있어요.</span></p>` + stations.map(stationCard).join('');
    }

    window.formatKoreanTime = formatKoreanTime;
    window.format24Time = format24Time;
    window.renderResults = renderResults;
    window.AppModules = window.AppModules || {};
    window.AppModules.renderer = Object.assign(window.AppModules.renderer || {}, { renderResults });
}());

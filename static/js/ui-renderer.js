/** Results presentation: keep unknown waits unknown; map and requests stay in their controllers. */
(function () {
    'use strict';

    const TAXI_SUGGESTION_MINUTES = 10;
    const boundContainers = new WeakSet();
    let lastJourneyResponse = null;
    let validJourneyIndices = new Set(), selectedJourneyIndex = null;

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
        if (message.startsWith('MOTIS 경로를 확인하지 못했습니다.')) return '일부 경로를 확인하지 못해 확인된 운행편을 표시해요.';
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
        if (section && !window.AppModules.shell) { section.hidden = false; section.style.display = ''; }
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
    function sourceNotes(value) {
        if (!value || typeof value !== 'object') return '';
        return `<details class="results-notes"><summary>출처·확인 기준</summary><pre class="result-source">${escapeHtml(JSON.stringify(value, null, 2))}</pre></details>`;
    }
    function routeWarnings(values, title = '계산·운행 안내') {
        const items = warningsMarkup(values, 'route-warnings');
        return items ? `<details class="route-details route-cautions"><summary><span>${escapeHtml(title)}</span>${icon('chevron-down')}</summary>${items}</details>` : '';
    }
    function setResultStatus(state) {
        const section = document.getElementById('results-section');
        if (section) section.dataset.state = state;
    }
    function journeyLegs(journey) {
        return journey.legs.map(leg => ({ ...record(leg), transport_type: leg.transport_type || journey.transport_type }));
    }

    function routeBadge(leg, alternate) {
        const route = text(leg.route_no).trim() || text(leg.route_name).trim() || '번호 미확인';
        return `<span class="route-badge${alternate ? ' route-badge--alternate' : ''}">${icon('bus')}<span>${leg.transport_type === 'shuttle' ? '셔틀 · ' : ''}${escapeHtml(route)}</span></span>`;
    }

    function directionLabel(leg, includeLegacyLabel = false) {
        const nextStop = text(record(leg.next_stop).name).trim();
        if (nextStop) return `다음 정류장 ${nextStop} 방면`;
        const direction = text(leg.direction).trim();
        return direction ? `${includeLegacyLabel ? '운행 방향 정보: ' : ''}${direction}` : '운행 방향 정보 미확인';
    }

    function routeTiming(journey, legs) {
        const access = record(journey.access_walk);
        const egress = record(journey.egress_walk);
        const planned = journey.transport_type === 'shuttle' && journey.schedule_comparable === true
            && journey.service_date_confirmed === true && legs.every(leg => leg.transport_type === 'shuttle'
                && scheduleTime(leg.scheduled_departure_at) && scheduleTime(leg.scheduled_arrival_at));
        let walk = number(journey.walk_min);
        let ride = number(journey.ride_min);
        if (walk === null && number(access.minutes) !== null && number(egress.minutes) !== null) walk = number(number(access.minutes) + number(egress.minutes));
        if (ride === null && legs.every(leg => number(leg.ride_min) !== null)) ride = number(legs.reduce((sum, leg) => sum + number(leg.ride_min), 0));
        const directionUnknown = !planned && (journey.total_unavailable_reason === 'direction_unverified'
            || legs.some(leg => leg.direction_verified !== true || leg.arrival_reference_only === true));
        const waitUnknown = directionUnknown || number(journey.wait_min) === null || legs.some(leg => number(leg.wait_min) === null);
        const complete = number(journey.total_min) !== null && walk !== null && ride !== null && !waitUnknown
            && !journey.total_unavailable_reason && (planned || journey.has_live_departure === true);
        return { access, egress, walk, ride, planned, directionUnknown, waitUnknown, complete,
            duration: complete ? number(journey.total_min) : (walk !== null && ride !== null ? number(walk + ride) : null) };
    }

    function directWalk(response) {
        const walk = record(response.direct_walk), duration = number(walk.minutes);
        const origin = record(response.origin), destination = record(response.destination);
        const samePoint = Number.isFinite(origin.lat) && Number.isFinite(origin.lon)
            && origin.lat === destination.lat && origin.lon === destination.lon;
        return walk.status === 'ok' && duration !== null && number(walk.distance_m) !== null
            && (duration > 0 || samePoint) ? { minutes: duration, distance_m: number(walk.distance_m) } : null;
    }

    function directWalkMarkup(response, timing, showAdvice = true) {
        const walk = directWalk(response);
        if (!walk) return '';
        const faster = showAdvice && timing && timing.complete && walk.minutes < timing.duration
            && record(response.recommendation).reason !== 'less_walking';
        return `<p class="route-direct-walk">${icon('walk')}<span>전체 도보 약 ${minutes(walk.minutes)}</span></p>`
            + (faster ? `<p class="route-walk-advice">걸어서 가는 편이 약 ${minutes(timing.duration - walk.minutes)} 빨라요. 가까운 스쿠터도 확인해 보세요.</p>` : '');
    }

    function taxiRecommendation(walkMinutes, walkDistance, destination, lookupReason = '', warnings = []) {
        if (walkMinutes === null || walkMinutes < TAXI_SUGGESTION_MINUTES) return '';
        const walkLength = distance(walkDistance);
        return `<article class="result-card route-taxi-recommendation${lookupReason ? ' route-walk-card' : ''}" aria-labelledby="taxi-recommendation-title">
            <span class="route-mobility-kicker">이동 추천</span>
            <h3 id="taxi-recommendation-title">택시를 타는 것을 추천해요</h3>
            <p class="taxi-room-walk-reference">${icon('walk')}<span>도보 약 ${minutes(walkMinutes)}${walkLength ? ` · ${walkLength}` : ''}</span></p>
            <button class="taxi-room-banner" type="button" data-destination="${escapeHtml(record(destination).name)}" data-lat="${escapeHtml(record(destination).lat)}" data-lon="${escapeHtml(record(destination).lon)}">
                <span class="taxi-room-banner-icon">${icon('taxi')}</span>
                <strong>택시 모집방을 만드시겠어요?</strong>${icon('arrow-right')}</button>
            ${routeWarnings([lookupReason, ...warnings], '경로 확인 안내')}</article>`;
    }

    function mobilityRecommendation(response, lookupReason = '') {
        const recommendation = record(response.recommendation), walk = directWalk(response);
        const onlyTimedWalk = recommendation.preferred_mode === 'undetermined' && recommendation.reason === 'comparison_unknown';
        if (recommendation.status !== 'ok' || (recommendation.preferred_mode !== 'walk' && !onlyTimedWalk) || !walk) return '';
        const warnings = Array.isArray(response.warnings) ? response.warnings : [];
        const taxiWarnings = onlyTimedWalk ? ['버스 도착 시각을 확인하지 못했어요.', ...warnings] : warnings;
        const taxi = taxiRecommendation(walk.minutes, walk.distance_m, response.destination, lookupReason, taxiWarnings);
        if (taxi) return taxi;
        const transit = record(recommendation.transit), index = transit.candidate_index;
        const journey = Number.isInteger(index) && Array.isArray(response.journeys) ? record(response.journeys[index]) : {};
        const validLegs = Array.isArray(journey.legs) && journey.legs.length && journey.legs.every(leg => leg && typeof leg === 'object' && !Array.isArray(leg));
        const timing = validLegs ? routeTiming(journey, journeyLegs(journey)) : null;
        const total = timing && timing.complete && number(transit.total_min) === timing.duration ? timing.duration : null;
        const transitLabel = timing?.planned ? '셔틀 시간표 기준' : '확인된 버스';
        const transitComparison = timing?.planned ? '셔틀 예상 경로' : '확인된 버스 경로';
        const access = number(recommendation.min_bus_access_min), stopWalk = number(recommendation.min_bus_walk_min);
        const approximate = value => value > 0 && value < 1 ? '1분 미만' : `${Math.round(value * 10) / 10}분`;
        let reason = '목적지까지 바로 걸어가는 경로를 우선 안내해요.';
        if (onlyTimedWalk) {
            reason = '도보 소요 시간은 확인했어요. 버스의 도착 시각은 미확인입니다.';
        } else if (access !== null && access >= walk.minutes) {
            reason = `목적지까지 도보 약 ${approximate(walk.minutes)}인데, 정류장까지 걷는 데만 약 ${approximate(access)}이 필요해요.`;
        } else if (recommendation.reason === 'stop_walk_overhead') {
            if (stopWalk !== null && stopWalk >= walk.minutes) reason = `목적지까지 도보 약 ${approximate(walk.minutes)}인데, 정류장 전후 도보만 약 ${approximate(stopWalk)}이 필요해요.`;
        } else if (recommendation.reason === 'short_trip') reason = '가까운 구간이라 정류장 우회와 버스 대기를 줄일 수 있어요.';
        else if (recommendation.reason === 'walk_only') reason = '버스 경로는 확인되지 않았지만 전체 보행 경로를 확인했어요.';
        else if (recommendation.reason === 'same_point') reason = '출발지와 목적지가 같은 지점이에요.';
        const saved = total === null || onlyTimedWalk ? null : Math.min(number(recommendation.time_saved_min) || 0, total - walk.minutes);
        const scooter = !onlyTimedWalk && record(recommendation.scooter).status === 'conditional';
        const title = onlyTimedWalk ? '확인된 도보 경로' : '도보 우선';
        const notes = [lookupReason, '이번 조회의 버스 후보와 전체 보행 경로를 기준으로 추천해요. 대기나 방향이 미확인이면 전체 시간을 비교하지 않아요.',
            ...warnings];
        return `<article class="result-card route-mobility-recommendation${lookupReason ? ' route-walk-card' : ''}" aria-labelledby="mobility-recommendation-title">
            <div class="route-mobility-heading"><span class="route-mobility-icon">${icon('walk')}</span><div><span class="route-mobility-kicker">이동 추천</span><h3 id="mobility-recommendation-title">${title}${scooter ? ' · 가까운 킥보드도 대안' : ''}</h3></div></div>
            <div class="route-mobility-times"><div class="route-mobility-time route-mobility-time--preferred"><span>전체 도보</span><strong>약 ${minutes(walk.minutes)}</strong><small>${escapeHtml(distance(walk.distance_m))}</small></div>
                <div class="route-mobility-time"><span>${transitLabel}</span><strong>${total !== null ? `약 ${minutes(total)}` : timing ? (timing.waitUnknown ? '대기 미확인' : '총시간 미확인') : '경로 미확인'}</strong><small>${total !== null ? '도보·대기·탑승 포함' : '전체 시간 비교 미완료'}</small></div></div>
            <p class="route-mobility-reason">${escapeHtml(reason)}</p>
            ${saved !== null && saved > 0 ? `<p class="route-mobility-saving">${transitComparison}보다 약 ${approximate(saved)} 짧아요.</p>` : ''}
            ${scooter ? '<p class="route-mobility-scooter">킥보드가 가까이 있다면 대안이에요. 기기 접근·주행 가능한 경로·반납을 확인해 주세요.</p>' : ''}
            ${routeWarnings(notes, lookupReason ? '추천·버스 조회 안내' : '추천 기준')}</article>`;
    }

    function walkingAlternative(response, reason) {
        const walk = directWalk(response);
        if (!walk) return null;
        const warnings = Array.isArray(response.warnings) ? response.warnings : [];
        const taxi = taxiRecommendation(walk.minutes, walk.distance_m, response.destination, reason, warnings);
        if (taxi) return { heading: '이동 추천', markup: taxi };
        const recommendation = mobilityRecommendation(response, reason);
        if (recommendation) return { heading: '도보 대안', markup: recommendation };
        const origin = text(record(response.origin).name) || '출발지', destination = text(record(response.destination).name) || '목적지';
        const markup = `<article class="result-card route-walk-card"><span class="route-rank">도보 대안</span><h3>목적지까지 걸어가기</h3>
            <p class="route-walk-endpoints">${escapeHtml(origin)} → ${escapeHtml(destination)}</p>${directWalkMarkup(response)}
            <p class="route-walk-distance">${escapeHtml(distance(walk.distance_m))} · 보행 경로 기준</p>
            <p class="route-walk-note">이번 조회에서 버스 경로는 확인하지 못했어요.</p>
            ${routeWarnings([reason, ...warnings], '버스 조회 안내')}</article>`;
        return { heading: '도보 대안', markup };
    }

    function timeHeadline(timing) {
        const label = timing.complete ? '총 소요 추정' : timing.duration === null ? '소요 시간' : '도보+탑승';
        const duration = timing.duration === null
            ? '<strong class="route-duration-unknown">시간 미확인</strong>'
            : `<span class="route-duration-prefix">약</span><strong>${timing.duration > 0 && timing.duration < 1 ? '1분 미만' : Math.ceil(timing.duration)}</strong>${timing.duration > 0 && timing.duration < 1 ? '' : '<span class="route-duration-unit">분</span>'}`;
        const detail = timing.complete ? (timing.planned ? '도보·셔틀·대기 포함' : '도보·버스·대기 포함')
            : timing.duration === null ? '도보 또는 탑승 시간 미확인 · 총 소요 미확인' : '대기 제외 · 환승 여유 제외 · 총 소요 미확인';
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

    function busStep(leg, index, timing, tripStart) {
        const shuttle = leg.transport_type === 'shuttle';
        const board = record(leg.board);
        const alight = record(leg.alight);
        const verified = leg.direction_verified === true && leg.arrival_reference_only !== true;
        const wait = (shuttle ? timing.planned : verified) ? number(leg.wait_min) : null;
        const arrival = verified ? number(leg.arrival_min) : null;
        const stopCount = number(leg.stop_count);
        const ride = number(leg.ride_min);
        const facts = [];
        if (stopCount !== null) facts.push(`${Math.round(stopCount)}개 정류장`);
        facts.push(ride === null ? '탑승 시간 미확인' : `탑승 약 ${minutes(ride)}`);
        if (leg.ride_time_basis === 'published_segment_estimate' && text(leg.ride_time_source)) facts.push(text(leg.ride_time_source));
        const waitText = wait === null ? (shuttle ? '저장된 시간표 기준 · 실제 운행·탑승 보장 아님' : '대기 시간 미확인')
            : `승차 대기 약 ${minutes(wait)}${shuttle ? ' · 학교 시간표 기준' : ''}`;
        const arrivalText = shuttle ? '' : arrival === null ? '' : ` · 조회 시점 기준 약 ${minutes(arrival)} 후 도착`;
        const depart = shuttle ? arrivalLabel(leg.scheduled_departure_at, tripStart) : '';
        const arrive = shuttle ? arrivalLabel(leg.scheduled_arrival_at, tripStart) : '';
        const timetable = depart ? `<p>${escapeHtml(`${depart} 출발 예정${arrive ? ` · ${arrive} 도착 예상` : ''}`)}</p>` : '';
        const routeId = leg.route_number_verified === false && text(leg.route_id)
            ? `<p class="journey-route-id">노선 ID ${escapeHtml(leg.route_id)} · 노선번호 확인 필요</p>` : '';
        return `<li class="journey-step journey-step--bus${index > 0 ? ' journey-step--alternate' : ''}">
            <span class="journey-step-icon">${icon('bus')}</span><div class="journey-step-content">
                <div class="journey-leg-heading">${routeBadge(leg, index > 0)}<span>${index > 0 ? '환승' : '승차'}</span></div>
                <strong>${escapeHtml(text(board.name) || '승차 정류장')}<span class="journey-stop-arrow">→</span>${escapeHtml(text(alight.name) || '하차 정류장')}</strong>
                <p>${escapeHtml(facts.join(' · '))}</p><p class="journey-direction">${escapeHtml(directionLabel(leg))}</p>${timetable}
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
                const previous = record(legs[index - 1].alight), next = record(leg.board);
                const same = (text(previous.id) && previous.id === next.id) ||
                    (number(previous.lat) !== null && number(previous.lon) !== null && previous.lat === next.lat && previous.lon === next.lon);
                html += `<li class="journey-step journey-step--transfer"><span class="journey-step-icon">${icon('swap')}</span><div class="journey-step-content"><strong>${same ? '같은 정류장에서 환승' : '환승 구간 · 연결 정보 확인 필요'}</strong><p>${escapeHtml(bufferText)}</p></div></li>`;
            }
            html += busStep(leg, index, timing, journey.query_at);
        });
        html += walkStep(timing.egress, '하차 후 도보', text(destination.name) || '목적지', true);
        return `<ol class="journey-timeline">${html}</ol>`;
    }

    function journeyCard(journey, index, mapIndex, response, walkPreferred = false) {
        const legs = journeyLegs(journey);
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
        if (timing.planned && journey.location_is_approximate) notes.push('승하차 위치는 대표 지점 기준');
        if (!notes.length) notes.push('도착 예상은 운행 상황에 따라 달라질 수 있어요');
        const warnings = warningList(journey.warnings);
        if (timing.directionUnknown) warnings.unshift('정류장 순서를 바탕으로 찾은 경로 후보예요. 상·하행과 종점·회차에서 계속 탈 수 있는지는 승차 전에 확인해 주세요.');
        const confidence = timing.directionUnknown ? '참고 경로 · 운행 방향 확인 필요'
            : timing.waitUnknown ? '참고 경로 · 대기 시간 미확인'
                : timing.complete ? (timing.planned ? '셔틀 시간표 기준' : '대기 포함 추정') : '참고 경로 · 총 소요 미확인';
        const direction = directionLabel(legs[0], true);
        const departure = timing.planned ? arrivalLabel(legs[0].scheduled_departure_at, journey.query_at) : '';
        const boardingText = [accessText, departure ? `${departure} 출발 예정` : ''].filter(Boolean).join(' · ');
        return `<article class="result-card route-card" data-journey-index="${mapIndex}" aria-labelledby="route-title-${index}">
            <div class="route-card-top"><span class="route-rank">${walkPreferred ? '버스 대안' : '경로'} ${String(index + 1).padStart(2, '0')}</span><span class="route-confidence${timing.complete ? '' : ' route-confidence--reference'}">${escapeHtml(confidence)}</span></div>
            <div class="boarding-guide"><span class="boarding-guide-icon">${icon('walk')}</span><div><span class="boarding-guide-label">${timing.directionUnknown ? '승차 후보 정류장' : '타는 정류장'}</span><h3 id="route-title-${index}">${escapeHtml(text(board.name) || '승차 정류장 미확인')}</h3><p>${escapeHtml(boardingText)}</p></div></div>
            <div class="route-chain"><span class="route-chain-walk">${icon('walk')}<span>${timing.walk === null ? '도보 미확인' : `총 도보 ${minutes(timing.walk)}`}</span></span><span class="route-chain-arrow">${icon('chevron-down')}</span>${chain}</div>
            <p class="route-card-direction">${escapeHtml(direction)}</p>
            <div class="route-overview"><div class="route-time-summary">${timeHeadline(timing)}</div><span class="route-transfer">${transferLabel}</span></div>
            ${directWalkMarkup(response, timing, !walkPreferred)}
            <p class="route-note${timing.directionUnknown || timing.waitUnknown ? ' route-note--warning' : ''}">${icon(timing.directionUnknown || timing.waitUnknown ? 'warning' : 'clock')}<span>${escapeHtml(notes.join(' · '))}</span></p>
            ${routeWarnings(warnings)}
            <div class="route-card-actions"><button class="route-map-button route-select" type="button" data-journey-index="${mapIndex}" aria-controls="route-detail" aria-pressed="false" aria-expanded="false" aria-label="경로 ${index + 1} 선택하고 상세 보기">${icon('map')}<span>경로 상세 보기</span>${icon('arrow-up-right')}</button></div></article>`;
    }

    const travelModes = {
        walk: ['도보', 'walk', 'walk'], walking: ['도보', 'walk', 'walk'],
        transfer_walk: ['환승 도보', 'walk', 'walk'], transfer: ['환승', 'swap', 'wait'],
        wait: ['승차 대기', 'clock', 'wait'], connection: ['연결 미확인', 'warning', 'wait'],
        city_bus: ['시내버스', 'bus', 'city'],
        bus: ['시내버스', 'bus', 'city'], shuttle: ['학교 셔틀', 'bus', 'city'],
        subway: ['지하철·전철', 'train', 'metro'], metro: ['지하철·전철', 'train', 'metro'],
        tram: ['트램', 'train', 'metro'],
        rail: ['철도', 'train', 'rail'], train: ['철도', 'train', 'rail'],
        intercity_bus: ['시외버스', 'bus', 'coach'], express_bus: ['고속버스', 'bus', 'coach'],
        ferry: ['여객선', 'pin', 'rail'], flight: ['항공', 'pin', 'rail'], air: ['항공', 'pin', 'rail']
    };
    const bookingHosts = new Set([
        'www.korail.com', 'korail.com', 'www.letskorail.com', 'etk.srail.kr',
        'www.bustago.or.kr', 'bustago.or.kr', 'www.kobus.co.kr', 'kobus.co.kr',
        'txbus.t-money.co.kr', 'intercitybus.tmoney.co.kr', 'island.haewoon.co.kr'
    ]);
    const kstClock = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', hour: 'numeric', minute: '2-digit', hour12: true });
    const kstCalendar = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', month: 'short', day: 'numeric' });

    function scheduleTime(value) {
        const time = text(value).trim();
        if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(time)) return null;
        const instant = new Date(/(?:Z|[+-]\d{2}:\d{2})$/i.test(time) ? time : time + '+09:00');
        return Number.isFinite(instant.getTime()) ? instant : null;
    }

    function arrivalLabel(value, tripStart) {
        const arrival = scheduleTime(value);
        if (!arrival) return '';
        const start = scheduleTime(tripStart);
        const serviceDay = instant => Math.floor((instant.getTime() + 9 * 3600000) / 86400000);
        const dayOffset = start ? serviceDay(arrival) - serviceDay(start) : 0;
        const day = dayOffset === 1 ? '다음 날 ' : dayOffset ? kstCalendar.format(arrival) + ' ' : '';
        return day + kstClock.format(arrival);
    }

    function wholeDuration(value) {
        const parsed = number(value);
        if (parsed === null) return '';
        const total = Math.ceil(parsed), hours = Math.floor(total / 60), remainder = total % 60;
        return [hours ? hours + '시간' : '', remainder || !hours ? remainder + '분' : ''].filter(Boolean).join(' ');
    }

    function bookingMarkup(booking, start, end) {
        if (!booking || typeof booking.url !== 'string') return '';
        let url;
        try { url = new URL(booking.url); } catch (_) { return ''; }
        if (url.protocol !== 'https:' || url.port || url.username || url.password || !bookingHosts.has(url.hostname)) return '';
        const provider = text(booking.name).trim() || '공식 예매처';
        return `<a class="national-booking" href="${escapeHtml(url.href)}" target="_blank" rel="noopener noreferrer" aria-label="${escapeHtml(provider + ' 예매 · ' + start + '에서 ' + end + '까지')}">${escapeHtml(provider)} 예매${icon('arrow-up-right')}</a>`;
    }

    function nationalRouteName(leg, label) {
        const service = text(leg.service_name).trim();
        const route = text(leg.route_name).trim();
        if (route && service.startsWith(route + ' ')) return service;
        return [...new Set([service, route])]
            .filter(name => name && name !== label).join(' ');
    }

    function nationalTimeline(journey) {
        const segments = journey.segments;
        const firstMovement = segments.findIndex(leg => leg.mode !== 'wait');
        return `<ol class="national-timeline" aria-label="통학 경로 구간">${segments.map((leg, index) => {
            const mode = text(leg.mode);
            const presentation = travelModes[mode];
            const [baseLabel, symbol, tone] = Array.isArray(presentation) ? presentation : ['이동', 'route', 'wait'];
            const headwayEstimate = leg.ride_time_basis === 'published_headway_estimate';
            let label = mode === 'wait' && (leg.route_name === '출발까지 대기' || headwayEstimate) ? leg.route_name : baseLabel;
            if (tone === 'walk' && mode !== 'transfer_walk' && segments.length > 1) {
                label = index === firstMovement ? '출발 도보' : index === segments.length - 1 ? '도착 도보' : '환승 도보';
            }
            const origin = text(record(leg.from_place).name) || '출발 지점';
            const destination = text(record(leg.to_place).name) || '도착 지점';
            const routeLabel = nationalRouteName(leg, label);
            const duration = wholeDuration(leg.duration_min) || '시간 미확인';
            const charge = number(leg.fare_won);
            const fare = charge === null ? '요금 미확인' : Math.round(charge).toLocaleString('ko-KR') + '원';
            const depart = arrivalLabel(leg.departure_at, journey.departure_at);
            const arrive = arrivalLabel(leg.arrival_at, journey.departure_at);
            const sourceName = (text(leg.source) || text(record(leg.source).name)).trim();
            const sourceLabel = sourceName === 'MOTIS' ? '' : sourceName;
            const rideTimeSource = leg.ride_time_basis === 'published_segment_estimate' ? text(leg.ride_time_source) : '';
            const source = [sourceLabel, leg.realtime === true ? (sourceName === 'TAGO' ? '실시간 출발 반영' : '실시간 운행 정보 반영') : '', rideTimeSource]
                .filter(Boolean).join(' · ');
            const endpoints = origin === destination ? escapeHtml(origin)
                : `${escapeHtml(origin)} <span class="national-endpoint-arrow" aria-label="에서">→</span> ${escapeHtml(destination)}`;
            const clocks = mode === 'shuttle' && depart
                ? `${depart} 출발 예정${arrive ? ` · ${arrive} 도착 예상` : ''}` : [depart, arrive].filter(Boolean).join(' → ');
            const timed = clocks ? `<p class="national-segment-clock">${headwayEstimate ? '예상 ' : ''}${escapeHtml(clocks)}</p>` : '';
            const walkDistance = tone === 'walk' ? distance(leg.distance_m) : '';
            const costs = tone === 'walk' ? [duration, walkDistance].filter(Boolean).join(' · ')
                : tone === 'wait' ? duration : `${duration} · ${fare}`;
            return `<li class="national-segment national-segment--${tone}">
                <span class="national-segment-pin">${icon(symbol)}</span>
                <div class="national-segment-info">
                    <p class="national-segment-mode">${escapeHtml(label)}${routeLabel ? `<span>${escapeHtml(routeLabel)}</span>` : ''}</p>
                    <p class="national-segment-title">${endpoints}</p>
                    ${timed}<p class="national-segment-facts">${escapeHtml(costs)}</p>
                    ${source ? `<p class="national-segment-source">${escapeHtml(source)}</p>` : ''}
                </div>
                ${bookingMarkup(leg.booking, origin, destination)}
            </li>`;
        }).join('')}</ol>`;
    }

    function nationalRouteSummary(segments) {
        const routes = [];
        for (const leg of segments) {
            if (leg.mode === 'shuttle') {
                const route = text(leg.route_name).trim();
                routes.push(route ? `셔틀 ${route}` : '학교 셔틀');
                continue;
            }
            const mode = travelModes[text(leg.mode)];
            if (Array.isArray(mode) && mode[2] !== 'walk' && mode[2] !== 'wait') {
                const label = mode[2] === 'metro' ? text(leg.route_name).trim()
                    : mode[2] === 'coach' ? mode[0] : nationalRouteName(leg, mode[0]);
                routes.push(label || mode[0]);
            }
        }
        if (!routes.length && segments.every(leg => travelModes[text(leg.mode)]?.[2] === 'walk')) return '도보';
        const summary = routes.slice(0, 4).join(' → ');
        return routes.length > 4 ? `${summary} 외 ${routes.length - 4}개` : summary;
    }

    function nationalCard(journey, index, mapIndex, response, walkingReference = false) {
        const duration = wholeDuration(journey.total_min);
        const arrival = duration && journey.complete !== false ? arrivalLabel(journey.arrival_at, journey.departure_at || response.departure_at) : '';
        const complete = journey.complete !== false && Boolean(duration && arrival);
        const first = index === 0;
        const priority = walkingReference ? '도보 참고 경로'
            : complete ? (first && response.status === 'ok' ? '추천 경로' : '통학 경로') : '확인된 구간';
        const transferCount = number(journey.transfer_count);
        const totalFare = number(journey.fare_won);
        const realtime = journey.segments.some(leg => leg.realtime === true);
        const headway = complete && journey.segments.some(leg => leg.ride_time_basis === 'published_headway_estimate');
        const publishedBus = complete && journey.segments.some(leg => leg.ride_time_basis === 'published_segment_estimate');
        const shuttle = journey.segments.some(leg => leg.mode === 'shuttle');
        const scheduled = complete && journey.segments.some(leg => ['subway', 'train', 'intercity_bus', 'express_bus'].includes(leg.mode));
        const timingSource = headway ? '버스 배차 간격 기반 추정'
            : realtime ? '일부 실시간 반영' : publishedBus ? '공개 시각표 기반 추정' : scheduled && !shuttle ? '시간표 기준' : '';
        const requestedStart = first ? scheduleTime(response.departure_at) : null;
        const departure = requestedStart ? `${kstCalendar.format(requestedStart)} ${kstClock.format(requestedStart)} 출발 기준` : '';
        const meta = [departure, transferCount === null ? '' : transferCount === 0 ? '환승 없음' : `환승 ${transferCount}회`,
            totalFare === null ? '' : `${Math.round(totalFare).toLocaleString('ko-KR')}원`, shuttle ? '셔틀 시간표 기준' : '', timingSource].filter(Boolean).join(' · ');
        const summary = nationalRouteSummary(journey.segments);
        return `<article class="result-card route-card national-itinerary${first ? ' national-itinerary--primary' : ''}" data-journey-index="${mapIndex}" aria-labelledby="national-route-title-${index}">
            <header class="national-itinerary-header">
                <h3 id="national-route-title-${index}" class="national-itinerary-kicker">${priority}</h3>
                <div class="national-itinerary-primary">
                    <strong>${escapeHtml(complete ? (journey.estimated ? '약 ' : '') + duration : '총 소요 미확인')}</strong>
                    ${arrival ? `<span>${escapeHtml(arrival)} 도착</span>` : ''}
                </div>
                ${summary ? `<p class="national-itinerary-route">${escapeHtml(summary)}</p>` : ''}
                ${meta ? `<p class="national-itinerary-meta">${escapeHtml(meta)}</p>` : ''}
            </header>
            <div class="national-itinerary-actions">
                <button type="button" class="route-select national-detail-button" data-journey-index="${mapIndex}" aria-controls="route-detail" aria-expanded="false" aria-pressed="false">경로 자세히 보기${icon('chevron-down')}</button>
            </div>
        </article>`;
    }

    function renderNationalResults(payload, container, options) {
        const candidates = Array.isArray(payload.journeys) ? payload.journeys
            .map((journey, index) => ({ journey, index }))
            .filter(item => Array.isArray(item.journey?.segments) && item.journey.segments.length && item.journey.segments.every(segment => segment && typeof segment === 'object')) : [];
        setResultsState('전국 통학 경로', candidates.length);
        if (!candidates.length) {
            const unavailable = payload.status === 'unavailable';
            const partial = payload.status === 'partial';
            setResultStatus(unavailable ? 'error' : partial ? 'partial' : 'empty');
            container.innerHTML = stateCard(unavailable ? 'error' : 'notice',
                unavailable ? '경로 정보를 불러오지 못했어요'
                    : partial ? '확인할 수 있는 통학 경로가 부족해요' : '연결되는 통학 경로가 없어요',
                unavailable ? '잠시 후 다시 조회해 주세요.'
                    : '출발 시각을 바꾸거나 가까운 다른 목적지에서 다시 검색해 보세요.')
                + resultsNotes(payload.warnings);
            return;
        }
        lastJourneyResponse = payload;
        validJourneyIndices = new Set(candidates.map(item => item.index));
        setResultStatus(payload.status === 'ok' ? 'ready' : 'partial');
        const best = candidates[0].journey;
        const walkOnly = best.complete === true && best.segments.every(leg => travelModes[leg.mode]?.[2] === 'walk');
        const taxi = walkOnly ? taxiRecommendation(number(best.total_min), null, payload.destination) : '';
        const [primary, ...alternatives] = candidates.map((item, index) =>
            nationalCard(item.journey, index, item.index, payload, index === 0 && Boolean(taxi)));
        const moreRoutes = alternatives.length ? `<details class="national-alternatives">
            <summary><span>다른 경로 ${alternatives.length}개</span>${icon('chevron-down')}</summary>
            ${alternatives.join('')}
        </details>` : '';
        container.innerHTML = taxi + primary + moreRoutes + resultsNotes(payload.warnings);
        selectJourney(candidates[0].index, { present: options.present });
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

    function selectJourney(index, options = {}) {
        if (!lastJourneyResponse || !validJourneyIndices.has(index)) return false;
        const changed = selectedJourneyIndex !== index;
        selectedJourneyIndex = index;
        const journey = lastJourneyResponse.journeys[index];
        const national = lastJourneyResponse.mode === 'national_journeys';
        const container = document.getElementById('result-container');
        container.querySelectorAll('.route-card').forEach(card => {
            const selected = Number(card.getAttribute('data-journey-index')) === index;
            card.classList.toggle('is-selected', selected);
            card.querySelector('.route-select')?.setAttribute('aria-pressed', String(selected));
        });
        const detail = document.getElementById('route-detail');
        if (detail) {
            const ordinal = [...validJourneyIndices].indexOf(index) + 1;
            document.getElementById('route-detail-heading').textContent = national ? '선택한 통학 경로 상세'
                : `선택한 경로 상세 · 경로 ${String(ordinal).padStart(2, '0')}`;
            if (changed) {
                const content = document.getElementById('route-detail-content');
                const legs = national ? [] : journeyLegs(journey);
                content.innerHTML = (national ? nationalTimeline(journey)
                    : journeyTimeline(journey, legs, lastJourneyResponse, routeTiming(journey, legs))) + routeWarnings(journey.warnings);
                content.scrollTop = 0;
            }
        }
        const shell = window.AppModules.shell;
        const present = options.present !== false && (!shell || shell.canPresent());
        if (present) {
            if (options.user && shell) shell.showDetail({ focus: options.focus });
            else if (options.user && detail) detail.hidden = false;
            try {
                if (typeof window.showJourneyOnMap === 'function') window.showJourneyOnMap(lastJourneyResponse, index, { focus: false });
            } catch (_) { /* A map failure must not hide the readable timetable. */ }
        }
        return true;
    }
    function resetResults() {
        window.AppModules.shell?.closeDetail({ returnFocus: false });
        lastJourneyResponse = null; validJourneyIndices = new Set(); selectedJourneyIndex = null;
        const detail = document.getElementById('route-detail'), content = document.getElementById('route-detail-content');
        if (detail) detail.hidden = true;
        if (content) content.replaceChildren();
    }
    function bindResultActions(container) {
        if (boundContainers.has(container) || typeof container.addEventListener !== 'function') return;
        container.addEventListener('click', event => {
            const taxi = event.target?.closest?.('.taxi-room-banner');
            if (taxi && container.contains(taxi)) {
                window.AppModules.rideRooms.startCreate({ name: taxi.dataset.destination, lat: taxi.dataset.lat, lon: taxi.dataset.lon });
                return;
            }
            if (event.target?.closest?.('.route-cautions, a')) return;
            const target = event.target && typeof event.target.closest === 'function' ? event.target.closest('[data-journey-index]') : null;
            if (!target || !container.contains(target) || !lastJourneyResponse) return;
            const index = Number(target.getAttribute('data-journey-index'));
            if (!Number.isInteger(index)) return;
            selectJourney(index, { user: true, focus: event.detail === 0 });
        });
        boundContainers.add(container);
    }

    function renderResults(data, options = {}) {
        const container = document.getElementById('result-container');
        if (!container) return;
        bindResultActions(container);
        const payload = record(data);
        const loading = document.getElementById('loading');
        container.hidden = false;
        resetResults();
        if (payload.loading === true) {
            setResultStatus('loading');
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
            const alternative = walkingAlternative(payload, payload.error);
            setResultStatus(alternative ? 'partial' : 'error');
            setResultsState(alternative?.heading || '다시 확인해 주세요', alternative ? 1 : null);
            container.innerHTML = alternative
                ? alternative.markup + sourceNotes(payload.source)
                : stateCard('error', '정보를 불러오지 못했어요', friendlyMessage(payload.error) || '잠시 후 다시 시도해 주세요.') + resultsNotes(payload.warnings) + sourceNotes(payload.source);
            return;
        }
        if (payload.notice) {
            setResultStatus('error');
            setResultsState('출발 준비', null);
            container.innerHTML = stateCard('notice', '여기부터 확인해 주세요', friendlyMessage(payload.notice)) + resultsNotes(payload.warnings) + sourceNotes(payload.source);
            return;
        }
        if (payload.mode === 'national_journeys') {
            renderNationalResults(payload, container, options);
            return;
        }
        if (payload.mode === 'journeys' || Array.isArray(payload.journeys) || directWalk(payload)) {
            const journeys = Array.isArray(payload.journeys) ? payload.journeys : [];
            const candidates = journeys.map((value, index) => ({ value: record(value), index })).filter(item => Array.isArray(item.value.legs) && item.value.legs.length && item.value.legs.every(leg => leg && typeof leg === 'object' && !Array.isArray(leg)));
            setResultsState('이동 경로', candidates.length);
            if (!candidates.length) {
                const alternative = walkingAlternative(payload, '검색 범위 안에서 연결 가능한 버스 경로를 확인하지 못했어요.');
                setResultStatus(alternative ? 'partial' : 'empty');
                if (alternative) setResultsState(alternative.heading, 1);
                container.innerHTML = alternative
                    ? alternative.markup + sourceNotes(payload.source)
                    : stateCard('empty', '검색 범위 안에서 경로를 찾지 못했어요', '출발지나 목적지를 바꿔 다시 찾아보세요. 가까운 정류장도 확인할 수 있어요.') + resultsNotes(payload.warnings) + sourceNotes(payload.source);
                return;
            }
            lastJourneyResponse = payload;
            validJourneyIndices = new Set(candidates.map(item => item.index));
            setResultStatus(candidates.some(item => { const timing = routeTiming(item.value, journeyLegs(item.value)); return !timing.complete; }) || warningList(payload.warnings).length ? 'partial' : 'ready');
            const recommendation = mobilityRecommendation(payload);
            container.innerHTML = recommendation + candidates.map((item, index) => journeyCard(item.value, index, item.index, payload, Boolean(recommendation))).join('') + resultsNotes(payload.warnings) + sourceNotes(payload.source);
            selectJourney(candidates[0].index, { present: options.present });
            return;
        }
        const stations = Array.isArray(data) ? data.filter(value => value && typeof value === 'object' && !Array.isArray(value)).map(record) : [];
        setResultsState('가까운 정류장', stations.length);
        if (!stations.length) {
            setResultStatus('empty');
            container.innerHTML = stateCard('empty', '주변 정류장을 찾지 못했어요', '출발 건물을 바꾸거나 내 위치를 다시 확인해 주세요.');
            return;
        }
        setResultStatus(stations.some(item => item.arrival_error) ? 'partial' : 'ready');
        container.innerHTML = `<p class="results-caption">${icon('clock')}<span>도착정보는 조회 시점 기준이며 운행 상황에 따라 달라질 수 있어요.</span></p>` + stations.map(stationCard).join('');
    }

    window.formatKoreanTime = formatKoreanTime;
    window.format24Time = format24Time;
    window.renderResults = renderResults;
    window.AppModules = window.AppModules || {};
    window.AppModules.renderer = Object.assign(window.AppModules.renderer || {}, { renderResults, resetResults, selectJourney,
        getSelectedButton: () => [...document.getElementById('result-container').querySelectorAll('.route-select')]
            .find(button => Number(button.getAttribute('data-journey-index')) === selectedJourneyIndex) || null,
        getSelectedIndex: () => selectedJourneyIndex });
}());

"""기상청 단기예보의 시간별 기온·강수·풍속을 조회한다."""

from datetime import datetime, timedelta
from math import cos, floor, log, pi, sin, tan

from services.environment_common import EnvironmentHTTP, EnvironmentProviderError, KST, kst_now, number


def forecast_grid(latitude, longitude):
    """기상청 DFS Lambert 격자 변환식. 입력은 WGS84 위도·경도이다."""
    radians = pi / 180.0
    re = 6371.00877 / 5.0
    slat1, slat2 = 30.0 * radians, 60.0 * radians
    olon, olat = 126.0 * radians, 38.0 * radians
    sn = log(cos(slat1) / cos(slat2)) / log(tan(pi * 0.25 + slat2 * 0.5) / tan(pi * 0.25 + slat1 * 0.5))
    sf = (tan(pi * 0.25 + slat1 * 0.5) ** sn) * cos(slat1) / sn
    ro = re * sf / (tan(pi * 0.25 + olat * 0.5) ** sn)
    ra = re * sf / (tan(pi * 0.25 + latitude * radians * 0.5) ** sn)
    theta = longitude * radians - olon
    if theta > pi:
        theta -= 2.0 * pi
    if theta < -pi:
        theta += 2.0 * pi
    theta *= sn
    return floor(ra * sin(theta) + 43 + 0.5), floor(ro - ra * cos(theta) + 136 + 0.5)


def latest_issue(at):
    """02/05/.../23시 발표는 10분 뒤 제공되므로 그 전에는 이전 발표를 쓴다."""
    available = kst_now(at) - timedelta(minutes=10)
    for hours_ago in range(24):
        candidate = (available - timedelta(hours=hours_ago)).replace(minute=0, second=0, microsecond=0)
        if candidate.hour in (2, 5, 8, 11, 14, 17, 20, 23):
            return candidate
    raise ValueError("예보 발표 시각을 계산하지 못했어요.")


class WeatherClient(EnvironmentHTTP):
    URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    CATEGORIES = {"TMP": ("temperature_c", -100, 70), "WSD": ("wind_speed_ms", 0, 150),
                  "POP": ("rain_probability", 0, 100), "PTY": ("precipitation_type", 0, 4)}

    def __init__(self, key, **kwargs):
        super().__init__(key, "WEATHER", **kwargs)

    def forecast(self, latitude, longitude, at=None):
        moment = kst_now(at if at is not None else self.now())
        grid = forecast_grid(latitude, longitude)
        issue = latest_issue(moment)
        key = (grid, issue.isoformat())

        def load():
            try:
                return self._fetch(grid, issue)
            except EnvironmentProviderError as error:
                if error.code != "WEATHER_NO_DATA":
                    raise
                # 발표 직후 제공이 지연될 때만 직전 발표를 한 번 확인한다.
                return self._fetch(grid, issue - timedelta(hours=3))

        return self.cache.get(key, load)

    def _fetch(self, grid, issue):
        accumulated, seen = [], set()
        expected = None
        for page in range(1, 5):
            body = self.get_body(self.URL, {
                "dataType": "JSON", "base_date": issue.strftime("%Y%m%d"),
                "base_time": issue.strftime("%H%M"), "nx": grid[0], "ny": grid[1],
                "pageNo": page, "numOfRows": 1000,
            })
            total = number(body.get("totalCount"), 0, 4000)
            if total is None or int(total) != total:
                raise self.error("INCOMPLETE")
            if total == 0:
                raise self.error("NO_DATA")
            if expected is not None and total != expected:
                raise self.error("INCOMPLETE")
            expected = int(total)
            rows = self.items(body)
            if not rows:
                raise self.error("INCOMPLETE")
            for row in rows:
                identity = tuple(str(row.get(field)) for field in
                                 ("baseDate", "baseTime", "fcstDate", "fcstTime", "category", "nx", "ny"))
                if identity in seen:
                    raise self.error("INCOMPLETE")
                seen.add(identity)
            accumulated.extend(rows)
            if len(accumulated) == expected:
                break
            if len(accumulated) > expected:
                raise self.error("INCOMPLETE")
        if len(accumulated) != expected:
            raise self.error("INCOMPLETE")
        periods = {}
        for item in accumulated:
            category = item.get("category")
            if not isinstance(category, str) or category not in self.CATEGORIES:
                continue
            try:
                raw_date, raw_time = str(item["fcstDate"]), str(item["fcstTime"]).zfill(4)
                if len(raw_date) != 8 or len(raw_time) != 4:
                    raise ValueError("date")
                forecast_at = datetime.strptime(raw_date + raw_time, "%Y%m%d%H%M").replace(tzinfo=KST)
                if forecast_at.minute != 0:
                    raise ValueError("hour")
                if str(item.get("baseDate")) != issue.strftime("%Y%m%d") or str(item.get("baseTime")).zfill(4) != issue.strftime("%H%M"):
                    raise ValueError("issue")
                if int(item.get("nx", -1)) != grid[0] or int(item.get("ny", -1)) != grid[1]:
                    raise ValueError("grid")
            except (KeyError, ValueError, TypeError, OverflowError):
                continue
            target, low, high = self.CATEGORIES[category]
            value = number(item.get("fcstValue"), low, high)
            if category == "PTY" and value is not None and value != int(value):
                value = None
            row = periods.setdefault(forecast_at, {"at": forecast_at})
            row[target] = value
        if not periods:
            raise self.error("NO_DATA")
        return {"issued_at": issue, "fetched_at": kst_now(self.now()),
                "periods": [periods[key] for key in sorted(periods)]}

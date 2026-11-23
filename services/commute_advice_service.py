"""캠퍼스 주변 예보·측정값을 근거로 통학 준비물 안내를 만든다."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from services.air_quality_client import AirQualityClient
from services.environment_common import EnvironmentProviderError, KST, kst_now
from services.weather_client import WeatherClient


CAMPUSES = {
    "hallym": {"id": "hallym", "name": "한림대학교", "lat": 37.8863, "lon": 127.7381},
    "knu": {"id": "knu", "name": "강원대학교 춘천캠퍼스", "lat": 37.8691, "lon": 127.7442},
}


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else None


def _time_label(value):
    return value.strftime("%m월 %d일 %H시")


def _display(value):
    return format(value, "g")


class CommuteAdviceService:
    def __init__(self, weather_key, air_key, station_name="중앙로", timeout_seconds=5,
                 *, requester=None, now=None, clock=None, cache_ttl_seconds=900,
                 weather_client=None, air_client=None):
        self.now = now or (lambda: datetime.now(KST))
        self.station_name = str(station_name or "중앙로").strip()
        self._station_invalid = (not self.station_name or len(self.station_name) > 50
                                 or any(ord(character) < 32 for character in self.station_name))
        if self._station_invalid:
            self.station_name = "측정소 미설정"
        options = {"requester": requester, "now": self.now, "clock": clock,
                   "timeout_seconds": timeout_seconds, "cache_ttl_seconds": cache_ttl_seconds}
        self.weather_client = weather_client or WeatherClient(weather_key, **options)
        self.air_client = air_client or AirQualityClient(air_key, **options)
        # 앱 인스턴스당 최대 두 요청만 동시에 실행한다. 실패해도 다른 공급자는 유지한다.
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="commute-advice")

    def __repr__(self):
        return "CommuteAdviceService(station_name=" + repr(self.station_name) + ")"

    def close(self):
        self._executor.shutdown(wait=True)

    def _air_measurements(self, current):
        if self._station_invalid:
            raise EnvironmentProviderError("AIR_QUALITY_STATION_INVALID")
        return self.air_client.measurements(self.station_name, current)

    @staticmethod
    def _weather_empty(code):
        return {"status": "unavailable", "code": code, "source": "기상청",
                "issued_at": None, "fetched_at": None, "forecast_at": None,
                "temperature_c": None, "wind_speed_ms": None, "rain_probability": None,
                "message": "날씨 정보를 불러오지 못했어요. 잠시 후 다시 확인해 주세요."}

    def _air_empty(self, code):
        return {"status": "unavailable", "code": code, "source": "에어코리아",
                "station_name": self.station_name, "observed_at": None, "fetched_at": None,
                "pm10": {"value": None, "grade": None}, "pm25": {"value": None, "grade": None},
                "message": "대기질 정보를 불러오지 못했어요. 잠시 후 다시 확인해 주세요."}

    def _weather(self, core, now, start, end):
        issued, fetched = core.get("issued_at"), core.get("fetched_at")
        if not isinstance(issued, datetime) or issued > now:
            return self._weather_empty("WEATHER_PROVIDER_RESPONSE"), []
        candidates = [item for item in core.get("periods", [])
                      if isinstance(item.get("at"), datetime) and start <= item["at"] < end]
        valid = [item for item in candidates if any(item.get(field) is not None for field in
                 ("temperature_c", "wind_speed_ms", "rain_probability", "precipitation_type"))]
        if not valid:
            empty = self._weather_empty("WEATHER_NO_DATA")
            empty.update(issued_at=_iso(issued), fetched_at=_iso(fetched),
                         message="해당 시간대의 예보를 아직 확인하지 못했어요.")
            return empty, []
        first = min(valid, key=lambda row: row["at"])
        stale = now - issued > timedelta(hours=6)
        complete = len(valid) == 6 and all(all(item.get(key) is not None for key in
                       ("temperature_c", "wind_speed_ms", "rain_probability", "precipitation_type")) for item in valid)
        status = "stale" if stale else ("ok" if complete else "partial")
        result = {"status": status, "code": "WEATHER_STALE" if stale else (None if complete else "WEATHER_PARTIAL"),
                  "source": "기상청", "issued_at": _iso(issued), "fetched_at": _iso(fetched),
                  "forecast_at": _iso(first["at"]), "temperature_c": first.get("temperature_c"),
                  "wind_speed_ms": first.get("wind_speed_ms"), "rain_probability": first.get("rain_probability"),
                  "message": ("예보 갱신이 지연되고 있어요. 마지막 발표 시각을 확인해 주세요." if stale
                              else ("선택한 캠퍼스 주변 격자의 예보예요." if complete
                                    else "일부 시간대나 날씨 항목을 확인하지 못했어요."))}
        return result, [] if stale else valid

    def _air(self, core, now):
        observed = core.get("observed_at")
        if not isinstance(observed, datetime) or observed > now:
            return self._air_empty("AIR_QUALITY_PROVIDER_RESPONSE")
        pm10 = core.get("pm10", {"value": None, "grade": None})
        pm25 = core.get("pm25", {"value": None, "grade": None})
        any_value = any(item.get("value") is not None for item in (pm10, pm25))
        complete = all(item.get("value") is not None and item.get("grade") is not None for item in (pm10, pm25))
        stale = now - observed > timedelta(hours=3)
        status = "stale" if stale else ("ok" if complete else ("partial" if any_value else "unavailable"))
        if stale:
            code, message = "AIR_QUALITY_STALE", "대기질 자료 갱신이 지연되고 있어요. 마지막 측정 시각을 확인해 주세요."
        elif complete:
            code, message = None, "춘천의 참고 측정소 자료예요. 캠퍼스에서 직접 측정한 값은 아니에요."
        elif any_value:
            code, message = "AIR_QUALITY_PARTIAL", "일부 미세먼지 농도 또는 1시간 등급을 확인하지 못했어요."
        else:
            code, message = "AIR_QUALITY_MEASUREMENT_UNAVAILABLE", "측정소 점검이나 자료 누락으로 대기질을 확인하지 못했어요."
        return {"status": status, "code": code, "source": "에어코리아", "station_name": self.station_name,
                "observed_at": _iso(observed), "fetched_at": _iso(core.get("fetched_at")),
                "pm10": pm10.copy(), "pm25": pm25.copy(), "message": message}

    @staticmethod
    def _weather_alerts(periods):
        alerts = []

        def add(identity, title, message, row, severity="notice"):
            alerts.append({"id": identity, "severity": severity, "title": title,
                           "message": message, "source": "기상청", "at": _iso(row["at"])})

        rain = next((row for row in periods if row.get("precipitation_type") in (1, 2, 4)
                     or (row.get("precipitation_type") != 3 and row.get("rain_probability") is not None
                         and row["rain_probability"] >= 60)), None)
        if rain is not None:
            kind = {1: "비", 2: "비 또는 눈", 4: "소나기"}.get(rain.get("precipitation_type"))
            reason = ((kind + " 예보가 있어요.") if kind is not None else
                      ("강수확률이 " + _display(rain["rain_probability"]) + "%로 예보됐어요."))
            add("rain", "우산을 챙기세요", _time_label(rain["at"]) + " " + reason + " 외출 전 우산을 준비하세요.", rain)
        snow = next((row for row in periods if row.get("precipitation_type") == 3), None)
        if snow is not None:
            add("snow", "눈 예보를 확인하세요", _time_label(snow["at"]) + " 눈 예보가 있어요. 따뜻하게 입고 미끄러운 길에 주의하세요.", snow)
        cold_wind = next((row for row in periods if row.get("temperature_c") is not None
                          and row.get("wind_speed_ms") is not None and row["temperature_c"] <= 5
                          and row["wind_speed_ms"] >= 4), None)
        if cold_wind is not None:
            add("cold_wind", "목도리나 장갑을 챙기세요", _time_label(cold_wind["at"]) + " 기온 "
                + _display(cold_wind["temperature_c"]) + "℃ · 풍속 " + _display(cold_wind["wind_speed_ms"])
                + "m/s 예보예요. 추위와 바람에 대비하세요.", cold_wind)
        else:
            cold = next((row for row in periods if row.get("temperature_c") is not None and row["temperature_c"] <= 5), None)
            if cold is not None:
                add("cold", "따뜻한 겉옷을 챙기세요", _time_label(cold["at"]) + " 기온 "
                    + _display(cold["temperature_c"]) + "℃ 예보예요. 따뜻하게 입고 나가세요.", cold)
            windy = next((row for row in periods if row.get("wind_speed_ms") is not None and row["wind_speed_ms"] >= 8), None)
            if windy is not None:
                add("wind", "바람에 대비하세요", _time_label(windy["at"]) + " 풍속 "
                    + _display(windy["wind_speed_ms"]) + "m/s 예보예요. 바람을 막을 겉옷을 준비하세요.", windy)
        return alerts

    def advice(self, campus="hallym"):
        if campus not in CAMPUSES:
            raise ValueError("INVALID_COMMUTE_CAMPUS")
        location = CAMPUSES[campus]
        current = kst_now(self.now())
        start = current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        end = start + timedelta(hours=6)
        weather_future = self._executor.submit(self.weather_client.forecast, location["lat"], location["lon"], current)
        air_future = self._executor.submit(self._air_measurements, current)
        try:
            weather, periods = self._weather(weather_future.result(), current, start, end)
        except EnvironmentProviderError as error:
            weather, periods = self._weather_empty(error.code), []
        except Exception:
            weather, periods = self._weather_empty("WEATHER_PROVIDER_RESPONSE"), []
        try:
            air = self._air(air_future.result(), current)
        except EnvironmentProviderError as error:
            air = self._air_empty(error.code)
        except Exception:
            air = self._air_empty("AIR_QUALITY_PROVIDER_RESPONSE")
        alerts = self._weather_alerts(periods)
        if air["status"] in ("ok", "partial"):
            dirty = [(name, air[key]["grade"]) for key, name in (("pm25", "초미세먼지"), ("pm10", "미세먼지"))
                     if air[key]["value"] is not None and air[key]["grade"] in ("나쁨", "매우 나쁨")]
            if dirty:
                description = ", ".join(name + " " + grade for name, grade in dirty)
                observed = datetime.fromisoformat(air["observed_at"])
                alerts.append({"id": "dust", "severity": "warning", "title": "마스크를 챙기세요",
                               "message": _time_label(observed) + " " + self.station_name + " 측정소 기준 "
                                          + description + " 수준이에요. 외출 전 마스크를 챙기세요.",
                               "source": "에어코리아", "at": air["observed_at"]})
        if weather["status"] == "ok" and air["status"] == "ok":
            status = "ok"
        elif any(value["status"] in ("ok", "partial") for value in (weather, air)):
            status = "partial"
        else:
            status = "unavailable"
        if alerts:
            summary = alerts[0]["title"] + (" · 안내 " + str(len(alerts)) + "개" if len(alerts) > 1 else "")
        elif status == "ok":
            summary = "확인된 예보와 대기질에서 추가 준비물 안내가 없어요."
        else:
            summary = "일부 환경 정보를 확인하지 못했어요." if status == "partial" else "환경 정보를 불러오지 못했어요."
        return {"status": status, "campus": {"id": campus, "name": location["name"]},
                "generated_at": current.isoformat(),
                "period": {"start": start.isoformat(), "end": end.isoformat(), "label": "앞으로 6시간"},
                "weather": weather, "air": air, "alerts": alerts, "summary": summary}

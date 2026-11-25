"""에어코리아 측정소의 PM10·PM2.5 및 1시간 등급을 정규화한다."""

from datetime import datetime, timedelta

from services.environment_common import EnvironmentHTTP, KST, kst_now, number


GRADE_NAMES = {1: "좋음", 2: "보통", 3: "나쁨", 4: "매우 나쁨"}


def observation_time(value):
    """일부 관측자료의 24:00도 다음날 00:00으로 정규화한다."""
    text = str(value or "").strip()
    try:
        if text.endswith(" 24:00"):
            return datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=KST) + timedelta(days=1)
        return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
    except (ValueError, TypeError):
        return None


def particulate(item, prefix):
    flag = item.get(prefix + "Flag")
    # 점검·통신장애 등 품질 플래그가 있으면 농도와 등급을 사용하지 않는다.
    if flag is not None and str(flag).strip():
        return {"value": None, "grade": None}
    concentration = number(item.get(prefix + "Value"), 0, 10000)
    grade = number(item.get(prefix + "Grade1h"), 1, 4)
    if grade is not None and grade != int(grade):
        grade = None
    return {"value": concentration,
            "grade": GRADE_NAMES.get(grade) if concentration is not None else None}


class AirQualityClient(EnvironmentHTTP):
    URL = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty"

    def __init__(self, key, **kwargs):
        super().__init__(key, "AIR_QUALITY", **kwargs)

    def measurements(self, station_name, at=None):
        def load():
            body = self.get_body(self.URL, {
                "stationName": station_name, "dataTerm": "DAILY", "returnType": "json",
                "pageNo": 1, "numOfRows": 24, "ver": "1.3",
            })
            rows = self.items(body)
            if not rows:
                raise self.error("NO_DATA")
            dated = [(observation_time(row.get("dataTime")), row) for row in rows]
            dated = [(stamp, row) for stamp, row in dated if stamp is not None]
            if not dated:
                raise self.error("PROVIDER_RESPONSE")
            latest, row = max(dated, key=lambda pair: pair[0])
            return {"observed_at": latest, "fetched_at": kst_now(self.now()),
                    "pm10": particulate(row, "pm10"), "pm25": particulate(row, "pm25")}
        return self.cache.get(station_name, load)

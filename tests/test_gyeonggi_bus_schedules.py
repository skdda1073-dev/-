import unittest
from datetime import datetime, time
from types import SimpleNamespace

from services.gyeonggi_bus_schedules import GyeonggiBusSchedules, KST


class GyeonggiBusSchedulesTests(unittest.TestCase):
    def test_published_direction_and_service_day_are_matched_by_exact_route_id(self):
        table = """
        <table>
          <tr><td><span>일반</span><a href="javascript:busInfoView('땡큐37', '222000040')">땡큐37</a></td>
            <td>차산리</td><td>만취대</td>
            <td>평일 : 06:20 ~ 20:30<br/>토요일 : 07:00 ~ 20:30<br/>일요일 : 07:00 ~ 20:30<br/>공휴일 : 07:00 ~ 20:30</td>
            <td>평일 : 07:25 ~ 21:35<br/>토요일 : 08:05 ~ 21:35<br/>일요일 : 08:05 ~ 21:35<br/>공휴일 : 08:05 ~ 21:35</td>
            <td>평일 : 60 ~ 100 분<br/>토요일 : 140 ~ 180 분<br/>일요일 : 140 ~ 180 분<br/>공휴일 : 160 ~ 180 분</td>
            <td>대원운수</td>
          </tr>
        </table>
        """
        requested = []

        def request(url, **kwargs):
            requested.append((url, kwargs["params"]))
            return SimpleNamespace(status_code=200, text=table)

        observed = datetime(2026, 11, 26, 9, tzinfo=KST)
        schedules = GyeonggiBusSchedules(requester=request, now=lambda: observed)
        result = schedules.route("31130", "GGB222000040")
        self.assertEqual((result.name, result.start, result.end), ("땡큐37", "차산리", "만취대"))
        weekday = result.frequencies["1", "weekday"]
        self.assertEqual((weekday.first, weekday.last), (time(7, 25), time(21, 35)))
        self.assertEqual((weekday.minimum, weekday.maximum, weekday.observed), (60, 100, observed.date()))
        self.assertEqual(result.frequencies["1", "sunday"].minimum, 140)
        self.assertEqual(result.frequencies["1", "holiday"].minimum, 160)
        self.assertIs(result, schedules.route("31130", "GGB222000040"))
        self.assertIsNone(schedules.route("32010", "GGB222000040"))
        self.assertEqual(requested, [("https://www.gbis.go.kr/service/busInfo/metroBus.action",
                                      {"centerId": "4136000000", "cmd": "metroBusList"})])

    def test_village_weekend_does_not_supply_holiday_data_and_incomplete_hours_are_ignored(self):
        table = """
        <tr><td><a href="javascript:busInfoView('땡큐61', '241349001')">땡큐61</a></td>
          <td>한강우성.해태아파트</td><td>금곡동구종점.금곡초교</td>
          <td>평일 : 05:30 ~ <br/>주말 : 06:30 ~ 21:35</td>
          <td>평일 : 06:00 ~ 23:10<br/>주말 : 07:00 ~ 22:10</td>
          <td>평일 : 0 ~ 40 분<br/>주말 : 45 ~ 45 분</td><td>남양주-태화상운</td>
        </tr>
        """

        def request(url, **_kwargs):
            return SimpleNamespace(status_code=200, text=table if "townBus" in url else "<html></html>")

        schedules = GyeonggiBusSchedules(requester=request)
        result = schedules.route("31130", "GGB241349001")
        self.assertEqual(set(result.frequencies), {(direction, day) for direction in ("0", "1")
                                                   for day in ("saturday", "sunday")})

"""Read GBIS boarding forecasts for an exact route, stop and sequence."""

from urllib.parse import unquote
from xml.etree import ElementTree

import requests


ARRIVAL_URL = "https://apis.data.go.kr/6410000/busarrivalservice/v2/getBusArrivalItemv2"


class GyeonggiBusArrivals:
    def __init__(self, service_key):
        self._key = unquote(service_key)

    def predictions(self, station_id, route_id, sequence):
        if not self._key:
            return ()
        station, route = station_id.removeprefix("GGB"), route_id.removeprefix("GGB")
        if not station.isascii() or not station.isdecimal() or not route.isascii() or not route.isdecimal():
            return ()
        try:
            response = requests.get(ARRIVAL_URL, params={"serviceKey": self._key, "stationId": station,
                                                          "routeId": route, "staOrder": sequence, "format": "xml"},
                                    timeout=(2, 4), allow_redirects=False)
            if response.status_code != 200:
                return ()
            root = ElementTree.fromstring(response.content)
        except (requests.RequestException, ElementTree.ParseError):
            return ()
        if root.findtext("./msgHeader/resultCode") != "0":
            return ()
        item = root.find("./msgBody/busArrivalItem")
        if item is None or item.findtext("flag") == "STOP" or (
                item.findtext("stationId"), item.findtext("routeId"), item.findtext("staOrder")) != (
                station, route, str(sequence)):
            return ()
        return tuple(int(seconds) for field in ("predictTimeSec1", "predictTimeSec2")
                     if (seconds := item.findtext(field, "")).isascii() and seconds.isdecimal()
                     and len(seconds) <= 4 and int(seconds) <= 7200)

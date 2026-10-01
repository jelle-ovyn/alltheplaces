from math import asin, ceil, cos, radians, sin, sqrt
from typing import Any, AsyncIterator, Iterable

import reverse_geocoder
from pyproj import Transformer
from scrapy import Spider
from scrapy.http import JsonRequest, Response

from locations.geo import EARTH_RADIUS, MILES_TO_KILOMETERS
from locations.items import Feature

BNG_TO_WGS84 = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)
# Westernmost point of Northern Ireland; nothing in the UK lies further west.
UK_WESTERN_LIMIT = -8.18


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    a = (
        sin(radians(lat2 - lat1) / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS * asin(sqrt(a))


class CashzoneGBSpider(Spider):
    name = "cashzone_gb"
    item_attributes = {"brand": "Cashzone", "brand_wikidata": "Q110738461"}

    # The locator API takes a centre, a radius and a page index, and reports the
    # exact number of ATMs within the radius (TotalRecCount). Paging is only
    # reliable a few pages deep: measured 2026-10-01, 3 pages returned every
    # ATM, but 9+ pages lost 12-33% to records repeated across pages. So cells of
    # a quadtree over the country are queried with a radius reaching their
    # corners, and a cell holding more than MAX_PAGES pages of ATMs is split.
    API_URL = "https://clsws.locatorsearch.net/Rest/LocatorSearchAPI.svc/GetLocations"
    NETWORK_ID = 814
    PAGE_SIZE = 50
    MAX_PAGES = 3
    # Root cell, in British National Grid metres, covering GB, Northern Ireland,
    # the Channel Islands and the Isle of Man. Cells are tiled in BNG rather than
    # by bearing so neighbouring cells abut exactly, leaving no gaps.
    ROOT_CENTRE = (250_000, 550_000)
    ROOT_HALF_M = 700_000
    # The API rejects a fractional radius, so it is rounded up to whole miles.
    # Splitting a cell already searched at 1 mile cannot narrow the search, so
    # such a cell is paged through in full however many ATMs it has.

    async def start(self) -> AsyncIterator[JsonRequest]:
        yield self.make_request(*self.ROOT_CENTRE, self.ROOT_HALF_M)

    def make_request(self, x: float, y: float, half_m: float, page: int = 1, seen: set | None = None) -> JsonRequest:
        lon, lat = BNG_TO_WGS84.transform(x, y)
        corners = [BNG_TO_WGS84.transform(x + dx * half_m, y + dy * half_m) for dx in (-1, 1) for dy in (-1, 1)]
        radius_km = max(distance_km(lat, lon, clat, clon) for clon, clat in corners) * 1.001 + 0.01
        miles = ceil(radius_km / MILES_TO_KILOMETERS)
        return JsonRequest(
            url=self.API_URL,
            data={
                "NetworkId": self.NETWORK_ID,
                "Latitude": lat,
                "Longitude": lon,
                "Miles": miles,
                "SearchByOptions": "",
                "PageIndex": page,
            },
            cb_kwargs={
                "x": x,
                "y": y,
                "half_m": half_m,
                "miles": miles,
                "page": page,
                "seen": seen if seen is not None else set(),
            },
            dont_filter=True,
        )

    def parse(
        self, response: Response, x: float, y: float, half_m: float, miles: int, page: int, seen: set
    ) -> Iterable[Feature | JsonRequest]:
        data = response.json()["data"]
        total = data["TotalRecCount"]
        for atm in data["ATMInfo"] or []:  # null for an empty cell
            seen.add(atm["LocationID"])
            yield self.parse_atm(atm)

        pages = ceil(total / self.PAGE_SIZE)
        if pages > self.MAX_PAGES and miles > 1:
            yield from self.split(x, y, half_m)
        elif page < pages:
            yield self.make_request(x, y, half_m, page + 1, seen)
        elif len(seen) < total:
            # Paging repeated some records and so dropped others: search finer.
            self.crawler.stats.inc_value("cashzone_gb/incomplete_paging")
            if miles > 1:
                yield from self.split(x, y, half_m)

    def split(self, x: float, y: float, half_m: float) -> Iterable[JsonRequest]:
        for dx in (-1, 1):
            for dy in (-1, 1):
                yield self.make_request(x + dx * half_m / 2, y + dy * half_m / 2, half_m / 2)

    def parse_atm(self, atm: dict[str, Any]) -> Feature:
        item = Feature()
        item["ref"] = str(atm["LocationID"])
        item["lat"] = atm["Latitude"]
        item["lon"] = atm["Longitude"]
        item["name"] = ", ".join(filter(None, [atm.get("InstitutionName"), atm.get("RetailOutlet")]))
        item["street_address"] = atm.get("Street")
        item["city"] = atm.get("City")
        item["postcode"] = atm.get("ZipCode")
        if self.misplaced_in_ireland(item):
            # ~1,190 ATMs (nearly all Santander) with GB addresses are placed
            # across the island of Ireland; drop the bogus point, keep the address.
            self.crawler.stats.inc_value("cashzone_gb/misplaced_in_ireland")
            item["lat"] = item["lon"] = None
        if atm.get("CUSpecific") == "Internal":
            item["extras"]["indoor"] = "yes"
        elif atm.get("CUSpecific") == "Through-The-Wall":
            item["extras"]["indoor"] = "no"
        return item

    @staticmethod
    def misplaced_in_ireland(item: Feature) -> bool:
        if item["lon"] < UK_WESTERN_LIMIT:
            return True
        if (item["postcode"] or "").upper().startswith("BT"):
            # A Northern Ireland postcode belongs on the island; reverse geocoding
            # is unreliable along the border, so don't second-guess it there.
            return False
        # Any other UK postcode is in Great Britain or the Crown Dependencies.
        place = reverse_geocoder.get((item["lat"], item["lon"]), mode=1, verbose=False)
        return place["cc"] == "IE" or place["admin1"] == "Northern Ireland"

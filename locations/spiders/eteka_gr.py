import re
from typing import Iterable

from locations.categories import Categories, Fuel, apply_category, apply_yes_no
from locations.items import Feature
from locations.storefinders.wp_go_maps import WpGoMapsSpider


class EtekaGRSpider(WpGoMapsSpider):
    name = "eteka_gr"
    item_attributes = {"brand": "ΕΤΕΚΑ", "brand_wikidata": "Q31283952", "name": "ΕΤΕΚΑ"}
    allowed_domains = ["eteka.com.gr"]
    # Map 1 is the Greek station list, map 2 an English duplicate, the others are company offices
    map_id = 1

    def pre_process_marker(self, marker: dict) -> dict:
        # Many coordinates use dots as thousands separators, e.g. "39.612.811", and some are "Ν/Α"
        for key in ("lat", "lng"):
            whole, _, fraction = marker[key].strip().partition(".")
            fraction = fraction.replace(".", "")
            marker[key] = f"{whole}.{fraction}" if whole.isdigit() and fraction.isdigit() else None
        return marker

    def post_process_item(self, item: Feature, location: dict) -> Iterable[Feature]:
        item.pop("name", None)
        item.pop("addr_full", None)
        address = location["address"].strip()
        if "," in address:
            item["street_address"], item["city"] = (part.strip() for part in address.rsplit(",", 1))
        else:
            item["addr_full"] = address
        if phone := re.search(r"\d[\d ]{8,}\d", location["title"]):
            item["phone"] = phone.group(0)
        if "υγραέριο κίνησης" in location["title"].lower():
            apply_yes_no(Fuel.LPG, item, True)
        apply_category(Categories.FUEL_STATION, item)
        yield item

from typing import Any, Iterable

from scrapy import Spider
from scrapy.http import Response

from locations.categories import Categories, Extras, apply_category, apply_yes_no
from locations.hours import OpeningHours
from locations.items import Feature


class CrossBankUSSpider(Spider):
    name = "cross_bank_us"
    item_attributes = {"brand": "Cross Bank", "name": "Cross Bank"}
    allowed_domains = ["www.mycrossbank.com"]
    start_urls = ["https://www.mycrossbank.com/about-us/locations-hours.html"]

    def parse(self, response: Response, **kwargs: Any) -> Iterable[Feature]:
        for location in response.css("#locList li.loc"):
            details_url = response.urljoin(location.css("a.seeDetails::attr(href)").get())
            item = Feature()
            item["ref"] = details_url.split("id=")[1].split("&")[0]
            item["website"] = details_url
            item["branch"] = location.attrib["data-title"].removesuffix(" Branch")
            item["lat"] = location.attrib["data-latitude"]
            item["lon"] = location.attrib["data-longitude"]
            item["street_address"] = location.attrib["data-address1"]
            item["city"] = location.attrib["data-city"]
            item["state"] = location.attrib["data-state"]
            item["postcode"] = location.attrib["data-zip"]
            item["country"] = "US"
            item["phone"] = location.xpath('.//span[@class="key"][text()="Phone"]/following-sibling::span/text()').get()
            item["extras"]["fax"] = location.xpath(
                './/span[@class="key"][text()="Fax"]/following-sibling::span/text()'
            ).get()

            item["opening_hours"] = OpeningHours()
            item["opening_hours"].add_ranges_from_string(
                " ".join(location.css(".lobbyHours div span::text").getall()).replace("Noon", "12:00pm")
            )
            if drive_through := location.css(".driveThroughHours"):
                oh = OpeningHours()
                oh.add_ranges_from_string(
                    " ".join(drive_through.css("div span::text").getall()).replace("Noon", "12:00pm")
                )
                item["extras"]["opening_hours:drive_through"] = oh.as_opening_hours()

            apply_category(Categories.BANK, item)
            apply_yes_no(Extras.ATM, item, bool(location.css(".hasATM")))
            apply_yes_no(Extras.DRIVE_THROUGH, item, bool(location.css(".driveThroughHours")))
            yield item

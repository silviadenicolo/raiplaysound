from datetime import datetime as dt
from datetime import timedelta
from itertools import chain
from os.path import join as pathjoin
from typing import List, Optional
from urllib.parse import urljoin

import requests
from feedendum import to_rss_string, Feed, FeedItem

NSITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


def url_to_filename(url: str) -> str:
    return url.split("/")[-1] + ".xml"


def _datetime_parser(s: str) -> Optional[dt]:
    if not s:
        return None
    try:
        return dt.strptime(s, "%d-%m-%Y %H:%M:%S")
    except ValueError:
        pass
    try:
        return dt.strptime(s, "%d-%m-%Y %H:%M")
    except ValueError:
        pass
    try:
        return dt.strptime(s, "%Y-%m-%d")
    except ValueError:
        pass
    return None


class RaiParser:
    def __init__(self, url: str, folderPath: str) -> None:
        self.url = url
        self.folderPath = folderPath
        self.inner: List[Feed] = []

    def extend(self, url: str) -> None:
        url = urljoin(self.url, url)
        if url == self.url:
            return
        if url in (f.url for f in self.inner):
            return
        parser = RaiParser(url, self.folderPath)
        self.inner.extend(parser.process())

    def _json_to_feed(self, feed: Feed, rdata) -> List[Feed]:
        feed.title = rdata["title"]
        feed.description = rdata["podcast_info"].get("description", "")
        feed.description = feed.description or rdata["title"]
        feed.url = self.url
        feed._data["image"] = {"url": urljoin(self.url, rdata["podcast_info"]["image"])}
        feed._data[f"{NSITUNES}author"] = "RaiPlaySound"
        feed._data["language"] = "it-it"
        feed._data[f"{NSITUNES}owner"] = {f"{NSITUNES}email": "timedum@gmail.com"}
        # Categories
        categories = set()  # to prevent duplicates
        for c in chain(
            rdata["podcast_info"]["genres"],
            rdata["podcast_info"]["subgenres"],
            rdata["podcast_info"]["dfp"].get("escaped_genres", []),
            rdata["podcast_info"]["dfp"].get("escaped_typology", []),
        ):
            categories.add(c["name"])
        try:
            for c in rdata["podcast_info"]["metadata"]["product_sources"]:
                categories.add(c["name"])
        except KeyError:
            pass
        feed._data[f"{NSITUNES}category"] = [{"@text": c} for c in categories]
        feed.update = _datetime_parser(rdata["block"]["update_date"])
        if not feed.update:
            feed.update = _datetime_parser(rdata["track_info"]["date"])
        last_update = dt.fromtimestamp(0)
        for item in rdata["block"]["cards"]:
            if "/playlist/" in item.get("weblink", ""):
                self.extend(item["weblink"])
            if not item.get("audio", None):
                continue
            fitem = FeedItem()
            fitem.title = item["toptitle"]
            fitem.id = "timendum-raiplaysound-" + item["uniquename"]
            # Keep original ordering by tweaking update seconds
            # Fix time in case of bad ordering
            dupdate = _datetime_parser(item["create_date"] + " " + item["create_time"])
            if dupdate <= last_update:
                dupdate = last_update + timedelta(seconds=1)
            fitem.update = dupdate
            last_update = dupdate
            fitem.url = urljoin(self.url, item["track_info"]["page_url"])
            fitem.content = item.get("description", item["title"])
            fitem._data = {
                "enclosure": {
                    "@type": "audio/mpeg",
                    "@url": urljoin(self.url, item["audio"]["url"]),
                },
                f"{NSITUNES}title": fitem.title,
                f"{NSITUNES}summary": fitem.content,
                f"{NSITUNES}duration": item["audio"]["duration"],
                "image": {"url": urljoin(self.url, item["image"])},
            }
            if item.get("downloadable_audio", None) and item["downloadable_audio"].get("url", None):
                fitem._data["enclosure"]["@url"] = urljoin(self.url, item["downloadable_audio"]["url"]).replace("http:", "https:")
            if item.get("season", None) and item.get("episode", None):
                fitem._data[f"{NSITUNES}season"] = item["season"]
                fitem._data[f"{NSITUNES}episode"] = item["episode"]
            feed.items.append(fitem)

    def process(self, skip_programmi=True, skip_film=True) -> List[Feed]:
        result = requests.get(self.url + ".json")
        try:
            result.raise_for_status()
        except requests.HTTPError as e:
            print(f"Error with {self.url}: {e}")
            return self.inner
        rdata = result.json()
        typology = rdata["podcast_info"].get("typology", "").lower()
        if skip_programmi and (typology in ("programmi radio", "informazione notiziari")):
            print(f"Skipped: {self.url}")
            return []
        if skip_film and (typology in ("film", "fiction")):
            print(f"Skipped: {self.url}")
            return []
        for tab in rdata["tab_menu"]:
            if tab["content_type"] == "playlist":
                self.extend(tab["weblink"])
        feed = Feed()
        self._json_to_feed(feed, rdata)
        if not feed.items and not self.inner:
            print(f"Empty: {self.url}")
        if feed.items:
            if all([i._data.get(f"{NSITUNES}episode") for i in feed.items]) and all(
                [i._data.get(f"{NSITUNES}season") for i in feed.items]
            ):
                try:
                    feed.items = sorted(
                        feed.items,
                        key=lambda e: int(e._data[f"{NSITUNES}episode"])
                        + int(e._data[f"{NSITUNES}season"]) * 10000,
                    )
                except ValueError:
                    # season or episode not an int
                    feed.items = sorted(
                        feed.items,
                        key=lambda e: str(e._data[f"{NSITUNES}season"]).zfill(5)
                        + str(e._data[f"{NSITUNES}episode"]).zfill(5),
                    )
            else:
                feed.sort_items()
            filename = url_to_filename(self.url)
            with open(pathjoin(self.folderPath, filename), "w", encoding="utf8") as wo:
                wo.write(to_rss_string(feed))
            print(f"Written {pathjoin(self.folderPath, filename)}")
        return [feed] + self.inner


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Genera un RSS da un programma di RaiPlaySound.",
        epilog="Info su https://github.com/timendum/raiplaysound/",
    )
    parser.add_argument("url", help="URL di un podcast (o playlist) su raiplaysound.")
    parser.add_argument(
        "-f", "--folder", help="Cartella in cui scrivere il RSS podcast.", default="."
    )
    parser.add_argument(
        "--film",
        help="Elabora il podcast anche se sembra un film.",
        action="store_true",
    )
    parser.add_argument(
        "--programma",
        help="Elabora il podcast anche se sembra un programma radio/tv.",
        action="store_true",
    )

    args = parser.parse_args()
    parser = RaiParser(args.url, args.folder)
    parser.process(skip_programmi=not args.programma, skip_film=not args.film)


if __name__ == "__main__":
    main()

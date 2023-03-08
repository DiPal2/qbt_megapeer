# VERSION: 0.5
# AUTHORS: DiPal

# Megapeer.vip search engine plugin for qBittorrent

import base64
import json
import logging
import re
import sys
import time
from concurrent.futures.thread import ThreadPoolExecutor
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Optional, Union
from urllib.error import URLError, HTTPError
import urllib.parse
import urllib.request


try:
    from novaprinter import prettyPrinter
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent.absolute()))
    from novaprinter import prettyPrinter

FILE = Path(__file__)
BASEDIR = FILE.parent.absolute()

FILENAME = FILE.stem
FILE_J, FILE_C = [BASEDIR / (FILENAME + fl) for fl in (".json", ".cookie")]

PAGES = 50


def rng(t: int) -> range:
    return range(1, -(-t // PAGES))

ITEM_DIVIDER = '<td class="row1 tLeft"><div class="topic-detail">'
SPLIT_ARRAY = [
                ["<span>Добавлен:</span> ", " в "],
                ['<a class="med tLink hl-tags bold" href="/', '">'],
                ['', '</a>'],
                ['<a class="gr-button tr-dl dl-stub" href="', '">'],
                ['\n', ' <img src="/pic/icon_tor_arrow.png"/>'],
            ]
NOT_FOUND_STR = '<span style="color:#0000FF">По вашему запросу ничего не найдено. Попробуйте изменить свой запрос и/или параметры поиска.</span>'

RE_RESULTS = re.compile(r'<td\sstyle="padding-left:\s10px;">Всего:\s(\d{1,4})</td>', re.S)
PATTERNS = ("%sbrowse.php?search=%s&cat=%i",)

# setup logging
logging.basicConfig(
    format="%(asctime)s %(name)-12s %(levelname)-8s %(message)s",
    datefmt="%m-%d %H:%M",
    level=logging.DEBUG
)

logger = logging.getLogger(__name__)


@dataclass
class Config:
    torrent_date: bool = True
    magnet: bool = False
    proxy: bool = False
    proxies: dict = field(default_factory=lambda: {"http": "", "https": ""})
    ua: str = ("Mozilla/5.0 (X11; Linux i686; rv:38.0) Gecko/20100101 "
               "Firefox/38.0 ")

    def to_str(self) -> str:
        return json.dumps(self.to_dict(), indent=4, sort_keys=False)

    def to_dict(self) -> dict:
        return {self._to_camel(k): v for k, v in self.__dict__.items()}

    def _validate_json(self, obj: dict) -> bool:
        is_valid = True
        for k, v in self.__dict__.items():
            _val = obj.get(self._to_camel(k))
            if type(_val) is not type(v):
                is_valid = False
                continue
            if type(_val) is dict:
                for dk, dv in v.items():
                    if type(_val.get(dk)) is not type(dv):
                        _val[dk] = dv
                        is_valid = False
            setattr(self, k, _val)
        return is_valid

    @staticmethod
    def _to_camel(s: str) -> str:
        return "".join(x.title() if i else x
                       for i, x in enumerate(s.split("_")))


config = Config()


class Megapeer:
    name = "Megapeer"
    url = "https://megapeer.vip/"
    url_dl = url.replace("//", "//d.") + "download/"
    supported_categories = {"all": 0,
                            "movies": 0,
                            "tv": 0,
                            "music": 94,
                            "games": 28,
                            "anime": 0,
                            "software": 107,
                            "pictures": 0,
                            "books": 52}

    # error message
    error: Optional[str] = None
    # establish connection
    session = urllib.request.build_opener()

    torrents: dict = {}

    def __init__(self):
        # add proxy handler if needed
        if config.proxy:
            if any(config.proxies.values()):
                self.session.add_handler(urllib.request.ProxyHandler(config.proxies))
                logger.debug("Proxy is set!")
            else:
                self.error = "Proxy enabled, but not set!"

        # change user-agent
        self.session.addheaders = [("User-Agent", config.ua), ("Referer", self.url + "browse.php")]

    def search(self, what: str, cat: str = "all") -> None:
        if self.error:
            self.pretty_error(what)
            return None
        phrase = urllib.parse.unquote(what)
        what = urllib.parse.quote_plus(phrase, encoding='cp1251')
        query = PATTERNS[0] % (self.url, what, self.supported_categories[cat])

        # make first request (maybe it enough)
        t0, total = time.time(), self.searching(query, phrase, True)
        if self.error:
            self.pretty_error(what)
            return None
        # do async requests
        if total > PAGES:
            query = query + "&page={}"
            qrs = [(query.format(x), phrase) for x in rng(total)]
            with ThreadPoolExecutor(len(qrs)) as executor:
                executor.map(self.searching_wrapper, qrs, timeout=30)

        logger.debug(f"--- {time.time() - t0} seconds ---")
        logger.info(f"Found torrents: {total}")

    def download_torrent(self, url: str) -> None:
        # Download url
        response = self._request(url)
        if self.error:
            self.pretty_error(url)
            return None

        # Create a torrent file
        with NamedTemporaryFile(suffix=".torrent", delete=False) as fd:
            fd.write(response)

            # return file path
            logger.debug(fd.name + " " + url)
            print(fd.name + " " + url)

    def searching_wrapper(self, args):
        return self.searching(*args)

    def searching(self, query: str, phrase:str, first: bool = False) -> Union[None, int]:
        logger.debug(f"searching {query}")
        response = self._request(query)
        if self.error:
            return None
        # with open('searching.htm', 'wb') as f:
        #     f.write(response)
        page, torrents_found = response.decode('cp1251'), -1
        if first:
            # firstly we check if there is a result
            result = RE_RESULTS.search(page)
            if not result:
                if NOT_FOUND_STR in page:
                    return 0
                self.error = "Unexpected page content"
                return None
            torrents_found = int(result[1])
            if not torrents_found:
                return 0
        self.draw(page, phrase.split(' '))

        return torrents_found
    
    @staticmethod
    def extractor(item, splitters):
        result = []
        for splitter in splitters:
            if splitter[0] == '':
                items = ['', item]
            else:
                items = item.split(splitter[0], 1)
            if(len(items)) < 2:
                break
            data = items[1].split(splitter[1], 1)
            result.append(data[0])
            if(len(data)) < 2:
                break
            item = data[1]
        return result

    def draw(self, html: str, phrases) -> None:
        splitted = html.split(ITEM_DIVIDER)
        for item in splitted:
            result = self.extractor(item, SPLIT_ARRAY)
            if len(result) < len(SPLIT_ARRAY):
                continue

            ct = unescape(result[0]).split(" ")
            months = ("января", "февраля", "марта", "апреля", "мая", "июня",
                      "июля", "августа", "сентября", "октября", "ноября", "декабря")
            for i, m in enumerate(months, 1):
                if m in ct[1]:
                    ct[1] = ct[1].replace(m, f"{i:02d}")
                    break
            ct = "[" + ct[2][-2:] + "." + ct[1] + "." + ("0" + ct[0])[-2:] + "] "

            tn = unescape(result[2].replace('<span class="brackets-pair">',"").replace("</span>",""))

            all_found = True
            for phrase in phrases:
                all_found = phrase in tn
                if not all_found:
                    break

            if all_found:
                prettyPrinter({
                    "engine_url": self.url,
                    "desc_link": self.url + result[1],
                    "name": ct + tn,
                    "link": self.url + result[3],
                    "size": result[4],
                    "seeds": 100,
                    "leech": 100
                })

    def _request(
            self, url: str, data: Optional[bytes] = None, repeated: bool = False
    ) -> Union[bytes, None]:
        try:
            with self.session.open(url, data, 5) as r:
                # checking that tracker isn't blocked
                if r.geturl().startswith((self.url, self.url_dl)):
                    return r.read()
                self.error = f"{url} is blocked. Try another proxy."
        except (URLError, HTTPError) as err:
            logger.error(err.reason)
            error = str(err.reason)
            if "timed out" in error and not repeated:
                logger.debug("Repeating request...")
                return self._request(url, data, True)
            if "no host given" in error:
                self.error = "Proxy is bad, try another!"
            elif hasattr(err, "code"):
                self.error = f"Request to {url} failed with status: {err.code}"
            else:
                self.error = f"{url} is not response! Maybe it is blocked."

        return None

    def pretty_error(self, what: str) -> None:
        prettyPrinter({"engine_url": self.url,
                       "name": f"[{urllib.parse.unquote(what)}][Error]: {self.error}",
                       "link": self.url + "error",
                       "size": "1 TB",  # lol
                       "seeds": 100,
                       "leech": 100})

        self.error = None


# pep8
megapeer = Megapeer

if __name__ == "__main__":
    engine = megapeer()
    engine.search("доктор кто")

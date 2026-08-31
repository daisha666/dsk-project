"""
dsk_Project
Base Collector
Version 0.1
"""

import requests
from bs4 import BeautifulSoup


class BaseCollector:
    """データ取得の基底クラス"""

    def __init__(self):

        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/138.0 Safari/537.36"
            )
        }

    def get_html(self, url):

        response = requests.get(
            url,
            headers=self.headers,
            timeout=(10, 20)  # (接続タイムアウト, 読み取りタイムアウト)秒
        )

        response.raise_for_status()

        return BeautifulSoup(
            response.text,
            "html.parser"
        )


if __name__ == "__main__":

    print("=" * 40)
    print("dsk_Project")
    print("BaseCollector 読み込み成功")
    print("=" * 40)

"""Example plugin. Every .py file in this folder is loaded at start. Delete or edit freely."""
import json
import urllib.request

from callme.tools import tool


@tool("Check whether a website is up and how fast it answers.",
      url={"type": "string", "description": "full URL, e.g. https://example.com"}, required=["url"])
def check_website(url: str) -> str:
    import time

    t = time.time()
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return f"{url} is up (HTTP {r.status}) in {time.time() - t:.1f}s"
    except Exception as e:  # noqa: BLE001
        return f"{url} looks down: {e}"


@tool("Get the current price of a cryptocurrency in USD.",
      coin={"type": "string", "description": "coingecko id, e.g. bitcoin, ethereum"}, required=["coin"])
def crypto_price(coin: str) -> str:
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd"
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.load(r)
    return f"{coin}: ${data.get(coin, {}).get('usd', '?')}"

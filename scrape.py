"""Daily price checks. Only records prices with recognizable product structured data.
No guessed prices, no bypassing anti-bot protections.
"""
import json
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
PRODUCTS = ROOT / "products.json"
HISTORY = ROOT / "data" / "history.json"
ALLOWED = {"www.bakuelectronics.az", "bakuelectronics.az", "kontakt.az", "www.kontakt.az", "irshad.az", "www.irshad.az", "soliton.az", "www.soliton.az", "musicgallery.az", "www.musicgallery.az"}
HEADERS = {"User-Agent": "PriceWatchAZ/1.0 (+personal price tracker; one request per product daily)", "Accept-Language": "az,en;q=0.8"}


def money(value):
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return None
    value = re.sub(r"[^\d,.]", "", value.strip())
    if not value:
        return None
    if "," in value and "." in value:
        value = value.replace(",", "") if value.rfind(".") > value.rfind(",") else value.replace(".", "").replace(",", ".")
    elif "," in value:
        value = value.replace(",", ".")
    try:
        n = Decimal(value)
        return float(n) if 0 < n < 1000000 else None
    except InvalidOperation:
        return None


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def extract(html, model):
    soup = BeautifulSoup(html, "html.parser")
    model_key = re.sub(r"[^a-z0-9]", "", model.lower())
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            obj = json.loads(tag.string or tag.get_text())
        except (ValueError, TypeError):
            continue
        for item in walk(obj):
            types = item.get("@type", [])
            types = [types] if isinstance(types, str) else types if isinstance(types, list) else []
            if not any(str(t).lower().endswith("product") for t in types):
                continue
            label = str(item.get("name", "") or "") + " " + str(item.get("sku", "") or "") + " " + str(item.get("mpn", "") or "")
            if model_key not in re.sub(r"[^a-z0-9]", "", label.lower()):
                continue
            offers = item.get("offers", [])
            offers = offers if isinstance(offers, list) else [offers]
            for offer in offers:
                if not isinstance(offer, dict):
                    continue
                currency = str(offer.get("priceCurrency", "")).upper()
                price = money(offer.get("price"))
                if not price and isinstance(offer.get("priceSpecification"), dict):
                    price = money(offer["priceSpecification"].get("price"))
                if price and currency in {"AZN", ""}:
                    return price, "AZN", "json-ld"
    return None, None, "No verified product-specific price found"


def main():
    products = json.loads(PRODUCTS.read_text(encoding="utf-8"))
    history = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for product in products:
        for offer in product.get("offers", []):
            url = offer.get("url", "")
            row = {"date": today, "product_id": product["id"], "store": offer.get("store", ""), "url": url, "price": None, "currency": "AZN", "status": "unavailable"}
            if urlparse(url).hostname not in ALLOWED or not url.startswith("https://"):
                row["status"] = "unsupported-domain"
            else:
                try:
                    response = requests.get(url, headers=HEADERS, timeout=18)
                    response.raise_for_status()
                    price, currency, note = extract(response.text, product["model"])
                    if price is not None:
                        row.update(price=price, currency=currency, status="ok")
                    else:
                        row["status"] = "price-not-found"
                    print(f"{product['model']} | {offer['store']}: {row['price']} ({row['status']}) - {note}")
                except requests.RequestException as exc:
                    row["status"] = "request-error"
                    print(f"{product['model']}: request failed: {type(exc).__name__}")
                time.sleep(1)
            history = [h for h in history if not (h.get("date") == today and h.get("product_id") == row["product_id"] and h.get("store") == row["store"] and h.get("url") == row["url"])]
            history.append(row)
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(sorted(history, key=lambda r: (r["date"], r["product_id"], r["store"])), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(history)} observations")


if __name__ == "__main__":
    main()

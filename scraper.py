import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import os
import re
import time
import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

SITEMAP_URLS = [
    "https://setadiran.ir/sitemap.xml",
    "https://setadiran.ir/sitemap_index.xml",
    "https://setadiran.ir/fa/sitemap.xml",
]

TENDER_URL_KEYWORDS = [
    "tender", "monaghese", "mozayede", "agahi", "forookh",
    "مناقصه", "مزایده", "آگهی", "فراخوان", "استعلام",
]

def send_telegram(message):
    """ارسال پیام به تلگرام"""
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(api_url, data=data)
        print("✅ پیام ارسال شد.")
    except Exception as e:
        print(f"❌ خطا در ارسال تلگرام: {e}")

def search_news(query):
    """جستجو در Google News RSS"""
    query_encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={query_encoded}&hl=fa&gl=IR&ceid=IR:fa"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as response:
        data = response.read()
    root = ET.fromstring(data)
    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title")
        link = item.findtext("link")
        pub_date = item.findtext("pubDate")
        items.append({"title": title, "link": link, "date": pub_date})
    return items

def get_page_title(url):
    """دریافت عنوان یک صفحه"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            content = response.read()
        try:
            html = content.decode("utf-8")
        except UnicodeDecodeError:
            html = content.decode("cp1256", errors="ignore")
        match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    except Exception as e:
        print(f"❌ خطا در دریافت عنوان {url}: {e}")
    return None

def search_setadiran_sitemap():
    """جستجوی مستقیم در sitemap سایت ستاد ایران"""
    found_items = []
    urls_to_check = list(SITEMAP_URLS)
    seen_sitemaps = set()

    while urls_to_check and len(found_items) < 10:
        sitemap_url = urls_to_check.pop(0)
        if sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)

        try:
            print(f"📂 بررسی: {sitemap_url}")
            req = urllib.request.Request(sitemap_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as response:
                data = response.read()
            root = ET.fromstring(data)

            locs = [loc.text.strip() for loc in root.findall(".//{*}loc")]

            sitemap_locs = [u for u in locs if "sitemap" in u.lower() or u.endswith(".xml")]
            page_locs = [u for u in locs if u not in sitemap_locs]
            urls_to_check.extend(sitemap_locs)

            tender_urls = [u for u in page_locs if any(k in u.lower() for k in TENDER_URL_KEYWORDS)]
            print(f"   {len(locs)} لینک یافت شد، {len(tender_urls)} مورد مرتبط با مناقصه")

            for u in tender_urls[:5]:
                title = get_page_title(u)
                if title:
                    found_items.append({"title": title, "link": u, "date": ""})
                    time.sleep(1)
                    if len(found_items) >= 10:
                        break
        except Exception as e:
            print(f"   ❌ خطا: {e}")

    return found_items

def main():
    all_items = []

    queries = [
        "مناقصه فناوری اطلاعات",
        "مناقصه شبکه",
        "مناقصه نت ایران",
        "آگهی مناقصه IT",
    ]
    for q in queries:
        try:
            print(f"🔍 جستجو: {q}")
            items = search_news(q)
            all_items.extend(items)
        except Exception as e:
            print(f"   ❌ خطا: {e}")

    setad_queries = [
        "site:setadiran.ir مناقصه",
        "site:setadiran.ir مزایده",
        "site:setadiran.ir آگهی",
        "site:setadiran.ir فراخوان",
    ]
    for q in setad_queries:
        try:
            print(f"🔍 جستجوی ستاد: {q}")
            items = search_news(q)
            all_items.extend(items)
        except Exception as e:
            print(f"   ❌ خطا: {e}")

    sitemap_items = search_setadiran_sitemap()
    all_items.extend(sitemap_items)

    seen = set()
    unique_items = []
    for item in all_items:
        if item["title"] not in seen:
            seen.add(item["title"])
            unique_items.append(item)

    if unique_items:
        message = "📢 *مناقصات جدید حوزه نت*\n\n"
        for item in unique_items[:5]:
            message += f"🔹 {item['title']}\n"
            message += f"🔗 {item['link']}\n\n"
        send_telegram(message)
    else:
        send_telegram("🔍 امروز مورد جدیدی از مناقصات حوزه نت پیدا نشد.")

if __name__ == "__main__":
    main()

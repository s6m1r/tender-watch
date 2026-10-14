import os
import re
import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
import requests

try:
    from zoneinfo import ZoneInfo
    TEHRAN = ZoneInfo("Asia/Tehran")
except Exception:
    TEHRAN = timezone(timedelta(hours=3, minutes=30))

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

MIN_DAYS_LEFT = 7        # حداقل روز باقی‌مانده برای ارسال
MAX_PAGES = 25           # حداکثر صفحه‌ای که هر بار بررسی می‌شود
MAX_OPEN_IN_MSG = 8      # حداکثر مناقصه با مهلت باز در هر پیام
MAX_UNKNOWN_IN_MSG = 5   # حداکثر موارد بدون مهلت مشخص
SEEN_FILE = "seen.json"  # برای جلوگیری از ارسال تکراری

SITEMAP_URLS = [
    "https://setadiran.ir/sitemap.xml",
    "https://setadiran.ir/sitemap_index.xml",
    "https://setadiran.ir/fa/sitemap.xml",
    "https://www.iooc.co.ir/sitemap.xml",
    "https://iooc.co.ir/sitemap.xml",
    "https://www.nioc.ir/sitemap.xml",
    "https://nioc.ir/sitemap.xml",
    "https://www.nigc.ir/sitemap.xml",
    "https://nigc.ir/sitemap.xml",
    "https://www.niordc.ir/sitemap.xml",
    "https://niordc.ir/sitemap.xml",
    "https://www.nipc.ir/sitemap.xml",
    "https://nipc.ir/sitemap.xml",
    "https://www.nisoc.ir/sitemap.xml",
    "https://nisoc.ir/sitemap.xml",
    "https://www.pogc.ir/sitemap.xml",
    "https://pogc.ir/sitemap.xml",
    "https://www.ppars.ir/sitemap.xml",
    "https://ppars.ir/sitemap.xml",
]

TENDER_URL_KEYWORDS = [
    "tender", "monaghese", "mozayede", "agahi", "forookh",
    "مناقصه", "مزایده", "آگهی", "فراخوان", "استعلام",
]

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# ---------- تبدیل تاریخ شمسی/میلادی ----------

def jalali_to_gregorian(jy, jm, jd):
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd
    if jm < 7:
        days += (jm - 1) * 31
    else:
        days += ((jm - 7) * 30) + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)
    sal_a = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    for i in range(1, 13):
        if gd <= sal_a[i]:
            gm = i
            break
        gd -= sal_a[i]
    return gy, gm, gd

def gregorian_to_jalali(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) \
           + ((gy2 + 399) // 400) + gd + g_d_m[gm - 1]
    jy = -1595 + (33 * (days // 12053))
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + (days // 31)
        jd = 1 + (days % 31)
    else:
        jm = 7 + ((days - 186) // 30)
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd

def jalali_safe(y, m, d):
    if 1300 <= y <= 1500 and 1 <= m <= 12 and 1 <= d <= 31 and not (m > 6 and d > 30):
        try:
            gy, gm, gd = jalali_to_gregorian(y, m, d)
            return datetime(gy, gm, gd).date()
        except ValueError:
            return None
    return None

# ---------- دریافت و پردازش صفحه ----------

def fetch_bytes(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()

def fetch_text(url, timeout=20):
    data = fetch_bytes(url, timeout)
    for enc in ("utf-8", "cp1256"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="ignore")

def html_to_text(html):
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.translate(_DIGITS)
    return re.sub(r"\s+", " ", text)

def get_title(html, fallback=""):
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    if m:
        t = re.sub(r"\s+", " ", m.group(1)).strip()
        if t:
            return t
    return fallback

def extract_dates(text):
    out = []
    # شمسی: 1405/08/05
    for m in re.finditer(r"\b(1[3-5]\d{2})[/\.\-](\d{1,2})[/\.\-](\d{1,2})\b", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        g = jalali_safe(y, mo, d)
        if g:
            out.append((m.start(), g, f"{y}/{mo:02d}/{d:02d}"))
    # شمسی: 05/08/1405
    for m in re.finditer(r"\b(\d{1,2})[/](\d{1,2})[/](1[3-5]\d{2})\b", text):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        g = jalali_safe(y, mo, d)
        if g:
            out.append((m.start(), g, f"{y}/{mo:02d}/{d:02d}"))
    # میلادی: 2026-11-01
    for m in re.finditer(r"\b(20[2-9]\d)[/\-\.](\d{1,2})[/\-\.](\d{1,2})\b", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            out.append((m.start(), datetime(y, mo, d).date(), f"{y}/{mo:02d}/{d:02d}"))
        except ValueError:
            pass
    return out

def find_deadline(text, today):
    """مهلت احتمالی را پیدا می‌کند؛ اولویت با تاریخ‌های نزدیک کلمه «مهلت»"""
    dates = extract_dates(text)
    if not dates:
        return None
    hints = [m.start() for m in re.finditer("مهلت|ضرب|موعد", text)]

    def score(item):
        pos, d, _ = item
        near = any(abs(pos - h) <= 80 for h in hints)
        return (near, (d - today).days)

    future = [c for c in dates if (c[1] - today).days >= 0]
    pool = future if future else dates
    best = max(pool, key=score)
    return best[1], best[2]

# ---------- منابع ----------

def search_news(query):
    query_encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={query_encoded}&hl=fa&gl=IR&ceid=IR:fa"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as response:
        data = response.read()
    root = ET.fromstring(data)
    items = []
    for item in root.findall(".//item"):
        items.append({
            "title": item.findtext("title") or "",
            "link": item.findtext("link") or "",
        })
    return items

def collect_sitemap_urls(limit=15):
    urls = []
    seen_sm = set()
    queue = list(SITEMAP_URLS)
    while queue and len(urls) < limit:
        sm = queue.pop(0)
        if sm in seen_sm:
            continue
        seen_sm.add(sm)
        try:
            root = ET.fromstring(fetch_bytes(sm))
            locs = [l.text.strip() for l in root.findall(".//{*}loc") if l.text]
            sub = [u for u in locs if "sitemap" in u.lower() or u.endswith(".xml")]
            pages = [u for u in locs if u not in sub]
            queue.extend(sub)
            for u in pages:
                if any(k in u.lower() for k in TENDER_URL_KEYWORDS) and u not in urls:
                    urls.append(u)
                    if len(urls) >= limit:
                        break
        except Exception as e:
            print(f"   ❌ sitemap: {sm} → {e}")
    return urls

# ---------- حافظه (جلوگیری از تکرار) ----------

def load_seen():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_seen(seen):
    items = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:500]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(dict(items), f, ensure_ascii=False, indent=1)

# ---------- پیام تلگرام ----------

def send_telegram(message):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(api_url, data=data, timeout=15)
        print("✅ پیام ارسال شد.")
    except Exception as e:
        print(f"❌ خطا در ارسال تلگرام: {e}")

def build_message(today, open_list, unknown_list):
    jy, jm, jd = gregorian_to_jalali(today.year, today.month, today.day)
    lines = [f"📅 امروز: {jy}/{jm:02d}/{jd:02d}", ""]
    if open_list:
        lines.append(f"✅ مناقصات با مهلت باز ({MIN_DAYS_LEFT} روز یا بیشتر):")
        lines.append("")
        for i, t in enumerate(open_list[:MAX_OPEN_IN_MSG], 1):
            lines.append(f"{i}) {t['title']}")
            lines.append(f"🔗 {t['link']}")
            lines.append(f"⏳ مهلت: {t['deadline']} ({t['left']} روز مانده)")
            lines.append("")
    if unknown_list:
        lines.append("🟡 مهلتش پیدا نشد (خودت بررسی کن):")
        for t in unknown_list[:MAX_UNKNOWN_IN_MSG]:
            lines.append(f"• {t['title']}")
            lines.append(f"  {t['link']}")
        lines.append("")
    msg = "\n".join(lines).strip()
    if len(msg) > 3900:
        msg = msg[:3900] + "\n…"
    return msg

# ---------- اجرای اصلی ----------

def main():
    today = datetime.now(TEHRAN).date()
    seen = load_seen()

    candidates = []
    for u in collect_sitemap_urls():
        candidates.append({"title": "", "link": u})

    queries = [
        "مناقصه نفت",
        "مزایده نفت و گاز",
        "مناقصه پتروشیمی",
        "مناقصه گاز",
        "مناقصه فناوری اطلاعات",
    ]
    site_queries = [
        "site:setadiran.ir مناقصه",
        "site:setadiran.ir مزایده",
        "site:nioc.ir مناقصه",
        "site:nioc.ir مزایده",
        "site:nigc.ir مناقصه",
        "site:nigc.ir مزایده",
        "site:niordc.ir مناقصه",
        "site:nipc.ir مناقصه",
        "site:nisoc.ir مناقصه",
        "site:iooc.co.ir مناقصه",
        "site:pogc.ir مناقصه",
        "site:ppars.ir مناقصه",
    ]
    for q in queries + site_queries:
        try:
            candidates.extend(search_news(q))
        except Exception as e:
            print(f"   ❌ جستجو: {q} → {e}")

    # حذف تکراری‌ها بر اساس لینک
    uniq, seen_urls = [], set()
    for c in candidates:
        u = c.get("link")
        if u and u not in seen_urls:
            seen_urls.add(u)
            uniq.append(c)

    open_list, unknown_list, skipped, checked = [], [], 0, 0
    for it in uniq:
        if checked >= MAX_PAGES:
            break
        url = it.get("link") or ""
        if not url or url in seen:
            continue
        if url.lower().endswith(".pdf"):
            seen[url] = today.isoformat()
            continue
        try:
            html = fetch_text(url)
        except Exception as e:
            print(f"   ⚠️ دریافت نشد: {url} → {e}")
            continue
        checked += 1
        title = get_title(html) or it.get("title") or url
        dl = find_deadline(html_to_text(html), today)
        seen[url] = today.isoformat()
        if dl:
            d, label = dl
            left = (d - today).days
            if left >= MIN_DAYS_LEFT:
                open_list.append({"title": title, "link": url, "deadline": label, "left": left})
            else:
                skipped += 1
        else:
            unknown_list.append({"title": title, "link": url})
        time.sleep(0.7)

    save_seen(seen)

    if open_list or unknown_list:
        msg = build_message(today, open_list, unknown_list)
        if skipped:
            msg += f"\n\n🗑 {skipped} مورد با مهلت کمتر از {MIN_DAYS_LEFT} روز حذف شد."
        send_telegram(msg)
    else:
        base = "🔍 امروز مناقصه جدیدی پیدا نشد."
        if skipped:
            base += f"\n({skipped} مورد هم مهلتش کمتر از {MIN_DAYS_LEFT} روز بود و حذف شد.)"
        send_telegram(base)

    print(f"بررسی‌شده: {checked} | باز: {len(open_list)} | نامشخص: {len(unknown_list)} | حذف‌شده: {skipped}")

if __name__ == "__main__":
    main()

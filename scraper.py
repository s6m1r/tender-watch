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

MIN_DAYS_LEFT = 7
MAX_PAGES = 40
MAX_LINKS_PER_SITE = 6
MAX_SUB_LINKS = 4
MAX_OPEN_IN_MSG = 8
MAX_UNKNOWN_IN_MSG = 6
SEEN_FILE = "seen.json"

SITES = [
    "https://setadiran.ir",
    "https://www.nioc.ir",
    "https://www.nigc.ir",
    "https://www.niordc.ir",
    "https://www.nipc.ir",
    "https://www.nisoc.ir",
    "https://www.iooc.co.ir",
    "https://www.pogc.ir",
    "https://www.ppars.ir",
]

SITEMAP_URLS = []
for s in SITES:
    SITEMAP_URLS += [f"{s}/sitemap.xml", f"{s}/sitemap_index.xml", f"{s}/fa/sitemap.xml"]

# 🔍 جستجوهایی که مستقیم توی داک‌داک‌گو زده میشن (لینک مستقیم برمی‌گردونن)
DDG_QUERIES = [
    "site:setadiran.ir مناقصه",
    "site:setadiran.ir مزایده",
    "site:setadiran.ir آگهی",
    "site:nioc.ir مناقصه",
    "site:nigc.ir مناقصه",
    "site:niordc.ir مناقصه",
    "site:nisoc.ir مناقصه",
    "site:iooc.co.ir مناقصه",
    "site:pogc.ir مناقصه",
    "مناقصه نفت",
]

TENDER_URL_KEYWORDS = [
    "tender", "monaghese", "mozayede", "agahi", "forookh", "estelam",
    "مناقصه", "مزایده", "آگهی", "فراخوان", "استعلام", "خریداری",
]

MONTH_NAMES = {
    "فروردین": 1, "اردیبهشت": 2, "خرداد": 3, "تیر": 4,
    "مرداد": 5, "امرداد": 5, "شهریور": 6, "مهر": 7, "آبان": 8,
    "آذر": 9, "اذر": 9, "دی": 10, "بهمن": 11, "اسفند": 12,
}
MONTH_RE = "فروردین|اردیبهشت|خرداد|تیر|مرداد|امرداد|شهریور|مهر|آبان|آذر|اذر|دی|بهمن|اسفند"

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# ---------- تبدیل تاریخ ----------

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

# ---------- ابزارها ----------

def normalize_text(s):
    s = s.translate(_DIGITS)
    s = s.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    return re.sub(r"\s+", " ", s)

def fetch_bytes(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()

def fetch_text(url, timeout=25):
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
    return normalize_text(text)

def get_title(html, fallback=""):
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    if m:
        t = normalize_text(m.group(1)).strip()
        if t:
            return t
    return fallback

def keyword_hit(s):
    s = normalize_text(s).lower()
    return any(k in s for k in TENDER_URL_KEYWORDS)

# ---------- جستجوی DuckDuckGo ----------

def duck_search(query, limit=10):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
    }
    for endpoint in ("https://html.duckduckgo.com/html/",
                     "https://lite.duckduckgo.com/lite/"):
        try:
            url = endpoint + "?" + urllib.parse.urlencode({"q": query})
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=25) as resp:
                html = resp.read().decode("utf-8", "ignore")
        except Exception as e:
            print(f"   ❌ DDG ({endpoint}): {e}")
            continue

        results = []
        for m in re.finditer(r'<a\s+([^>]+)>(.*?)</a>', html, re.S | re.I):
            attrs, inner = m.group(1), m.group(2)
            if "result__a" not in attrs and "result-link" not in attrs:
                continue
            hm = re.search(r'href="([^"]+)"', attrs)
            if not hm:
                continue
            href = hm.group(1)
            if "uddg=" in href:
                qs = urllib.parse.urlparse(href).query
                href = urllib.parse.parse_qs(qs).get("uddg", [href])[0]
            if not href.startswith("http"):
                continue
            title = normalize_text(re.sub(r"<[^>]+>", " ", inner)).strip()
            results.append({"title": title, "link": href})
            if len(results) >= limit:
                break

        uniq, seen = [], set()
        for r in results:
            if r["link"] not in seen:
                seen.add(r["link"])
                uniq.append(r)
        if uniq:
            return uniq
    return []

# ---------- استخراج تاریخ ----------

def extract_dates(text, today):
    out = []
    jy_now = gregorian_to_jalali(today.year, today.month, today.day)[0]

    for m in re.finditer(r"\b(1[3-5]\d{2})\s*[/\.\-]\s*(\d{1,2})\s*[/\.\-]\s*(\d{1,2})\b", text):
        g = jalali_safe(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if g:
            out.append((m.start(), g, f"{m.group(1)}/{int(m.group(2)):02d}/{int(m.group(3)):02d}"))

    for m in re.finditer(r"\b(\d{1,2})\s*[/]\s*(\d{1,2})\s*[/]\s*(1[3-5]\d{2})\b", text):
        g = jalali_safe(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if g:
            out.append((m.start(), g, f"{m.group(3)}/{int(m.group(2)):02d}/{int(m.group(1)):02d}"))

    for m in re.finditer(r"\b(20[2-9]\d)\s*[/\.\-]\s*(\d{1,2})\s*[/\.\-]\s*(\d{1,2})\b", text):
        try:
            out.append((m.start(), datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date(),
                        f"{m.group(1)}/{int(m.group(2)):02d}/{int(m.group(3)):02d}"))
        except ValueError:
            pass

    pat = rf"\b(\d{{1,2}})\s*(?:روز)?\s*(?:ماه)?\s*({MONTH_RE})(?:ماه)?\s*(1[3-5]\d{{2}})?\b"
    for m in re.finditer(pat, text):
        d = int(m.group(1))
        mo = MONTH_NAMES[m.group(2)]
        y = int(m.group(3)) if m.group(3) else jy_now
        g = jalali_safe(y, mo, d)
        if g:
            out.append((m.start(), g, f"{y}/{mo:02d}/{d:02d}"))

    for m in re.finditer(r"(?:مهلت|موعد|ضرب)\D{0,30}?(\d{1,3})\s*روز", text):
        n = int(m.group(1))
        if 1 <= n <= 180:
            out.append((m.start(), today + timedelta(days=n), f"{n} روز بعد"))

    return out

def find_deadline(text, today):
    dates = extract_dates(text, today)
    if not dates:
        return None, 0
    hints = [m.start() for m in re.finditer("مهلت|ضرب|موعد|تحویل", text)]

    def score(item):
        pos, d, _ = item
        near = any(abs(pos - h) <= 80 for h in hints)
        return (near, (d - today).days)

    future = [c for c in dates if (c[1] - today).days >= 0]
    pool = future if future else dates
    best = max(pool, key=score)
    return (best[1], best[2]), len(dates)

# ---------- منابع ----------

def collect_sitemap_urls(limit=15):
    urls, seen_sm = [], set()
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
                if keyword_hit(u) and u not in urls:
                    urls.append(u)
                    if len(urls) >= limit:
                        break
        except Exception:
            pass
    return urls

A_TAG = re.compile(r'<a\b[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)

def extract_anchor_links(html, base):
    res = []
    for m in A_TAG.finditer(html):
        href = m.group(1).strip()
        if href.lower().startswith(("javascript", "#", "mailto", "tel")):
            continue
        full = urllib.parse.urljoin(base, href)
        if not full.startswith("http"):
            continue
        label = normalize_text(re.sub(r"<[^>]+>", " ", m.group(2))).strip()
        res.append((full, label))
    return res

def collect_from_sites():
    found = []
    for site in SITES:
        try:
            html = fetch_text(site + "/")
        except Exception as e:
            print(f"   🌐 {site} → در دسترس نیست ({e})")
            continue
        links = extract_anchor_links(html, site)
        hits = [(u, t) for u, t in links if keyword_hit(u) or keyword_hit(t)]
        print(f"   🌐 {site}: {len(hits)} لینک مرتبط در صفحه اول")
        for u, t in hits[:MAX_LINKS_PER_SITE]:
            found.append({"title": t, "link": u})
    return found

# ---------- تحلیل ----------

def analyze(candidates, today, seen):
    open_list, unknown_list, skipped, checked = [], [], 0, 0
    for it in candidates:
        if checked >= MAX_PAGES:
            break
        url = it.get("link") or ""
        if not url or url in seen:
            continue
        if ".pdf" in url.lower():
            seen[url] = today.isoformat()
            continue
        try:
            html = fetch_text(url)
        except Exception as e:
            print(f"   ⚠️ {url} → {e}")
            continue
        text = html_to_text(html)
        if len(text) < 400:
            seen[url] = today.isoformat()
            continue
        checked += 1
        title = get_title(html, fallback=it.get("title") or url)
        (d, label), ndates = find_deadline(text, today)
        print(f"   📄 {checked}. {title[:60]} | تاریخ‌ها: {ndates} | مهلت: {label} ({(d-today).days if d else '-'} روز)")

        if d is not None and (d - today).days >= MIN_DAYS_LEFT:
            open_list.append({"title": title, "link": url, "deadline": label, "left": (d - today).days})
            seen[url] = today.isoformat()
            time.sleep(0.7)
            continue

        if d is not None:
            skipped += 1
            seen[url] = today.isoformat()
            time.sleep(0.7)
            continue

        # صفحه فهرست؟ یک لایه عمیق‌تر
        sub = [(u, t) for u, t in extract_anchor_links(html, url)
               if (keyword_hit(u) or keyword_hit(t)) and u not in seen][:MAX_SUB_LINKS]
        seen[url] = today.isoformat()
        if sub:
            for su, st in sub:
                if checked >= MAX_PAGES:
                    break
                if ".pdf" in su.lower():
                    continue
                try:
                    shtml = fetch_text(su)
                except Exception:
                    continue
                stext = html_to_text(shtml)
                if len(stext) < 400:
                    seen[su] = today.isoformat()
                    continue
                checked += 1
                stitle = get_title(shtml, fallback=st or su)
                (sd, slabel), _ = find_deadline(stext, today)
                print(f"      ↳ {checked}. {stitle[:55]} | مهلت: {slabel if sd else '-'}")
                seen[su] = today.isoformat()
                if sd:
                    left = (sd - today).days
                    if left >= MIN_DAYS_LEFT:
                        open_list.append({"title": stitle, "link": su, "deadline": slabel, "left": left})
                    else:
                        skipped += 1
                else:
                    unknown_list.append({"title": stitle, "link": su})
                time.sleep(0.5)
        else:
            unknown_list.append({"title": title, "link": url})
        time.sleep(0.7)
    return open_list, unknown_list, skipped, checked

# ---------- پیام ----------

def send_telegram(message):
    api_url = f"https://api.telegram.org/bot{TELEGRA

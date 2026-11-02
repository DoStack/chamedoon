# SPIKE P0: writes public/feeds/sample.v1.json, made-up listings with public fields only, sized to about 300 KB gzip.
import gzip
import hashlib
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

TARGET_GZIP = 300 * 1024
OUT = Path(__file__).resolve().parent.parent / "public" / "feeds" / "sample.v1.json"

AREAS = "نورث یورک،داون‌تاون،اسکاربرو،اتوبیکوک،میسیساگا،ریچموند هیل،تورنهیل،مارکهام،یورک میلز،ون،اوکویل،برمپتون،ایست یورک،لسلی ویل،یانگ و اگلینتون،یانگ و فینچ،بیوکرست،دان میلز،هایلند کریک،لیبرتی ویلیج".split("،")
OPEN = ["یک واحد تمیز و نوساز", "اتاق دنج و آفتاب‌گیر", "آپارتمان روشن با نمای شهر", "زیرزمین مستقل و گرم", "استودیوی مبله و جمع‌وجور", "سوئیت کوچک و آرام", "واحد بازسازی‌شده"]
NEAR = ["نزدیک مترو", "دو دقیقه تا ایستگاه اتوبوس", "کنار مرکز خرید", "نزدیک دانشگاه", "در محله‌ای امن", "نزدیک پارک", "کنار بزرگراه"]
TERMS = ["آب و برق و اینترنت با صاحبخانه است", "قبض‌ها جداست", "پارکینگ دارد", "لباسشویی مشترک دارد", "حیوان خانگی مجاز نیست", "ودیعه یک ماه است", "قرارداد سالانه است", "از اول ماه آینده خالی می‌شود", "مناسب دانشجو است", "مناسب زوج جوان است"]


def listing(rng: random.Random, i: int) -> dict:
    seen = datetime(2026, 11, 2) - timedelta(minutes=rng.randint(0, 60 * 24 * 30))
    has_price = rng.random() > 0.08
    return {
        "id": 100000 + i,
        "kind": rng.choices(["offer", "wanted"], [9, 1])[0],
        "property_type": rng.choices(["room", "studio", "apartment", "house", "basement", "other"], [35, 12, 25, 5, 20, 3])[0],
        "rental_mode": rng.choices(["long_term", "sublet", "lease_transfer", "short_term", "unknown"], [50, 15, 5, 10, 20])[0],
        "country": "CA",
        "region": "ON",
        "city": "Toronto",
        "area": rng.choice(AREAS),
        "price_amount": rng.randrange(550, 3400, 25) if has_price else None,
        "price_currency": "CAD",
        "price_period": "month",
        "rooms": rng.choice([None, 0, 1, 1, 1, 2, 2, 3, 4]),
        "size_m2": rng.choice([None, None, rng.randint(14, 120)]),
        "furnished": rng.choice(["yes", "partial", "no", "unknown"]),
        "bills_included": rng.choice(["yes", "no", "unknown"]),
        "roommate_gender": rng.choices(["any", "female", "male", "couple"], [6, 2, 1, 1])[0],
        "lister_type": rng.choice(["owner", "agent", "unknown"]),
        "available_from": (date(2026, 11, 2) + timedelta(days=rng.randint(0, 90))).isoformat() if rng.random() > 0.3 else None,
        "first_seen": seen.isoformat(timespec="minutes"),
        "last_seen": (seen + timedelta(hours=rng.randint(0, 72))).isoformat(timespec="minutes"),
        "summary_fa": f"{rng.choice(OPEN)} در {rng.choice(AREAS)}، {rng.choice(NEAR)}؛ {'، '.join(rng.sample(TERMS, 3))}.",
        "thumb": f"https://media.example.invalid/h/{hashlib.sha1(str(i).encode()).hexdigest()[:24]}-t.webp",
        "photo_count": rng.randint(0, 8),
    }


def render(n: int) -> bytes:
    rng = random.Random(20261102)
    doc = {"version": 1, "city": "toronto", "generated_at": "2026-11-02T00:00:00Z", "sample": True, "listings": [listing(rng, i) for i in range(n)]}
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


lo, hi = 100, 20000
while hi - lo > 5:
    mid = (lo + hi) // 2
    if len(gzip.compress(render(mid), 6)) < TARGET_GZIP:
        lo = mid
    else:
        hi = mid
body = render(lo)
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_bytes(body)
print(f"listings={lo} raw={len(body)} gzip={len(gzip.compress(body, 6))}")

"""Deterministic fake sales data for the Kopi Lereng coffee chain.

Same seed, same rows, every time. That keeps screenshots, tests and example answers stable.
"""

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

SEED = 2026
FIRST_DAY = date(2025, 1, 1)
LAST_DAY = date(2026, 9, 30)
PRICE_RISE_ON = date(2026, 1, 1)
WIB = timezone(timedelta(hours=7), "WIB")

# (name, city, region, opened_on, traffic weight)
STORES = [
    ("Kopi Lereng Kemang", "Jakarta", "Jawa", date(2023, 3, 1), 1.6),
    ("Kopi Lereng Sudirman", "Jakarta", "Jawa", date(2023, 9, 12), 1.5),
    ("Kopi Lereng Kelapa Gading", "Jakarta", "Jawa", date(2024, 5, 20), 1.2),
    ("Kopi Lereng Braga", "Bandung", "Jawa", date(2022, 11, 5), 1.3),
    ("Kopi Lereng Dago", "Bandung", "Jawa", date(2024, 2, 14), 1.0),
    ("Kopi Lereng Tunjungan", "Surabaya", "Jawa", date(2023, 6, 1), 1.2),
    ("Kopi Lereng Darmo", "Surabaya", "Jawa", date(2025, 4, 18), 0.9),
    ("Kopi Lereng Prawirotaman", "Yogyakarta", "Jawa", date(2023, 1, 20), 1.1),
    ("Kopi Lereng Simpang Lima", "Semarang", "Jawa", date(2024, 8, 3), 0.9),
    ("Kopi Lereng Salatiga", "Salatiga", "Jawa", date(2025, 6, 14), 0.7),
    ("Kopi Lereng Ijen", "Malang", "Jawa", date(2024, 10, 1), 0.8),
    ("Kopi Lereng Renon", "Denpasar", "Bali", date(2024, 4, 9), 1.0),
    ("Kopi Lereng Merdeka Walk", "Medan", "Sumatra", date(2024, 12, 1), 0.9),
    ("Kopi Lereng Losari", "Makassar", "Sulawesi", date(2026, 2, 1), 0.8),
]

# (name, category, current price, popularity, available from)
PRODUCTS = [
    ("Es Kopi Susu Gula Aren", "coffee", 28_000, 10.0, None),
    ("Americano", "coffee", 25_000, 5.0, None),
    ("Cappuccino", "coffee", 32_000, 5.5, None),
    ("Caffe Latte", "coffee", 32_000, 6.0, None),
    ("Flat White", "coffee", 34_000, 2.5, None),
    ("Kopi Tubruk", "coffee", 18_000, 3.0, None),
    ("V60 Gayo", "coffee", 38_000, 2.0, None),
    ("V60 Toraja", "coffee", 40_000, 1.5, None),
    ("Mocha", "coffee", 35_000, 2.5, None),
    ("Affogato", "coffee", 36_000, 1.5, None),
    ("Es Kopi Pandan", "coffee", 30_000, 4.0, date(2025, 8, 1)),
    ("Matcha Latte", "non_coffee", 34_000, 3.5, None),
    ("Cokelat Panas", "non_coffee", 30_000, 2.0, None),
    ("Teh Tarik", "non_coffee", 22_000, 2.5, None),
    ("Es Teh Lemon", "non_coffee", 20_000, 3.0, None),
    ("Wedang Jahe", "non_coffee", 18_000, 1.2, None),
    ("Croissant", "food", 26_000, 3.0, None),
    ("Pisang Goreng Keju", "food", 24_000, 3.5, None),
    ("Roti Bakar Srikaya", "food", 22_000, 2.5, None),
    ("Banana Bread", "food", 28_000, 2.0, None),
    ("Nasi Goreng Kampung", "food", 42_000, 2.2, date(2025, 3, 1)),
    ("Biji Kopi Gayo 250g", "beans", 95_000, 0.6, None),
    ("Biji Kopi Toraja 250g", "beans", 105_000, 0.5, None),
    ("Biji Kopi Kintamani 250g", "beans", 98_000, 0.5, None),
]

FIRST_NAMES = """
    Andi Ayu Bayu Budi Citra Dewi Dimas Eka Fajar Fitri Galih Gita Hendra Indah Intan
    Joko Kartika Lestari Made Nadia Nurul Oki Putri Rama Rizky Sari Siti Teguh Tri
    Utami Wahyu Wayan Yoga Yuni Agus Bella Clara Dian Raka
""".split()
LAST_NAMES = """
    Saputra Wijaya Pratama Santoso Hidayat Kusuma Siregar Nasution Lubis Wibowo Gunawan
    Setiawan Halim Rahmawati Nugroho Purnomo Sihombing Tanjung Ginting Hakim Permana
    Suryadi Utomo Daeng
""".split()

CUSTOMER_COUNT = 700
MEMBER_SHARE = 0.62
REFUND_RATE = 0.02
# Hour of day (WIB) and how busy it is.
HOURS = {7: 4, 8: 9, 9: 7, 10: 5, 11: 6, 12: 9, 13: 8, 14: 5, 15: 6, 16: 8, 17: 7,
         18: 5, 19: 6, 20: 5, 21: 3}  # fmt: skip


@dataclass
class SeedData:
    stores: list[tuple] = field(default_factory=list)
    products: list[tuple] = field(default_factory=list)
    customers: list[tuple] = field(default_factory=list)
    orders: list[tuple] = field(default_factory=list)
    order_items: list[tuple] = field(default_factory=list)


def generate(seed: int = SEED) -> SeedData:
    rng = random.Random(seed)
    data = SeedData()

    for store_id, (name, city, region, opened_on, _) in enumerate(STORES, start=1):
        data.stores.append((store_id, name, city, region, opened_on))
    for product_id, (name, category, price, _, _) in enumerate(PRODUCTS, start=1):
        data.products.append((product_id, name, category, price))

    data.customers = _customers(rng)
    members_by_city: dict[str, list[tuple]] = {}
    for customer in data.customers:
        members_by_city.setdefault(customer[2], []).append(customer)

    item_id = 0
    for day in _days(FIRST_DAY, LAST_DAY):
        todays_orders = [
            (_order_time(rng, day), store_id, city)
            for store_id, (_, city, _, opened_on, weight) in enumerate(STORES, start=1)
            if day >= opened_on
            for _ in range(_orders_today(rng, day, weight))
        ]
        # Receipt numbers follow the clock, like they would in a real POS export.
        todays_orders.sort()
        for ordered_at, store_id, city in todays_orders:
            order_id = len(data.orders) + 1
            data.orders.append(
                (
                    order_id,
                    store_id,
                    _pick_member(rng, day, city, members_by_city, data.customers),
                    ordered_at,
                    _channel(rng, day),
                    _payment(rng, day),
                    "refunded" if rng.random() < REFUND_RATE else "completed",
                )
            )
            for product_id, quantity, unit_price in _basket(rng, day):
                item_id += 1
                data.order_items.append((item_id, order_id, product_id, quantity, unit_price))

    return data


def _customers(rng: random.Random) -> list[tuple]:
    cities = [s[1] for s in STORES]
    weights = [s[4] for s in STORES]
    joined_start = date(2024, 6, 1)
    joined_span = (date(2026, 8, 31) - joined_start).days

    customers = []
    for customer_id in range(1, CUSTOMER_COUNT + 1):
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        city = rng.choices(cities, weights)[0]
        # More people join early on, when the loyalty program launched.
        joined_on = joined_start + timedelta(days=int(joined_span * rng.random() ** 1.6))
        tier = rng.choices(["bronze", "silver", "gold"], [65, 25, 10])[0]
        customers.append((customer_id, name, city, joined_on, tier))
    return customers


def _days(first: date, last: date):
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


def _progress(day: date) -> float:
    """0.0 on the first day of data, 1.0 on the last."""
    return (day - FIRST_DAY).days / (LAST_DAY - FIRST_DAY).days


def _orders_today(rng: random.Random, day: date, weight: float) -> int:
    expected = 0.36 * weight * (0.8 + 0.5 * _progress(day))
    if day.weekday() >= 5:
        expected *= 1.35
    if day.month == 12:
        expected *= 1.2
    whole = int(expected)
    return whole + (1 if rng.random() < expected - whole else 0)


def _order_time(rng: random.Random, day: date) -> datetime:
    hour = rng.choices(list(HOURS), list(HOURS.values()))[0]
    return datetime.combine(day, time(hour, rng.randrange(60), rng.randrange(60)), tzinfo=WIB)


def _pick_member(
    rng: random.Random,
    day: date,
    city: str,
    members_by_city: dict[str, list[tuple]],
    everyone: list[tuple],
) -> int | None:
    if rng.random() >= MEMBER_SHARE:
        return None
    pool = members_by_city.get(city) if rng.random() < 0.85 else None
    pool = pool or everyone
    for _ in range(3):
        customer = rng.choice(pool)
        if customer[3] <= day:
            return customer[0]
    return None


def _channel(rng: random.Random, day: date) -> str:
    delivery = 20 + 12 * _progress(day)
    return rng.choices(["dine_in", "takeaway", "delivery"], [46, 34, delivery])[0]


def _payment(rng: random.Random, day: date) -> str:
    p = _progress(day)
    return rng.choices(
        ["qris", "cash", "debit_card", "ewallet"],
        [36 + 14 * p, 24 - 12 * p, 15, 25],
    )[0]


def _basket(rng: random.Random, day: date) -> list[tuple[int, int, int]]:
    available = [
        (product_id, spec)
        for product_id, spec in enumerate(PRODUCTS, start=1)
        if spec[4] is None or spec[4] <= day
    ]
    lines = rng.choices([1, 2, 3, 4], [45, 35, 15, 5])[0]
    chosen: list[tuple[int, tuple]] = []
    while len(chosen) < lines:
        pick = rng.choices(available, [spec[3] for _, spec in available])[0]
        if pick not in chosen:
            chosen.append(pick)

    basket = []
    for product_id, (_, category, price, _, _) in chosen:
        quantity = 1 if category == "beans" else rng.choices([1, 2, 3], [80, 17, 3])[0]
        # Drinks went up Rp 2.000 at the start of 2026; older receipts show the old price.
        if day < PRICE_RISE_ON and category in ("coffee", "non_coffee"):
            price -= 2_000
        basket.append((product_id, quantity, price))
    return basket

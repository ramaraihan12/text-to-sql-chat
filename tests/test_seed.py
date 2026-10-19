from collections import Counter

import pytest

from sqlchat.seed import FIRST_DAY, LAST_DAY, PRICE_RISE_ON, PRODUCTS, STORES, SeedData, generate


@pytest.fixture(scope="module")
def data() -> SeedData:
    return generate()


def test_same_seed_gives_same_rows(data: SeedData) -> None:
    again = generate()
    assert again.orders == data.orders
    assert again.order_items == data.order_items
    assert again.customers == data.customers


def test_different_seed_gives_different_rows(data: SeedData) -> None:
    assert generate(seed=7).orders != data.orders


def test_size_is_a_few_thousand_rows(data: SeedData) -> None:
    assert len(data.stores) == len(STORES)
    assert len(data.products) == len(PRODUCTS)
    assert 2_000 < len(data.orders) < 6_000
    assert len(data.order_items) > len(data.orders)


def test_foreign_keys_point_at_existing_rows(data: SeedData) -> None:
    store_ids = {s[0] for s in data.stores}
    customer_ids = {c[0] for c in data.customers}
    product_ids = {p[0] for p in data.products}
    order_ids = {o[0] for o in data.orders}

    assert all(o[1] in store_ids for o in data.orders)
    assert all(o[2] is None or o[2] in customer_ids for o in data.orders)
    assert all(i[1] in order_ids for i in data.order_items)
    assert all(i[2] in product_ids for i in data.order_items)


def test_orders_respect_opening_and_join_dates(data: SeedData) -> None:
    opened = {s[0]: s[4] for s in data.stores}
    joined = {c[0]: c[3] for c in data.customers}
    for _, store_id, customer_id, ordered_at, *_ in data.orders:
        day = ordered_at.date()
        assert FIRST_DAY <= day <= LAST_DAY
        assert day >= opened[store_id]
        if customer_id is not None:
            assert day >= joined[customer_id]


def test_order_ids_follow_time(data: SeedData) -> None:
    times = [o[3] for o in data.orders]
    assert times == sorted(times)


def test_drinks_were_cheaper_before_the_price_rise(data: SeedData) -> None:
    order_day = {o[0]: o[3].date() for o in data.orders}
    product = {p[0]: p for p in data.products}
    for _, order_id, product_id, _, unit_price in data.order_items:
        _, _, category, current_price = product[product_id]
        if order_day[order_id] < PRICE_RISE_ON and category in ("coffee", "non_coffee"):
            assert unit_price == current_price - 2_000
        else:
            assert unit_price == current_price


def test_mix_looks_like_a_coffee_shop(data: SeedData) -> None:
    sold = Counter()
    for _, _, product_id, quantity, _ in data.order_items:
        sold[product_id] += quantity
    best_seller = data.products[sold.most_common(1)[0][0] - 1][1]
    assert best_seller == "Es Kopi Susu Gula Aren"

    statuses = Counter(o[6] for o in data.orders)
    assert statuses["refunded"] < statuses["completed"] * 0.05

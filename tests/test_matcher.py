import types
import pytest
import bot as botmod

def sub(**kwargs):
    # Лёгкий объект-подписка с нужными полями
    return types.SimpleNamespace(**kwargs)

def test_match_price_rooms_area_district_any():
    ad = {
        "price": "60000",
        "rooms": "1",
        "count_meters_flat": "35",
        "count_meters_metro": 500,  # близко
        "location": "СВАО",
    }
    s = sub(
        min_price=35000, max_price=65000,
        min_rooms=1, max_rooms=1,
        min_flat=30, max_flat=50,
        district="ANY",
        metro_close=False,   # метро «не важно»
    )
    assert botmod.is_ad_match_subscription(ad, s) is True

def test_match_district_mismatch():
    ad = {"price": 50000, "rooms": 2, "count_meters_flat": 40, "location": "ЮАО"}
    s = sub(district="СВАО", metro_close=False)
    assert botmod.is_ad_match_subscription(ad, s) is False  # округа не совпали

def test_metro_close_true_respects_800m_threshold(monkeypatch):
    # В функции жёстко зашито 800.0 как лимит близости метро
    ad_far = {"count_meters_metro": 1200.1}
    ad_near = {"count_meters_metro": 799.9}
    s = sub(metro_close=True)
    assert botmod.is_ad_match_subscription(ad_far, s) is False   # > 800 -> нет
    assert botmod.is_ad_match_subscription(ad_near, s) is True    # <= 800 -> ок

def test_metro_close_false_ignores_distance():
    ad_far = {"count_meters_metro": 5000}
    s = sub(metro_close=False)
    assert botmod.is_ad_match_subscription(ad_far, s) is True  # метро «не важно»

def test_studio_room_zero_treated_as_one():
    ad = {"rooms": 0}
    s = sub(min_rooms=1, max_rooms=1)
    assert botmod.is_ad_match_subscription(ad, s) is True
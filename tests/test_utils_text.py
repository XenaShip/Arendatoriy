# tests/test_utils_text.py
import math
from pathlib import Path
import pytest

import bot as botmod
from bot import is_yes, is_no, coerce_to_bool


# ---------- safe_parse_number ----------

def test_safe_parse_number_basic():
    f = botmod.safe_parse_number
    assert f(123) == 123.0
    assert f("60 000") == 60000.0
    assert f("60\u00A0000") == 60000.0  # NBSP
    assert f("34,6") == 34.6
    assert f("abc") is None
    assert f(None) is None

@pytest.mark.parametrize("s,expected", [
    ("  70 000  ", 70000.0),
    ("12 345,67", 12345.67),
    ("34.6", 34.6),
    ("-12", -12.0),
])
def test_safe_parse_number_more_cases(s, expected):
    f = botmod.safe_parse_number
    out = f(s)
    # сравниваем численно (для дробей) и строго (для целых)
    if isinstance(expected, float) and not expected.is_integer():
        assert math.isclose(out, expected, rel_tol=1e-9)
    else:
        assert out == expected


# ---------- build_post_text ----------

def test_build_post_text_adds_contacts_and_quote_once():
    base = "  Привет \n\n мир  "
    contacts = "https://t.me/ivan"
    out = botmod.build_post_text(base, contacts, add_quote=True)

    # абзацы нормализованы (в тексте есть «двойные» переносы)
    assert "\n\n" in out

    # контакты добавлены ровно один раз
    assert out.count(f"Контакты: {contacts}") == 1

    # цитата присутствует
    assert "arendatoriy_find_bot" in out

    # повторный вызов с теми же данными не должен дублировать контакты
    out2 = botmod.build_post_text(out, contacts, add_quote=True)
    assert out2.count(f"Контакты: {contacts}") == 1


def test_build_post_text_without_contacts_still_adds_quote():
    base = "Описание квартиры"
    out = botmod.build_post_text(base, contacts=None, add_quote=True)
    assert "Контакты:" not in out
    assert "arendatoriy_find_bot" in out


# ---------- булевы ответы: is_yes / is_no / coerce_to_bool ----------

@pytest.mark.parametrize("s", ["да", "Да", "YES", "Yes", " true ", "OK", "ага", " y "])
def test_is_yes_true_cases(s):
    assert is_yes(s) is True
    assert is_no(s) is False  # симметрия

@pytest.mark.parametrize("s", ["нет", "Нет", "NO", " false ", "неа", " n "])
def test_is_no_true_cases(s):
    assert is_no(s) is True
    assert is_yes(s) is False  # симметрия

@pytest.mark.parametrize("s,exp", [
    (None, None),
    ("", None),
    ("не знаю", None),
    ("maybe", None),
])
def test_coerce_to_bool_tri_state(s, exp):
    assert coerce_to_bool(s, default=None) is exp

def test_coerce_to_bool_numbers():
    assert coerce_to_bool(1) is True
    assert coerce_to_bool(0) is False
    # любое другое число интерпретируем как default (None по умолчанию)
    assert coerce_to_bool(2) is None
    assert coerce_to_bool(-1) is None


# ---------- файлы: _is_non_empty_file ----------

def test__is_non_empty_file(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("x")
    assert botmod._is_non_empty_file(str(p)) is True
    assert botmod._is_non_empty_file(str(tmp_path / "missing.txt")) is False

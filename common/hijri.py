"""Hijri → Gregorian date for visa dates written like '14-05-1448' (FR-5.1).

Uses the standard arithmetic (tabular Islamic) calendar. The official Saudi Umm al-Qura calendar is based
on moon sighting tables and can differ by ±1–2 days, so the form shows this as '≈' and staff can correct
the Gregorian date. Self-check: python -m common.hijri
"""
import re
from datetime import date

_EPOCH = 1948440  # Julian day of 1 Muharram 1 AH (civil, Friday 16 July 622)


def parse_hijri(text):
    """'14-05-1448', '14/5/1448', '1448-05-14' -> (1448, 5, 14) or None."""
    nums = [int(n) for n in re.findall(r"\d+", text or "")]
    if len(nums) != 3:
        return None
    if nums[0] > 1000:                 # year first
        y, m, d = nums
    else:
        d, m, y = nums
    if not (1 <= m <= 12 and 1 <= d <= 30 and 1300 <= y <= 1600):
        return None
    return y, m, d


def hijri_to_gregorian(y, m, d):
    jd = d + (29 * (m - 1) + (m) // 2) + 354 * (y - 1) + (3 + 11 * y) // 30 + _EPOCH - 1
    # Julian day number -> Gregorian (Fliegel & Van Flandern)
    a = jd + 32044
    b = (4 * a + 3) // 146097
    c = a - 146097 * b // 4
    dd = (4 * c + 3) // 1461
    e = c - 1461 * dd // 4
    mm = (5 * e + 2) // 153
    day = e - (153 * mm + 2) // 5 + 1
    month = mm + 3 - 12 * (mm // 10)
    year = 100 * b + dd - 4800 + mm // 10
    return date(year, month, day)


def to_gregorian(text):
    """Visa date as typed -> Gregorian date, or None if it cannot be read."""
    parts = parse_hijri(text)
    return hijri_to_gregorian(*parts) if parts else None


if __name__ == "__main__":
    # Known dates (Umm al-Qura); tabular result must be within 2 days
    checks = {"01-01-1446": date(2024, 7, 7), "01-09-1445": date(2024, 3, 11), "01-10-1446": date(2025, 3, 30),
              "01-01-1447": date(2025, 6, 26), "03-04-1447": date(2025, 9, 25)}
    for text, real in checks.items():
        got = to_gregorian(text)
        assert abs((got - real).days) <= 2, (text, got, real)
        print(f"{text} -> {got}  (Umm al-Qura {real})")
    assert parse_hijri("1448/5/14") == (1448, 5, 14) and to_gregorian("junk") is None
    print("ok")

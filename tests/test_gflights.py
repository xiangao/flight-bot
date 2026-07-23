import base64
from code.gflights import gflights_url, SEAT


def _decode_body(url: str) -> bytes:
    tfs = url.split("tfs=")[1].split("&")[0]
    # gflights_url quotes the base64 string; undo that for decoding
    from urllib.parse import unquote
    return base64.b64decode(unquote(tfs))


def test_seat_enum_values():
    assert SEAT["economy"] == 1
    assert SEAT["business"] == 3


def test_two_leg_url_contains_both_airport_pairs():
    url = gflights_url([("BOS", "PEK", "2026-10-06"), ("PEK", "BOS", "2026-10-27")])
    assert url.startswith("https://www.google.com/travel/flights?tfs=")
    body = _decode_body(url)
    for code in (b"BOS", b"PEK", b"2026-10-06", b"2026-10-27"):
        assert code in body


def test_three_leg_url_contains_all_three_airport_pairs():
    url = gflights_url([
        ("BOS", "NRT", "2026-10-13"),
        ("KIX", "SHA", "2026-10-21"),
        ("HKG", "BOS", "2026-11-06"),
    ])
    body = _decode_body(url)
    for code in (b"BOS", b"NRT", b"KIX", b"SHA", b"HKG"):
        assert code in body


def test_adults_and_seat_affect_url_length():
    one_adult = gflights_url([("BOS", "PEK", "2026-10-06")], seat=1, adults=1)
    two_adults = gflights_url([("BOS", "PEK", "2026-10-06")], seat=1, adults=2)
    assert len(two_adults) > len(one_adult)

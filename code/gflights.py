"""Google Flights multi-city deep-link builder, shared by the searcher (which
opens these URLs to read live prices) and the dashboard (which links out to
the same search a viewer can re-run).

Wire format of Google's flight-search protobuf (field numbers reverse-engineered,
same schema the `fast-flights` library uses):
  Airport   { string code = 2 }
  FlightData{ string date = 2; repeated Airport from = 13; repeated Airport to = 14 }
  TFSData   { repeated FlightData legs = 3; repeated Passenger pax = 8;
              Seat seat = 9; TripType trip = 19 }   (trip: multi-city = 3)

Vendored from jal-bot's code/gflights.py (2026-07-23) — see
docs/superpowers/specs/2026-07-23-scraping-rewrite-design.md. A round-trip is
just a 2-leg multi-city search (A->B, then B->A), so this same builder covers
both of flight-bot's route types; no changes were needed from the original.
"""
import base64
from urllib.parse import quote

# Google Flights cabin enum (Seat): economy=1, premium-economy=2, business=3, first=4
SEAT = {"economy": 1, "premium-economy": 2, "premium economy": 2, "business": 3, "first": 4}


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def _ld(field: int, payload: bytes) -> bytes:      # length-delimited (wire type 2)
    return _varint(field << 3 | 2) + _varint(len(payload)) + payload


def _vf(field: int, value: int) -> bytes:          # varint field (wire type 0)
    return _varint(field << 3) + _varint(value)


def _leg(dep: str, arr: str, date: str) -> bytes:
    return (_ld(2, date.encode())
            + _ld(13, _ld(2, dep.encode()))
            + _ld(14, _ld(2, arr.encode())))


def gflights_url(legs, seat: int = 1, adults: int = 1) -> str:
    """legs: list of (dep_iata, arr_iata, 'YYYY-MM-DD'). Returns a Google Flights
    multi-city search URL with every leg, cabin, and passenger count pre-filled."""
    body = b"".join(_ld(3, _leg(*leg)) for leg in legs)
    body += b"".join(_vf(8, 1) for _ in range(adults))   # passengers: adult=1
    body += _vf(9, seat) + _vf(19, 3)                    # seat, trip=multi-city
    tfs = base64.b64encode(body).decode()
    return f"https://www.google.com/travel/flights?tfs={quote(tfs)}&curr=USD&hl=en"

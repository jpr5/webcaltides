"""Constituents that Kartverket and LINZ define differently from the TCD, and the phase conversion.

Kartverket and LINZ analyse with IOS (Foreman) constituent definitions. Two of them differ from the
TCD's (congen, after Schureman SP98) by the longitude of the solar perigee p1, a term so slow
(1.96e-6 deg/h) that the speed check cannot see it:

    name   provider (IOS) argument       TCD argument   Kartverket XDO served
    SA     h - p1                        h              ZZAZZYZ
    S1     T + p1 - 90                   T              AAYZZAA

T is the mean solar hour angle, h the mean longitude of the sun. A phase lag g is relative to its own
argument V, so the same wave A cos(V_ios - g_ios) is A cos(V_tcd - g_tcd) with
g_tcd = g_ios - (V_ios - V_tcd):

    SA: g_tcd = g_ios + p1
    S1: g_tcd = g_ios - p1 + 90

p1 is taken at P1_EPOCH (`p1_deg`); it moves 0.0172 deg a year, so a set converted with it is off by
0.0172 deg per year away from the epoch (for SA of 15 cm, 0.005 cm a year).

Checked and the same in both systems (definitions compared name by name; see the ADPORT sa-fix
evidence): SSA, MM, MF, MSF, M3 and every other constituent the two sources serve. M3 is the same
argument in both (IOS 3 tau + phase -180, XDO CZZZZZB, is 3T - 3s + 3h, congen's M3: lunar time tau
counts from midnight, so 3 tau carries 3 x 180 deg, which cancels the -180); shifting it by 180 deg
makes the providers' own predictions fit worse (Bergen event sd 0.7 -> 0.9 cm), as it should. The
conversions above were confirmed the same way: against Kartverket's and LINZ's published
predictions over a year, SA's conversion takes the monthly mean bias at Bodo from -18..+18 cm to
-0.1..+0.1 cm, and S1's lowers the event sd.
"""
from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["IOS_TO_TCD", "KARTVERKET_XDO", "P1_EPOCH", "P1_RATE_DEG_PER_HOUR", "p1_deg", "to_tcd_phase"]

# The mid-point of the first release year. A later release can move it; see the module docstring.
P1_EPOCH = datetime(2026, 7, 1, tzinfo=timezone.utc)
P1_RATE_DEG_PER_HOUR = 1.719175 / (36525 * 24)

# name -> (p1 coefficient, constant) of V_ios - V_tcd; g_tcd = g_ios - (k * p1 + c)
IOS_TO_TCD = {"SA": (-1, 0.0), "S1": (1, -90.0)}
# The XDO code Kartverket serves with the IOS definition (letters: Z 0, A 1, Y -1; tau s h p N' p1 phase)
KARTVERKET_XDO = {"SA": "ZZAZZYZ", "S1": "AAYZZAA"}


def p1_deg(t: datetime = P1_EPOCH) -> float:
    """Longitude of the solar perigee (deg), Schureman, Manual of Harmonic Analysis and Prediction of Tides
    (US C&GS Special Publication 98, 1958), Table 1: p1 = 281 13' 15.0" + 1 43' 09.03" T + 1.63" T^2
    + 0.012" T^3, T in Julian centuries from Greenwich mean noon 1899 Dec 31. The TCD agrees: its SA
    and SA-IOS (congen's h and h - p1) differ by 283.39 deg at 2026-01-01."""
    T = (t - datetime(1899, 12, 31, 12, tzinfo=timezone.utc)).total_seconds() / 86400.0 / 36525.0
    return (281 + 13 / 60 + 15.0 / 3600 + (1 + 43 / 60 + 9.03 / 3600) * T + 1.63 / 3600 * T ** 2
            + 0.012 / 3600 * T ** 3) % 360.0


def to_tcd_phase(name: str, g_ios: float, t: datetime = P1_EPOCH) -> float:
    """The phase lag relative to the TCD's argument of a constituent served with the IOS definition
    (SA, S1); any other name is returned unchanged."""
    if name not in IOS_TO_TCD:
        return g_ios
    k, c = IOS_TO_TCD[name]
    return (g_ios - (k * p1_deg(t) + c)) % 360.0

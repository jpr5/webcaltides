# frozen_string_literal: true

# NodalSchureman: node factors (f) and equilibrium arguments (V0 + u) for 13
# principal tidal constituents.
#
# Source of the mathematics (public domain):
#   P. Schureman, "Manual of Harmonic Analysis and Prediction of Tides",
#   U.S. Coast and Geodetic Survey Special Publication No. 98 (SP98),
#   revised edition 1940 (reprinted 1958).
# Formula and table numbers in the comments below are SP98's own.
#
# This is an independent implementation written from a written functional
# specification of the required behaviour and from SP98. It is not derived from
# any other program's source code.
#
# Copyright (c) 2026 Jordan Ritter
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

require "date"

module NodalSchureman
    # The covered constituents. Any other name gets no entry.
    CONSTITUENTS = %w[K1 K2 L2 LDA2 M1 M2 N2 NU2 O1 P1 Q1 S1 S2].freeze

    DEG = Math::PI / 180.0

    # Converts degrees, arc-minutes and arc-seconds to degrees.
    def self.dms(d, m, s)
        d + m / 60.0 + s / 3600.0
    end

    # SP98 Table 1 (p. 162), epoch 1900 January 1. Held fixed for all years.
    OMEGA = dms(23, 27, 8.26)       # obliquity of the ecliptic
    INCL  = dms(5, 8, 43.3546)      # inclination of the moon's orbit to the ecliptic

    # Ratios for the Napier analogies (SP98, explanation of Table 6, p. 156),
    # computed from the exact omega and i, not the printed 1.01883 / 0.64412.
    NAPIER_SUM  = Math.cos(0.5 * (OMEGA - INCL) * DEG) / Math.cos(0.5 * (OMEGA + INCL) * DEG)
    NAPIER_DIFF = Math.sin(0.5 * (OMEGA - INCL) * DEG) / Math.sin(0.5 * (OMEGA + INCL) * DEG)

    # Epoch of the Table 1 polynomials: Greenwich mean noon, 1899 December 31
    # (proleptic Gregorian). Stored as a Julian day number at 00:00 plus 12 h.
    EPOCH_JD = Date.new(1899, 12, 31, Date::GREGORIAN).jd
    SECONDS_PER_DAY = 86_400

    # V of SP98 Table 2 for each constituent, as integer multipliers of
    # [T_h, s, h, p] plus a constant in degrees.
    V_TERMS = {
        "O1"   => [[1, -2, 1, 0], 90],
        "K1"   => [[1, 0, 1, 0], -90],
        "P1"   => [[1, 0, -1, 0], 90],
        "Q1"   => [[1, -3, 1, 1], 90],
        "S1"   => [[1, 0, 0, 0], 0],
        "M1"   => [[1, -1, 1, 1], -90],
        "M2"   => [[2, -2, 2, 0], 0],
        "S2"   => [[2, 0, 0, 0], 0],
        "N2"   => [[2, -3, 2, 1], 0],
        "NU2"  => [[2, -3, 4, -1], 0],
        "LDA2" => [[2, -1, 0, 1], 180],
        "L2"   => [[2, -1, 2, -1], 180],
        "K2"   => [[2, 0, 2, 0], 0]
    }.freeze

    module_function

    def sind(x) = Math.sin(x * DEG)
    def cosd(x) = Math.cos(x * DEG)
    def tand(x) = Math.tan(x * DEG)

    # Full-circle arctangent of y over x, in degrees, in (-180, 180].
    def atan2d(y, x) = Math.atan2(y, x) / DEG

    # Reduces an angle to [0, 360).
    def mod360(x)
        r = x % 360.0
        r >= 360.0 ? 0.0 : r
    end

    # Reduces an angle to [-180, 180).
    def pm180(x)
        mod360(x + 180.0) - 180.0
    end

    # Days (exact Rational) from the 1899-12-31 12:00 epoch to the instant
    # (year-month-day hour:00 UTC) + shift_hours.
    def days_since_epoch(year, month, day, hour, shift_hours)
        jd = Date.new(year, month, day, Date::GREGORIAN).jd
        seconds = (jd - EPOCH_JD) * SECONDS_PER_DAY + hour * 3600 - 43_200 + shift_hours.to_r * 3600
        Rational(seconds, SECONDS_PER_DAY)
    end

    # Astronomical arguments at an instant given as exact days since the epoch.
    # Returns T_h (hour angle of the mean sun, [0, 360), computed exactly from
    # the instant) and the mean longitudes s, h, p, N of SP98 Table 1 in
    # degrees, not reduced.
    #
    # The longitudes are evaluated in plain double precision, unreduced, as the
    # reference behaviour is. At |T| of a few centuries the unreduced values
    # reach ~1e6..1e7 degrees, where one ulp is ~1e-10..1e-9 degrees, so a
    # different (even more exact) evaluation does not reproduce the reference
    # to 1e-9 degrees. T is the exact day count divided by 36 525, rounded once.
    def arguments(days)
        t = (days / 36_525).to_f
        th = ((days - days.floor) * 360).to_f
        s = dms(270, 26, 14.72) + (1336 * 360.0 + 1_108_411.20 / 3600.0) * t +
            (9.09 / 3600.0) * t**2 + (0.0068 / 3600.0) * t**3
        h = dms(279, 41, 48.04) + (129_602_768.13 / 3600.0) * t + (1.089 / 3600.0) * t**2
        p = dms(334, 19, 40.87) + (11 * 360.0 + 392_515.94 / 3600.0) * t -
            (37.24 / 3600.0) * t**2 - (0.045 / 3600.0) * t**3
        n = dms(259, 10, 57.12) - (5 * 360.0 + 482_912.63 / 3600.0) * t +
            (7.58 / 3600.0) * t**2 + (0.008 / 3600.0) * t**3
        { th: th, s: s, h: h, p: p, n: n }
    end

    # Nodal quantities from N and p (SP98; see the explanation of Table 6 and
    # formulas (191), (197), (203), (204), (213), (214), (224), (232)).
    def nodal_quantities(n, p)
        # I: inclination of the lunar orbit to the equator.
        cos_i = Math.cos(INCL * DEG) * Math.cos(OMEGA * DEG) -
                Math.sin(INCL * DEG) * Math.sin(OMEGA * DEG) * cosd(n)
        big_i = Math.acos(cos_i.clamp(-1.0, 1.0)) / DEG

        # nu and xi from the Napier analogies. Each half-angle is placed in the
        # same half-turn as N/2 (cosine of the same sign). The ratios are positive,
        # so atan2(ratio * sin(N/2), cos(N/2)) does exactly that, and gives the
        # limit nu = 0, N - xi = 180 at N = 180 (mod 360).
        half_n = 0.5 * n
        a = atan2d(NAPIER_SUM * sind(half_n), cosd(half_n))   # (N - xi + nu) / 2
        b = atan2d(NAPIER_DIFF * sind(half_n), cosd(half_n))  # (N - xi - nu) / 2
        nu = pm180(a - b)
        xi = n - (a + b)

        # nu' (224) and 2nu'' (232).
        sin2i = sind(2 * big_i)
        nu_p = atan2d(sin2i * sind(nu), sin2i * cosd(nu) + 0.3347)
        sin_i_sq = sind(big_i)**2
        two_nu_pp = atan2d(sin_i_sq * sind(2 * nu), sin_i_sq * cosd(2 * nu) + 0.0727)

        # P (191)/(204); Q (203) in the quadrant of P; Qu = P - Q (204);
        # 1/Qa (197).
        big_p = p - xi
        q = atan2d(0.483 * sind(big_p), cosd(big_p))
        qu = big_p - q
        inv_qa = Math.sqrt(2.310 + 1.435 * cosd(2 * big_p))

        # R (214) and 1/Ra (213).
        tan_half_i = tand(0.5 * big_i)
        cot_half_i_sq = 1.0 / tan_half_i**2
        r = atan2d(sind(2 * big_p), cot_half_i_sq / 6.0 - cosd(2 * big_p))
        inv_ra = Math.sqrt(1.0 - 12.0 * tan_half_i**2 * cosd(2 * big_p) + 36.0 * tan_half_i**4)

        { i: big_i, nu: nu, xi: xi, nu_p: nu_p, two_nu_pp: two_nu_pp,
          qu: qu, inv_qa: inv_qa, r: r, inv_ra: inv_ra }
    end

    # u and f for every covered constituent, from the nodal quantities.
    def u_and_f(nq)
        big_i = nq[:i]
        nu = nq[:nu]
        xi = nq[:xi]

        f_o1 = sind(big_i) * cosd(0.5 * big_i)**2 / 0.3800                       # (75)
        f_m2 = cosd(0.5 * big_i)**4 / 0.9154                                       # (78)
        f_k1 = Math.sqrt(0.8965 * sind(2 * big_i)**2 +
                         0.6001 * sind(2 * big_i) * cosd(nu) + 0.1006)             # (227)
        f_k2 = Math.sqrt(19.0444 * sind(big_i)**4 +
                         2.7702 * sind(big_i)**2 * cosd(2 * nu) + 0.0981)          # (235)
        f_m1 = f_o1 * nq[:inv_qa]                                                  # (206)/(207)
        f_l2 = f_m2 * nq[:inv_ra]                                                  # (215)

        u_o1 = 2 * xi - nu
        u_m2 = 2 * xi - 2 * nu

        {
            "O1"   => [u_o1, f_o1],
            "K1"   => [-nq[:nu_p], f_k1],
            "P1"   => [0.0, 1.0],
            "Q1"   => [u_o1, f_o1],
            "S1"   => [0.0, 1.0],
            "M1"   => [-nu - nq[:qu], f_m1],
            "M2"   => [u_m2, f_m2],
            "S2"   => [0.0, 1.0],
            "N2"   => [u_m2, f_m2],
            "NU2"  => [u_m2, f_m2],
            "LDA2" => [u_m2, f_m2],
            "L2"   => [u_m2 - nq[:r], f_l2],
            "K2"   => [-nq[:two_nu_pp], f_k2]
        }
    end

    # Computes f and V0 + u for all 13 covered constituents.
    #
    # year, month, day: UTC calendar date (proleptic Gregorian) at which u and f
    #   are evaluated; V0 is evaluated at the start of the same year.
    # hour: whole UTC hour for u and f (default 12).
    # shift_hours: station time-meridian shift m, east-positive, may be
    #   fractional. Both instants are moved by +m hours:
    #     t0 = (year-01-01 00:00 UTC) + m   (V0)
    #     t1 = (year-month-day hour:00 UTC) + m   (u and f)
    #
    # Returns { name => { f:, u:, v0:, v0_plus_u: } } with u in [-180, 180),
    # v0 and v0_plus_u in [0, 360), all in degrees.
    def compute(year, month:, day:, hour: 12, shift_hours: 0)
        a0 = arguments(days_since_epoch(year, 1, 1, 0, shift_hours))
        a1 = arguments(days_since_epoch(year, month, day, hour, shift_hours))
        uf = u_and_f(nodal_quantities(a1[:n], a1[:p]))
        CONSTITUENTS.each_with_object({}) do |name, out|
            (c_th, c_s, c_h, c_p), const = V_TERMS.fetch(name)
            # V of Table 2, summed in the order T_h, s, h, p, constant.
            v0 = c_th * a0[:th] + c_s * a0[:s] + c_h * a0[:h] + c_p * a0[:p] + const
            u, f = uf.fetch(name)
            out[name] = { f: f, u: pm180(u), v0: mod360(v0), v0_plus_u: mod360(v0 + u) }
        end
    end

    # f and V0 + u for one constituent, or nil if the name is not covered.
    def factor(name, year, month:, day:, hour: 12, shift_hours: 0)
        return nil unless CONSTITUENTS.include?(name)

        compute(year, month: month, day: day, hour: hour, shift_hours: shift_hours)[name]
    end
end

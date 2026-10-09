# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jordan Ritter
#
# NodalSchureman: node factors f and equilibrium arguments V0 + u for 13
# tidal constituents, after
#
#   P. Schureman, "Manual of Harmonic Analysis and Prediction of Tides",
#   U.S. Coast and Geodetic Survey Special Publication No. 98 (SP98),
#   revised edition 1940 (reprinted 1958). Public domain.
#
# This module is an independent implementation written from a written
# functional specification ("webcaltides legacy nodal corrections: clean-room
# functional spec", SHA-256
# 4457ec58c3fce39c67971a2351898e2655bb5fbf49cc88dec65f17e5a0b1ad9b) and from
# SP98 itself. Formula numbers in parentheses are SP98's own numbers;
# "Table n" is an SP98 table. No other implementation's source was used.
#
# Time handling follows the specification: UTC treated as GMT, days of
# exactly 86 400 s, proleptic Gregorian calendar, and both evaluation
# instants shifted by +shift_hours (east-positive meridian shift).
#
#   t0 = Y-01-01 00:00 UTC + m hours      -> V0 (uses T_h, s, h, p)
#   t1 = Y-month-day H:00 UTC + m hours   -> u and f (use N and p)
#
# Following spec section 9, everything is evaluated in double precision,
# except that the day count is exact (Rational) so that the hour angle of
# the mean sun, T_h, is computed exactly from the instant.

require 'date'

module NodalSchureman
    CONSTITUENTS = %w[K1 K2 L2 LDA2 M1 M2 N2 NU2 O1 P1 Q1 S1 S2].freeze

    # Julian Day Number of 1899-12-31 (Gregorian). The epoch of SP98 Table 1
    # is Greenwich mean noon of that day.
    EPOCH_JDN = Date.new(1899, 12, 31, Date::GREGORIAN).jd

    # SP98 Table 1 mean longitudes in degrees (spec section 4). Arc-seconds
    # are divided by 3600 and one revolution is 360 degrees. Evaluated in
    # double precision, term by term in the order SP98 prints them.
    def s_lon(t)
        (270 + 26 / 60.0 + 14.72 / 3600.0) + (1336 * 360 + 1108411.20 / 3600.0) * t +
            9.09 / 3600.0 * t**2 + 0.0068 / 3600.0 * t**3
    end

    def h_lon(t)
        (279 + 41 / 60.0 + 48.04 / 3600.0) + 129602768.13 / 3600.0 * t + 1.089 / 3600.0 * t**2
    end

    def p_lon(t)
        (334 + 19 / 60.0 + 40.87 / 3600.0) + (11 * 360 + 392515.94 / 3600.0) * t -
            37.24 / 3600.0 * t**2 - 0.045 / 3600.0 * t**3
    end

    def n_lon(t)
        (259 + 10 / 60.0 + 57.12 / 3600.0) - (5 * 360 + 482912.63 / 3600.0) * t +
            7.58 / 3600.0 * t**2 + 0.008 / 3600.0 * t**3
    end
    module_function :s_lon, :h_lon, :p_lon, :n_lon

    DEG = Math::PI / 180.0

    # SP98 Table 1, epoch 1900 January 1. Fixed for all years.
    OMEGA = (23 + 27 / 60.0 + 8.26 / 3600.0) * DEG          # obliquity of the ecliptic
    INCL  = (5 + 8 / 60.0 + 43.3546 / 3600.0) * DEG         # inclination of moon's orbit

    # Ratios for the Napier analogies of the SP98 Figure 1 triangle
    # (explanation of Table 6), computed from the exact omega and i.
    RATIO_SUM  = Math.cos((OMEGA - INCL) / 2) / Math.cos((OMEGA + INCL) / 2)
    RATIO_DIFF = Math.sin((OMEGA - INCL) / 2) / Math.sin((OMEGA + INCL) / 2)

    # V from SP98 Table 2, as integer coefficients of (T_h, s, h, p) plus a
    # constant in degrees.
    V_COEFFS = {
        'O1'   => [1, -2, 1, 0, 90],
        'K1'   => [1, 0, 1, 0, -90],
        'P1'   => [1, 0, -1, 0, 90],
        'Q1'   => [1, -3, 1, 1, 90],
        'S1'   => [1, 0, 0, 0, 0],
        'M1'   => [1, -1, 1, 1, -90],
        'M2'   => [2, -2, 2, 0, 0],
        'S2'   => [2, 0, 0, 0, 0],
        'N2'   => [2, -3, 2, 1, 0],
        'NU2'  => [2, -3, 4, -1, 0],
        'LDA2' => [2, -1, 0, 1, 180],
        'L2'   => [2, -1, 2, -1, 180],
        'K2'   => [2, 0, 2, 0, 0]
    }.freeze

    module_function

    # All 13 constituents for one year and one UTC day. Returns
    # { name => { f:, u:, v0: } } with u in [-180, 180] and v0 in [0, 360]. The upper
    # bounds are closed because Float rounding can land on them.
    def compute(year, month:, day:, hour: 12, shift_hours: 0.0)
        v0 = equilibrium_arguments(year, shift_hours)
        nodal = nodal_terms(year, month, day, hour, shift_hours)

        CONSTITUENTS.each_with_object({}) do |name, out|
            f, u = nodal.fetch(name)
            out[name] = { f: f, u: wrap_pm180(u), v0: v0.fetch(name) }
        end
    end

    # One constituent, or nil for any name outside the spec's 13.
    def constituent(name, year, month:, day:, hour: 12, shift_hours: 0.0)
        return nil unless CONSTITUENTS.include?(name)

        compute(year, month: month, day: day, hour: hour, shift_hours: shift_hours)[name]
    end

    # --- time ------------------------------------------------------------

    # Days since 1899-12-31 12:00 GMT, as an exact Rational.
    def days_since_epoch(year, month, day, hour, shift_hours)
        jdn = Date.new(year, month, day, Date::GREGORIAN).jd
        (jdn - EPOCH_JDN) - Rational(1, 2) + (Rational(hour) + shift_hours.to_r) / 24
    end

    # --- V0 at t0 --------------------------------------------------------

    def equilibrium_arguments(year, shift_hours)
        d  = days_since_epoch(year, 1, 1, 0, shift_hours)
        t  = d.to_f / 36_525.0
        th = ((d - d.floor) * 360).to_f     # hour angle of the mean sun, exact from the instant
        s  = s_lon(t)
        h  = h_lon(t)
        p  = p_lon(t)

        V_COEFFS.transform_values do |(a, b, c, e, k)|
            (a * th + b * s + c * h + e * p + k) % 360.0
        end
    end

    # --- u and f at t1 ---------------------------------------------------

    def nodal_terms(year, month, day, hour, shift_hours)
        d  = days_since_epoch(year, month, day, hour, shift_hours)
        t  = d.to_f / 36_525.0
        n_deg  = n_lon(t) % 720.0    # mod 720 so that N/2 keeps its half-turn
        p_deg  = p_lon(t) % 360.0
        n = n_deg * DEG

        # I, the inclination of the lunar orbit to the equator (Table 6 explanation).
        cos_i = Math.cos(INCL) * Math.cos(OMEGA) - Math.sin(INCL) * Math.sin(OMEGA) * Math.cos(n)
        big_i = Math.acos(cos_i.clamp(-1.0, 1.0))

        # nu and xi from the SP98 Figure 1 triangle. Each half-angle is in the
        # half-turn of N/2 (cosine with the sign of cos N/2).
        half_n = n / 2
        a_sum  = Math.atan2(RATIO_SUM * Math.sin(half_n), Math.cos(half_n))   # (N - xi + nu)/2
        a_diff = Math.atan2(RATIO_DIFF * Math.sin(half_n), Math.cos(half_n))  # (N - xi - nu)/2
        nu = a_sum - a_diff
        xi = n - (a_sum + a_diff)

        sin_i  = Math.sin(big_i)
        sin2i  = Math.sin(2 * big_i)
        cos_hi = Math.cos(big_i / 2)
        tan_hi = Math.tan(big_i / 2)

        # (224) nu'
        nu_p = Math.atan2(sin2i * Math.sin(nu), sin2i * Math.cos(nu) + 0.3347)
        # (232) 2nu''
        two_nu_pp = Math.atan2(sin_i**2 * Math.sin(2 * nu), sin_i**2 * Math.cos(2 * nu) + 0.0727)

        # (191)/(204) P; (203) Q in the quadrant of P; (204) Qu = P - Q
        big_p = p_deg * DEG - xi
        big_q = Math.atan2(0.483 * Math.sin(big_p), Math.cos(big_p))
        q_u   = big_p - big_q
        # (197) 1/Qa
        inv_qa = Math.sqrt(2.310 + 1.435 * Math.cos(2 * big_p))
        # (214) R
        big_r = Math.atan2(Math.sin(2 * big_p), (1.0 / 6.0) / tan_hi**2 - Math.cos(2 * big_p))
        # (213) 1/Ra
        inv_ra = Math.sqrt(1 - 12 * tan_hi**2 * Math.cos(2 * big_p) + 36 * tan_hi**4)

        f_o1 = sin_i * cos_hi**2 / 0.3800                                                      # (75)
        f_m2 = cos_hi**4 / 0.9154                                                               # (78)
        f_k1 = Math.sqrt(0.8965 * sin2i**2 + 0.6001 * sin2i * Math.cos(nu) + 0.1006)           # (227)
        f_k2 = Math.sqrt(19.0444 * sin_i**4 + 2.7702 * sin_i**2 * Math.cos(2 * nu) + 0.0981)   # (235)
        f_m1 = f_o1 * inv_qa                                                                    # (206)/(207)
        f_l2 = f_m2 * inv_ra                                                                    # (215)

        u_o1 = (2 * xi - nu) / DEG
        u_m2 = (2 * xi - 2 * nu) / DEG

        {
            'O1'   => [f_o1, u_o1],
            'Q1'   => [f_o1, u_o1],
            'K1'   => [f_k1, -nu_p / DEG],
            'P1'   => [1.0, 0.0],
            'S1'   => [1.0, 0.0],
            'M1'   => [f_m1, (-nu - q_u) / DEG],
            'M2'   => [f_m2, u_m2],
            'S2'   => [1.0, 0.0],
            'N2'   => [f_m2, u_m2],
            'NU2'  => [f_m2, u_m2],
            'LDA2' => [f_m2, u_m2],
            'L2'   => [f_l2, u_m2 - big_r / DEG],
            'K2'   => [f_k2, -two_nu_pp / DEG]
        }
    end

    def wrap_pm180(x)
        ((x + 180.0) % 360.0) - 180.0
    end
end

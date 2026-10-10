# frozen_string_literal: true

# Measures four ways of evaluating V0 against the oracle. All four take f and
# u from the shipped module (lib/), so f and u are the same in every row;
# only the V0 arithmetic differs.
#
#   1 exact     : Rational longitudes and V0, reduced mod 360, then to Float.
#   2a dbl-mul  : Float longitudes, arc-seconds multiplied by (1/3600.0),
#                 raw V0 + u (not reduced first).
#   2b dbl-div  : Float longitudes, arc-seconds divided by 3600 (spec
#                 section 4 wording), raw V0 + u.
#   3  shipped  : the module's own V0 (Float, reduced mod 360 before u).
#
# T_h is exact (from the Rational day count) in every row.
require 'json'
require 'digest'
require_relative '../../../lib/nodal_schureman'

ORACLE_SHA256 = '236929d0c62da30b6b2cbed0d7322a466afd3e77ef4889b595bab0bdd04f7367'
oracle_bytes = File.binread(File.expand_path('../../../spec/fixtures/harmonics/nodal_oracle.json', __dir__))
actual = Digest::SHA256.hexdigest(oracle_bytes)
abort "oracle SHA-256 mismatch: expected #{ORACLE_SHA256}, got #{actual}" unless actual == ORACLE_SHA256
O = JSON.parse(oracle_bytes)
AS = Rational(1, 3600)

def poly(c, t) = c[0] + c[1] * t + c[2] * t**2 + c[3] * t**3

EXACT = {
    s: [270 + Rational(26, 60) + Rational('14.72') * AS, 1336 * 360 + Rational('1108411.20') * AS,
        Rational('9.09') * AS, Rational('0.0068') * AS],
    h: [279 + Rational(41, 60) + Rational('48.04') * AS, Rational('129602768.13') * AS,
        Rational('1.089') * AS, 0],
    p: [334 + Rational(19, 60) + Rational('40.87') * AS, 11 * 360 + Rational('392515.94') * AS,
        -Rational('37.24') * AS, -Rational('0.045') * AS]
}.freeze

M = 1.0 / 3600
MUL = {
    s: [270 + 26 / 60.0 + 14.72 * M, 1336 * 360 + 1108411.20 * M, 9.09 * M, 0.0068 * M],
    h: [279 + 41 / 60.0 + 48.04 * M, 129602768.13 * M, 1.089 * M, 0.0],
    p: [334 + 19 / 60.0 + 40.87 * M, 11 * 360 + 392515.94 * M, -37.24 * M, -0.045 * M]
}.freeze

def v0_for(form, c)
    d = NodalSchureman.days_since_epoch(c['year'], 1, 1, 0, c['shift_hours'])
    a, b, cc, e, k = NodalSchureman::V_COEFFS.fetch(c['constituent'])
    case form
    when :exact
        t = d / 36_525
        th = (d - d.floor) * 360
        ((a * th + b * poly(EXACT[:s], t) + cc * poly(EXACT[:h], t) + e * poly(EXACT[:p], t) + k) % 360).to_f
    when :mul
        t = d.to_f / 36_525.0
        th = ((d - d.floor) * 360).to_f
        a * th + b * poly(MUL[:s], t) + cc * poly(MUL[:h], t) + e * poly(MUL[:p], t) + k
    when :div
        t = d.to_f / 36_525.0
        th = ((d - d.floor) * 360).to_f
        a * th + b * NodalSchureman.s_lon(t) + cc * NodalSchureman.h_lon(t) + e * NodalSchureman.p_lon(t) + k
    end
end

circ = ->(x, y) { (((x - y + 180.0) % 360.0) - 180.0).abs }
{ '1  exact' => :exact, '2a dbl-mul' => :mul, '2b dbl-div' => :div, '3  shipped' => :shipped }.each do |label, form|
    w = { f: 0.0, u: 0.0, vu: 0.0 }
    n9 = 0
    n4 = 0
    fails = []
    O['cases'].each do |c|
        r = NodalSchureman.constituent(c['constituent'], c['year'], month: c['month'], day: c['day'],
                                       hour: c['hour'], shift_hours: c['shift_hours'])
        v0 = form == :shipped ? r[:v0] : v0_for(form, c)
        e = { f: (r[:f] - c['f']).abs, u: circ.(r[:u], c['u_mod_pm180']),
              vu: circ.(v0 + r[:u], c['V0_plus_u_mod360']) }
        e.each { |k, v| w[k] = v if v > w[k] }
        n9 += 1 if e.values.any? { |v| v > 1e-9 }
        n4 += 1 if e[:vu] > 4e-9 || e[:f] > 1e-9 || e[:u] > 1e-9
        fails << format('%s dV0+u=%.3e', c.values_at('constituent', 'year', 'month', 'day', 'hour', 'shift_hours').inspect, e[:vu]) if e[:vu] > 1e-9
    end
    puts format('%-11s worst df=%.2e du=%.2e dV0+u=%.3e | points failing 1e-9 on all fields: %d | failing (f,u 1e-9; V0+u 4e-9): %d',
                label, w[:f], w[:u], w[:vu], n9, n4)
    fails.each { |l| puts "             #{l}" }
end

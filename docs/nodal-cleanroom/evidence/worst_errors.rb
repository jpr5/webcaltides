# frozen_string_literal: true

# Worst |error| per field of the shipped module over all 780 oracle points,
# and the points over 1e-9 and over 4e-9.
require 'json'
require 'digest'
require_relative '../../../lib/nodal_schureman'

ORACLE_SHA256 = '236929d0c62da30b6b2cbed0d7322a466afd3e77ef4889b595bab0bdd04f7367'
oracle_bytes = File.binread(File.expand_path('../../../spec/fixtures/harmonics/nodal_oracle.json', __dir__))
actual = Digest::SHA256.hexdigest(oracle_bytes)
abort "oracle SHA-256 mismatch: expected #{ORACLE_SHA256}, got #{actual}" unless actual == ORACLE_SHA256
o = JSON.parse(oracle_bytes)
circ = ->(a, b) { (((a - b + 180.0) % 360.0) - 180.0).abs }
worst = { f: [0, nil], vu: [0, nil], u: [0, nil] }
over = []
o['cases'].each do |c|
    r = NodalSchureman.constituent(c['constituent'], c['year'], month: c['month'], day: c['day'],
                                   hour: c['hour'], shift_hours: c['shift_hours'])
    key = c.values_at('constituent', 'year', 'month', 'day', 'hour', 'shift_hours')
    e = { f: (r[:f] - c['f']).abs, vu: circ.(r[:v0] + r[:u], c['V0_plus_u_mod360']),
          u: circ.(r[:u], c['u_mod_pm180']) }
    e.each { |k, v| worst[k] = [v, key] if v > worst[k][0] }
    over << [key, e] if e.values.any? { |v| v > 1e-9 }
end
max_raw_v0 = o['cases'].map { |c| c['V0_raw'].abs }.max
worst.each { |k, (v, key)| puts format('worst %-2s %.3e at %s', k, v, key.inspect) }
puts format('largest |V0_raw| in oracle: %.4e (2^23 = %.4e); ulp there = 2^-30 = %.3e',
            max_raw_v0, 2.0**23, 2.0**-30)
puts format('worst V0+u residual in ulps of 2^-30: %.2f', worst[:vu][0] / 2.0**-30)
puts "points over 1e-9 in any field: #{over.size} of #{o['cases'].size}"
over.each { |key, e| puts format('  %s  df=%.2e dV0+u=%.3e du=%.2e', key.inspect, e[:f], e[:vu], e[:u]) }
puts "points over 4e-9 in V0+u: #{over.count { |_, e| e[:vu] > 4e-9 }}"

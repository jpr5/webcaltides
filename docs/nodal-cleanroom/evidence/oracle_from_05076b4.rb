# frozen_string_literal: true

# Checks that the committed oracle is the output of the old engine.
#
# Usage, from this repository's root:
#   mkdir /tmp/wct-05076b4 && git archive 05076b4 | tar -x -C /tmp/wct-05076b4
#   bundle exec ruby docs/nodal-cleanroom/evidence/oracle_from_05076b4.rb /tmp/wct-05076b4
#
# It loads lib/harmonics_engine.rb from that checkout of webcaltides commit
# 05076b459775c30cbce82c2aba522beaaf1567dc (the old, libcongen-derived code; it
# is run, not copied) and, for each of the oracle's 60 instants, calls
#   Harmonics::Engine#calculate_nodal_factors(year, month, day, shift_hours, hour)
# in legacy mode. From each constituent's { 'f', 'u', 'V0' } it derives:
#   f, V0_raw = V0, u_raw = u, V0_plus_u_mod360 = (V0 + u) % 360.0,
#   u_mod_pm180 = ((u + 180.0) % 360.0) - 180.0
# and compares all five with the oracle, bit for bit. It exits 0 only when
# all 780 points match.
require 'json'
require 'digest'
require 'logger'
require 'tmpdir'

root = ARGV.fetch(0) { abort 'usage: oracle_from_05076b4.rb PATH-TO-05076b4-CHECKOUT' }
ORACLE_SHA256 = '236929d0c62da30b6b2cbed0d7322a466afd3e77ef4889b595bab0bdd04f7367'
oracle_bytes = File.binread(File.expand_path('../../../spec/fixtures/harmonics/nodal_oracle.json', __dir__))
actual = Digest::SHA256.hexdigest(oracle_bytes)
abort "oracle SHA-256 mismatch: expected #{ORACLE_SHA256}, got #{actual}" unless actual == ORACLE_SHA256
oracle = JSON.parse(oracle_bytes)

ENV['HARMONICS_NODAL'] = 'legacy'
require File.join(root, 'lib/harmonics_engine.rb')
abort 'not the 05076b4 engine (no BASES table)' unless defined?(Harmonics::Engine::BASES)

engine = Harmonics::Engine.new(Logger.new(nil), Dir.mktmpdir)
fields = %w[f V0_raw u_raw V0_plus_u_mod360 u_mod_pm180]
mismatches = 0
oracle.fetch('cases').group_by { |c| c.values_at('year', 'month', 'day', 'hour', 'shift_hours') }.each do |(y, m, d, h, s), cases|
    factors = engine.send(:calculate_nodal_factors, y, m, d, s, h)
    cases.each do |c|
        nf = factors.fetch(c['constituent'])
        got = { 'f' => nf['f'], 'V0_raw' => nf['V0'], 'u_raw' => nf['u'],
                'V0_plus_u_mod360' => (nf['V0'] + nf['u']) % 360.0,
                'u_mod_pm180' => ((nf['u'] + 180.0) % 360.0) - 180.0 }
        fields.each do |k|
            next if got[k] == c[k]

            mismatches += 1
            puts "MISMATCH #{c.values_at('constituent', 'year', 'month', 'day', 'hour', 'shift_hours').inspect} #{k}: #{got[k]} != #{c[k]}"
        end
    end
end
puts "oracle points: #{oracle['cases'].size}; fields compared: #{oracle['cases'].size * fields.size}; mismatches: #{mismatches}"
exit(mismatches.zero? ? 0 : 1)

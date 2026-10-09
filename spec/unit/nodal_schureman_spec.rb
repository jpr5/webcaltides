# frozen_string_literal: true

# Checks NodalSchureman against all 780 points of the clean-room oracle.
#
# The oracle is identified by its SHA-256, not by a path outside this
# repository. A copy is committed at spec/fixtures/harmonics/nodal_oracle.json. Its digest is
# verified when this file is loaded, so a wrong oracle aborts the run before
# any example exists, in any random order.
#
# NODAL_LIB may name another copy of the module (used by the mutation check in
# docs/nodal-cleanroom/evidence/). By default the module in lib/ is tested.

require 'json'
require 'digest'
default_lib = File.expand_path('../../lib/nodal_schureman', __dir__)
nodal_lib = ENV.fetch('NODAL_LIB', default_lib)
warn "NOTE: NODAL_LIB is set: testing #{nodal_lib}, not #{default_lib}.rb" unless nodal_lib == default_lib
require(nodal_lib)

RSpec.describe NodalSchureman do
    oracle_sha256 = '236929d0c62da30b6b2cbed0d7322a466afd3e77ef4889b595bab0bdd04f7367'
    oracle_bytes  = File.binread(File.expand_path('../fixtures/harmonics/nodal_oracle.json', __dir__))
    actual_sha256 = Digest::SHA256.hexdigest(oracle_bytes)
    unless actual_sha256 == oracle_sha256
        raise "oracle SHA-256 mismatch: expected #{oracle_sha256}, got #{actual_sha256}"
    end

    oracle = JSON.parse(oracle_bytes)

    # f and u tolerance.
    tolerance = 1e-9

    # (V0+u) tolerance, in degrees. Raw V0 is a sum of mean longitudes. The
    # largest |raw V0| in the oracle is 7.0e6 degrees, below 2^23, so one
    # double ulp there is 2^-30 (about 9.3e-10 degrees). The worst residual
    # measured against the oracle is 1.97e-9 degrees, about 2.1 ulps (see
    # docs/nodal-cleanroom/evidence/v0_variants.txt). 4e-9 degrees is about 4 ulps, and about
    # 0.5 microseconds of M2 phase (M2 runs at 28.984 degrees per hour).
    # It is tight enough to fail when T_h is computed as a plain Float
    # instead of exactly (see docs/nodal-cleanroom/evidence/mutation_th_float.txt).
    tolerance_v0u = 4e-9

    # Circular difference as defined in spec section 9: ((a - b + 180) mod 360) - 180.
    def circular_diff(a, b)
        ((a - b + 180.0) % 360.0) - 180.0
    end

    it 'has 780 oracle points' do
        expect(oracle['cases'].size).to eq(780)
    end

    it 'covers exactly the 13 spec constituents' do
        expect(NodalSchureman::CONSTITUENTS.sort).to eq(oracle['constituents'].sort)
    end

    it 'returns nothing for names outside the spec' do
        %w[Mf Mm J1 M3 M4 MS4 MN4 2N2 MU2 SA SSA k1 m2].each do |name|
            expect(NodalSchureman.constituent(name, 2026, month: 7, day: 2)).to be_nil
            expect(NodalSchureman.compute(2026, month: 7, day: 2)).not_to have_key(name)
        end
    end

    describe 'oracle points' do
        oracle['cases'].each do |c|
            label = format('%-4s %d-%02d-%02d H=%02d m=%+.1f',
                           c['constituent'], c['year'], c['month'], c['day'], c['hour'], c['shift_hours'])

            it label do
                r = NodalSchureman.constituent(c['constituent'], c['year'],
                                               month: c['month'], day: c['day'],
                                               hour: c['hour'], shift_hours: c['shift_hours'])
                expect(r).not_to be_nil

                df   = (r[:f] - c['f']).abs
                dvu  = circular_diff(r[:v0] + r[:u], c['V0_plus_u_mod360']).abs
                du   = circular_diff(r[:u], c['u_mod_pm180']).abs

                aggregate_failures do
                    expect(df).to  be <= tolerance, "f: |d|=#{df}"
                    expect(dvu).to be <= tolerance_v0u, "V0+u: |d|=#{dvu}"
                    expect(du).to  be <= tolerance, "u: |d|=#{du}"
                end
            end
        end
    end
end

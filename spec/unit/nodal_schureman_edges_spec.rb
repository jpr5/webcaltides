# frozen_string_literal: true

# Edge cases of NodalSchureman's public helpers and of the nu/xi solution at
# the singular point N = 180 degrees, which the 780 oracle points do not land on.
RSpec.describe NodalSchureman, 'edge cases' do
    # Circular difference in degrees, in [-180, 180).
    def circ(a, b)
        ((a - b + 180.0) % 360.0) - 180.0
    end

    describe '.wrap_pm180' do
        {
            0.0 => 0.0, -0.0 => 0.0, 179.0 => 179.0, 180.0 => -180.0, -180.0 => -180.0,
            540.0 => -180.0, -540.0 => -180.0, 360.0 => 0.0, -360.0 => 0.0, 190.0 => -170.0,
            -190.0 => 170.0, 10_000_000.5 => -79.5, -1e-20 => 0.0, 1e-20 => 0.0
        }.each do |x, want|
            it "maps #{x} to #{want}" do
                expect(described_class.wrap_pm180(x)).to eq(want)
            end
        end

        it 'stays in [-180, 180] and keeps the angle, over a wide range of inputs' do
            rng = Random.new(20_261_009)
            10_000.times do
                x = (rng.rand - 0.5) * 2e7
                w = described_class.wrap_pm180(x)
                expect(w).to be_between(-180.0, 180.0)
                expect(circ(w, x).abs).to be <= 1e-9
            end
        end

        # The documented range is the closed [-180, 180]. One ulp below -180,
        # the float arithmetic rounds to +180.0, which is the same angle as -180.
        # This pins that behaviour; no caller depends on which of -180 and +180
        # is returned, because only cos(V0 + u + ...) reaches a prediction.
        it 'returns +180.0 (the same angle as -180) one ulp below -180' do
            expect(described_class.wrap_pm180(-180.0.prev_float)).to eq(180.0)
        end
    end

    describe 'V0 from .compute' do
        # The closed ranges [0, 360] and [-180, 180]: x % 360.0 rounds to 360.0
        # for a tiny negative x, and wrap_pm180 can return +180.0 (see above).
        it 'is in [0, 360] for every constituent, year 1600 to 2400 and shift' do
            (1600..2400).step(50).each do |year|
                [0.0, -5.0, 9.5].each do |m|
                    described_class.compute(year, month: 7, day: 2, shift_hours: m).each_value do |r|
                        expect(r[:v0]).to be_between(0.0, 360.0).inclusive
                        expect(r[:u]).to be_between(-180.0, 180.0).inclusive
                    end
                end
            end
        end
    end

    # SP98 Table 1: N decreases by about 0.053 degrees a day, so it passes
    # 180 (mod 360) about every 18.6 years. At N = 180 (mod 360) nu = 0 and
    # N - xi = 180, so xi = 0 and u = 0 for O1, M2, K1 and K2; on either side
    # u has opposite signs (measured asymmetry 0.0014 degrees or less up to a
    # year from the crossing).
    #
    # At each crossing cos(N/2) changes sign, which is where the rule that puts
    # each half-angle in the half-turn of N/2 matters. Reducing N modulo 720
    # rather than 360 does not change nu or xi (modulo 360): a half-turn shift
    # of N/2 moves both half-angles by 180 degrees. Consecutive crossings are
    # N = 180 and N = 540 (mod 720), so the two examples cover N/2 = 90 and
    # N/2 = 270 (mod 360) as the module computes them.
    describe 'the N = 180 limit' do
        # The UTC hour nearest the instant at which N = target, by bisection
        # on the public n_lon over the public day count.
        def hour_at(target, from_year)
            n_at = ->(time) { described_class.n_lon(described_class.days_since_epoch(time.year, time.month, time.day, time.hour, 0.0).to_f / 36_525.0) }
            lo = Time.utc(from_year)
            hi = lo + 40 * 365.25 * 86_400
            lo_n = n_at.call(lo)
            hi_n = n_at.call(hi)
            raise "no crossing of #{target}" unless (lo_n - target) * (hi_n - target) <= 0

            while hi - lo > 3600
                mid = Time.at(lo.to_i + ((hi.to_i - lo.to_i) / 7200) * 3600).utc
                if (n_at.call(mid) - target) * (lo_n - target) <= 0
                    hi = mid
                else
                    lo = mid
                    lo_n = n_at.call(lo)
                end
            end
            [lo, hi].min_by { |t| (n_at.call(t) - target).abs }
        end

        def u_at(time)
            described_class.compute(time.year, month: time.month, day: time.day, hour: time.hour)
        end

        # N(t) is unreduced and negative-going; find the crossings of 180 + 360k
        # nearest to two consecutive k, one for each half of the 720 cycle.
        n2026 = NodalSchureman.n_lon(NodalSchureman.days_since_epoch(2026, 1, 1, 0, 0.0).to_f / 36_525.0)
        k = ((n2026 - 180.0) / 360.0).floor
        [180.0 + 360.0 * k, 180.0 + 360.0 * (k - 1)].each do |target|
            it "gives nu = xi = 0 at N = #{target} (#{target % 720} mod 720) and is continuous through it" do
                at = hour_at(target, 2026)
                r = u_at(at)
                %w[O1 M2 K1 K2 N2].each { |name| expect(r[name][:u].abs).to be < 0.01, "u(#{name}) = #{r[name][:u]} at #{at}" }

                # nu and xi are odd functions of N about the crossing, so u for
                # these constituents (which have no P term) is too. A half-angle
                # in the wrong half-turn breaks this on one side.
                [30, 180, 365].each do |days|
                    before = u_at(at - days * 86_400)
                    after = u_at(at + days * 86_400)
                    %w[O1 M2 K1 K2].each do |name|
                        expect((before[name][:u] + after[name][:u]).abs).to be < 0.01, "u(#{name}) not odd about #{at} at +/-#{days} days"
                    end
                end

                # Hour by hour across the crossing: no jump in u or f.
                series = (-24..24).map { |h| u_at(at + h * 3600) }
                series.each_cons(2) do |a, b|
                    %w[O1 M2 K1 K2 L2 M1].each do |name|
                        expect(circ(b[name][:u], a[name][:u]).abs).to be < 0.01, "u(#{name}) jumps near #{at}"
                        expect((b[name][:f] - a[name][:f]).abs).to be < 1e-3, "f(#{name}) jumps near #{at}"
                    end
                end
            end
        end
    end
end

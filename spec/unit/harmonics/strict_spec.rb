# frozen_string_literal: true

require 'tmpdir'

# The strict engine mode (OTC SDK prediction spec, revision 20, sections 4 and 6.1).
# The synthetic cases use small made-up astronomical tables (2000-2030), so that each
# rule can be pinned exactly; the TCD cases use the shipped tables.
RSpec.describe Harmonics::Engine, 'strict mode' do
    let(:logger) { Logger.new('/dev/null') }
    let(:engine) { described_class.new(logger, Dir.mktmpdir) }

    # rows: name => [speed, { year => [v0u, f] }]; other years are V0+u 0, f 1.
    def table(rows)
        years = (2000..2030)
        Harmonics::Strict::AstroTable.from_h(
            'first_year' => years.first, 'last_year' => years.last,
            'constituents' => rows.to_h do |name, (speed, by_year)|
                by_year ||= {}
                [name, { 'speed_deg_per_hour' => speed,
                         'v0u_deg' => years.map { |y| by_year.dig(y, 0) || 0.0 },
                         'f' => years.map { |y| by_year.dig(y, 1) || 1.0 } }]
            end
        )
    end

    def raw_table(constituents)
        Harmonics::Strict::AstroTable.from_h('first_year' => 2000, 'last_year' => 2030, 'constituents' => constituents)
    end

    def tide(name, amp, phase)
        { 'name' => name, 'amplitude_m' => amp, 'phase_deg' => phase }
    end

    def bin(name, amp, phase, extra = {})
        { 'azimuth_deg' => 0.0,
          'constituents' => [{ 'name' => name, 'major_amplitude_ms' => amp, 'major_phase_deg' => phase,
                               'minor_amplitude_ms' => 0.0, 'minor_phase_deg' => 0.0 }] }.merge(extra)
    end

    def events(list, key = 'height')
        list.map { |e| [e['time'].utc.iso8601, e['type'], e[key]&.round(9)] }
    end

    def code_of
        yield
        nil
    rescue Harmonics::Strict::Error => e
        e.code
    end

    let(:t2025) { Time.utc(2025, 6, 1) }

    describe 'refusals' do
        let(:astro) { table('S12' => [30.0]) }

        it 'raises unsupported_constituent for a constituent with no table row' do
            expect(code_of { engine.predict_strict([tide('S12', 1, 0), tide('XX9', 1, 0)], [t2025], astro: astro) }).to eq('unsupported_constituent')
            expect(code_of { engine.extremes_strict([tide('XX9', 1, 0)], t2025, t2025 + 3600, astro: astro) }).to eq('unsupported_constituent')
        end

        it 'raises time_out_of_range outside the table, also when only the grid padding reaches out' do
            expect(code_of { engine.predict_strict([tide('S12', 1, 0)], [Time.utc(1999, 12, 31, 23, 59, 59)], astro: astro) }).to eq('time_out_of_range')
            expect(code_of { engine.predict_strict([tide('S12', 1, 0)], [Time.utc(2031)], astro: astro) }).to eq('time_out_of_range')
            expect(code_of { engine.extremes_strict([tide('S12', 1, 0)], Time.utc(2030, 12, 31), Time.utc(2030, 12, 31, 23, 30), astro: astro) }).to eq('time_out_of_range')
        end

        it 'raises invalid_argument for start after stop and is empty for start == stop' do
            expect(code_of { engine.extremes_strict([tide('S12', 1, 0)], t2025 + 1, t2025, astro: astro) }).to eq('invalid_argument')
            expect(engine.extremes_strict([tide('S12', 1, 0)], t2025, t2025, astro: astro)).to eq([])
        end

        it 'raises time_out_of_range when only the last grid point is in the next year' do
            # stop + 3600 s is exactly 2031-01-01T00:00Z, a grid point outside the table.
            expect(code_of { engine.extremes_strict([tide('S12', 1, 0)], Time.utc(2030, 12, 31, 12), Time.utc(2030, 12, 31, 23), astro: astro) }).to eq('time_out_of_range')
            # Heights before stop never evaluate 2031.
            expect(engine.predict_strict([tide('S12', 1, 0)], [Time.utc(2030, 12, 31, 23, 59, 59)], astro: astro).size).to eq(1)
        end

        it 'raises unsupported_constituent for a row with no speed' do
            nospeed = raw_table('S12' => { 'speed_deg_per_hour' => nil, 'v0u_deg' => [0.0] * 31, 'f' => [1.0] * 31 })
            expect(code_of { engine.predict_strict([tide('S12', 1, 0)], [t2025], astro: nospeed) }).to eq('unsupported_constituent')
        end

        it 'raises invalid_argument for a nil or non-finite V0+u or f in a table row' do
            [[nil, 1.0], [Float::NAN, 1.0], [0.0, nil], [0.0, Float::INFINITY], ['1.0', 1.0]].each do |v0u, f|
                code = code_of do
                    rows = raw_table('S12' => { 'speed_deg_per_hour' => 30.0, 'v0u_deg' => [0.0] * 25 + [v0u] + [0.0] * 5, 'f' => [1.0] * 25 + [f] + [1.0] * 5 })
                    engine.predict_strict([tide('S12', 1, 0)], [t2025], astro: rows)
                end
                expect(code).to eq('invalid_argument'), "v0u #{v0u.inspect}, f #{f.inspect}"
            end
        end

        it 'raises invalid_argument for a non-numeric, nil or non-finite amplitude or phase' do
            [nil, '1.0', Float::NAN, Float::INFINITY].each do |bad|
                expect(code_of { engine.predict_strict([tide('S12', bad, 0)], [t2025], astro: astro) }).to eq('invalid_argument'), "amplitude #{bad.inspect}"
                expect(code_of { engine.extremes_strict([tide('S12', 1, bad)], t2025, t2025 + 3600, astro: astro) }).to eq('invalid_argument'), "phase #{bad.inspect}"
            end
            expect(code_of { engine.predict_strict([{ 'name' => 'S12', 'phase_deg' => 0 }], [t2025], astro: astro) }).to eq('invalid_argument')
        end

        it 'raises invalid_argument for an empty constituent list or a duplicate name' do
            expect(code_of { engine.predict_strict([], [t2025], astro: astro) }).to eq('invalid_argument')
            expect(code_of { engine.extremes_strict([tide('S12', 1, 0), tide('S12', 1, 0)], t2025, t2025 + 3600, astro: astro) }).to eq('invalid_argument')
        end

        it 'raises invalid_argument for a bad datum term' do
            [nil, '2.0', Float::NAN].each do |bad|
                expect(code_of { engine.predict_strict([tide('S12', 1, 0)], [t2025], datum_term: bad, astro: astro) }).to eq('invalid_argument'), bad.inspect
                expect(code_of { engine.extremes_strict([tide('S12', 1, 0)], t2025, t2025 + 3600, datum_term: bad, astro: astro) }).to eq('invalid_argument'), bad.inspect
            end
        end

        it 'raises invalid_argument for a time that is not a Time or a finite number' do
            [nil, '2025-06-01', Float::NAN].each do |bad|
                expect(code_of { engine.predict_strict([tide('S12', 1, 0)], [bad], astro: astro) }).to eq('invalid_argument'), bad.inspect
                expect(code_of { engine.extremes_strict([tide('S12', 1, 0)], bad, t2025, astro: astro) }).to eq('invalid_argument'), bad.inspect
                expect(code_of { engine.extremes_strict([tide('S12', 1, 0)], t2025, bad, astro: astro) }).to eq('invalid_argument'), bad.inspect
            end
            expect(engine.predict_strict([tide('S12', 1, 0)], [t2025.to_i], astro: astro)).to eq(engine.predict_strict([tide('S12', 1, 0)], [t2025], astro: astro))
        end
    end

    describe 'heights' do
        it 'is the sum with V0+u and f of the instant year, the angle reduced with floor, and no datum offset' do
            astro = table('S12' => [30.0, { 2025 => [10.0, 0.9], 2026 => [200.0, 1.1] }])
            t = Time.utc(2025, 12, 31, 23)
            d = (t - Time.utc(2025)) / 3600.0
            h = engine.predict_strict([tide('S12', 2.0, 350.0)], [t, Time.utc(2026)], astro: astro)
            expect(h[0]).to be_within(1e-12).of(0.9 * 2.0 * Math.cos(((30.0 * d + 10.0 - 350.0) % 360) * Math::PI / 180))
            expect(h[1]).to be_within(1e-12).of(1.1 * 2.0 * Math.cos((200.0 - 350.0 + 360) * Math::PI / 180))
            expect(engine.predict_strict([tide('S12', 2.0, 350.0)], [t], datum_term: 1.5, astro: astro)[0]).to be_within(1e-12).of(h[0] + 1.5)
        end
    end

    describe 'extremes' do
        it 'reports a high and a low 3 minutes apart inside every 6-minute step' do
            # 1 degree per second: highs 45 s and lows 225 s after each grid point.
            ev = engine.extremes_strict([tide('F', 1.0, 45.0)], t2025, t2025 + 3600, astro: table('F' => [3600.0]))
            expect(ev.size).to eq(20)
            ev.each_slice(2).with_index do |(hi, lo), k|
                expect([hi['type'], hi['time'], lo['type'], lo['time']]).to eq(['high', t2025 + 360 * k + 45, 'low', t2025 + 360 * k + 225])
                # The root is within 0.005 s, and the curve turns 1 degree a second.
                expect(hi['height']).to be_within(1e-8).of(1.0)
                expect(lo['height']).to be_within(1e-8).of(-1.0)
            end
        end

        it 'reports four extremes inside one step (high, low, high, low), in order' do
            ev = engine.extremes_strict([tide('F', 1.0, 60.0)], t2025, t2025 + 360, astro: table('F' => [7200.0]))
            expect(ev.map { |e| [e['type'], e['time'] - t2025] }).to eq([['high', 30], ['low', 120], ['high', 210], ['low', 300]])
        end

        it 'reports the window edges: an event at start, none at stop' do
            ev = engine.extremes_strict([tide('F', 1.0, 45.0)], t2025 + 45, t2025 + 405, astro: table('F' => [3600.0]))
            expect(ev.map { |e| e['time'] - t2025 }).to eq([45, 225])
        end

        it 'adds the datum term to the event heights' do
            ev = engine.extremes_strict([tide('F', 1.0, 45.0)], t2025, t2025 + 360, datum_term: 2.0, astro: table('F' => [3600.0]))
            expect(ev.map { |e| e['height'].round(6) }).to eq([3.0, 1.0])
        end

        # 2020 is a leap year: 15 deg/h * 8784 h is a whole number of turns, so
        # the old year's argument at T is V0+u(2020) - g.
        let(:new_year) { Time.utc(2021) }

        def diurnal(v2020, v2021, f2020 = 1.0, f2021 = 1.0)
            table('D1' => [15.0, { 2020 => [v2020, f2020], 2021 => [v2021, f2021] }])
        end

        it 'evaluates a step that ends at New Year with the old year tables' do
            # Old table: high 3 min before T. New table: high 1 min before T (same side).
            ev = engine.extremes_strict([tide('D1', 1.0, 0.0)], new_year - 6 * 3600, new_year + 6 * 3600, astro: diurnal(0.75, 0.25, 1.0, 1.2))
            expect(events(ev)).to eq([['2020-12-31T23:57:00Z', 'high', 1.0]])
        end

        it 'inserts a high at New Year when the jump hides it on both sides (missed event)' do
            # Old table: rising at T, high 10 min after it. New table: falling at T.
            # The new year's f differs, so the inserted height shows which table it used.
            ev = engine.extremes_strict([tide('D1', 1.0, 0.0)], new_year - 6 * 3600, new_year + 6 * 3600, astro: diurnal(357.5, 2.5, 1.0, 1.2))
            expect(events(ev)).to eq([['2021-01-01T00:00:00Z', 'high', (1.2 * Math.cos(2.5 * Math::PI / 180)).round(9)]])
        end

        it 'keeps one high when each table puts it on its own side of New Year (doubled event)' do
            # Old table: high 10 min before T. New table: high 8 min after T.
            higher_new = engine.extremes_strict([tide('D1', 1.0, 0.0)], new_year - 6 * 3600, new_year + 6 * 3600, astro: diurnal(2.5, 358.0, 1.0, 1.01))
            expect(events(higher_new)).to eq([['2021-01-01T00:08:00Z', 'high', 1.01]])
            equal = engine.extremes_strict([tide('D1', 1.0, 0.0)], new_year - 6 * 3600, new_year + 6 * 3600, astro: diurnal(2.5, 358.0))
            expect(events(equal)).to eq([['2020-12-31T23:50:00Z', 'high', 1.0]])
        end

        # A semidiurnal crest at 00:30 with a tiny wiggle whose extremes are 0.95 s
        # apart.  Near the crest the wiggle makes one odd cluster of about a dozen
        # roots; on each side it makes high-low pairs closer than 1 s on a
        # rising or falling tide (even clusters).  Only the crest is an extremum.
        it 'reports one high for a crest made of sub-second roots, and nothing for the pairs beside it' do
            crest = Time.utc(2025, 6, 1, 0, 30)
            astro = table('S12' => [30.0], 'W' => [360.0 / 1.9 * 3600])
            ev = engine.extremes_strict([tide('S12', 1.0, 15.0), tide('W', 3.8e-7, 0.0)], crest - 3600, crest + 3600, astro: astro)
            expect(ev.map { |e| e['type'] }).to eq(['high'])
            expect((ev.first['time'] - crest).abs).to be <= 10
            expect(ev.first['height']).to be_within(1e-6).of(1.0)
        end
    end

    describe 'close-root filter' do
        def cand(r, kind, value = 0.0)
            { r: r, kind: kind, value: value }
        end
        let(:higher) { ->(x, y) { x[:kind] == :down ? x[:value] > y[:value] : x[:value] < y[:value] } }

        it 'drops an even cluster and keeps one of an odd cluster, the more extreme, the earliest on a tie' do
            list = [cand(0.0, :down, 1.0), cand(0.8, :up, 0.9),                          # even: dropped
                    cand(100.0, :down, 1.0), cand(100.4, :up, 0.9), cand(100.9, :down, 1.1), # odd: second high
                    cand(200.0, :down, 1.0), cand(200.4, :up, 0.9), cand(200.9, :down, 1.0), # tie: first high
                    cand(300.0, :up, -1.0), cand(301.0, :down, 1.0)]                          # 1 s apart: two clusters
            kept = Harmonics::Strict.filter_extrema(list, &higher)
            expect(kept.map { |c| c[:r] }).to eq([100.9, 200.0, 300.0, 301.0])
        end

        it 'keeps the slack at index 2*floor((n - 1)/4) of an odd cluster' do
            three = [cand(0.0, :up), cand(0.3, :down), cand(0.6, :up)]
            five = [cand(10.0, :up), cand(10.2, :down), cand(10.4, :up), cand(10.6, :down), cand(10.8, :up)]
            two = [cand(20.0, :up), cand(20.6, :down)]
            expect(Harmonics::Strict.filter_slacks(three + five + two).map { |c| c[:r] }).to eq([0.0, 10.4])
        end
    end

    describe 'currents' do
        let(:astro) { table('S12' => [30.0, { 2025 => [20.0, 0.95] }], 'F' => [5400.0]) }

        it 'gives major and minor components, speed and direction, with the flood sign and the means' do
            b = { 'azimuth_deg' => 30.0, 'mean_flood_dir_deg' => 210.0, 'mean_major_ms' => 0.1, 'mean_minor_ms' => -0.02,
                  'constituents' => [{ 'name' => 'S12', 'major_amplitude_ms' => 1.0, 'major_phase_deg' => 40.0,
                                       'minor_amplitude_ms' => 0.2, 'minor_phase_deg' => 130.0 }] }
            t = Time.utc(2025, 3, 4, 5, 6, 7)
            d = (t - Time.utc(2025)) / 3600.0
            u = 0.1 + 0.95 * Math.cos((30.0 * d + 20.0 - 40.0) * Math::PI / 180)
            v = -0.02 + 0.95 * 0.2 * Math.cos((30.0 * d + 20.0 - 130.0) * Math::PI / 180)
            e = u * Math.sin(30 * Math::PI / 180) + v * Math.sin(120 * Math::PI / 180)
            n = u * Math.cos(30 * Math::PI / 180) + v * Math.cos(120 * Math::PI / 180)
            r = engine.currents_strict(b, [t], astro: astro).first
            expect(r['velocity_major_ms']).to be_within(1e-12).of(-u)
            expect(r['velocity_minor_ms']).to be_within(1e-12).of(-v)
            expect(r['speed_ms']).to be_within(1e-12).of(Math.hypot(e, n))
            expect(r['direction_deg']).to be_within(1e-9).of(Math.atan2(e, n) * 180 / Math::PI % 360)

            flipped = engine.currents_strict(b, [t], minor_sign: -1, astro: astro).first
            expect(flipped['velocity_minor_ms']).to eq(r['velocity_minor_ms'])
            expect(flipped['direction_deg']).not_to be_within(1e-6).of(r['direction_deg'])
        end

        it 'gives 0.0, never 360.0, for a current at azimuth 180 flowing north' do
            b = bin('S12', 0.0, 0.0, 'azimuth_deg' => 180.0, 'mean_major_ms' => -1.0)
            expect(engine.currents_strict(b, [t2025], astro: astro).first['direction_deg']).to eq(0.0)
        end

        it 'accepts only +1 or -1 as the minor-axis sign, +1 by default' do
            b = bin('S12', 1.0, 0.0)
            [0, 2, -1.5, nil, '1'].each do |bad|
                expect(code_of { engine.currents_strict(b, [t2025], minor_sign: bad, astro: astro) }).to eq('invalid_argument'), bad.inspect
            end
            expect(engine.currents_strict(b, [t2025], astro: astro)).to eq(engine.currents_strict(b, [t2025], minor_sign: 1, astro: astro))
        end

        it 'raises invalid_argument for a missing azimuth, an explicit null mean, or a bad current constant' do
            no_azimuth = bin('S12', 1.0, 0.0).reject { |k, _| k == 'azimuth_deg' }
            expect(code_of { engine.currents_strict(no_azimuth, [t2025], astro: astro) }).to eq('invalid_argument')
            expect(code_of { engine.current_events_strict(no_azimuth, t2025, t2025 + 3600, astro: astro) }).to eq('invalid_argument')
            %w[mean_major_ms mean_minor_ms].each do |k|
                expect(code_of { engine.currents_strict(bin('S12', 1.0, 0.0, k => nil), [t2025], astro: astro) }).to eq('invalid_argument'), k
            end
            expect(code_of { engine.current_events_strict(bin('S12', 1.0, 0.0, 'mean_major_ms' => nil), t2025, t2025 + 3600, astro: astro) }).to eq('invalid_argument')
            expect(code_of { engine.currents_strict(bin('S12', Float::NAN, 0.0), [t2025], astro: astro) }).to eq('invalid_argument')
            expect(code_of { engine.current_events_strict(bin('S12', 1.0, nil), t2025, t2025 + 3600, astro: astro) }).to eq('invalid_argument')
            expect(code_of { engine.currents_strict(bin('S12', 1.0, 0.0, 'constituents' => []), [t2025], astro: astro) }).to eq('invalid_argument')
            # An absent mean counts as 0.
            expect(engine.currents_strict(bin('S12', 1.0, 0.0), [t2025], astro: astro)).to eq(engine.currents_strict(bin('S12', 1.0, 0.0, 'mean_major_ms' => 0.0), [t2025], astro: astro))
        end

        it 'gives a null direction at zero speed' do
            r = engine.currents_strict(bin('S12', 0.0, 0.0), [t2025], astro: astro).first
            expect([r['speed_ms'], r['direction_deg']]).to eq([0.0, nil])
        end

        it 'reports two slacks 2 minutes apart inside one step, and the max flood and ebb between them' do
            # 1.5 deg/s: max flood 20 s and 260 s, slacks 80 s and 200 s, max ebb 140 s after each grid point.
            ev = engine.current_events_strict(bin('F', 1.0, 30.0, 'mean_flood_dir_deg' => 0.0, 'mean_ebb_dir_deg' => 180.0), t2025, t2025 + 360, astro: astro)
            expect(ev.map { |e| [e['time'] - t2025, e['type'], e['velocity_ms'].round(6), e['direction_deg']] }).to eq(
                [[20, 'max_flood', 1.0, 0.0], [80, 'slack_before_ebb', 0.0, nil], [140, 'max_ebb', -1.0, 180.0],
                 [200, 'slack_before_flood', 0.0, nil], [260, 'max_flood', 1.0, 0.0], [320, 'slack_before_ebb', 0.0, nil]]
            )
        end

        it 'reports one max flood for a maximum made of sub-second roots' do
            crest = Time.utc(2025, 6, 1, 0, 30)
            astro = table('S12' => [30.0], 'W' => [360.0 / 1.9 * 3600])
            b = bin('S12', 1.0, 15.0)
            b['constituents'] << { 'name' => 'W', 'major_amplitude_ms' => 3.8e-7, 'major_phase_deg' => 0.0, 'minor_amplitude_ms' => 0.0, 'minor_phase_deg' => 0.0 }
            ev = engine.current_events_strict(b, crest - 3600, crest + 3600, astro: astro)
            expect(ev.map { |e| e['type'] }).to eq(['max_flood'])
            expect((ev.first['time'] - crest).abs).to be <= 10
        end

        it 'reports one slack for a zero crossing made of sub-second roots' do
            # A falling current at 03:30 with a wiggle that crosses zero every 0.95 s
            # near the crossing: one odd cluster, and even clusters beside it.
            slack = Time.utc(2025, 6, 1, 3, 30)
            astro = table('S12' => [30.0], 'W' => [360.0 / 1.9 * 3600])
            b = bin('S12', 1.0, 15.0)
            b['constituents'] << { 'name' => 'W', 'major_amplitude_ms' => 0.0087, 'major_phase_deg' => 0.0, 'minor_amplitude_ms' => 0.0, 'minor_phase_deg' => 0.0 }
            ev = engine.current_events_strict(b, slack - 1800, slack + 1800, astro: astro).select { |e| e['type'].start_with?('slack') }
            expect(ev.map { |e| e['type'] }).to eq(['slack_before_ebb'])
            expect((ev.first['time'] - slack).abs).to be <= 60
        end

        it 'does not report a weak ebb (a minimum above zero) and has no slack then' do
            ev = engine.current_events_strict(bin('S12', 0.3, 0.0, 'mean_major_ms' => 0.5), t2025, t2025 + 86_400, astro: astro)
            expect(ev.map { |e| e['type'] }.uniq).to eq(['max_flood'])
        end

        it 'flips the major axis when the azimuth is the ebb direction' do
            b = bin('S12', 1.0, 0.0, 'azimuth_deg' => 90.0, 'mean_flood_dir_deg' => 270.0)
            plain = engine.current_events_strict(bin('S12', 1.0, 0.0), t2025, t2025 + 86_400, astro: astro)
            flipped = engine.current_events_strict(b, t2025, t2025 + 86_400, astro: astro)
            swap = { 'max_flood' => 'max_ebb', 'max_ebb' => 'max_flood', 'slack_before_flood' => 'slack_before_ebb', 'slack_before_ebb' => 'slack_before_flood' }
            expect(flipped.map { |e| [e['time'], e['type'], e['velocity_ms']] }).to eq(plain.map { |e| [e['time'], swap[e['type']], -e['velocity_ms']] })
        end

        describe 'at New Year' do
            let(:new_year) { Time.utc(2021) }

            def slacks(v2020, v2021)
                astro = table('D1' => [15.0, { 2020 => [v2020, 1.0], 2021 => [v2021, 1.0] }])
                engine.current_events_strict(bin('D1', 1.0, 0.0), new_year - 3 * 3600, new_year + 3 * 3600, astro: astro)
            end

            it 'inserts a slack missed on both sides' do
                expect(events(slacks(87.5, 92.5), 'velocity_ms')).to eq([['2021-01-01T00:00:00Z', 'slack_before_ebb', 0.0]])
            end

            it 'keeps the earlier of a doubled slack' do
                expect(events(slacks(92.5, 88.0), 'velocity_ms')).to eq([['2020-12-31T23:50:00Z', 'slack_before_ebb', 0.0]])
            end

            def maxima(v2020, v2021, f2020, f2021, extra = {})
                astro = table('D1' => [15.0, { 2020 => [v2020, f2020], 2021 => [v2021, f2021] }])
                ev = engine.current_events_strict(bin('D1', 1.0, 0.0, extra), new_year - 3 * 3600, new_year + 3 * 3600, astro: astro)
                events(ev.reject { |e| e['type'].start_with?('slack') }, 'velocity_ms')
            end

            it 'inserts a max flood missed on both sides, with the new year W' do
                expect(maxima(357.5, 2.5, 1.0, 1.2)).to eq([['2021-01-01T00:00:00Z', 'max_flood', (1.2 * Math.cos(2.5 * Math::PI / 180)).round(9)]])
            end

            it 'keeps the least W of a doubled max ebb' do
                # Old table: minimum 10 min before T. New table: minimum 8 min after T, lower.
                expect(maxima(182.5, 178.0, 1.0, 1.01)).to eq([['2021-01-01T00:08:00Z', 'max_ebb', -1.01]])
            end

            it 'keeps the greatest W of a doubled maximum, by sign and not by size' do
                # With a mean of -0.9: the old maximum is +0.1 (a flood), the new one
                # -0.2 (weaker than slack).  The signed rule keeps the flood.
                expect(maxima(2.5, 358.0, 1.0, 0.7, 'mean_major_ms' => -0.9)).to eq([['2020-12-31T23:50:00Z', 'max_flood', 0.1]])
            end
        end
    end

    describe 'subordinate tide stations' do
        let(:astro) { table('S12' => [30.0], 'D1' => [15.0]) }
        let(:ref) { [tide('S12', 1.0, 15.0), tide('D1', 0.3, 40.0)] }
        let(:t0) { Time.utc(2025, 6, 1) }
        let(:t1) { Time.utc(2025, 6, 4) }
        let(:ref_events) { engine.extremes_strict(ref, t0 - 86_400, t1 + 86_400, datum_term: 2.0, astro: astro) }

        def shifted(offsets)
            ref_events.map do |e|
                high = e['type'] == 'high'
                k = high ? offsets.fetch(:hh) : offsets.fetch(:hl)
                [e['time'] + 60 * (high ? offsets.fetch(:th) : offsets.fetch(:tl)), e['type'], offsets[:r] ? e['height'] * k : e['height'] + k]
            end.select { |t, _, _| t >= t0 && t < t1 }.sort_by(&:first)
        end

        def sub(offsets)
            engine.subordinate_extremes_strict(ref, 2.0, offsets, t0, t1, astro: astro).map { |e| [e['time'], e['type'], e['height']] }
        end

        it 'applies time offsets and ratios to chart-datum heights' do
            got = sub('height_adjusted_type' => 'R', 'time_offset_high_min' => 37, 'time_offset_low_min' => 20,
                      'height_offset_high' => 0.9, 'height_offset_low' => 0.8)
            expect(got).to eq(shifted(r: true, th: 37, tl: 20, hh: 0.9, hl: 0.8))
            expect(got.size).to be >= 5
        end

        it 'applies additive offsets, absent offsets as zero and identity, and orders swapped events by time' do
            expect(sub('height_adjusted_type' => 'A', 'time_offset_high_min' => -12.5, 'height_offset_high' => 0.15))
                .to eq(shifted(th: -12.5, tl: 0, hh: 0.15, hl: 0))
            swapped = sub('height_adjusted_type' => 'R', 'time_offset_high_min' => 400, 'time_offset_low_min' => -300)
            expect(swapped).to eq(shifted(r: true, th: 400, tl: -300, hh: 1, hl: 1))
            expect(swapped.map(&:first)).to eq(swapped.map(&:first).sort)
        end

        it 'folds equal ratio offsets into constants whose curve is the shifted, scaled reference curve' do
            folded = engine.subordinate_folded_strict(ref, 2.0, { 'height_adjusted_type' => 'R', 'time_offset_high_min' => 37, 'time_offset_low_min' => 37,
                                                                  'height_offset_high' => 0.9, 'height_offset_low' => 0.9 }, astro: astro)
            expect(folded['method']).to eq('folded_offsets')
            expect(folded['datum_term']).to be_within(1e-12).of(1.8)
            times = (0...20).map { |i| t0 + i * 3517 }
            sub_h = engine.predict_strict(folded['constituents'], times, datum_term: folded['datum_term'], astro: astro)
            ref_h = engine.predict_strict(ref, times.map { |t| t - 37 * 60 }, datum_term: 2.0, astro: astro)
            sub_h.zip(ref_h).each { |s, r| expect(s).to be_within(1e-12).of(0.9 * r) }

            curve = engine.extremes_strict(folded['constituents'], t0, t1, datum_term: folded['datum_term'], astro: astro)
            method = engine.subordinate_extremes_strict(ref, 2.0, { 'height_adjusted_type' => 'R', 'time_offset_high_min' => 37, 'time_offset_low_min' => 37,
                                                                   'height_offset_high' => 0.9, 'height_offset_low' => 0.9 }, t0, t1, astro: astro)
            expect(curve.size).to eq(method.size)
            curve.zip(method).each do |c, m|
                expect(c['type']).to eq(m['type'])
                expect((c['time'] - m['time']).abs).to be <= 1
                expect(c['height']).to be_within(1e-5).of(m['height'])
            end
        end

        it 'folds equal additive offsets' do
            folded = engine.subordinate_folded_strict(ref, 2.0, { 'height_adjusted_type' => 'A', 'time_offset_high_min' => -12, 'time_offset_low_min' => -12,
                                                                  'height_offset_high' => 0.15, 'height_offset_low' => 0.15 }, astro: astro)
            t = t0 + 12_345
            sub_h = engine.predict_strict(folded['constituents'], [t], datum_term: folded['datum_term'], astro: astro).first
            ref_h = engine.predict_strict(ref, [t + 12 * 60], datum_term: 2.0, astro: astro).first
            expect(sub_h).to be_within(1e-12).of(ref_h + 0.15)
        end

        it 'adds the offset to the unrounded reference root and rounds once' do
            # The reference highs are at 45.4 s after each grid point, the lows at 225.4 s.
            fast = table('F' => [3600.0])
            ref_f = [tide('F', 1.0, 45.4)]
            got = engine.subordinate_extremes_strict(ref_f, 0.0, { 'height_adjusted_type' => 'R', 'time_offset_high_min' => 0.005, 'time_offset_low_min' => 0.005 },
                                                     t2025, t2025 + 360, astro: fast)
            expect(got.map { |e| [e['time'] - t2025, e['type']] }).to eq([[46, 'high'], [226, 'low']])
        end

        it 'keeps the reference order of two shifted events that round to the same second' do
            fast = table('F' => [3600.0])
            got = engine.subordinate_extremes_strict([tide('F', 1.0, 45.4)], 0.0, { 'height_adjusted_type' => 'R', 'time_offset_high_min' => 3, 'time_offset_low_min' => 0 },
                                                     t2025, t2025 + 360, astro: fast)
            expect(got.map { |e| [e['time'] - t2025, e['type']] }).to eq([[225, 'high'], [225, 'low']])
        end

        it 'finds a reference event more than an hour outside the window that an offset moves into it' do
            # The first reference low is 06:30 + ..., moved by -400 min; the window starts
            # 10 min before the shifted event, which only the padding of 400 + 60 min reaches.
            offsets = { 'height_adjusted_type' => 'R', 'time_offset_high_min' => 0, 'time_offset_low_min' => -400 }
            lows = ref_events.select { |e| e['type'] == 'low' && e['time'] > t0 + 400 * 60 + 2 * 3600 }
            target = lows.first['time'] - 400 * 60
            got = engine.subordinate_extremes_strict(ref, 2.0, offsets, target - 600, target + 600, astro: astro)
            expect(got.map { |e| [e['time'], e['type']] }).to eq([[target, 'low']])
        end

        it 'raises datum_unavailable for a null chart datum term and invalid_argument for bad offsets' do
            ok = { 'height_adjusted_type' => 'R', 'time_offset_high_min' => 37, 'time_offset_low_min' => 37, 'height_offset_high' => 0.9, 'height_offset_low' => 0.9 }
            expect(code_of { engine.subordinate_extremes_strict(ref, nil, ok, t0, t1, astro: astro) }).to eq('datum_unavailable')
            expect(code_of { engine.subordinate_folded_strict(ref, nil, ok, astro: astro) }).to eq('datum_unavailable')
            expect(code_of { engine.subordinate_extremes_strict(ref, Float::NAN, ok, t0, t1, astro: astro) }).to eq('invalid_argument')
            expect(code_of { engine.subordinate_extremes_strict(ref, 2.0, ok.merge('time_offset_high_min' => '37'), t0, t1, astro: astro) }).to eq('invalid_argument')
            expect(code_of { engine.subordinate_extremes_strict(ref, 2.0, ok.merge('height_offset_low' => Float::NAN), t0, t1, astro: astro) }).to eq('invalid_argument')
            expect(code_of { engine.subordinate_folded_strict(ref, 2.0, ok.merge('time_offset_low_min' => '37'), astro: astro) }).to eq('invalid_argument')
            %w[X r].push(nil).each do |type|
                expect(code_of { engine.subordinate_extremes_strict(ref, 2.0, ok.merge('height_adjusted_type' => type), t0, t1, astro: astro) }).to eq('invalid_argument'), type.inspect
            end
        end

        it 'refuses to fold differing offsets (extremes_only) or a ratio of 0 or less (invalid_argument)' do
            differ = { 'height_adjusted_type' => 'R', 'time_offset_high_min' => 37, 'time_offset_low_min' => 20, 'height_offset_high' => 0.9, 'height_offset_low' => 0.9 }
            expect { engine.subordinate_folded_strict(ref, 2.0, differ, astro: astro) }.to raise_error(Harmonics::Strict::Error, /extremes_only: high and low water offsets differ/)
            [0, -0.5].each do |k|
                equal = { 'height_adjusted_type' => 'R', 'height_offset_high' => k, 'height_offset_low' => k }
                expect(code_of { engine.subordinate_folded_strict(ref, 2.0, equal, astro: astro) }).to eq('invalid_argument'), k.inspect
                # The ratio guard comes before the equal-offsets test.
                expect(code_of { engine.subordinate_folded_strict(ref, 2.0, differ.merge('height_offset_low' => k), astro: astro) }).to eq('invalid_argument'), k.inspect
            end
            # The reference is checked first: a bad reference set wins over a bad ratio.
            zero = { 'height_adjusted_type' => 'R', 'height_offset_high' => 0, 'height_offset_low' => 0 }
            expect(code_of { engine.subordinate_folded_strict([tide('XX9', 1.0, 0.0)], 2.0, zero, astro: astro) }).to eq('unsupported_constituent')
            expect(code_of { engine.subordinate_extremes_strict([tide('XX9', 1.0, 0.0)], 2.0, zero, t0, t1, astro: astro) }).to eq('unsupported_constituent')
            expect(code_of { engine.subordinate_extremes_strict(ref, nil, zero, t0, t1, astro: astro) }).to eq('datum_unavailable')
        end
    end

    describe 'subordinate current stations' do
        let(:astro) { table('S12' => [30.0]) }
        let(:ref) { bin('S12', 1.0, 15.0) }
        let(:t0) { Time.utc(2025, 6, 1) }
        let(:t1) { Time.utc(2025, 6, 3) }
        let(:offset) do
            { 'time_adj_max_flood_min' => 30, 'time_adj_max_ebb_min' => nil, 'time_adj_slack_before_flood_min' => -15,
              'time_adj_slack_before_ebb_min' => 45, 'flood_amp_ratio' => 0.8, 'ebb_amp_ratio' => 0.7,
              'mean_flood_dir_deg' => 12.0, 'mean_ebb_dir_deg' => 190.0 }
        end

        it 'shifts and scales the reference events and drops a type with a null adjustment' do
            got = engine.subordinate_current_events_strict(ref, offset, t0, t1, astro: astro)
            expect(got['omitted_event_types']).to eq(['max_ebb'])
            shift = { 'max_flood' => 30, 'slack_before_flood' => -15, 'slack_before_ebb' => 45 }
            expected = engine.current_events_strict(ref, t0 - 86_400, t1 + 86_400, astro: astro).filter_map do |e|
                next unless shift[e['type']]

                t = e['time'] + 60 * shift[e['type']]
                next unless t >= t0 && t < t1

                [t, e['type'], e['type'] == 'max_flood' ? e['velocity_ms'] * 0.8 : 0.0, e['type'] == 'max_flood' ? 12.0 : nil]
            end.sort_by(&:first)
            expect(got['events'].map { |e| [e['time'], e['type'], e['velocity_ms'], e['direction_deg']] }).to eq(expected)
            expect(expected.size).to be >= 8
        end

        it 'shifts max ebb by its own adjustment, scales it by ebb_amp_ratio and gives the ebb direction' do
            got = engine.subordinate_current_events_strict(ref, offset.merge('time_adj_max_ebb_min' => -20), t0, t1, astro: astro)
            expect(got['omitted_event_types']).to eq([])
            ebbs = engine.current_events_strict(ref, t0 - 86_400, t1 + 86_400, astro: astro).select { |e| e['type'] == 'max_ebb' }
                         .map { |e| [e['time'] - 20 * 60, 'max_ebb', e['velocity_ms'] * 0.7, 190.0] }.select { |t, *| t >= t0 && t < t1 }
            expect(got['events'].select { |e| e['type'] == 'max_ebb' }.map { |e| [e['time'], e['type'], e['velocity_ms'], e['direction_deg']] }).to eq(ebbs)
            expect(ebbs.size).to be >= 3
        end

        it 'lists omitted types in the fixed order' do
            all_null = offset.merge('time_adj_max_flood_min' => nil, 'time_adj_max_ebb_min' => nil, 'time_adj_slack_before_flood_min' => nil, 'time_adj_slack_before_ebb_min' => nil)
            got = engine.subordinate_current_events_strict(ref, all_null, t0, t1, astro: astro)
            expect(got).to eq('events' => [], 'omitted_event_types' => %w[max_flood max_ebb slack_before_flood slack_before_ebb])
            ratios = engine.subordinate_current_events_strict(ref, offset.merge('time_adj_max_ebb_min' => 5, 'ebb_amp_ratio' => nil, 'flood_amp_ratio' => nil), t0, t1, astro: astro)
            expect(ratios['omitted_event_types']).to eq(%w[max_flood max_ebb])
        end

        it 'adds the adjustment to the unrounded reference root and rounds once' do
            fast = table('F' => [5400.0])
            # 1.5 deg/s, phase 30.6: max flood 20.4 s after each grid point.
            got = engine.subordinate_current_events_strict(bin('F', 1.0, 30.6), offset.merge('time_adj_max_flood_min' => 0.005), t2025, t2025 + 60, astro: fast)
            expect(got['events'].select { |e| e['type'] == 'max_flood' }.map { |e| [e['time'] - t2025, e['type']] }).to eq([[21, 'max_flood']])
        end

        it 'finds a reference event more than an hour outside the window that an adjustment moves into it' do
            far = offset.merge('time_adj_max_flood_min' => -400)
            flood = engine.current_events_strict(ref, t0 + 8 * 3600, t1, astro: astro).find { |e| e['type'] == 'max_flood' }
            target = flood['time'] - 400 * 60
            got = engine.subordinate_current_events_strict(ref, far, target - 600, target + 600, astro: astro)
            expect(got['events'].map { |e| [e['time'], e['type']] }).to eq([[target, 'max_flood']])
        end

        it 'raises invalid_argument for a String adjustment or ratio' do
            expect(code_of { engine.subordinate_current_events_strict(ref, offset.merge('time_adj_max_flood_min' => '30'), t0, t1, astro: astro) }).to eq('invalid_argument')
            expect(code_of { engine.subordinate_current_events_strict(ref, offset.merge('flood_amp_ratio' => '0.8'), t0, t1, astro: astro) }).to eq('invalid_argument')
        end
    end

    # An explicit null is accepted exactly where the format 1.0 schema allows null
    # (otc-1.0.schema.json, SHA-256 e27d5f6b...): in current_offset, the four time
    # adjustments and the two amplitude ratios, where null drops that event type.
    # Everywhere else the schema types the field as a number, so null is refused;
    # leaving the key out keeps its documented default.
    describe 'explicit nulls, field by field against the schema' do
        let(:astro) { table('S12' => [30.0]) }
        let(:t0) { Time.utc(2025, 6, 1) }
        let(:t1) { Time.utc(2025, 6, 2) }
        let(:ref) { [tide('S12', 1.0, 15.0)] }
        let(:tide_offsets) do
            { 'height_adjusted_type' => 'R', 'time_offset_high_min' => 10, 'time_offset_low_min' => 10, 'height_offset_high' => 0.9, 'height_offset_low' => 0.9 }
        end
        let(:current_offset) do
            { 'time_adj_max_flood_min' => 30, 'time_adj_max_ebb_min' => 20, 'time_adj_slack_before_flood_min' => -15,
              'time_adj_slack_before_ebb_min' => 45, 'flood_amp_ratio' => 0.8, 'ebb_amp_ratio' => 0.7,
              'mean_flood_dir_deg' => 12.0, 'mean_ebb_dir_deg' => 190.0 }
        end

        %w[mean_major_ms mean_minor_ms mean_flood_dir_deg mean_ebb_dir_deg azimuth_deg].each do |key|
            it "refuses null current_bin #{key} (schema: number)" do
                b = bin('S12', 1.0, 0.0, 'mean_flood_dir_deg' => 0.0, 'mean_ebb_dir_deg' => 180.0).merge(key => nil)
                expect(code_of { engine.currents_strict(b, [t0], astro: astro) }).to eq('invalid_argument')
                expect(code_of { engine.current_events_strict(b, t0, t1, astro: astro) }).to eq('invalid_argument')
            end
        end

        %w[time_offset_high_min time_offset_low_min height_offset_high height_offset_low].each do |key|
            it "refuses null subordinate_offsets #{key} (schema: number)" do
                o = tide_offsets.merge(key => nil)
                expect(code_of { engine.subordinate_extremes_strict(ref, 2.0, o, t0, t1, astro: astro) }).to eq('invalid_argument')
                expect(code_of { engine.subordinate_folded_strict(ref, 2.0, o, astro: astro) }).to eq('invalid_argument')
            end
        end

        %w[mean_flood_dir_deg mean_ebb_dir_deg].each do |key|
            it "refuses null current_offset #{key} (schema: number)" do
                expect(code_of { engine.subordinate_current_events_strict(bin('S12', 1.0, 15.0), current_offset.merge(key => nil), t0, t1, astro: astro) }).to eq('invalid_argument')
            end
        end

        {
            'time_adj_max_flood_min' => 'max_flood', 'time_adj_max_ebb_min' => 'max_ebb',
            'time_adj_slack_before_flood_min' => 'slack_before_flood', 'time_adj_slack_before_ebb_min' => 'slack_before_ebb',
            'flood_amp_ratio' => 'max_flood', 'ebb_amp_ratio' => 'max_ebb'
        }.each do |key, type|
            it "accepts null current_offset #{key} (schema: number or null) and drops #{type}" do
                got = engine.subordinate_current_events_strict(bin('S12', 1.0, 15.0), current_offset.merge(key => nil), t0, t1, astro: astro)
                expect(got['omitted_event_types']).to eq([type])
                expect(got['events'].map { |e| e['type'] }).not_to include(type)
                expect(got['events']).not_to be_empty
            end
        end

        it 'keeps the defaults when the keys are absent' do
            expect(engine.subordinate_extremes_strict(ref, 2.0, { 'height_adjusted_type' => 'R' }, t0, t1, astro: astro).size).to be >= 3
            no_dirs = current_offset.reject { |k, _| k.end_with?('_dir_deg') }
            events = engine.subordinate_current_events_strict(bin('S12', 1.0, 15.0), no_dirs, t0, t1, astro: astro)['events']
            expect(events.map { |e| e['direction_deg'] }.uniq).to eq([nil])
        end
    end

    # The format 1.0 schema's ranges (otc-1.0.schema.json, SHA-256 e27d5f6b...):
    # amplitudes and amplitude ratios have minimum 0; phases and directions
    # (direction_deg) are in [0, 360).  The strict mode refuses what the schema
    # refuses, and accepts the bounds it allows.
    describe 'schema ranges, field by field' do
        let(:astro) { table('S12' => [30.0]) }
        let(:t0) { Time.utc(2025, 6, 1) }
        let(:t1) { Time.utc(2025, 6, 2) }
        let(:base_bin) { bin('S12', 1.0, 15.0, 'mean_flood_dir_deg' => 0.0, 'mean_ebb_dir_deg' => 180.0) }
        let(:current_offset) do
            { 'time_adj_max_flood_min' => 30, 'time_adj_max_ebb_min' => 20, 'time_adj_slack_before_flood_min' => -15,
              'time_adj_slack_before_ebb_min' => 45, 'flood_amp_ratio' => 0.8, 'ebb_amp_ratio' => 0.7,
              'mean_flood_dir_deg' => 12.0, 'mean_ebb_dir_deg' => 190.0 }
        end

        def with_constituent(key, value)
            b = base_bin.dup
            b['constituents'] = [b['constituents'].first.merge(key => value)]
            b
        end

        { 'amplitude_m' => [[-0.1], [0.0]], 'phase_deg' => [[-1.0, 360.0], [0.0, 359.999]] }.each do |key, (bad, good)|
            it "refuses set constituent #{key} #{bad.join(' or ')} and accepts #{good.join(' and ')}" do
                c = tide('S12', 1.0, 15.0)
                bad.each do |v|
                    expect(code_of { engine.predict_strict([c.merge(key => v)], [t0], astro: astro) }).to eq('invalid_argument'), v.inspect
                    expect(code_of { engine.subordinate_extremes_strict([c.merge(key => v)], 2.0, { 'height_adjusted_type' => 'R' }, t0, t1, astro: astro) }).to eq('invalid_argument'), v.inspect
                end
                good.each { |v| expect(engine.predict_strict([c.merge(key => v)], [t0], astro: astro).size).to eq(1) }
            end
        end

        { 'major_amplitude_ms' => [[-0.1], [0.0]], 'minor_amplitude_ms' => [[-0.1], [0.0]],
          'major_phase_deg' => [[-1.0, 360.0], [0.0, 359.999]], 'minor_phase_deg' => [[-1.0, 360.0], [0.0, 359.999]] }.each do |key, (bad, good)|
            it "refuses current constituent #{key} #{bad.join(' or ')} and accepts #{good.join(' and ')}" do
                bad.each do |v|
                    expect(code_of { engine.currents_strict(with_constituent(key, v), [t0], astro: astro) }).to eq('invalid_argument'), v.inspect
                    expect(code_of { engine.current_events_strict(with_constituent(key, v), t0, t1, astro: astro) }).to eq('invalid_argument'), v.inspect
                end
                good.each { |v| expect(engine.currents_strict(with_constituent(key, v), [t0], astro: astro).size).to eq(1) }
            end
        end

        %w[azimuth_deg mean_flood_dir_deg mean_ebb_dir_deg].each do |key|
            it "refuses current_bin #{key} -1 or 360 and accepts 0 and 359.999" do
                [-1.0, 360.0].each do |v|
                    expect(code_of { engine.currents_strict(base_bin.merge(key => v), [t0], astro: astro) }).to eq('invalid_argument'), v.inspect
                    expect(code_of { engine.current_events_strict(base_bin.merge(key => v), t0, t1, astro: astro) }).to eq('invalid_argument'), v.inspect
                end
                [0.0, 359.999].each { |v| expect(engine.currents_strict(base_bin.merge(key => v), [t0], astro: astro).size).to eq(1) }
            end
        end

        %w[mean_flood_dir_deg mean_ebb_dir_deg].each do |key|
            it "refuses current_offset #{key} -1 or 360 and accepts 0 and 359.999" do
                [-1.0, 360.0].each do |v|
                    expect(code_of { engine.subordinate_current_events_strict(base_bin, current_offset.merge(key => v), t0, t1, astro: astro) }).to eq('invalid_argument'), v.inspect
                end
                [0.0, 359.999].each do |v|
                    expect(engine.subordinate_current_events_strict(base_bin, current_offset.merge(key => v), t0, t1, astro: astro)['events']).not_to be_empty
                end
            end
        end

        %w[flood_amp_ratio ebb_amp_ratio].each do |key|
            it "refuses current_offset #{key} -0.1 and accepts 0" do
                expect(code_of { engine.subordinate_current_events_strict(base_bin, current_offset.merge(key => -0.1), t0, t1, astro: astro) }).to eq('invalid_argument')
                expect(engine.subordinate_current_events_strict(base_bin, current_offset.merge(key => 0), t0, t1, astro: astro)['omitted_event_types']).to eq([])
            end
        end
    end

    # Unknown keys: current_bin, current_offset and subordinate_offsets have
    # additionalProperties false in the format 1.0 schema, so a key the schema
    # does not list is refused.  The keys it lists but the strict mode does not
    # read (bin, depth_m, depth_type, reference ids, licence_id) are accepted.
    describe 'unknown keys' do
        let(:astro) { table('S12' => [30.0]) }
        let(:t0) { Time.utc(2025, 6, 1) }
        let(:t1) { Time.utc(2025, 6, 2) }
        let(:full_bin) do
            bin('S12', 1.0, 15.0).merge('bin' => 1, 'depth_m' => nil, 'depth_type' => 'surface', 'mean_flood_dir_deg' => 0.0,
                                        'mean_ebb_dir_deg' => 180.0, 'mean_major_ms' => 0.0, 'mean_minor_ms' => 0.0)
        end
        let(:full_offset) do
            { 'bin' => 1, 'depth_m' => 3.0, 'depth_type' => 'surface', 'reference_station_id' => 'OTC-REF', 'reference_bin' => 1, 'licence_id' => 'x',
              'time_adj_max_flood_min' => 30, 'time_adj_max_ebb_min' => 20, 'time_adj_slack_before_flood_min' => -15,
              'time_adj_slack_before_ebb_min' => 45, 'flood_amp_ratio' => 0.8, 'ebb_amp_ratio' => 0.7,
              'mean_flood_dir_deg' => 12.0, 'mean_ebb_dir_deg' => 190.0 }
        end
        let(:full_tide_offsets) do
            { 'reference_station_id' => 'OTC-REF', 'licence_id' => 'x', 'height_adjusted_type' => 'R', 'time_offset_high_min' => 10,
              'time_offset_low_min' => 10, 'height_offset_high' => 0.9, 'height_offset_low' => 0.9 }
        end

        it 'accepts every key the schema lists' do
            expect(engine.currents_strict(full_bin, [t0], astro: astro).size).to eq(1)
            expect(engine.subordinate_current_events_strict(full_bin, full_offset, t0, t1, astro: astro)['events']).not_to be_empty
            expect(engine.subordinate_extremes_strict([tide('S12', 1.0, 15.0)], 2.0, full_tide_offsets, t0, t1, astro: astro)).not_to be_empty
            # datum: added to subordinate_offsets by spec revision 20, not read here.
            with_datum = full_tide_offsets.merge('datum' => { 'named' => { 'mllw' => 0.0 }, 'chart_datum' => 'mllw' })
            expect(engine.subordinate_extremes_strict([tide('S12', 1.0, 15.0)], 2.0, with_datum, t0, t1, astro: astro)).not_to be_empty
        end

        it 'refuses an unknown current_bin key' do
            b = full_bin.merge('mean_major' => 0.5)
            expect(code_of { engine.currents_strict(b, [t0], astro: astro) }).to eq('invalid_argument')
            expect(code_of { engine.current_events_strict(b, t0, t1, astro: astro) }).to eq('invalid_argument')
            expect(code_of { engine.subordinate_current_events_strict(b, full_offset, t0, t1, astro: astro) }).to eq('invalid_argument')
        end

        it 'refuses an unknown current_offset key' do
            expect(code_of { engine.subordinate_current_events_strict(full_bin, full_offset.merge('time_adj_max_flood' => 30), t0, t1, astro: astro) }).to eq('invalid_argument')
        end

        it 'refuses an unknown subordinate_offsets key' do
            o = full_tide_offsets.merge('time_offset_high' => 10)
            expect(code_of { engine.subordinate_extremes_strict([tide('S12', 1.0, 15.0)], 2.0, o, t0, t1, astro: astro) }).to eq('invalid_argument')
            expect(code_of { engine.subordinate_folded_strict([tide('S12', 1.0, 15.0)], 2.0, o, astro: astro) }).to eq('invalid_argument')
        end

        it 'moves subordinate heights to another datum by datum_shift (section 4.8 step 5)' do
            ref_c = [tide('S12', 1.0, 15.0)]
            chart = engine.subordinate_extremes_strict(ref_c, 2.0, full_tide_offsets, t0, t1, astro: astro)
            shifted = engine.subordinate_extremes_strict(ref_c, 2.0, full_tide_offsets, t0, t1, datum_shift: 0.35, astro: astro)
            expect(shifted.map { |e| [e['time'], e['type']] }).to eq(chart.map { |e| [e['time'], e['type']] })
            shifted.zip(chart).each { |s, c| expect(s['height']).to be_within(1e-12).of(c['height'] + 0.35) }

            folded = engine.subordinate_folded_strict(ref_c, 2.0, full_tide_offsets, astro: astro)
            folded_shift = engine.subordinate_folded_strict(ref_c, 2.0, full_tide_offsets, datum_shift: -0.2, astro: astro)
            expect(folded_shift['datum_term']).to be_within(1e-12).of(folded['datum_term'] - 0.2)
            expect(folded_shift['constituents']).to eq(folded['constituents'])

            [nil, '0.35', Float::NAN].each do |bad|
                expect(code_of { engine.subordinate_extremes_strict(ref_c, 2.0, full_tide_offsets, t0, t1, datum_shift: bad, astro: astro) }).to eq('invalid_argument'), bad.inspect
                expect(code_of { engine.subordinate_folded_strict(ref_c, 2.0, full_tide_offsets, datum_shift: bad, astro: astro) }).to eq('invalid_argument'), bad.inspect
            end
        end

        it 'refuses a height ratio of 0 or less for the event method' do
            [0, -0.5].each do |k|
                %w[height_offset_high height_offset_low].each do |key|
                    o = full_tide_offsets.merge(key => k)
                    expect(code_of { engine.subordinate_extremes_strict([tide('S12', 1.0, 15.0)], 2.0, o, t0, t1, astro: astro) }).to eq('invalid_argument'), "#{key} #{k}"
                end
            end
            # An additive offset may be 0 or negative.
            a = full_tide_offsets.merge('height_adjusted_type' => 'A', 'height_offset_high' => 0, 'height_offset_low' => -0.5)
            expect(engine.subordinate_extremes_strict([tide('S12', 1.0, 15.0)], 2.0, a, t0, t1, astro: astro)).not_to be_empty
        end
    end

    describe 'with the shipped TCD' do
        # M2 alone: |h'(a)| + |h'(b)| equals M*(b - a) to first order at a root,
        # so the root-free test needs a margin for rounding (spec revision 20).
        # The counts are checked against a brute-force 5 s sign scan of the same
        # function.
        describe 'a single-constituent set (M2 alone, 30 days)' do
            let(:t0) { Time.utc(2026, 10, 1) }
            let(:t1) { Time.utc(2026, 10, 31) }
            let(:m2_bin) { bin('M2', 1.0, 100.0) }

            def scan(f)
                year = 2026
                times = (t0.to_i..t1.to_i).step(5).to_a
                values = times.map { |t| f.call(t.to_f, year) }
                (1...times.size).filter_map do |i|
                    if values[i - 1].positive? && values[i] <= 0 then [times[i], :down]
                    elsif values[i - 1].negative? && values[i] >= 0 then [times[i], :up]
                    end
                end
            end

            def match(events, expected, types)
                expect(events.size).to eq(expected.size)
                events.zip(expected).each do |e, (t, kind)|
                    expect(e['type']).to eq(types.fetch(kind))
                    expect((e['time'].to_i - t).abs).to be <= 5
                end
            end

            it 'finds every extremum' do
                sum = Harmonics::Strict.tide_sum(engine.strict_astro_table, [tide('M2', 1.0, 100.0)])
                expected = scan(->(t, y) { sum.derivative(t, y) })
                expect(expected.size).to be >= 115
                match(engine.extremes_strict([tide('M2', 1.0, 100.0)], t0, t1), expected, { down: 'high', up: 'low' })
            end

            it 'finds every max flood, max ebb and slack' do
                sum = Harmonics::Strict.tide_sum(engine.strict_astro_table, [tide('M2', 1.0, 100.0)])
                events = engine.current_events_strict(m2_bin, t0, t1)
                peaks = scan(->(t, y) { sum.derivative(t, y) })
                slacks = scan(->(t, y) { sum.value(t, y) })
                expect(peaks.size).to be >= 115
                expect(slacks.size).to be >= 115
                match(events.reject { |e| e['type'].start_with?('slack') }, peaks, { down: 'max_flood', up: 'max_ebb' })
                match(events.select { |e| e['type'].start_with?('slack') }, slacks, { down: 'slack_before_ebb', up: 'slack_before_flood' })
            end
        end

        # Large amplitudes late in the year: rounding in the phase argument
        # omega*Delta gives an evaluation error that grows with the amplitude and
        # with Delta (hours since 1 January), so the margin includes the bound
        # E of spec revision 20.  These cases dropped events before it.
        describe 'large amplitudes late in the year (spec revision 20)' do
            def scan_window(f, from, to)
                times = (from.to_i..to.to_i).step(5).to_a
                values = times.map { |t| f.call(t.to_f, Harmonics::Strict.year_of(t)) }
                (1...times.size).filter_map do |i|
                    if values[i - 1].positive? && values[i] <= 0 then [times[i], :down]
                    elsif values[i - 1].negative? && values[i] >= 0 then [times[i], :up]
                    end
                end
            end

            def match_scan(events, expected, types, label)
                expect(events.map { |e| e['type'] }).to eq(expected.map { |_, kind| types.fetch(kind) }), label
                events.zip(expected).each { |e, (t, _)| expect((e['time'].to_i - t).abs).to be <= 5, label }
            end

            [
                ['M2', 5.0, Time.utc(2099, 12, 20), Time.utc(2099, 12, 25)],
                ['M2', 7.5, Time.utc(2099, 12, 20), Time.utc(2099, 12, 25)],
                ['M2', 5.0, Time.utc(2025, 12, 22), Time.utc(2025, 12, 26)],
                ['M2', 7.5, Time.utc(2025, 12, 22), Time.utc(2025, 12, 26)],
                ['K1', 40.0, Time.utc(2025, 12, 22), Time.utc(2025, 12, 26)]
            ].each do |name, amp, from, to|
                label = "#{name} #{amp} m from #{from.strftime('%Y-%m-%d')} to #{to.strftime('%Y-%m-%d')}"

                it "finds every extremum, max flood, max ebb and slack for #{label}" do
                    c = tide(name, amp, 37.0)
                    sum = Harmonics::Strict.tide_sum(engine.strict_astro_table, [c])
                    deriv = scan_window(->(t, y) { sum.derivative(t, y) }, from, to)
                    zeros = scan_window(->(t, y) { sum.value(t, y) }, from, to)
                    match_scan(engine.extremes_strict([c], from, to), deriv, { down: 'high', up: 'low' }, "extremes, #{label}")
                    events = engine.current_events_strict(bin(name, amp, 37.0), from, to)
                    match_scan(events.reject { |e| e['type'].start_with?('slack') }, deriv, { down: 'max_flood', up: 'max_ebb' }, "peaks, #{label}")
                    match_scan(events.select { |e| e['type'].start_with?('slack') }, zeros, { down: 'slack_before_ebb', up: 'slack_before_flood' }, "slacks, #{label}")
                end
            end
        end

        let(:m2) { tide('M2', 1.0, 100.0) }
        let(:t) { Time.utc(2026, 10, 9, 12) }

        it 'raises for an unknown constituent and for 2101' do
            expect(code_of { engine.predict_strict([m2, tide('XX9', 0.5, 10.0)], [t]) }).to eq('unsupported_constituent')
            expect(code_of { engine.predict_strict([m2], [Time.utc(2101, 6, 1)]) }).to eq('time_out_of_range')
        end

        it 'takes the speed from the TCD, never from the set' do
            times = [t, t + 200 * 86_400]
            off = engine.predict_strict([m2.merge('speed_deg_per_hour' => engine.strict_astro_table.row('M2').speed + 1e-7)], times)
            expect(off).to eq(engine.predict_strict([m2], times))
        end

        it 'computes U, V, sigma and direction of a current bin as by hand, at two instants' do
            b = { 'azimuth_deg' => 30.0, 'mean_flood_dir_deg' => 210.0, 'mean_major_ms' => 0.1, 'mean_minor_ms' => -0.02,
                  'constituents' => [{ 'name' => 'M2', 'major_amplitude_ms' => 1.0, 'major_phase_deg' => 40.0,
                                       'minor_amplitude_ms' => 0.2, 'minor_phase_deg' => 130.0 }] }
            m2_row = TCD.open(engine.xtide_file) { |db| db.constituents.find('M2') }
            [Time.utc(2026, 10, 9, 12), Time.utc(2027, 3, 1, 6, 30)].each do |at|
                i = at.year - 1700
                d = (at - Time.utc(at.year)) / 3600.0
                arg = ->(g) { (m2_row.speed * d + m2_row.equilibrium[i] - g) * Math::PI / 180 }
                u = 0.1 + m2_row.node_factors[i] * Math.cos(arg.call(40.0))
                v = -0.02 + m2_row.node_factors[i] * 0.2 * Math.cos(arg.call(130.0))
                e = u * Math.sin(Math::PI / 6) + v * Math.sin(2 * Math::PI / 3)
                n = u * Math.cos(Math::PI / 6) + v * Math.cos(2 * Math::PI / 3)
                r = engine.currents_strict(b, [at]).first
                expect(r['velocity_major_ms']).to be_within(1e-9).of(-u) # sigma = -1: the azimuth is the ebb
                expect(r['velocity_minor_ms']).to be_within(1e-9).of(-v)
                expect(r['speed_ms']).to be_within(1e-9).of(Math.hypot(e, n))
                expect(r['direction_deg']).to be_within(1e-7).of(Math.atan2(e, n) * 180 / Math::PI % 360)
            end
        end

        it "matches NOAA's Boston high and low water from NOAA's constants" do
            noaa = JSON.parse(File.read(File.expand_path('../../fixtures/harmonics/noaa_8443970_boston.json', __dir__)))
            names = { 'LAM2' => 'LDA2', 'RHO' => 'RHO1', 'SIGMA1' => 'SIG1' }
            consts = noaa['harcon'].map { |n, a, ph| tide(names[n] || n, a, ph) }
            consts.select! { |c| (engine.strict_astro_table.row(c['name']) rescue nil) }
            ours = engine.extremes_strict(consts, Time.utc(2026, 11, 1), Time.utc(2027, 1, 1))
            expect(ours.size).to eq(noaa['hilo'].size)
            errs = noaa['hilo'].zip(ours).map do |(time, v, ty), o|
                expect(o['type']).to eq(ty == 'H' ? 'high' : 'low')
                [(o['time'] - Time.parse("#{time} UTC")).abs / 60.0, (o['height'] - v).abs]
            end
            expect(errs.sum(&:first) / errs.size).to be <= 0.5
            expect(errs.map(&:first).max).to be <= 2.0
            expect(errs.map(&:last).max).to be <= 0.02
        end

        context 'with the station list' do
            before(:all) do
                @dir = Dir.mktmpdir
                @engine = Harmonics::Engine.new(Logger.new('/dev/null'), @dir)
                @engine.stations
            end

            after(:all) { FileUtils.rm_rf(@dir) }

            def otc(id)
                d = @engine.station_data(id, 'tide')
                [d['constituents'].map { |c| tide(c['name'], c['amp'], c['phase']) }, d['datum_offset']]
            end

            it 'gives the default heights at a reference station once the TCD datum offset is added' do
                id = @engine.stations.find { |s| s['type'] == 'tide' && s['name'].start_with?('Boston, Boston Harbor') }['id']
                consts, z0 = otc(id)
                series = @engine.generate_predictions(id, Time.utc(2026, 12, 31, 18), Time.utc(2027, 1, 1, 6), type: 'tide', step_seconds: 1800)
                strict = @engine.predict_strict(consts, series.map { |p| p['time'] })
                series.zip(strict).each { |p, h| expect(h + z0).to be_within(1e-9).of(p['height']) }
            end

            # Richardson Hammock, St. Joseph Bay: a high and a low 15 minutes
            # apart, both of which the default 15-minute search misses.
            it 'finds both extremes of a close pair' do
                consts, z0 = otc('X11dcf24')
                ev = @engine.extremes_strict(consts, Time.utc(2026, 10, 9, 23), Time.utc(2026, 10, 10, 1), datum_term: z0)
                expect(ev.map { |e| [e['time'].iso8601, e['type']] }).to eq([['2026-10-10T00:06:48Z', 'high'], ['2026-10-10T00:21:51Z', 'low']])
            end

            # The default search reports a high and a low within 30 s of
            # 2053-01-01T00:00Z here: the table jump read as a pair of extremes.
            it 'reports no extremes at the New Year table jump when the tide rises through it' do
                consts, z0 = otc('X11dcf24')
                ev = @engine.extremes_strict(consts, Time.utc(2052, 12, 31, 21), Time.utc(2053, 1, 1, 3), datum_term: z0)
                expect(ev.map { |e| [e['time'].iso8601, e['type']] }).to eq([['2053-01-01T00:17:22Z', 'high']])
            end
        end
    end
end

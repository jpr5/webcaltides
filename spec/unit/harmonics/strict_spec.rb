# frozen_string_literal: true

require 'tmpdir'

# The strict engine mode (OTC SDK prediction spec, revision 11, sections 4 and 6.1).
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

        it 'reports a high, a low and a high inside one step, in order' do
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
            ev = engine.extremes_strict([tide('D1', 1.0, 0.0)], new_year - 6 * 3600, new_year + 6 * 3600, astro: diurnal(357.5, 2.5))
            expect(events(ev)).to eq([['2021-01-01T00:00:00Z', 'high', Math.cos(2.5 * Math::PI / 180).round(9)]])
        end

        it 'keeps one high when each table puts it on its own side of New Year (doubled event)' do
            # Old table: high 10 min before T. New table: high 8 min after T.
            higher_new = engine.extremes_strict([tide('D1', 1.0, 0.0)], new_year - 6 * 3600, new_year + 6 * 3600, astro: diurnal(2.5, 358.0, 1.0, 1.01))
            expect(events(higher_new)).to eq([['2021-01-01T00:08:00Z', 'high', 1.01]])
            equal = engine.extremes_strict([tide('D1', 1.0, 0.0)], new_year - 6 * 3600, new_year + 6 * 3600, astro: diurnal(2.5, 358.0))
            expect(events(equal)).to eq([['2020-12-31T23:50:00Z', 'high', 1.0]])
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

        it 'refuses to fold differing offsets or a ratio that is not positive' do
            differ = { 'height_adjusted_type' => 'R', 'time_offset_high_min' => 37, 'time_offset_low_min' => 20, 'height_offset_high' => 0.9, 'height_offset_low' => 0.9 }
            expect { engine.subordinate_folded_strict(ref, 2.0, differ, astro: astro) }.to raise_error(Harmonics::Strict::Error, /extremes_only: high and low water offsets differ/)
            zero = { 'height_adjusted_type' => 'R', 'height_offset_high' => 0, 'height_offset_low' => 0 }
            expect { engine.subordinate_folded_strict(ref, 2.0, zero, astro: astro) }.to raise_error(Harmonics::Strict::Error, /extremes_only: the height ratio is not positive/)
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
    end

    describe 'with the shipped TCD' do
        let(:m2) { tide('M2', 1.0, 100.0) }
        let(:t) { Time.utc(2026, 10, 9, 12) }

        it 'raises for an unknown constituent and for 2101, where the default path returns a number' do
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
            # apart, which the default 15-minute search reports as one event.
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

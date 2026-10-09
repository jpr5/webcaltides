# frozen_string_literal: true

require 'tmpdir'

# The default engine's output on a fixed set of stations and windows, checked in
# at spec/fixtures/harmonics/default_golden.json.  The strict mode
# (lib/harmonics_strict.rb) must not change it.  The fixture was written by the
# code at master 05076b4, before the strict mode existed.  To write it again
# (only when the default output is meant to change):
#
#   GOLDEN_WRITE=1 bundle exec rspec spec/unit/harmonics/default_golden_spec.rb
RSpec.describe Harmonics::Engine, 'default output golden' do
    GOLDEN_FIXTURE = File.expand_path('../../fixtures/harmonics/default_golden.json', __dir__)

    # Tide references (TICON, XTide), tide subordinates, current references and
    # current subordinates, plus two stations with close extremes and two
    # subordinate tides with a level add (Newark Slough X373345f, +2.6/+0.1 ft;
    # Ano Nuevo Island X8e55f8e, -0.7/-0.1 ft), so that dropping the add fails.
    GOLDEN_STATIONS = {
        'tide' => %w[T0009262 X3d9cbc0 X000dc3f X7ea97a3 X11dcf24 X3539388 X373345f X8e55f8e],
        'current' => %w[X0031733_10 X52d69cd_14 X000c846_14 X8399992_15]
    }.freeze

    # A window in October 2026 and one across the New Year 2052 -> 2053.
    GOLDEN_WINDOWS = [
        [Time.utc(2026, 10, 1), Time.utc(2026, 10, 4)],
        [Time.utc(2052, 12, 31), Time.utc(2053, 1, 2)]
    ].freeze

    let(:fmt) { ->(t) { t.utc.strftime('%Y-%m-%dT%H:%M:%S.%N') } }

    def output(engine)
        GOLDEN_STATIONS.flat_map do |type, ids|
            ids.flat_map do |id|
                GOLDEN_WINDOWS.map do |t0, t1|
                    series = engine.generate_predictions(id, t0, t1, type: type, step_seconds: 3 * 3600)
                    peaks = engine.generate_peaks_optimized(id, t0, t1, type: type)
                    ["#{type}:#{id}:#{t0.year}", {
                        'series' => series.map { |p| [fmt[p['time']], p['height'], p['units']] },
                        'peaks' => peaks.map { |p| [fmt[p['time']], p['type'], p['height'], p['units']] }
                    }]
                end
            end
        end.to_h
    end

    # Peak times come from a parabola through three samples and peak heights are
    # rounded to 3 decimals, so the last bits of libm (which differ between macOS
    # and Linux) can move a time by nanoseconds or flip a rounding.  Peaks are
    # compared to 1 ms and to one rounding unit; types, units and counts exactly.
    PEAK_TIME_S = 0.001
    PEAK_HEIGHT = 0.0011

    it 'matches the checked-in output (peak types exactly, times to 1 ms, heights to 1e-9)' do
        got = Dir.mktmpdir { |dir| output(described_class.new(Logger.new('/dev/null'), dir)) }
        if ENV['GOLDEN_WRITE']
            File.write(GOLDEN_FIXTURE, JSON.pretty_generate(got) + "\n")
            skip "wrote #{GOLDEN_FIXTURE}"
        end

        golden = JSON.parse(File.read(GOLDEN_FIXTURE))
        expect(got.keys).to eq(golden.keys)
        golden.each do |key, g|
            expect(got[key]['peaks'].size).to eq(g['peaks'].size), "peak count differs for #{key}"
            got[key]['peaks'].zip(g['peaks']).each do |(t, type, h, u), (gt, gtype, gh, gu)|
                expect([type, u]).to eq([gtype, gu]), "peak type or units differ for #{key} at #{gt}"
                expect(Time.parse("#{t} UTC")).to be_within(PEAK_TIME_S).of(Time.parse("#{gt} UTC")), "peak time differs for #{key} at #{gt}"
                expect(h).to be_within(PEAK_HEIGHT).of(gh), "peak height differs for #{key} at #{gt}"
            end
            expect(got[key]['series'].size).to eq(g['series'].size), "series size differs for #{key}"
            got[key]['series'].zip(g['series']).each do |(t, h, u), (gt, gh, gu)|
                expect([t, u]).to eq([gt, gu]), "series time or units differ for #{key}"
                expect(h).to be_within(1e-9).of(gh), "height differs for #{key} at #{t}"
            end
            expect(g['peaks']).not_to be_empty, "no peaks in the fixture for #{key}"
        end
    end
end

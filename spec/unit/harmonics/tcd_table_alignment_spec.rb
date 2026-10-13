# frozen_string_literal: true

require 'tmpdir'

# A TCD whose constituent count is not a multiple of 8. libtcd starts each
# constituent's equilibrium and node factor table on a byte boundary; tcd 1.0.x
# did not, so with such a count every table after the first was read at the
# wrong bits. On the 178-constituent OTC TCD the engine got 175 of 178
# equilibrium arguments and all 178 node factors for 2026 wrong (M2 f 2.9782,
# libtcd 0.9674). tcd 1.1 reads them as libtcd does. The production TCD has 176
# constituents, so it was not affected.
#
# The fixture (9 constituents, years 2026-2027, one reference station) was
# written by TCD::Writer from the values below; the last example checks that.
RSpec.describe Harmonics::Engine, 'TCD with a constituent count that is not a multiple of 8' do
    let(:fixtures) { File.expand_path('../../fixtures/harmonics', __dir__) }
    let(:fixture_tcd) { File.join(fixtures, 'nine-constituents.tcd') }
    let(:start_year) { 2026 }
    let(:speeds) do
        { 'M2' => 28.9841042, 'S2' => 30.0, 'N2' => 28.4397295, 'K2' => 30.0821373, 'K1' => 15.0410686,
          'O1' => 13.9430356, 'P1' => 14.9589314, 'Q1' => 13.3986609, 'MA2' => 28.9430356 }
    end
    # Distinct for every constituent and year, and exact at the file's
    # precision (0.01 degree, 0.0001).
    let(:equilibrium) { ->(i, y) { (i * 37.25 + y * 101.5) % 360 } }
    let(:node_factor) { ->(i, y) { 0.9 + i * 0.0125 + y * 0.05 } }

    def build_fixture(path)
        constituents = speeds.each_with_index.map do |(name, speed), i|
            { name: name, speed: speed,
              equilibrium: [0, 1].map { |y| equilibrium[i, y] },
              node_factors: [0, 1].map { |y| node_factor[i, y] } }
        end
        writer = TCD::Writer.new(constituents: constituents, start_year: start_year,
                                 last_modified: '2026-10-12 00:00 UTC')
        writer.add_station(name: 'Nine Constituent Test', latitude: 42.35, longitude: -71.05,
                           tzfile: 'America/New_York', level_units: 'feet',
                           amplitudes: Array.new(speeds.size) { |i| 0.1 * (i + 1) },
                           epochs: Array.new(speeds.size) { |i| 10.0 * i })
        writer.write(path)
    end

    around do |ex|
        saved = %w[XTIDE_FILE TICON_FILE HARMONICS_NODAL].to_h { |k| [k, ENV[k]] }
        ENV['XTIDE_FILE'] = fixture_tcd
        ENV['TICON_FILE'] = File.join(fixtures, 'test-ticon.json')
        ENV.delete('HARMONICS_NODAL')
        ex.run
    ensure
        saved.each { |k, v| v.nil? ? ENV.delete(k) : ENV[k] = v }
    end

    it 'gives back every constituent\'s equilibrium argument and node factor for each year' do
        Dir.mktmpdir do |cache|
            engine = described_class.new(Logger.new(nil), cache)
            expect(engine.send(:tcd_nodal_table)['years']).to eq(2026..2027)

            [0, 1].each do |y|
                nodal = engine.send(:get_nodal_factors, start_year + y, 1, 1, 0.0)
                expect(nodal.keys).to match_array(speeds.keys)
                speeds.each_key.with_index do |name, i|
                    year = start_year + y
                    expect(nodal[name]['V0']).to be_within(1e-9).of(equilibrium[i, y]),
                                                 "#{name} #{year} V0: read #{nodal[name]['V0']}, written #{equilibrium[i, y]}"
                    expect(nodal[name]['f']).to be_within(1e-9).of(node_factor[i, y]),
                                                "#{name} #{year} f: read #{nodal[name]['f']}, written #{node_factor[i, y]}"
                end
            end
        end
    end

    it 'has a fixture that TCD::Writer writes from these values' do
        Dir.mktmpdir do |dir|
            path = File.join(dir, 'nine.tcd')
            build_fixture(path)
            expect(File.binread(path)).to eq(File.binread(fixture_tcd))
        end
    end
end

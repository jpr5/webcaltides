# frozen_string_literal: true

require 'tmpdir'

# An XTide reference tide station and its "(sub)" twin at the same point have the same id.  The
# engine keeps the twin that matches how NOAA predicts that station (data/noaa_station_types.json):
# the reference's harmonics for a harmonic station, the "(sub)" offsets for a subordinate one.
RSpec.describe Harmonics::Engine, 'XTide reference/"(sub)" twins' do
    let(:logger) { Logger.new('/dev/null') }

    # NOAA hilo predictions (MLLW, feet, GMT) for 2026-10-01 and 2026-10-02.
    NAKCHAMIK_NOAA = [
        ['2026-10-01 01:08', 'High', 9.955], ['2026-10-01 08:04', 'Low', -0.877],
        ['2026-10-01 14:31', 'High', 7.080], ['2026-10-01 19:35', 'Low', 3.267],
        ['2026-10-02 01:51', 'High', 9.650], ['2026-10-02 08:59', 'Low', -0.421],
        ['2026-10-02 15:32', 'High', 6.396], ['2026-10-02 20:18', 'Low', 3.900]
    ].freeze
    SEA_BRIGHT_NOAA = [
        ['2026-10-01 04:38', 'High', 3.218], ['2026-10-01 10:31', 'Low', 0.248],
        ['2026-10-01 16:59', 'High', 3.849], ['2026-10-01 23:31', 'Low', 0.409],
        ['2026-10-02 05:42', 'High', 3.079], ['2026-10-02 11:24', 'Low', 0.433],
        ['2026-10-02 18:03', 'High', 3.728]
    ].freeze

    # Worst [minutes, feet] error of the engine's events against NOAA's.
    def worst_error(engine, id, noaa)
        ours = engine.generate_peaks_optimized(id, Time.utc(2026, 10, 1), Time.utc(2026, 10, 3), type: 'tide')
        noaa.map do |t, type, height|
            t = Time.strptime("#{t} +0000", '%Y-%m-%d %H:%M %z')
            m = ours.select { |o| o['type'] == type }.min_by { |o| (o['time'] - t).abs }
            [((m['time'] - t) / 60.0).abs, (m['height'] - height).abs]
        end.transpose.map(&:max)
    end

    context 'with the shipped TCD and NOAA station types' do
        before(:all) do
            @dir = Dir.mktmpdir
            @engine = Harmonics::Engine.new(Logger.new('/dev/null'), @dir)
            @engine.stations
        end

        after(:all) { FileUtils.rm_rf(@dir) }

        # NOAA predicts these from harmonics.
        %w[Xe1d6ce3 Xcb0d27a Xccaf9cd X0a0cf37 Xfc04dc7 X360c83b].each do |id|
            it "serves the reference station for #{id}, a NOAA harmonic station" do
                data = @engine.station_data(id, 'tide')
                expect(data['ref_key']).to be_nil
                expect(data['constituents']).not_to be_empty
                expect(data['name']).not_to include('(sub)')
            end
        end

        it 'serves the "(sub)" for Sea Bright (Xc7078fe), a NOAA subordinate station' do
            data = @engine.station_data('Xc7078fe', 'tide')
            expect(data['ref_key']).to eq('X65b1301')
            expect(data['name']).to end_with('(sub)')
        end

        it 'predicts Nakchamik Island (Xcb0d27a) within 5 min and 0.1 ft of NOAA 9458779' do
            dt, dh = worst_error(@engine, 'Xcb0d27a', NAKCHAMIK_NOAA)
            expect(dt).to be < 5
            expect(dh).to be < 0.1
        end

        it 'still predicts Sea Bright (Xc7078fe) within 5 min and 0.05 ft of NOAA 8531804' do
            dt, dh = worst_error(@engine, 'Xc7078fe', SEA_BRIGHT_NOAA)
            expect(dt).to be < 5
            expect(dh).to be < 0.05
        end
    end

    describe '#store_station_data' do
        let(:engine) { described_class.new(logger, Dir.mktmpdir) }
        let(:ref) { { 'name' => 'Point', 'type' => 'tide', 'ref_key' => nil, 'constituents' => [{ 'name' => 'M2' }] } }
        let(:sub) { { 'name' => 'Point (sub)', 'type' => 'tide', 'ref_key' => 'XREF', 'constituents' => [] } }
        let(:current) { { 'name' => 'Point current', 'type' => 'current', 'ref_key' => nil, 'constituents' => [] } }

        before { engine.instance_variable_set(:@noaa_station_types, { '111' => 'harmonic', '222' => 'subordinate' }) }

        def store(*entries, noaa_id:)
            entries.each { |e| engine.send(:store_station_data, 'XID', e, noaa_id: noaa_id) }
            engine.stations_cache['XID']
        end

        it 'keeps the reference of a NOAA harmonic station, whichever is parsed first' do
            expect(store(ref, sub, noaa_id: '111')).to eq(ref)
            engine.stations_cache.clear
            expect(store(sub, ref, noaa_id: '111')).to eq(ref)
        end

        it 'keeps the "(sub)" of a NOAA subordinate station, whichever is parsed first' do
            expect(store(ref, sub, noaa_id: '222')).to eq(sub)
            engine.stations_cache.clear
            expect(store(sub, ref, noaa_id: '222')).to eq(sub)
        end

        it 'keeps the one parsed later for a station NOAA does not list, or with no NOAA id' do
            expect(store(ref, sub, noaa_id: '333')).to eq(sub)
            engine.stations_cache.clear
            expect(store(ref, sub, noaa_id: nil)).to eq(sub)
        end

        it 'still stores a station of the other type under its typed key' do
            store(ref, current, sub, noaa_id: '111')
            expect(engine.stations_cache['XID']).to eq(ref)
            expect(engine.stations_cache['XID@current']).to eq(current)
        end
    end

    describe '#noaa_station_types' do
        it 'is empty, and the later twin is kept, when the file is missing' do
            engine = described_class.new(logger, Dir.mktmpdir)
            engine.instance_variable_set(:@noaa_station_types_file, '/nonexistent/noaa_station_types.json')
            expect(engine.send(:noaa_station_types)).to eq({})
        end

        it 'is empty when the file does not hold an object' do
            Dir.mktmpdir do |dir|
                File.write("#{dir}/types.json", '[]')
                engine = described_class.new(logger, dir)
                engine.instance_variable_set(:@noaa_station_types_file, "#{dir}/types.json")
                expect(engine.send(:noaa_station_types)).to eq({})
            end
        end
    end

    describe '#source_files_checksum' do
        it 'changes with the NOAA station types file, so the station caches are built again' do
            Dir.mktmpdir do |dir|
                File.write("#{dir}/a.json", '{"1": "harmonic"}')
                File.write("#{dir}/b.json", '{"1": "subordinate"}')
                sums = %w[a b].map do |f|
                    engine = described_class.new(logger, dir)
                    engine.instance_variable_set(:@noaa_station_types_file, "#{dir}/#{f}.json")
                    engine.source_files_checksum
                end
                expect(sums.uniq.size).to eq(2)
                expect(sums.map { |s| s.split('_').last }.uniq.size).to eq(1)
            end
        end
    end
end

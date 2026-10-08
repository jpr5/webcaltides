# frozen_string_literal: true

# The quarterly tide and current station lists hold station records from the harmonics engine.  When
# the engine changes those records (Harmonics::Engine::CACHE_VERSION), a list cached by the older code
# must not be served for the rest of the quarter: its file name changes, so it is built again.
RSpec.describe 'Station lists and the harmonics station cache version', :aggregate_failures do
    let(:stale) { build_station(id: 'XOLD', bid: 'XOLD', public_id: 'XOLD', name: 'Old Current', provider: 'xtide', depth: 0.0) }
    let(:fresh) { build_station(id: 'XOLD', bid: 'XOLD', public_id: 'XOLD', name: 'Old Current', provider: 'xtide', depth: nil) }
    let(:xtide) { double('xtide', current_stations: [fresh], tide_stations: [fresh]) }

    around do |example|
        with_test_cache_dir do |dir|
            %i[@current_stations @tide_stations @tide_stations_retry_at].each { |v| WebCalTides.instance_variable_set(v, nil) }
            example.run
        ensure
            %i[@current_stations @tide_stations @tide_stations_retry_at].each { |v| WebCalTides.instance_variable_set(v, nil) }
        end
    end

    before do
        allow(WebCalTides).to receive(:harmonics_checksum).and_return('cafebabe')
        allow(WebCalTides).to receive(:current_clients) { |provider = nil| provider ? xtide : { xtide: xtide } }
        allow(WebCalTides).to receive(:tide_clients) { |provider = nil| provider ? xtide : { xtide: xtide } }
    end

    # Writes a list as the older engine version's code would have cached it.
    def cache_with_older_engine(file_method)
        current = Harmonics::Engine::CACHE_VERSION
        stub_const('Harmonics::Engine::CACHE_VERSION', current - 1)
        old_file = WebCalTides.public_send(file_method)
        File.write(old_file, [stale.to_h].to_json)
        stub_const('Harmonics::Engine::CACHE_VERSION', current)
        old_file
    end

    it 'builds the current station list again instead of serving one cached by an older engine version' do
        old_file = cache_with_older_engine(:current_station_cache_file)

        expect(WebCalTides.current_station_cache_file).not_to eq(old_file)
        expect(WebCalTides.current_stations.map(&:depth)).to eq([nil])
    end

    it 'builds the tide station list again instead of serving one cached by an older engine version' do
        old_file = cache_with_older_engine(:tide_station_cache_file)

        expect(WebCalTides.tide_station_cache_file).not_to eq(old_file)
        expect(WebCalTides.tide_stations.map(&:depth)).to eq([nil])
    end
end

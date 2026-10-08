# frozen_string_literal: true

RSpec.describe 'Current station cache', :aggregate_failures do
    # Regions other than 'United States' skip the NOAA region enrichment (which needs tide stations)
    let(:noaa_station)  { build_station(id: 'BOS1101', bid: 'BOS1101_1', public_id: 'BOS1101', name: 'Boston Harbor', region: 'Massachusetts', provider: 'noaa') }
    let(:xtide_station) { build_station(id: 'XT1', bid: 'XT1', public_id: 'XT1', name: 'Cape Cod Canal', region: 'Massachusetts', provider: 'xtide') }

    let(:noaa)  { double('noaa', current_stations: [noaa_station]) }
    let(:xtide) { double('xtide', current_stations: [xtide_station]) }
    let(:clients) { { noaa: noaa, xtide: xtide } }

    around do |example|
        with_test_cache_dir do |dir|
            @cache_dir = dir
            WebCalTides.instance_variable_set(:@current_stations, nil)
            WebCalTides.instance_variable_set(:@current_stations_retry_at, nil)
            example.run
        ensure
            WebCalTides.instance_variable_set(:@current_stations, nil)
            WebCalTides.instance_variable_set(:@current_stations_retry_at, nil)
        end
    end

    before do
        allow(WebCalTides).to receive(:harmonics_checksum).and_return('cafebabe')
        allow(WebCalTides).to receive(:current_clients) { |provider = nil| provider ? clients[provider.to_sym] : clients }
    end

    def station_cache_files
        Dir.glob("#{@cache_dir}/current_stations_*.json")
    end

    it 'caches a complete list' do
        expect(WebCalTides.current_stations.map(&:bid)).to contain_exactly('BOS1101_1', 'XT1')
        expect(station_cache_files).to eq([WebCalTides.current_station_cache_file])
    end

    [
        ['returns an empty station list', -> { [] }],
        ['returns no station list',       -> { nil }],
        ['raises',                        -> { raise NoMethodError, "undefined method `map!' for {}:Hash" }]
    ].each do |what, failure|
        it "serves the other providers' stations uncached when NOAA #{what}, and retries it later" do
            allow(noaa).to receive(:current_stations, &failure)

            start = Time.current.utc
            expect(WebCalTides.current_stations.map(&:bid)).to eq(['XT1'])
            expect(WebCalTides.find_current_stations(by: ['cape cod']).map(&:bid)).to eq(['XT1'])
            expect(station_cache_files).to be_empty

            # Before the retry interval: still the in-memory list, no refetch
            WebCalTides.current_stations
            expect(noaa).to have_received(:current_stations).once

            allow(noaa).to receive(:current_stations).and_return([noaa_station])
            Timecop.freeze(start + WebCalTides::CURRENT_STATIONS_RETRY + 1) do
                expect(WebCalTides.current_stations.map(&:bid)).to contain_exactly('BOS1101_1', 'XT1')
                expect(station_cache_files).to eq([WebCalTides.current_station_cache_file])
            end
        end
    end

    it 'does not cache an empty list when every provider comes back empty' do
        allow(noaa).to receive(:current_stations).and_return([])
        allow(xtide).to receive(:current_stations).and_return([])

        expect(WebCalTides.current_stations).to eq([])
        expect(station_cache_files).to be_empty
    end

    it 'keeps the last list it had when a retry comes back empty' do
        allow(noaa).to receive(:current_stations).and_return([])
        start = Time.current.utc
        WebCalTides.current_stations

        allow(xtide).to receive(:current_stations).and_return([])
        Timecop.freeze(start + WebCalTides::CURRENT_STATIONS_RETRY + 1) do
            expect(WebCalTides.current_stations.map(&:bid)).to eq(['XT1'])
            expect(station_cache_files).to be_empty
        end
    end

    it 'rebuilds an empty cached station list instead of serving it for the quarter' do
        cache_file = WebCalTides.current_station_cache_file
        File.write(cache_file, '[]')

        expect(WebCalTides.current_stations.map(&:bid)).to contain_exactly('BOS1101_1', 'XT1')
        expect(JSON.parse(File.read(cache_file)).length).to eq(2)
    end
end

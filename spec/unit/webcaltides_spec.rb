# frozen_string_literal: true

RSpec.describe WebCalTides do
    describe '#update_tzcache' do
        around do |example|
            # Reset tzcache state before each test (class variables for cross-request thread safety)
            WebCalTides.class_variable_set(:@@tzcache, nil) if WebCalTides.class_variable_defined?(:@@tzcache)
            WebCalTides.class_variable_set(:@@tzcache_mutex, nil) if WebCalTides.class_variable_defined?(:@@tzcache_mutex)

            with_test_cache_dir do |dir|
                example.run
            end
        end

        it 'updates the cache and persists to disk' do
            WebCalTides.update_tzcache("42.0 -71.0", "America/New_York")

            cache = WebCalTides.class_variable_get(:@@tzcache)
            expect(cache["42.0 -71.0"]).to eq("America/New_York")

            # Verify file was written
            cache_file = "#{Server.settings.cache_dir}/tzs.json"
            expect(File.exist?(cache_file)).to be true
            expect(JSON.parse(File.read(cache_file))).to include("42.0 -71.0" => "America/New_York")
        end

        it 'returns the value that was set' do
            result = WebCalTides.update_tzcache("37.0 -122.0", "America/Los_Angeles")
            expect(result).to eq("America/Los_Angeles")
        end

        it 'initializes cache from disk if not loaded' do
            # Pre-populate cache file
            cache_file = "#{Server.settings.cache_dir}/tzs.json"
            File.write(cache_file, { "existing_key" => "Existing/Zone" }.to_json)

            # Update should load existing cache first
            WebCalTides.update_tzcache("new_key", "New/Zone")

            cache = WebCalTides.class_variable_get(:@@tzcache)
            expect(cache["existing_key"]).to eq("Existing/Zone")
            expect(cache["new_key"]).to eq("New/Zone")
        end

        it 'handles concurrent updates without corruption' do
            threads = 10.times.map do |i|
                Thread.new do
                    WebCalTides.update_tzcache("#{i}.0 #{i}.0", "Zone/Test#{i}")
                end
            end
            threads.each(&:join)

            cache = WebCalTides.class_variable_get(:@@tzcache)
            expect(cache.keys.length).to eq(10)

            # Verify all values are present
            10.times do |i|
                expect(cache["#{i}.0 #{i}.0"]).to eq("Zone/Test#{i}")
            end

            # Verify file is valid JSON with all entries
            cache_file = "#{Server.settings.cache_dir}/tzs.json"
            persisted = JSON.parse(File.read(cache_file))
            expect(persisted.keys.length).to eq(10)
        end
    end

    describe '#timezone_for' do
        around do |example|
            WebCalTides.class_variable_set(:@@tzcache, nil) if WebCalTides.class_variable_defined?(:@@tzcache)
            WebCalTides.class_variable_set(:@@tzcache_mutex, nil) if WebCalTides.class_variable_defined?(:@@tzcache_mutex)

            with_test_cache_dir do |dir|
                example.run
            end
        end

        it 'returns cached value without external lookup' do
            # Pre-populate cache
            WebCalTides.update_tzcache("42.3584 -71.0511", "America/New_York")

            # Should return cached value without calling Timezone.lookup
            expect(Timezone).not_to receive(:lookup)
            result = WebCalTides.timezone_for(42.3584, -71.0511)
            expect(result).to eq("America/New_York")
        end

        it 'normalizes longitude to -180..180 range' do
            # Pre-populate with normalized key
            WebCalTides.update_tzcache("35.0 140.0", "Asia/Tokyo")

            # Query with longitude > 180 (TICON format)
            result = WebCalTides.timezone_for(35.0, 500.0)  # 500 - 360 = 140
            expect(result).to eq("Asia/Tokyo")
        end
    end

    describe '#timezone_from_region' do
        def make_station(location: nil, region: nil)
            Models::Station.new(
                name: 'Test Station',
                alternate_names: [],
                id: 'TEST1',
                public_id: 'TEST1',
                region: region,
                location: location,
                lat: 37.75,
                lon: -122.7,
                url: nil,
                provider: 'noaa',
                bid: 'TEST1',
                depth: nil
            )
        end

        it 'extracts timezone from US state abbreviation in location' do
            station = make_station(location: 'Shell Point, Tampa Bay, FL')
            result = WebCalTides.send(:timezone_from_region, station)
            expect(result).to eq('America/New_York')
        end

        it 'extracts timezone from California state abbreviation' do
            station = make_station(location: 'San Francisco Bay, CA')
            result = WebCalTides.send(:timezone_from_region, station)
            expect(result).to eq('America/Los_Angeles')
        end

        it 'extracts timezone from Alaska state abbreviation' do
            station = make_station(location: 'Anchorage, AK')
            result = WebCalTides.send(:timezone_from_region, station)
            expect(result).to eq('America/Anchorage')
        end

        it 'extracts timezone from Hawaii state abbreviation' do
            station = make_station(location: 'Honolulu, HI')
            result = WebCalTides.send(:timezone_from_region, station)
            expect(result).to eq('Pacific/Honolulu')
        end

        it 'extracts timezone from Canadian region' do
            station = make_station(region: 'Pacific Canada')
            result = WebCalTides.send(:timezone_from_region, station)
            expect(result).to eq('America/Vancouver')
        end

        it 'extracts timezone from Atlantic Canada region' do
            station = make_station(region: 'Atlantic Canada')
            result = WebCalTides.send(:timezone_from_region, station)
            expect(result).to eq('America/Halifax')
        end

        it 'matches Hawaii keyword in region' do
            station = make_station(region: 'Hawaii, USA')
            result = WebCalTides.send(:timezone_from_region, station)
            expect(result).to eq('Pacific/Honolulu')
        end

        it 'matches Alaska keyword in region' do
            station = make_station(region: 'Alaska, USA')
            result = WebCalTides.send(:timezone_from_region, station)
            expect(result).to eq('America/Anchorage')
        end

        it 'returns nil when no match found' do
            station = make_station(location: 'Unknown Place', region: 'Unknown Region')
            result = WebCalTides.send(:timezone_from_region, station)
            expect(result).to be_nil
        end
    end

    describe '#timezone_from_longitude' do
        it 'returns Pacific/Honolulu for longitude near -155' do
            result = WebCalTides.send(:timezone_from_longitude, -155.0)
            expect(result).to eq('Pacific/Honolulu')
        end

        it 'returns America/Los_Angeles for longitude near -120' do
            result = WebCalTides.send(:timezone_from_longitude, -120.0)
            expect(result).to eq('America/Los_Angeles')
        end

        it 'returns America/New_York for longitude near -75' do
            result = WebCalTides.send(:timezone_from_longitude, -75.0)
            expect(result).to eq('America/New_York')
        end

        it 'returns Europe/London for longitude near 0' do
            result = WebCalTides.send(:timezone_from_longitude, 0.0)
            expect(result).to eq('Europe/London')
        end

        it 'returns Asia/Tokyo for longitude near 135' do
            result = WebCalTides.send(:timezone_from_longitude, 135.0)
            expect(result).to eq('Asia/Tokyo')
        end

        it 'returns Etc/GMT format for unmapped offsets' do
            result = WebCalTides.send(:timezone_from_longitude, 45.0)  # offset +3
            expect(result).to eq('Etc/GMT-3')
        end

        it 'normalizes longitude > 180' do
            result = WebCalTides.send(:timezone_from_longitude, 240.0)  # 240 - 360 = -120
            expect(result).to eq('America/Los_Angeles')
        end
    end

    describe '#timezone_fallback' do
        def make_station(location: nil, region: nil)
            Models::Station.new(
                name: 'Test Station',
                alternate_names: [],
                id: 'TEST1',
                public_id: 'TEST1',
                region: region,
                location: location,
                lat: 37.75,
                lon: -122.7,
                url: nil,
                provider: 'noaa',
                bid: 'TEST1',
                depth: nil
            )
        end

        it 'returns UTC when station is nil' do
            result = WebCalTides.send(:timezone_fallback, 37.75, -122.7, nil)
            expect(result).to eq('UTC')
        end

        it 'uses region mapping when available' do
            station = make_station(location: 'Golden Gate, CA')
            result = WebCalTides.send(:timezone_fallback, 37.75, -122.7, station)
            expect(result).to eq('America/Los_Angeles')
        end

        it 'falls back to longitude when region mapping fails' do
            station = make_station(location: 'Unknown Offshore', region: 'Unknown')
            result = WebCalTides.send(:timezone_fallback, 37.75, -122.7, station)
            expect(result).to eq('America/Los_Angeles')  # -122.7 / 15 = -8.18, rounds to -8
        end
    end

    describe '#timezone_for with fallback' do
        around do |example|
            WebCalTides.class_variable_set(:@@tzcache, nil) if WebCalTides.class_variable_defined?(:@@tzcache)
            WebCalTides.class_variable_set(:@@tzcache_mutex, nil) if WebCalTides.class_variable_defined?(:@@tzcache_mutex)

            with_test_cache_dir do |dir|
                example.run
            end
        end

        def make_station(location: nil, region: nil, lat: 37.75, lon: -122.7)
            Models::Station.new(
                name: 'Test Station',
                alternate_names: [],
                id: 'TEST1',
                public_id: 'TEST1',
                region: region,
                location: location,
                lat: lat,
                lon: lon,
                url: nil,
                provider: 'noaa',
                bid: 'TEST1',
                depth: nil
            )
        end

        it 'uses fallback when Timezone.lookup returns nil' do
            station = make_station(location: 'Offshore Point, CA')

            allow(Timezone).to receive(:lookup).and_return(nil)

            result = WebCalTides.timezone_for(37.75, -122.7, station)
            expect(result).to eq('America/Los_Angeles')
        end

        it 'uses Timezone.lookup result when available' do
            station = make_station(location: 'Test Point, CA')

            tz_mock = double('Timezone', name: 'America/Los_Angeles')
            allow(Timezone).to receive(:lookup).and_return(tz_mock)

            result = WebCalTides.timezone_for(37.75, -122.7, station)
            expect(result).to eq('America/Los_Angeles')
        end

        it 'uses longitude fallback when no station provided and lookup returns nil' do
            allow(Timezone).to receive(:lookup).and_return(nil)

            result = WebCalTides.timezone_for(37.75, -122.7)
            expect(result).to eq('UTC')  # No station, so UTC fallback
        end
    end
end

# frozen_string_literal: true

RSpec.describe WebCalTides do
    describe '#update_tzcache' do
        around do |example|
            # Reset tzcache data before each test (but not the mutex)
            WebCalTides.class_variable_set(:@@tzcache, nil) if WebCalTides.class_variable_defined?(:@@tzcache)

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

    describe '#cache_current_stations - NOAA region enrichment' do
        around do |example|
            with_test_cache_dir do |dir|
                example.run
            end
        end

        let(:mock_noaa_current_stations) do
            [
                Models::Station.new(id: 'CURR1', name: 'Current 1', lat: 37.8, lon: -122.4, provider: 'noaa', region: 'United States'),
                Models::Station.new(id: 'CURR2', name: 'Current 2', lat: 47.6, lon: -122.3, provider: 'noaa', region: 'United States'),
            ]
        end

        let(:mock_region_mapping) do
            {
                'regions' => {
                    'CURR1' => 'California',
                    'CURR2' => 'Washington'
                },
                'generated_at' => Time.current.utc.iso8601
            }
        end

        before do
            # Mock tide stations and current clients to avoid actual API calls
            allow(WebCalTides).to receive(:tide_stations).and_return([])
            allow(WebCalTides).to receive(:current_clients).and_return({})
        end

        it 'loads cached regions when file exists' do
            # Write mock region mapping to cache
            regions_file = WebCalTides.send(:noaa_current_regions_file)
            FileUtils.mkdir_p(File.dirname(regions_file))
            File.write(regions_file, mock_region_mapping.to_json)

            # Call cache_current_stations with mock stations
            result = WebCalTides.send(:cache_current_stations, stations: mock_noaa_current_stations)

            expect(result).to be true
            expect(mock_noaa_current_stations[0].region).to eq('California')
            expect(mock_noaa_current_stations[1].region).to eq('Washington')
        end

        it 'rebuilds regions when cache file missing' do
            # Ensure cache file doesn't exist
            regions_file = WebCalTides.send(:noaa_current_regions_file)
            FileUtils.rm_f(regions_file)

            # Mock build_noaa_current_regions to return a mapping
            allow(WebCalTides).to receive(:build_noaa_current_regions).and_return({
                'CURR1' => 'California',
                'CURR2' => 'Washington'
            })

            result = WebCalTides.send(:cache_current_stations, stations: mock_noaa_current_stations)

            expect(result).to be true
            expect(WebCalTides).to have_received(:build_noaa_current_regions).with(mock_noaa_current_stations)
            expect(mock_noaa_current_stations[0].region).to eq('California')
            expect(mock_noaa_current_stations[1].region).to eq('Washington')
        end

        it 'handles corrupted JSON with empty regions fallback' do
            # Write invalid JSON to cache file
            regions_file = WebCalTides.send(:noaa_current_regions_file)
            FileUtils.mkdir_p(File.dirname(regions_file))
            File.write(regions_file, '{invalid json')

            # Should use empty regions fallback without crashing
            result = WebCalTides.send(:cache_current_stations, stations: mock_noaa_current_stations)

            expect(result).to be true
            # Stations should keep their original "United States" region
            expect(mock_noaa_current_stations[0].region).to eq('United States')
            expect(mock_noaa_current_stations[1].region).to eq('United States')
        end

        it 'detects quarter change and forces rebuild' do
            # Write cache file with old quarter
            old_quarter_file = "#{Server.settings.cache_dir}/noaa_current_regions_2025Q4.json"
            FileUtils.mkdir_p(File.dirname(old_quarter_file))
            File.write(old_quarter_file, mock_region_mapping.to_json)

            # Mock Time.current to be in new quarter
            allow(Time).to receive(:current).and_return(Time.utc(2026, 1, 15))

            # Mock build_noaa_current_regions
            allow(WebCalTides).to receive(:build_noaa_current_regions).and_return({
                'CURR1' => 'California',
                'CURR2' => 'Washington'
            })

            result = WebCalTides.send(:cache_current_stations, stations: mock_noaa_current_stations)

            expect(result).to be true
            # Should rebuild because old quarter file doesn't match current quarter
            expect(WebCalTides).to have_received(:build_noaa_current_regions)
        end

        it 'uses cached regions within same quarter' do
            # Write current quarter cache file
            regions_file = WebCalTides.send(:noaa_current_regions_file)
            FileUtils.mkdir_p(File.dirname(regions_file))
            File.write(regions_file, mock_region_mapping.to_json)

            # Mock build_noaa_current_regions to track if it's called
            allow(WebCalTides).to receive(:build_noaa_current_regions).and_return({})

            result = WebCalTides.send(:cache_current_stations, stations: mock_noaa_current_stations)

            expect(result).to be true
            # Should NOT rebuild since cache exists for current quarter
            expect(WebCalTides).not_to have_received(:build_noaa_current_regions)
        end

        it 'enriches current stations with region from mapping' do
            regions_file = WebCalTides.send(:noaa_current_regions_file)
            FileUtils.mkdir_p(File.dirname(regions_file))
            File.write(regions_file, mock_region_mapping.to_json)

            result = WebCalTides.send(:cache_current_stations, stations: mock_noaa_current_stations)

            expect(result).to be true
            expect(mock_noaa_current_stations[0].region).to eq('California')
            expect(mock_noaa_current_stations[1].region).to eq('Washington')
        end

        it 'skips enrichment when region not in mapping' do
            # Create station not in mapping
            unmapped_station = Models::Station.new(
                id: 'CURR_UNKNOWN',
                name: 'Unknown Current',
                lat: 20.0,
                lon: -100.0,
                provider: 'noaa',
                region: 'United States'
            )

            regions_file = WebCalTides.send(:noaa_current_regions_file)
            FileUtils.mkdir_p(File.dirname(regions_file))
            File.write(regions_file, mock_region_mapping.to_json)

            result = WebCalTides.send(:cache_current_stations, stations: [unmapped_station])

            expect(result).to be true
            # Should keep original region
            expect(unmapped_station.region).to eq('United States')
        end
    end

    describe '#group_stations_by_proximity' do
        def make_test_station(id:, lat:, lon:, provider: 'noaa', region: 'Test', depth: nil)
            Models::Station.new(
                name: "Station #{id}",
                alternate_names: [],
                id: id,
                public_id: id,
                region: region,
                location: "Location #{id}",
                lat: lat,
                lon: lon,
                url: nil,
                provider: provider,
                bid: id,
                depth: depth
            )
        end

        it 'skips stations with nil latitude when matching groups' do
            stations = [
                make_test_station(id: 'S1', lat: 37.8, lon: -122.4),
                make_test_station(id: 'S2', lat: nil, lon: -122.5),
                make_test_station(id: 'S3', lat: 37.9, lon: -122.6)
            ]

            groups = WebCalTides.send(:group_stations_by_proximity, stations)

            # Should create 3 groups (each station in its own group)
            # S2 with nil lat cannot match existing groups
            expect(groups.size).to eq(3)
            group_ids = groups.map { |g| g.primary.id }.sort
            expect(group_ids).to eq(['S1', 'S2', 'S3'])
        end

        it 'skips stations with nil longitude when matching groups' do
            stations = [
                make_test_station(id: 'S1', lat: 37.8, lon: -122.4),
                make_test_station(id: 'S2', lat: 37.8, lon: nil),
                make_test_station(id: 'S3', lat: 37.8, lon: -122.4)
            ]

            groups = WebCalTides.send(:group_stations_by_proximity, stations)

            # S1 and S3 should group together (same location), S2 in its own group
            expect(groups.size).to eq(2)

            # Find the group containing S1
            s1_group = groups.find { |g| g.primary.id == 'S1' || g.alternatives.any? { |s| s.id == 'S1' } }
            expect(s1_group).not_to be_nil
            all_ids = ([s1_group.primary] + s1_group.alternatives).map(&:id).sort
            expect(all_ids).to eq(['S1', 'S3'])

            # S2 should be in its own group
            s2_group = groups.find { |g| g.primary.id == 'S2' }
            expect(s2_group).not_to be_nil
        end

        it 'groups valid stations even when some have missing coordinates' do
            stations = [
                make_test_station(id: 'S1', lat: 37.8, lon: -122.4),
                make_test_station(id: 'S2', lat: nil, lon: nil),
                make_test_station(id: 'S3', lat: 37.8, lon: -122.401),  # Very close to S1
                make_test_station(id: 'S4', lat: nil, lon: -122.4),
            ]

            groups = WebCalTides.send(:group_stations_by_proximity, stations)

            # S1 and S3 should group together, S2 and S4 in separate groups
            expect(groups.size).to eq(3)

            # Find group containing S1
            s1_group = groups.find { |g| g.primary.id == 'S1' || g.alternatives.any? { |s| s.id == 'S1' } }
            expect(s1_group).not_to be_nil
            all_ids = ([s1_group.primary] + s1_group.alternatives).map(&:id).sort
            expect(all_ids).to eq(['S1', 'S3'])
        end

        it 'handles stations without depth attribute when match_depth: true' do
            # Create stations where some don't respond to :depth
            station_with_depth = make_test_station(id: 'S1', lat: 37.8, lon: -122.4, depth: 10)
            station_no_depth = make_test_station(id: 'S2', lat: 37.8, lon: -122.4)
            allow(station_no_depth).to receive(:respond_to?).with(:depth).and_return(false)

            stations = [station_with_depth, station_no_depth]

            groups = WebCalTides.send(:group_stations_by_proximity, stations, match_depth: true)

            # Should create 2 separate groups (depth mismatch)
            expect(groups.size).to eq(2)
        end

        it 'groups stations by depth when depth present' do
            stations = [
                make_test_station(id: 'S1', lat: 37.8, lon: -122.4, depth: 10),
                make_test_station(id: 'S2', lat: 37.8, lon: -122.4, depth: 10),
                make_test_station(id: 'S3', lat: 37.8, lon: -122.4, depth: 20),
            ]

            groups = WebCalTides.send(:group_stations_by_proximity, stations, match_depth: true)

            # S1 and S2 should group (same depth), S3 separate
            expect(groups.size).to eq(2)
            expect(groups[0].primary.id).to eq('S1')
            expect(groups[0].alternatives.map(&:id)).to eq(['S2'])
            expect(groups[1].primary.id).to eq('S3')
        end

        it 'skips depth matching when station lacks depth field' do
            station1 = make_test_station(id: 'S1', lat: 37.8, lon: -122.4, depth: 10)
            station2 = make_test_station(id: 'S2', lat: 37.8, lon: -122.4)

            # Make S2 not respond to :depth
            allow(station2).to receive(:respond_to?).with(:depth).and_return(false)

            stations = [station1, station2]

            groups = WebCalTides.send(:group_stations_by_proximity, stations, match_depth: true)

            # Should create separate groups (nil != 10)
            expect(groups.size).to eq(2)
        end

        it 'returns empty array when stations is empty' do
            groups = WebCalTides.send(:group_stations_by_proximity, [])
            expect(groups).to eq([])
        end

        it 'returns empty array when stations is nil' do
            groups = WebCalTides.send(:group_stations_by_proximity, nil)
            expect(groups).to eq([])
        end
    end
end

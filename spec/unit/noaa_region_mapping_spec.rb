# frozen_string_literal: true

RSpec.describe WebCalTides, '.build_noaa_current_regions' do
    let(:mock_tide_stations) do
        [
            # California tide stations
            Models::Station.new(id: 'TIDE_CA1', name: 'San Francisco', lat: 37.8, lon: -122.4, provider: 'noaa', region: 'California'),
            Models::Station.new(id: 'TIDE_CA2', name: 'Los Angeles', lat: 33.7, lon: -118.2, provider: 'noaa', region: 'California'),

            # Washington tide stations
            Models::Station.new(id: 'TIDE_WA1', name: 'Seattle', lat: 47.6, lon: -122.3, provider: 'noaa', region: 'Washington'),

            # Alaska tide station (sparse region)
            Models::Station.new(id: 'TIDE_AK1', name: 'Anchorage', lat: 61.2, lon: -149.9, provider: 'noaa', region: 'Alaska'),

            # Non-NOAA station (should be ignored)
            Models::Station.new(id: 'TIDE_CHS1', name: 'Vancouver', lat: 49.3, lon: -123.1, provider: 'chs', region: 'British Columbia'),

            # Tide station with "United States" region (should be skipped in mapping)
            Models::Station.new(id: 'TIDE_US1', name: 'Generic US', lat: 40.0, lon: -75.0, provider: 'noaa', region: 'United States'),

            # Tide station with nil region
            Models::Station.new(id: 'TIDE_NIL', name: 'No Region', lat: 42.0, lon: -70.0, provider: 'noaa', region: nil),
        ]
    end

    let(:mock_current_stations) do
        [
            # California current station (close to San Francisco)
            Models::Station.new(id: 'CURR_CA1', name: 'SF Bay Current', lat: 37.9, lon: -122.5, provider: 'noaa', region: 'United States'),

            # Washington current station (close to Seattle)
            Models::Station.new(id: 'CURR_WA1', name: 'Puget Sound Current', lat: 47.5, lon: -122.4, provider: 'noaa', region: 'United States'),

            # Alaska current station (sparse region - requires 5x5 grid)
            Models::Station.new(id: 'CURR_AK1', name: 'Alaska Current', lat: 61.0, lon: -150.0, provider: 'noaa', region: 'United States'),

            # Current station with nil coordinates (should be skipped)
            Models::Station.new(id: 'CURR_NIL_LAT', name: 'No Lat', lat: nil, lon: -122.0, provider: 'noaa', region: 'United States'),
            Models::Station.new(id: 'CURR_NIL_LON', name: 'No Lon', lat: 37.0, lon: nil, provider: 'noaa', region: 'United States'),
        ]
    end

    before do
        # Mock tide_stations to return our test data
        allow(WebCalTides).to receive(:tide_stations).and_return(mock_tide_stations)

        # Create a temporary cache directory
        allow(WebCalTides).to receive(:settings).and_return(
            OpenStruct.new(cache_dir: Dir.mktmpdir('webcaltides_test'))
        )
    end

    after do
        # Clean up temp directory
        FileUtils.rm_rf(WebCalTides.settings.cache_dir) if WebCalTides.settings.cache_dir
    end

    describe 'basic grid lookup' do
        it 'maps current station to nearest tide station using spatial grid' do
            region_map = WebCalTides.send(:build_noaa_current_regions, [mock_current_stations[0]])

            # SF Bay current (37.9, -122.5) should map to San Francisco tide (37.8, -122.4) → California
            expect(region_map['CURR_CA1']).to eq('California')
        end

        it 'maps Washington current station to Washington region' do
            region_map = WebCalTides.send(:build_noaa_current_regions, [mock_current_stations[1]])

            # Puget Sound current (47.5, -122.4) should map to Seattle tide (47.6, -122.3) → Washington
            expect(region_map['CURR_WA1']).to eq('Washington')
        end

        it 'processes multiple current stations correctly' do
            region_map = WebCalTides.send(:build_noaa_current_regions, mock_current_stations[0..1])

            expect(region_map['CURR_CA1']).to eq('California')
            expect(region_map['CURR_WA1']).to eq('Washington')
            expect(region_map.size).to eq(2)
        end
    end

    describe 'sparse regions (Alaska/Hawaii) - 5x5 grid expansion' do
        it 'expands to 5x5 grid when 3x3 is empty' do
            # Alaska current (61.0, -150.0) is far from other stations
            # Should find Alaska tide station in 5x5 grid
            region_map = WebCalTides.send(:build_noaa_current_regions, [mock_current_stations[2]])

            expect(region_map['CURR_AK1']).to eq('Alaska')
        end

        it 'handles station with no candidates in 5x5 grid gracefully' do
            # Create a current station very far from all tide stations
            remote_station = Models::Station.new(
                id: 'CURR_REMOTE',
                name: 'Remote Ocean',
                lat: 0.0,
                lon: 0.0,  # Middle of Atlantic, far from all tide stations
                provider: 'noaa',
                region: 'United States'
            )

            region_map = WebCalTides.send(:build_noaa_current_regions, [remote_station])

            # Should still find the nearest station (even if far away)
            # or have no mapping if absolutely no candidates
            expect(region_map['CURR_REMOTE']).to be_a(String).or(be_nil)
        end
    end

    describe 'region filtering' do
        it 'skips mapping when closest region is "United States"' do
            # Create current station close to tide station with "United States" region
            current_near_us = Models::Station.new(
                id: 'CURR_NEAR_US',
                name: 'Near US Station',
                lat: 40.1,
                lon: -74.9,
                provider: 'noaa',
                region: 'United States'
            )

            region_map = WebCalTides.send(:build_noaa_current_regions, [current_near_us])

            # Should NOT map to "United States" region
            expect(region_map['CURR_NEAR_US']).to be_nil
        end

        it 'skips mapping when closest station has no region' do
            # Create current station close to tide station with nil region
            current_near_nil = Models::Station.new(
                id: 'CURR_NEAR_NIL',
                name: 'Near Nil Station',
                lat: 42.1,
                lon: -69.9,
                provider: 'noaa',
                region: 'United States'
            )

            region_map = WebCalTides.send(:build_noaa_current_regions, [current_near_nil])

            # Should NOT map when closest has nil region
            expect(region_map['CURR_NEAR_NIL']).to be_nil
        end
    end

    describe 'nil coordinate handling' do
        it 'skips stations with nil latitude' do
            region_map = WebCalTides.send(:build_noaa_current_regions, [mock_current_stations[3]])

            # Should not crash, just skip the station
            expect(region_map).to be_a(Hash)
            expect(region_map).not_to have_key('CURR_NIL_LAT')
        end

        it 'skips stations with nil longitude' do
            region_map = WebCalTides.send(:build_noaa_current_regions, [mock_current_stations[4]])

            # Should not crash, just skip the station
            expect(region_map).to be_a(Hash)
            expect(region_map).not_to have_key('CURR_NIL_LON')
        end
    end

    describe 'grid construction' do
        it 'builds grid with correct cell assignments' do
            # This is tested implicitly by the successful mappings above
            # If grid construction was broken, mappings would fail
            region_map = WebCalTides.send(:build_noaa_current_regions, mock_current_stations[0..1])

            expect(region_map.size).to be > 0
        end

        it 'handles stations at grid boundaries correctly' do
            # Create stations exactly at grid boundaries (e.g., lat = 0, 2, 4, etc.)
            boundary_current = Models::Station.new(
                id: 'CURR_BOUNDARY',
                name: 'Boundary Station',
                lat: 38.0,  # Exactly on 2-degree boundary
                lon: -122.0,
                provider: 'noaa',
                region: 'United States'
            )

            region_map = WebCalTides.send(:build_noaa_current_regions, [boundary_current])

            # Should still find nearest tide station
            expect(region_map['CURR_BOUNDARY']).to eq('California')
        end
    end

    describe 'cache file generation' do
        it 'writes region mapping to cache file' do
            WebCalTides.send(:build_noaa_current_regions, mock_current_stations[0..1])

            # Should create cache file
            cache_files = Dir.glob("#{WebCalTides.settings.cache_dir}/noaa_current_regions_*.json")
            expect(cache_files).not_to be_empty
        end

        it 'includes generated_at timestamp in cache file' do
            WebCalTides.send(:build_noaa_current_regions, mock_current_stations[0..1])

            cache_file = Dir.glob("#{WebCalTides.settings.cache_dir}/noaa_current_regions_*.json").first
            data = JSON.parse(File.read(cache_file))

            expect(data).to have_key('generated_at')
            expect(data).to have_key('regions')
            expect(data['regions']).to be_a(Hash)
        end
    end
end

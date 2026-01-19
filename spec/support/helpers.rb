# frozen_string_literal: true

module TestHelpers
    # Create a test Station with default values
    def build_station(attrs = {})
        defaults = {
            name: 'Test Station',
            alternate_names: [],
            id: 'TEST123',
            public_id: 'TEST123',
            region: 'Test Region',
            location: 'Test Location',
            lat: 42.3601,
            lon: -71.0589,
            url: 'https://example.com/station',
            provider: 'noaa',
            bid: nil,
            depth: nil
        }
        Models::Station.new(**defaults.merge(attrs))
    end

    # Create a test TideData with default values
    def build_tide_data(attrs = {})
        defaults = {
            type: 'High',
            units: 'ft',
            prediction: 10.5,
            time: DateTime.now,
            url: 'https://example.com/tide'
        }
        Models::TideData.new(**defaults.merge(attrs))
    end

    # Create a test CurrentData with default values
    def build_current_data(attrs = {})
        defaults = {
            bin: '1',
            type: 'flood',
            mean_flood_dir: '045',
            mean_ebb_dir: '225',
            time: DateTime.now,
            depth: '10',
            velocity_major: 2.5,
            url: 'https://example.com/current'
        }
        Models::CurrentData.new(**defaults.merge(attrs))
    end

    # Load a JSON fixture file
    def load_fixture(filename)
        path = File.join(__dir__, '..', 'fixtures', 'json', filename)
        JSON.parse(File.read(path))
    end

    # Freeze time for consistent testing
    def freeze_time(time = Time.utc(2025, 6, 15, 12, 0, 0))
        Timecop.freeze(time)
    end

    # Create a temporary cache directory for tests
    def with_test_cache_dir
        Dir.mktmpdir('webcaltides_test_cache') do |dir|
            original_cache = Server.settings.cache_dir
            Server.set :cache_dir, dir
            yield dir
            Server.set :cache_dir, original_cache
        end
    end
end

RSpec.configure do |config|
    config.include TestHelpers

    # Pre-populate timezone cache with common test coordinates to avoid GeoNames API calls
    config.before(:suite) do
        # Default test station coordinates (Boston)
        WebCalTides.update_tzcache("42.3601 -71.0589", "America/New_York")
        # Other commonly used test coordinates
        WebCalTides.update_tzcache("42.3601 -71.0601", "America/New_York")  # xtide boston variant
        WebCalTides.update_tzcache("37.7749 -122.4194", "America/Los_Angeles")  # San Francisco
        WebCalTides.update_tzcache("47.6062 -122.3321", "America/Los_Angeles")  # Seattle
    end
end

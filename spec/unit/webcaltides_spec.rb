# frozen_string_literal: true

RSpec.describe WebCalTides do
  describe '#update_tzcache' do
    around do |example|
      # Reset tzcache state before each test
      WebCalTides.instance_variable_set(:@tzcache, nil)
      WebCalTides.instance_variable_set(:@tzcache_mutex, nil)

      with_test_cache_dir do |dir|
        example.run
      end
    end

    it 'updates the cache and persists to disk' do
      WebCalTides.update_tzcache("42.0 -71.0", "America/New_York")

      cache = WebCalTides.instance_variable_get(:@tzcache)
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

      cache = WebCalTides.instance_variable_get(:@tzcache)
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

      cache = WebCalTides.instance_variable_get(:@tzcache)
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
      WebCalTides.instance_variable_set(:@tzcache, nil)
      WebCalTides.instance_variable_set(:@tzcache_mutex, nil)

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
end

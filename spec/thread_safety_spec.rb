# frozen_string_literal: true

RSpec.describe "Thread Safety" do
    describe "lunar_phases" do
        it "handles concurrent access without data loss" do
            # Mock the lunar client to avoid external API calls during thread safety test
            allow_any_instance_of(Clients::Lunar).to receive(:phases_for_year).and_return([
                { "datetime" => "2026-01-01T12:00:00Z", "type" => "new_moon" },
                { "datetime" => "2026-01-15T12:00:00Z", "type" => "full_moon" }
            ])

            # Test concurrent access to lunar_phases cache
            threads = 5.times.map do |i|
                Thread.new do
                    10.times do
                        phases = WebCalTides.lunar_phases(
                            Date.today + (i * 30),
                            Date.today + (i * 30) + 90
                        )
                        expect(phases).to be_an(Array)
                    end
                end
            end
            threads.each(&:join)
        end
    end

    describe "tide_stations" do
        it "initializes once even with concurrent access" do
            # Ensure cache file exists to avoid triggering API calls in CI
            unless File.exist?(WebCalTides.tide_station_cache_file)
                # Mock the cache to avoid HTTP requests
                allow(File).to receive(:exist?).and_call_original
                allow(File).to receive(:exist?).with(WebCalTides.tide_station_cache_file).and_return(true)
                allow(File).to receive(:read).and_call_original
                allow(File).to receive(:read).with(WebCalTides.tide_station_cache_file).and_return('[]')
            end

            # Get reference to current value
            original = WebCalTides.instance_variable_get(:@tide_stations)

            threads = 5.times.map do
                Thread.new { WebCalTides.tide_stations }
            end

            results = threads.map(&:value)

            # All threads should get the same object_id
            expect(results.map(&:object_id).uniq.size).to eq(1)
            # And it should be the original (already initialized by cache warming or previous test)
            expect(results.first.object_id).to eq(original.object_id) if original
        end
    end

    describe "current_stations" do
        it "initializes once even with concurrent access" do
            # Ensure cache file exists to avoid triggering API calls in CI
            unless File.exist?(WebCalTides.current_station_cache_file)
                # Mock the cache to avoid HTTP requests
                allow(File).to receive(:exist?).and_call_original
                allow(File).to receive(:exist?).with(WebCalTides.current_station_cache_file).and_return(true)
                allow(File).to receive(:read).and_call_original
                allow(File).to receive(:read).with(WebCalTides.current_station_cache_file).and_return('[]')
            end

            # Get reference to current value
            original = WebCalTides.instance_variable_get(:@current_stations)

            threads = 5.times.map do
                Thread.new { WebCalTides.current_stations }
            end

            results = threads.map(&:value)

            # All threads should get the same object_id
            expect(results.map(&:object_id).uniq.size).to eq(1)
            # And it should be the original (already initialized)
            expect(results.first.object_id).to eq(original.object_id) if original
        end
    end

end

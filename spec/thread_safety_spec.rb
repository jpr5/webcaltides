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
            cache_file = WebCalTides.tide_station_cache_file
            FileUtils.mkdir_p(File.dirname(cache_file))
            File.write(cache_file, '[]') unless File.exist?(cache_file)
            WebCalTides.instance_variable_set(:@tide_stations, nil)

            threads = 5.times.map do
                Thread.new { WebCalTides.tide_stations }
            end

            results = threads.map(&:value)

            # All threads should get the same object_id
            expect(results.map(&:object_id).uniq.size).to eq(1)
        end
    end

    describe "current_stations" do
        it "initializes once even with concurrent access" do
            cache_file = WebCalTides.current_station_cache_file
            FileUtils.mkdir_p(File.dirname(cache_file))
            File.write(cache_file, '[]') unless File.exist?(cache_file)
            WebCalTides.instance_variable_set(:@current_stations, nil)

            threads = 5.times.map do
                Thread.new { WebCalTides.current_stations }
            end

            results = threads.map(&:value)

            # All threads should get the same object_id
            expect(results.map(&:object_id).uniq.size).to eq(1)
        end
    end

    describe "stress tests: high contention scenarios" do
        describe "harmonics client initialization" do
            it "initializes once with 100 concurrent threads" do
                WebCalTides.instance_variable_set(:@harmonics_client, nil)

                threads = 100.times.map do
                    Thread.new { WebCalTides.send(:get_harmonics_client) }
                end

                results = threads.map(&:value)

                # All threads should get the same object_id
                expect(results.map(&:object_id).uniq.size).to eq(1)
            end

            it "all threads see same object_id after contention" do
                WebCalTides.instance_variable_set(:@harmonics_client, nil)

                threads = 150.times.map do
                    Thread.new { WebCalTides.send(:get_harmonics_client) }
                end

                clients = threads.map(&:value)

                # Verify all returned the exact same object
                first_client_id = clients.first.object_id
                expect(clients.all? { |c| c.object_id == first_client_id }).to be true
            end
        end

        describe "station cache initialization under contention" do
            it "initializes tide stations cache once under contention" do
                cache_file = WebCalTides.tide_station_cache_file
                FileUtils.mkdir_p(File.dirname(cache_file))
                File.write(cache_file, '[]') unless File.exist?(cache_file)
                WebCalTides.instance_variable_set(:@tide_stations, nil)

                threads = 100.times.map do
                    Thread.new { WebCalTides.tide_stations }
                end

                results = threads.map(&:value)

                # All threads should get the same object_id
                expect(results.map(&:object_id).uniq.size).to eq(1)
            end

            it "initializes current stations cache once under contention" do
                cache_file = WebCalTides.current_station_cache_file
                FileUtils.mkdir_p(File.dirname(cache_file))
                File.write(cache_file, '[]') unless File.exist?(cache_file)
                WebCalTides.instance_variable_set(:@current_stations, nil)

                threads = 100.times.map do
                    Thread.new { WebCalTides.current_stations }
                end

                results = threads.map(&:value)

                # All threads should get the same object_id
                expect(results.map(&:object_id).uniq.size).to eq(1)
            end
        end

        describe "rapid create/destroy cycles" do
            it "handles rapid create/destroy cycles without corruption" do
                # Test that mutexes don't deadlock or corrupt data
                # under rapid repeated initialization/reset cycles
                10.times do
                    WebCalTides.instance_variable_set(:@harmonics_client, nil)

                    threads = 10.times.map do
                        Thread.new { WebCalTides.send(:get_harmonics_client) }
                    end

                    results = threads.map(&:value)

                    # Each cycle should have exactly one unique object
                    expect(results.map(&:object_id).uniq.size).to eq(1)
                end
            end
        end

        describe "lunar phases under high load" do
            it "handles 1000+ concurrent lunar phase lookups" do
                # Mock the lunar client to avoid external API calls
                allow_any_instance_of(Clients::Lunar).to receive(:phases_for_year).and_return([
                    { "datetime" => "2026-01-01T12:00:00Z", "type" => "new_moon" },
                    { "datetime" => "2026-01-15T12:00:00Z", "type" => "full_moon" }
                ])

                # Create 1000 threads accessing lunar phases concurrently
                threads = 1000.times.map do |i|
                    Thread.new do
                        phases = WebCalTides.lunar_phases(
                            Date.today + (i % 365),
                            Date.today + (i % 365) + 90
                        )
                        expect(phases).to be_an(Array)
                    end
                end

                # Should complete without deadlocks or exceptions
                threads.each(&:join)
            end
        end
    end

end

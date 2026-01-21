# frozen_string_literal: true

RSpec.describe Clients::Harmonics do
    let(:logger) { Logger.new('/dev/null') }
    let(:client) { described_class.new(logger) }

    # Paths to test fixture files
    # Use real TCD file from data/ directory (tests will use actual harmonics data)
    let(:fixture_xtide) { Dir.glob(File.expand_path('../../../data/harmonics-dwf-*.tcd', __FILE__)).max }
    let(:fixture_ticon) { File.expand_path('../../fixtures/harmonics/test-ticon.json', __FILE__) }

    describe '#initialize' do
        it 'creates a Harmonics::Engine instance' do
            expect(client.engine).to be_a(Harmonics::Engine)
        end
    end

    describe '#tide_stations' do
        context 'when harmonics data files exist' do
            around do |example|
                original_xtide = ENV['XTIDE_FILE']
                original_ticon = ENV['TICON_FILE']
                ENV['XTIDE_FILE'] = fixture_xtide
                ENV['TICON_FILE'] = fixture_ticon

                with_test_cache_dir do
                    example.run
                end
            ensure
                ENV['XTIDE_FILE'] = original_xtide
                ENV['TICON_FILE'] = original_ticon
            end

            it 'returns an array of stations' do
                stations = client.tide_stations
                expect(stations).to be_an(Array)
                expect(stations).not_to be_empty
            end

            it 'returns stations with xtide or ticon provider' do
                stations = client.tide_stations
                expect(stations.map(&:provider).uniq).to all(be_in(['xtide', 'ticon']))
            end

            it 'returns only tide stations' do
                stations = client.tide_stations
                # Tide stations have type='tide' (filtered by client)
                # All returned stations should have numeric depth (or nil for tide stations)
                expect(stations).to all(satisfy { |s| s.depth.nil? || s.depth.is_a?(Numeric) })
            end

            it 'returns stations with valid IANA timezone format (no leading colon)' do
                # Test at engine level where timezone metadata is available
                engine_stations = client.engine.stations.select { |s| s['type'] == 'tide' }
                stations_with_tz = engine_stations.reject { |s| s['timezone'].nil? || s['timezone'].empty? }
                expect(stations_with_tz).not_to be_empty

                stations_with_tz.each do |station|
                    tz = station['timezone']
                    # Timezone should not start with colon (TCD format artifact that should be stripped)
                    expect(tz).not_to start_with(':'),
                        "Station '#{station['name']}' has invalid timezone: #{tz}"

                    # Timezone should match IANA format (e.g., America/New_York, Pacific/Honolulu)
                    # Or be 'UTC' for some stations
                    expect(tz).to match(%r{^(UTC|[A-Z][a-z_]+/[A-Z][a-z_]+)}),
                        "Station '#{station['name']}' has invalid timezone format: #{tz}"
                end
            end
        end

        context 'when harmonics data files are missing' do
            before do
                allow_any_instance_of(Harmonics::Engine).to receive(:ensure_source_files!).and_raise(
                    Harmonics::Engine::MissingSourceFilesError.new('Missing data files')
                )
            end

            it 'raises MissingSourceFilesError' do
                expect { client.tide_stations }.to raise_error(Harmonics::Engine::MissingSourceFilesError)
            end
        end
    end

    describe '#current_stations' do
        context 'when harmonics data files exist' do
            around do |example|
                original_xtide = ENV['XTIDE_FILE']
                original_ticon = ENV['TICON_FILE']
                ENV['XTIDE_FILE'] = fixture_xtide
                ENV['TICON_FILE'] = fixture_ticon

                with_test_cache_dir do
                    example.run
                end
            ensure
                ENV['XTIDE_FILE'] = original_xtide
                ENV['TICON_FILE'] = original_ticon
            end

            it 'returns an array of current stations' do
                stations = client.current_stations
                expect(stations).to be_an(Array)
                expect(stations).not_to be_empty
            end

            it 'returns stations with depth information' do
                stations = client.current_stations
                # Current stations should have depth or bid
                stations_with_depth = stations.select { |s| s.depth || s.bid }
                expect(stations_with_depth).not_to be_empty
            end
        end
    end

    describe '#tide_data_for' do
        let(:station) do
            Models::Station.new(
                name: 'Test XTide Station',
                id: 'X1234567',
                public_id: 'X1234567',
                provider: 'xtide',
                lat: 42.0,
                lon: -71.0
            )
        end

        context 'with mocked engine' do
            let(:mock_peaks) do
                [
                    { 'type' => 'High', 'time' => Time.utc(2025, 6, 15, 6, 30), 'height' => 10.5, 'units' => 'ft' },
                    { 'type' => 'Low', 'time' => Time.utc(2025, 6, 15, 12, 45), 'height' => 0.5, 'units' => 'ft' },
                    { 'type' => 'High', 'time' => Time.utc(2025, 6, 15, 19, 0), 'height' => 11.0, 'units' => 'ft' }
                ]
            end

            before do
                allow(client.engine).to receive(:find_station).and_return({ 'id' => 'X1234567' })
                allow(client.engine).to receive(:generate_peaks_optimized).and_return(mock_peaks)
            end

            it 'generates peaks using the optimized harmonics method' do
                expect(client.engine).to receive(:generate_peaks_optimized)
                client.tide_data_for(station, Time.utc(2025, 6, 15))
            end

            it 'returns TideData objects' do
                data = client.tide_data_for(station, Time.utc(2025, 6, 15))
                expect(data).to all(be_a(Models::TideData))
            end

            it 'preserves high and low tide types' do
                data = client.tide_data_for(station, Time.utc(2025, 6, 15))

                highs = data.select { |d| d.type == 'High' }
                lows = data.select { |d| d.type == 'Low' }

                expect(highs.length).to eq(2)
                expect(lows.length).to eq(1)
            end
        end
    end

    describe 'TimeWindow module' do
        it 'includes TimeWindow module' do
            expect(described_class.ancestors).to include(Clients::TimeWindow)
        end
    end

    describe '#detect_zero_crossings' do
        it 'interpolates crossing time using actual time delta between points' do
            # Simulate peaks that are 6 hours apart (like subordinate station output)
            # Flood at 06:00 (+2.0 kn), Ebb at 12:00 (-1.0 kn)
            # Zero crossing should be at ~10:00 (2/3 of the way, based on height ratio)
            predictions = [
                { 'time' => Time.utc(2025, 6, 15, 6, 0), 'height' => 2.0, 'units' => 'knots' },
                { 'time' => Time.utc(2025, 6, 15, 12, 0), 'height' => -1.0, 'units' => 'knots' }
            ]

            crossings = client.detect_zero_crossings(predictions)

            expect(crossings.length).to eq(1)

            crossing_time = crossings.first['time']
            # With heights 2.0 and -1.0, ratio = 2.0/(2.0+1.0) = 0.667
            # Expected crossing: 06:00 + 0.667 * 6 hours = 06:00 + 4 hours = 10:00
            expect(crossing_time).to be_within(1.minute).of(Time.utc(2025, 6, 15, 10, 0))
        end

        it 'places slack time hours between peaks, not seconds after' do
            # This catches the specific bug where 60-second step was hardcoded
            predictions = [
                { 'time' => Time.utc(2025, 6, 15, 1, 0), 'height' => -1.5, 'units' => 'knots' },
                { 'time' => Time.utc(2025, 6, 15, 7, 0), 'height' => 2.0, 'units' => 'knots' }
            ]

            crossings = client.detect_zero_crossings(predictions)
            crossing_time = crossings.first['time']

            # Slack must be more than 1 hour after the first peak
            # (the bug would put it ~26 seconds after)
            expect(crossing_time - predictions.first['time']).to be > 1.hour
            # And before the second peak
            expect(predictions.last['time'] - crossing_time).to be > 1.hour
        end
    end
end

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

            it 'gives a TICON current its name depth, and no depth (not its datum offset) when the name has none' do
                ticon = client.current_stations.select { |s| s.provider == 'ticon' }.index_by(&:name)
                expect(ticon['Active Pass (depth 10 ft), British Columbia Current'].depth).to eq(10.0)
                expect(ticon.fetch('Sydney Harbour Current').depth).to be_nil
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
            # Peaks 6 hours apart, as for a subordinate station without slack
            # offsets, which falls back to interpolating between its peaks
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

        # Subordinate slack times take flood_begins for the slack before flood
        # and ebb_begins for the slack before ebb, so the direction must be right.
        it 'marks a crossing from ebb to flood as the slack before flood, and from flood to ebb as before ebb' do
            predictions = [
                { 'time' => Time.utc(2025, 6, 15, 0, 0), 'height' => -1.0, 'units' => 'knots' },
                { 'time' => Time.utc(2025, 6, 15, 1, 0), 'height' => 1.0, 'units' => 'knots' },
                { 'time' => Time.utc(2025, 6, 15, 2, 0), 'height' => -1.0, 'units' => 'knots' }
            ]

            expect(client.detect_zero_crossings(predictions).map { |c| c['begins'] }).to eq(%w[flood ebb])
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

    describe '#current_data_for event types' do
        # Bug: every XTide current event was labelled "flood".  The XTide parser
        # stored a current station's name depth ("(depth 13 ft)") as its datum
        # offset, so every predicted velocity was shifted positive.
        #
        # Expected events are NOAA's own predictions (currents_predictions,
        # interval=MAX_SLACK) for the same stations, fetched 2026-10-07.
        context 'with the real XTide data' do
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

            let(:window_start) { Time.utc(2026, 10, 7) }
            let(:window_end) { Time.utc(2026, 10, 7, 16) }

            before do
                # A 16-hour window instead of 13 months, to keep the spec fast
                allow(client).to receive(:beginning_of_window).and_return(window_start)
                allow(client).to receive(:end_of_window).and_return(window_end)
            end

            def events_for(bid)
                station = client.current_stations.find { |s| s.bid == bid }
                expect(station).not_to be_nil, "no XTide current station #{bid}"
                client.current_data_for(station, window_start)
            end

            # NOAA's list is every event in the window, so the events must match
            # it one to one: same count, same order of types, each within 5
            # minutes and 0.05 knots.  No extra or duplicate events.
            def expect_noaa_events(events, noaa)
                expect(events.map(&:type)).to eq(noaa.map { |_, type, _| type })
                events.zip(noaa).each do |e, (time, type, velocity)|
                    expect(e.time.to_time).to be_within(5.minutes).of(time), "#{type} at #{e.time}, NOAA #{time}"
                    expect(e.velocity_major).to be_within(0.05).of(velocity)
                end
            end

            def expect_signed_events(events)
                expect(events.map(&:type).uniq).to contain_exactly('flood', 'ebb', 'slack')
                events.each do |e|
                    case e.type
                    when 'flood' then expect(e.velocity_major).to be > 0
                    when 'ebb'   then expect(e.velocity_major).to be < 0
                    end
                end
            end

            it 'gives flood, ebb and slack at a reference station (Cape Cod Canal, NOAA COD0904)' do
                events = events_for('X5016721_13')

                expect_signed_events(events)
                expect_noaa_events(events, [
                    [Time.utc(2026, 10, 7, 1, 24), 'ebb', -4.29],
                    [Time.utc(2026, 10, 7, 5, 11), 'slack', 0.0],
                    [Time.utc(2026, 10, 7, 9, 17), 'flood', 4.21],
                    [Time.utc(2026, 10, 7, 11, 37), 'slack', 0.0],
                    [Time.utc(2026, 10, 7, 14, 23), 'ebb', -4.28]
                ])
            end

            it 'gives flood, ebb and slack at a subordinate station (Wareham River, NOAA ACT2026)' do
                events = events_for('X2d7f27f')

                expect_signed_events(events)
                expect_noaa_events(events, [
                    [Time.utc(2026, 10, 7, 0, 0), 'ebb', -0.43],
                    [Time.utc(2026, 10, 7, 3, 2), 'slack', 0.0],
                    [Time.utc(2026, 10, 7, 8, 44), 'flood', 0.42],
                    [Time.utc(2026, 10, 7, 9, 59), 'slack', 0.0],
                    [Time.utc(2026, 10, 7, 12, 59), 'ebb', -0.43],
                    [Time.utc(2026, 10, 7, 15, 25), 'slack', 0.0]
                ])
            end

            # A subordinate station's events are the reference station's events
            # moved by its time offsets, which reach about 9 hours for slacks and
            # over 2 hours for max currents at hundreds of stations.  An event
            # moved in from beyond the window must still be there: the events
            # for [start, end] must equal those of a window 12 hours wider on
            # each side, clipped to [start, end].
            def window_events(bid, from, to)
                station = client.current_stations.find { |s| s.bid == bid }
                allow(client).to receive(:beginning_of_window).and_return(from)
                allow(client).to receive(:end_of_window).and_return(to)
                client.current_data_for(station, from).map { |e| [e.type, e.time.to_time.utc.round] }
            end

            def expect_no_edge_loss(bid, from, to)
                narrow = window_events(bid, from, to)
                wide = window_events(bid, from - 12.hours, to + 12.hours).select { |_, t| t.between?(from, to) }
                expect(narrow).to eq(wide)
                narrow
            end

            it 'keeps subordinate slacks moved in from before the window start (Point Lookout, flood begins +05:08)' do
                events = expect_no_edge_loss('X05f2f68', Time.utc(2026, 11, 1), Time.utc(2026, 11, 2))
                expect(events).to include(['slack', Time.utc(2026, 11, 1, 1, 50, 3)])
            end

            it 'keeps subordinate slacks moved in near the window start (Tuckernuck Island, flood begins +04:08)' do
                events = expect_no_edge_loss('X03f647c', Time.utc(2026, 10, 7, 1), Time.utc(2026, 10, 8, 1))
                expect(events.map(&:first).tally).to eq('slack' => 4, 'flood' => 2, 'ebb' => 2)
            end

            it 'keeps subordinate max currents moved in from after the window end (max/min time -08:10)' do
                events = expect_no_edge_loss('X485d5d6', Time.utc(2026, 10, 7), Time.utc(2026, 10, 8))
                expect(events).to include(['flood', Time.utc(2026, 10, 7, 21, 45, 32)])
            end

            it 'keeps a subordinate max ebb moved in from after the window end (min time -08:05)' do
                events = expect_no_edge_loss('X1cab8cf_10', Time.utc(2026, 10, 7), Time.utc(2026, 10, 8))
                expect(events).to include(['ebb', Time.utc(2026, 10, 7, 19, 4, 21)])
            end
        end
    end

    describe 'XTide subordinate stations and current metadata' do
        context 'with the real XTide data' do
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

            let(:engine) { client.engine }

            # Tide feeds (tide_data_for) take a subordinate's peaks from its
            # reference's peaks over the window's months plus one month each side.
            # Each call uses a new client, so the engine's per-month reference
            # peak cache from one window cannot fill in for another.
            def tide_feed_peaks(id, from, to)
                feed = described_class.new(logger)
                station = feed.tide_stations.find { |s| s.id == id }
                allow(feed).to receive(:beginning_of_window).and_return(from)
                allow(feed).to receive(:end_of_window).and_return(to)
                feed.tide_data_for(station, from).map { |t| [t.type, t.time.to_time.utc.round, t.prediction] }
            end

            it 'keeps subordinate tide peaks moved in from before the window start in a tide feed (time offsets +11:36/+12:21)' do
                from, to = Time.utc(2026, 10, 7), Time.utc(2026, 10, 8)
                narrow = tide_feed_peaks('X0f812f0', from, to)
                wide = tide_feed_peaks('X0f812f0', from - 12.hours, to + 12.hours).select { |_, t, _| t.between?(from, to) }

                expect(narrow).to eq(wide)
                expect(narrow.map { |type, t, _| [type, t] }).to include(['Low', Time.utc(2026, 10, 7, 9, 41, 41)])
            end

            # The same, through Engine#generate_predictions, which shares
            # reference_predictions and subordinate_peaks (and so the
            # subordinate_margin) with subordinate current feeds.
            it 'keeps subordinate tide peaks moved in from before the window start in Engine#generate_predictions' do
                from, to = Time.utc(2026, 10, 7), Time.utc(2026, 10, 8)
                peaks = ->(a, b) { engine.generate_predictions('X0f812f0', a, b).map { |p| [p['type'], p['time'].round] } }
                wide = peaks.(from - 12.hours, to + 12.hours).select { |_, t| t.between?(from, to) }

                expect(peaks.(from, to)).to eq(wide)
                expect(wide).to include(['Low', Time.utc(2026, 10, 7, 9, 41, 41)])
            end

            # Subordinate tide heights are the reference's times the level
            # multiplier plus the level add.  206 XTide subordinate tides have an
            # add (and a multiplier of 1).  Expected values are NOAA's own
            # predictions (datum MLLW, interval=hilo, feet), fetched 2026-10-07.
            def expect_noaa_tides(peaks, noaa)
                expect(peaks.map(&:first)).to eq(noaa.map { |_, type, _| type })
                peaks.zip(noaa).each do |(type, time, height), (noaa_time, _, noaa_height)|
                    expect(time).to be_within(2.minutes).of(noaa_time), "#{type} at #{time}, NOAA #{noaa_time}"
                    expect(height).to be_within(0.05).of(noaa_height), "#{type} at #{time}: #{height} ft, NOAA #{noaa_height} ft"
                end
            end

            it 'adds the level add to subordinate tide heights (Newark Slough, NOAA 9414506, high +2.6 ft, low +0.1 ft)' do
                expect(engine.station_data('X373345f')).to include('h_level_add' => 2.6, 'l_level_add' => 0.1, 'h_height_mult' => 1.0)

                expect_noaa_tides(tide_feed_peaks('X373345f', Time.utc(2026, 11, 1), Time.utc(2026, 11, 3)), [
                    [Time.utc(2026, 11, 1, 7, 54), 'Low', -0.335],
                    [Time.utc(2026, 11, 1, 14, 51), 'High', 7.381],
                    [Time.utc(2026, 11, 1, 20, 22), 'Low', 3.343],
                    [Time.utc(2026, 11, 2, 1, 10), 'High', 7.992],
                    [Time.utc(2026, 11, 2, 9, 2), 'Low', -0.047],
                    [Time.utc(2026, 11, 2, 15, 46), 'High', 7.623],
                    [Time.utc(2026, 11, 2, 21, 49), 'Low', 2.845]
                ])
            end

            it 'adds a negative level add to subordinate tide heights (Ano Nuevo Island, NOAA 9413878, high -0.7 ft, low -0.1 ft)' do
                expect_noaa_tides(tide_feed_peaks('X8e55f8e', Time.utc(2026, 11, 1), Time.utc(2026, 11, 3)), [
                    [Time.utc(2026, 11, 1, 4, 52), 'Low', -0.532],
                    [Time.utc(2026, 11, 1, 12, 16), 'High', 4.1],
                    [Time.utc(2026, 11, 1, 17, 20), 'Low', 3.146],
                    [Time.utc(2026, 11, 1, 22, 35), 'High', 4.711],
                    [Time.utc(2026, 11, 2, 6, 0), 'Low', -0.243],
                    [Time.utc(2026, 11, 2, 13, 11), 'High', 4.342],
                    [Time.utc(2026, 11, 2, 18, 47), 'Low', 2.648],
                    [Time.utc(2026, 11, 2, 23, 57), 'High', 4.355]
                ])
            end

            it 'applies the level add in Engine#generate_predictions too (Newark Slough)' do
                peaks = engine.generate_predictions('X373345f', Time.utc(2026, 11, 1, 14), Time.utc(2026, 11, 1, 16))
                expect(peaks.map { |p| p['type'] }).to eq(['High'])
                expect(peaks.first['height']).to be_within(0.05).of(7.381)
            end

            # XTide ids come from the coordinates.  Sea Bright (tide) and Seabright
            # Bridge (current, no depth) are at the same point, so both are
            # Xc7078fe, and the tide feed was predicted from the current's data
            # (velocities in knots shown as feet).  4 other pairs are the same.
            it 'predicts a tide from its own data when a current has the same id (Sea Bright, Xc7078fe, NOAA 8531804)' do
                expect_noaa_tides(tide_feed_peaks('Xc7078fe', Time.utc(2026, 11, 1), Time.utc(2026, 11, 3)), [
                    [Time.utc(2026, 11, 1, 0, 26), 'Low', 0.295],
                    [Time.utc(2026, 11, 1, 6, 41), 'High', 3.086],
                    [Time.utc(2026, 11, 1, 12, 27), 'Low', 0.526],
                    [Time.utc(2026, 11, 1, 18, 58), 'High', 3.561],
                    [Time.utc(2026, 11, 2, 1, 34), 'Low', 0.338],
                    [Time.utc(2026, 11, 2, 7, 43), 'High', 3.135],
                    [Time.utc(2026, 11, 2, 13, 44), 'Low', 0.595],
                    [Time.utc(2026, 11, 2, 20, 0), 'High', 3.45]
                ])

                expect(engine.station_data('Xc7078fe', 'tide')['name']).to start_with('Sea Bright')
                expect(engine.station_data('Xc7078fe', 'current')['name']).to start_with('Seabright Bridge')
                expect(engine.station_cache_ids).to include('Xc7078fe')
                expect(engine.station_cache_ids).not_to include(a_string_including('@'))
            end

            it 'still predicts the current with the same id as a tide (Seabright Bridge, Xc7078fe)' do
                from, to = Time.utc(2026, 11, 1), Time.utc(2026, 11, 3)
                station = client.current_stations.find { |s| s.bid == 'Xc7078fe' }
                allow(client).to receive(:beginning_of_window).and_return(from)
                allow(client).to receive(:end_of_window).and_return(to)

                events = client.current_data_for(station, from)
                expect(events.map(&:type).uniq).to contain_exactly('flood', 'ebb', 'slack')
                expect(events.map(&:velocity_major).max).to be_between(0.5, 3.0)
            end

            it 'keeps a tide and a current with the same id apart in a warm station cache' do
                client.engine.stations # cold: parses the TCD and writes the station cache
                warm = described_class.new(logger).engine
                expect(warm).not_to receive(:parse_xtide_file)

                %w[Xc7078fe X00ccbd8 Xbfea58e X5ba9a0a X5389118].each do |id|
                    expect(warm.station_data(id, 'tide')['type']).to eq('tide'), id
                    expect(warm.station_data(id, 'current')['type']).to eq('current'), id
                end
            end

            it 'stores the TCD datum offset (mean flow) of a current and predicts around it' do
                # Glacier Bay entrance has a real mean ebb flow of 1.244 knots
                data = engine.station_data('X0114c7a_17')
                expect(data['datum_offset']).to eq(-1.244)

                predictions = engine.generate_predictions('X0114c7a_17', Time.utc(2026, 10, 1), Time.utc(2026, 10, 30), step_seconds: 600)
                mean = predictions.sum { |p| p['height'] } / predictions.size
                expect(mean).to be_within(0.05).of(-1.244)
            end

            it 'gives a current its name depth, and no depth when the name has none' do
                stations = client.current_stations.index_by(&:bid)
                expect(stations['X5016721_13'].depth).to eq(13.0)  # "(depth 13 ft)"
                expect(stations['X2d7f27f'].depth).to be_nil       # Wareham River, no depth in name
            end

            it 'gives the same subordinate events from a warm station cache as from a cold one' do
                # Cold: parses the TCD and writes the station cache.  Warm: a new
                # engine on the same cache dir loads the station cache instead.
                from, to = Time.utc(2026, 10, 7), Time.utc(2026, 10, 9)
                events = lambda do |c|
                    allow(c).to receive(:beginning_of_window).and_return(from)
                    allow(c).to receive(:end_of_window).and_return(to)
                    %w[X2d7f27f X05f2f68].flat_map do |bid|
                        station = c.current_stations.find { |s| s.bid == bid }
                        c.current_data_for(station, from).map { |e| [bid, e.type, e.time, e.velocity_major] }
                    end
                end

                cold = events.(client)
                expect(File).to exist(engine.stations_cache_file)

                warm_client = described_class.new(logger)
                expect(warm_client.engine).not_to receive(:parse_xtide_file)
                warm = events.(warm_client)

                expect(warm_client.engine.station_data('X2d7f27f')).to include('flood_begins' => '-02:09:00', 'ebb_begins' => '-01:38:00')
                expect(cold.count { |e| e[1] == 'slack' }).to be > 0
                expect(warm).to eq(cold)
            end
        end

        describe 'Engine#offset_seconds' do
            let(:engine) { client.engine }

            it 'parses signed [+-]HH:MM[:SS] offsets' do
                expect(engine.offset_seconds('+02:09:00')).to eq(2 * 3600 + 9 * 60)
                expect(engine.offset_seconds('-01:30:00')).to eq(-(3600 + 30 * 60))
                expect(engine.offset_seconds('-08:55:00')).to eq(-(8 * 3600 + 55 * 60))
                expect(engine.offset_seconds('-00:05')).to eq(-300)
                expect(engine.offset_seconds('+00:00:00')).to eq(0)
            end

            it 'treats a missing or null offset as no offset' do
                expect(engine.offset_seconds(nil)).to eq(0)
                expect(engine.offset_seconds('\N')).to eq(0)
            end
        end

        describe 'Engine#subordinate_margin' do
            let(:engine) { client.engine }

            it 'is the largest time offset plus 1 hour' do
                data = { 'h_time_offset' => '+00:30:00', 'l_time_offset' => '+01:10:00',
                         'flood_begins' => '-08:55:00', 'ebb_begins' => '+04:00:00' }
                expect(engine.subordinate_margin(data)).to eq(9.hours + 55.minutes)
            end

            it 'is never less than 2 hours' do
                expect(engine.subordinate_margin({ 'h_time_offset' => '+00:10:00' })).to eq(2.hours)
                expect(engine.subordinate_margin({})).to eq(2.hours)
            end
        end

        describe 'subordinate current without slack offsets' do
            let(:station) { build_station(name: 'Sub', id: 'SUB', bid: 'SUB', provider: 'xtide', depth: nil) }
            let(:peaks) do
                [
                    { 'type' => 'High', 'time' => Time.utc(2026, 10, 7, 6), 'height' => 2.0, 'units' => 'knots' },
                    { 'type' => 'Low', 'time' => Time.utc(2026, 10, 7, 12), 'height' => -1.0, 'units' => 'knots' }
                ]
            end

            before do
                allow(client).to receive(:beginning_of_window).and_return(Time.utc(2026, 10, 7))
                allow(client).to receive(:end_of_window).and_return(Time.utc(2026, 10, 8))
                allow(client.engine).to receive(:station_data).with('SUB', 'current')
                    .and_return({ 'ref_key' => 'REF', 'flood_begins' => nil, 'ebb_begins' => '+00:30:00' })
                allow(client.engine).to receive(:reference_predictions).and_return([])
                allow(client.engine).to receive(:subordinate_peaks).and_return(peaks)
                allow(logger).to receive(:warn)
            end

            it 'interpolates slack between its peaks and logs a warning' do
                events = client.current_data_for(station, Time.utc(2026, 10, 7))

                expect(events.map(&:type)).to eq(%w[flood slack ebb])
                expect(events[1].time.to_time).to be_within(1.minute).of(Time.utc(2026, 10, 7, 10))
                expect(logger).to have_received(:warn).with(/SUB has no flood_begins\/ebb_begins offset.*flood_begins=nil/)
            end
        end
    end

    describe '#max_current_type' do
        it 'labels a maximum above zero as flood and a minimum below zero as ebb' do
            expect(client.max_current_type({ 'type' => 'High', 'height' => 1.2 })).to eq('flood')
            expect(client.max_current_type({ 'type' => 'Low', 'height' => -0.8 })).to eq('ebb')
        end

        it 'does not label the weakest point of an ebb or flood as a max current' do
            # Double ebb: the weakest ebb between two ebb maxima is a maximum below zero
            expect(client.max_current_type({ 'type' => 'High', 'height' => -0.1 })).to be_nil
            expect(client.max_current_type({ 'type' => 'Low', 'height' => 0.1 })).to be_nil
        end
    end

    describe 'TCD constituent loading bug fix' do
        # Bug: loading the TCD file overwrote @constituent_definitions, which the
        # engine's own nodal calculation then failed on with NoMethodError.  The
        # calculation (NodalSchureman) no longer reads @constituent_definitions.
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

            it 'can calculate nodal factors after loading TCD data' do
                # This test reproduces the production bug:
                # 1. Load TCD file (overwrites the constituent definitions)
                # 2. Try to calculate nodal factors (failed on the overwritten definitions)

                # Load stations to trigger TCD parsing
                stations = client.tide_stations
                expect(stations).not_to be_empty

                # Computed nodal factors after the TCD load must not raise. 2101 is
                # outside the TCD table, so this reaches NodalSchureman even in
                # the default tcd mode (2026 would use the TCD tables instead).
                expect(NodalSchureman).to receive(:compute).and_call_original
                factors = nil
                expect {
                    factors = client.engine.send(:get_nodal_factors, 2101, 2, 3, 0.0, 12)
                }.not_to raise_error
                expect(factors.keys).to match_array(NodalSchureman::CONSTITUENTS)
            end

            it 'generates peaks for XTide station without errors' do
                # Find a reference XTide station (has constituents, not subordinate)
                stations = client.tide_stations
                xtide_station = stations.find do |s|
                    s.id.to_s.start_with?('X') && !s.id.to_s.start_with?('Xac')
                end

                skip 'No XTide reference station found' unless xtide_station

                # This should not raise NoMethodError about nil[]
                expect {
                    data = client.tide_data_for(xtide_station, Time.utc(2026, 2, 3))
                    expect(data).to be_an(Array)
                }.not_to raise_error
            end
        end
    end
end

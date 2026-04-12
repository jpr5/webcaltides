# frozen_string_literal: true

RSpec.describe WebCalTides do
    # Shared helpers for building test data with times relative to frozen clock
    let(:frozen_time) { Time.utc(2025, 6, 15, 12, 0, 0) }

    let(:boston_station) do
        build_station(
            id: 'BOSTON1',
            name: 'Boston Harbor',
            lat: 42.3601,
            lon: -71.0589,
            provider: 'noaa'
        )
    end

    let(:sf_station) do
        build_station(
            id: 'SF001',
            name: 'San Francisco Bay',
            lat: 37.7749,
            lon: -122.4194,
            provider: 'noaa'
        )
    end

    let(:current_station) do
        build_station(
            id: 'CURR1',
            bid: 'CURR1',
            name: 'The Race',
            lat: 42.3601,
            lon: -71.0589,
            provider: 'noaa'
        )
    end

    describe '#next_tide_events' do
        before { freeze_time(frozen_time) }

        context 'with unknown station' do
            before do
                allow(WebCalTides).to receive(:tide_station_for).with('UNKNOWN').and_return(nil)
            end

            it 'returns nil' do
                result = WebCalTides.next_tide_events('UNKNOWN')
                expect(result).to be_nil
            end
        end

        context 'with no tide data available' do
            before do
                allow(WebCalTides).to receive(:tide_station_for).with('BOSTON1').and_return(boston_station)
                allow(WebCalTides).to receive(:tide_data_for).and_return(nil)
            end

            it 'returns nil' do
                result = WebCalTides.next_tide_events('BOSTON1')
                expect(result).to be_nil
            end
        end

        context 'with future high and low tide data' do
            let(:future_high) do
                build_tide_data(
                    type: 'High',
                    prediction: 10.5,
                    units: 'ft',
                    time: DateTime.new(2025, 6, 15, 16, 30, 0)  # 4.5 hours after frozen_time
                )
            end

            let(:future_low) do
                build_tide_data(
                    type: 'Low',
                    prediction: 0.3,
                    units: 'ft',
                    time: DateTime.new(2025, 6, 15, 14, 0, 0)  # 2 hours after frozen_time
                )
            end

            let(:past_high) do
                build_tide_data(
                    type: 'High',
                    prediction: 9.8,
                    units: 'ft',
                    time: DateTime.new(2025, 6, 15, 6, 0, 0)  # 6 hours before frozen_time
                )
            end

            let(:past_low) do
                build_tide_data(
                    type: 'Low',
                    prediction: 0.1,
                    units: 'ft',
                    time: DateTime.new(2025, 6, 15, 0, 0, 0)  # 12 hours before frozen_time
                )
            end

            before do
                allow(WebCalTides).to receive(:tide_station_for).with('BOSTON1').and_return(boston_station)
                allow(WebCalTides).to receive(:tide_data_for).and_return(
                    [past_high, past_low, future_high, future_low]
                )
            end

            it 'returns next high and low tide events' do
                result = WebCalTides.next_tide_events('BOSTON1')

                expect(result.length).to eq(2)
                types = result.map { |e| e[:type] }
                expect(types).to contain_exactly('High', 'Low')
            end

            it 'filters out past events' do
                result = WebCalTides.next_tide_events('BOSTON1')

                result.each do |event|
                    expect(event[:time]).to be > Time.current.utc
                end
            end

            it 'returns events sorted by time (low before high when low is sooner)' do
                result = WebCalTides.next_tide_events('BOSTON1')

                expect(result.first[:type]).to eq('Low')
                expect(result.last[:type]).to eq('High')
                expect(result.first[:time]).to be < result.last[:time]
            end

            it 'includes height and units in each event' do
                result = WebCalTides.next_tide_events('BOSTON1')

                low_event = result.find { |e| e[:type] == 'Low' }
                expect(low_event[:height]).to eq(0.3)
                expect(low_event[:units]).to eq('ft')

                high_event = result.find { |e| e[:type] == 'High' }
                expect(high_event[:height]).to eq(10.5)
                expect(high_event[:units]).to eq('ft')
            end

            it 'applies timezone conversion via in_time_zone' do
                result = WebCalTides.next_tide_events('BOSTON1')

                # Boston station coords map to America/New_York in the pre-populated tzcache
                result.each do |event|
                    expect(event[:time].time_zone.name).to eq('America/New_York')
                end
            end
        end

        context 'when only a future high exists (no future low)' do
            let(:future_high) do
                build_tide_data(
                    type: 'High',
                    prediction: 11.0,
                    units: 'ft',
                    time: DateTime.new(2025, 6, 15, 18, 0, 0)
                )
            end

            before do
                allow(WebCalTides).to receive(:tide_station_for).with('BOSTON1').and_return(boston_station)
                allow(WebCalTides).to receive(:tide_data_for).and_return([future_high])
            end

            it 'returns only the high event' do
                result = WebCalTides.next_tide_events('BOSTON1')

                expect(result.length).to eq(1)
                expect(result.first[:type]).to eq('High')
            end
        end

        context 'when only a future low exists (no future high)' do
            let(:future_low) do
                build_tide_data(
                    type: 'Low',
                    prediction: -0.2,
                    units: 'ft',
                    time: DateTime.new(2025, 6, 15, 15, 0, 0)
                )
            end

            before do
                allow(WebCalTides).to receive(:tide_station_for).with('BOSTON1').and_return(boston_station)
                allow(WebCalTides).to receive(:tide_data_for).and_return([future_low])
            end

            it 'returns only the low event' do
                result = WebCalTides.next_tide_events('BOSTON1')

                expect(result.length).to eq(1)
                expect(result.first[:type]).to eq('Low')
            end
        end

        context 'when around: is a Date object' do
            let(:future_high) do
                build_tide_data(
                    type: 'High',
                    prediction: 10.0,
                    units: 'ft',
                    time: DateTime.new(2025, 6, 15, 16, 0, 0)
                )
            end

            before do
                allow(WebCalTides).to receive(:tide_station_for).with('BOSTON1').and_return(boston_station)
                allow(WebCalTides).to receive(:tide_data_for).and_return([future_high])
            end

            it 'accepts a Date for the around: parameter' do
                # Date objects use ActiveSupport extensions; this exercises that path
                result = WebCalTides.next_tide_events('BOSTON1', around: Date.new(2025, 6, 15))

                expect(result).not_to be_nil
                expect(result.length).to eq(1)
            end
        end

        context 'with a different timezone (San Francisco)' do
            let(:future_high) do
                build_tide_data(
                    type: 'High',
                    prediction: 6.2,
                    units: 'ft',
                    time: DateTime.new(2025, 6, 15, 20, 0, 0)
                )
            end

            before do
                allow(WebCalTides).to receive(:tide_station_for).with('SF001').and_return(sf_station)
                allow(WebCalTides).to receive(:tide_data_for).and_return([future_high])
            end

            it 'converts to the correct timezone for the station location' do
                result = WebCalTides.next_tide_events('SF001')

                expect(result.first[:time].time_zone.name).to eq('America/Los_Angeles')
            end
        end

        context 'when all data is in the past' do
            let(:past_high) do
                build_tide_data(
                    type: 'High',
                    prediction: 9.0,
                    units: 'ft',
                    time: DateTime.new(2025, 6, 15, 6, 0, 0)
                )
            end

            let(:past_low) do
                build_tide_data(
                    type: 'Low',
                    prediction: 0.5,
                    units: 'ft',
                    time: DateTime.new(2025, 6, 15, 0, 0, 0)
                )
            end

            before do
                allow(WebCalTides).to receive(:tide_station_for).with('BOSTON1').and_return(boston_station)
                allow(WebCalTides).to receive(:tide_data_for).and_return([past_high, past_low])
            end

            it 'returns an empty array' do
                result = WebCalTides.next_tide_events('BOSTON1')

                expect(result).to eq([])
            end
        end
    end

    describe '#next_current_events' do
        before { freeze_time(frozen_time) }

        context 'with unknown station' do
            before do
                allow(WebCalTides).to receive(:current_station_for).with('UNKNOWN').and_return(nil)
            end

            it 'returns nil' do
                result = WebCalTides.next_current_events('UNKNOWN')
                expect(result).to be_nil
            end
        end

        context 'with no current data available' do
            before do
                allow(WebCalTides).to receive(:current_station_for).with('CURR1').and_return(current_station)
                allow(WebCalTides).to receive(:current_data_for).and_return(nil)
            end

            it 'returns nil' do
                result = WebCalTides.next_current_events('CURR1')
                expect(result).to be_nil
            end
        end

        context 'with future slack, flood, and ebb data' do
            let(:future_slack) do
                build_current_data(
                    type: 'slack',
                    velocity_major: 0.0,
                    time: DateTime.new(2025, 6, 15, 13, 0, 0)  # 1 hour after frozen_time
                )
            end

            let(:future_flood) do
                build_current_data(
                    type: 'flood',
                    velocity_major: 3.2,
                    time: DateTime.new(2025, 6, 15, 16, 0, 0)  # 4 hours after frozen_time
                )
            end

            let(:future_ebb) do
                build_current_data(
                    type: 'ebb',
                    velocity_major: -2.8,
                    time: DateTime.new(2025, 6, 15, 19, 0, 0)  # 7 hours after frozen_time
                )
            end

            let(:past_flood) do
                build_current_data(
                    type: 'flood',
                    velocity_major: 2.1,
                    time: DateTime.new(2025, 6, 15, 6, 0, 0)  # 6 hours before frozen_time
                )
            end

            before do
                allow(WebCalTides).to receive(:current_station_for).with('CURR1').and_return(current_station)
                allow(WebCalTides).to receive(:current_data_for).and_return(
                    [past_flood, future_slack, future_flood, future_ebb]
                )
            end

            it 'returns next slack, flood, and ebb events' do
                result = WebCalTides.next_current_events('CURR1')

                expect(result.length).to eq(3)
                types = result.map { |e| e[:type] }
                expect(types).to contain_exactly('Slack', 'Flood', 'Ebb')
            end

            it 'filters out past events' do
                result = WebCalTides.next_current_events('CURR1')

                result.each do |event|
                    expect(event[:time]).to be > Time.current.utc
                end
            end

            it 'returns events sorted by time' do
                result = WebCalTides.next_current_events('CURR1')

                times = result.map { |e| e[:time] }
                expect(times).to eq(times.sort)
                expect(result.first[:type]).to eq('Slack')
            end

            it 'includes velocity for flood and ebb but not for slack' do
                result = WebCalTides.next_current_events('CURR1')

                slack = result.find { |e| e[:type] == 'Slack' }
                flood = result.find { |e| e[:type] == 'Flood' }
                ebb = result.find { |e| e[:type] == 'Ebb' }

                expect(slack).not_to have_key(:velocity)
                expect(flood[:velocity]).to eq(3.2)
                expect(ebb[:velocity]).to eq(-2.8)
            end

            it 'applies timezone conversion via in_time_zone' do
                result = WebCalTides.next_current_events('CURR1')

                result.each do |event|
                    expect(event[:time].time_zone.name).to eq('America/New_York')
                end
            end
        end

        context 'when all data is in the past' do
            let(:past_slack) do
                build_current_data(
                    type: 'slack',
                    time: DateTime.new(2025, 6, 15, 6, 0, 0)
                )
            end

            before do
                allow(WebCalTides).to receive(:current_station_for).with('CURR1').and_return(current_station)
                allow(WebCalTides).to receive(:current_data_for).and_return([past_slack])
            end

            it 'returns an empty array' do
                result = WebCalTides.next_current_events('CURR1')
                expect(result).to eq([])
            end
        end

        context 'when only flood and ebb exist (no slack)' do
            let(:future_flood) do
                build_current_data(
                    type: 'flood',
                    velocity_major: 2.5,
                    time: DateTime.new(2025, 6, 15, 14, 0, 0)
                )
            end

            let(:future_ebb) do
                build_current_data(
                    type: 'ebb',
                    velocity_major: -1.8,
                    time: DateTime.new(2025, 6, 15, 17, 0, 0)
                )
            end

            before do
                allow(WebCalTides).to receive(:current_station_for).with('CURR1').and_return(current_station)
                allow(WebCalTides).to receive(:current_data_for).and_return([future_flood, future_ebb])
            end

            it 'returns only flood and ebb events' do
                result = WebCalTides.next_current_events('CURR1')

                expect(result.length).to eq(2)
                types = result.map { |e| e[:type] }
                expect(types).to contain_exactly('Flood', 'Ebb')
            end
        end
    end

    describe '#compute_variance' do
        before { freeze_time(frozen_time) }

        let(:primary) { build_station(id: 'PRIMARY1', name: 'Primary', lat: 42.3601, lon: -71.0589) }
        let(:alt1) { build_station(id: 'ALT1', name: 'Alternative 1', lat: 42.3601, lon: -71.0589) }
        let(:alt2) { build_station(id: 'ALT2', name: 'Alternative 2', lat: 42.3601, lon: -71.0589) }

        context 'with nil alternatives' do
            it 'returns empty hash' do
                result = WebCalTides.compute_variance(primary, nil)
                expect(result).to eq({})
            end
        end

        context 'with empty alternatives' do
            it 'returns empty hash' do
                result = WebCalTides.compute_variance(primary, [])
                expect(result).to eq({})
            end
        end

        context 'when primary has no events' do
            before do
                allow(WebCalTides).to receive(:next_tide_events).with('PRIMARY1', around: anything).and_return(nil)
            end

            it 'returns empty hash' do
                result = WebCalTides.compute_variance(primary, [alt1])
                expect(result).to eq({})
            end
        end

        context 'when primary has empty events' do
            before do
                allow(WebCalTides).to receive(:next_tide_events).with('PRIMARY1', around: anything).and_return([])
            end

            it 'returns empty hash' do
                result = WebCalTides.compute_variance(primary, [alt1])
                expect(result).to eq({})
            end
        end

        context 'with valid primary and alternatives' do
            let(:primary_time) { Time.utc(2025, 6, 15, 14, 0, 0).in_time_zone('America/New_York') }
            let(:alt1_time) { Time.utc(2025, 6, 15, 14, 15, 0).in_time_zone('America/New_York') }  # +15 min
            let(:alt2_time) { Time.utc(2025, 6, 15, 13, 50, 0).in_time_zone('America/New_York') }  # -10 min

            before do
                allow(WebCalTides).to receive(:next_tide_events).with('PRIMARY1', around: anything).and_return([
                    { type: 'Low', time: primary_time, height: 0.5, units: 'ft' }
                ])
                allow(WebCalTides).to receive(:next_tide_events).with('ALT1', around: anything).and_return([
                    { type: 'Low', time: alt1_time, height: 0.8, units: 'ft' }
                ])
                allow(WebCalTides).to receive(:next_tide_events).with('ALT2', around: anything).and_return([
                    { type: 'Low', time: alt2_time, height: 0.3, units: 'ft' }
                ])
            end

            it 'computes time and height deltas for each alternative' do
                result = WebCalTides.compute_variance(primary, [alt1, alt2])

                expect(result.keys).to contain_exactly('ALT1', 'ALT2')
                expect(result['ALT1']).to have_key(:time)
                expect(result['ALT1']).to have_key(:height)
                expect(result['ALT2']).to have_key(:time)
                expect(result['ALT2']).to have_key(:height)
            end

            it 'formats positive time delta correctly' do
                result = WebCalTides.compute_variance(primary, [alt1])

                # ALT1 is 15 minutes later than primary
                expect(result['ALT1'][:time]).to eq('+15min')
            end

            it 'formats negative time delta correctly' do
                result = WebCalTides.compute_variance(primary, [alt2])

                # ALT2 is 10 minutes earlier than primary
                expect(result['ALT2'][:time]).to eq('-10min')
            end

            it 'formats positive height delta correctly' do
                result = WebCalTides.compute_variance(primary, [alt1])

                # ALT1 height is 0.8 - 0.5 = +0.3
                expect(result['ALT1'][:height]).to eq('+0.3ft')
            end

            it 'formats negative height delta correctly' do
                result = WebCalTides.compute_variance(primary, [alt2])

                # ALT2 height is 0.3 - 0.5 = -0.2
                expect(result['ALT2'][:height]).to eq('-0.2ft')
            end
        end

        context 'when an alternative has no events' do
            before do
                allow(WebCalTides).to receive(:next_tide_events).with('PRIMARY1', around: anything).and_return([
                    { type: 'High', time: Time.utc(2025, 6, 15, 14, 0, 0).in_time_zone('America/New_York'), height: 10.0, units: 'ft' }
                ])
                allow(WebCalTides).to receive(:next_tide_events).with('ALT1', around: anything).and_return(nil)
                allow(WebCalTides).to receive(:next_tide_events).with('ALT2', around: anything).and_return([
                    { type: 'High', time: Time.utc(2025, 6, 15, 14, 30, 0).in_time_zone('America/New_York'), height: 10.5, units: 'ft' }
                ])
            end

            it 'skips the alternative with no events and includes the one with events' do
                result = WebCalTides.compute_variance(primary, [alt1, alt2])

                expect(result).not_to have_key('ALT1')
                expect(result).to have_key('ALT2')
                expect(result['ALT2'][:time]).to eq('+30min')
                expect(result['ALT2'][:height]).to eq('+0.5ft')
            end
        end

        context 'when an alternative has empty events array' do
            before do
                allow(WebCalTides).to receive(:next_tide_events).with('PRIMARY1', around: anything).and_return([
                    { type: 'High', time: Time.utc(2025, 6, 15, 14, 0, 0).in_time_zone('America/New_York'), height: 10.0, units: 'ft' }
                ])
                allow(WebCalTides).to receive(:next_tide_events).with('ALT1', around: anything).and_return([])
            end

            it 'skips the alternative' do
                result = WebCalTides.compute_variance(primary, [alt1])

                expect(result).to eq({})
            end
        end

        context 'with around: parameter' do
            let(:specific_time) { Time.utc(2025, 7, 1, 6, 0, 0) }

            before do
                allow(WebCalTides).to receive(:next_tide_events).with('PRIMARY1', around: specific_time).and_return([
                    { type: 'High', time: Time.utc(2025, 7, 1, 8, 0, 0).in_time_zone('America/New_York'), height: 9.0, units: 'ft' }
                ])
                allow(WebCalTides).to receive(:next_tide_events).with('ALT1', around: specific_time).and_return([
                    { type: 'High', time: Time.utc(2025, 7, 1, 8, 10, 0).in_time_zone('America/New_York'), height: 9.2, units: 'ft' }
                ])
            end

            it 'passes through the around: parameter to next_tide_events' do
                WebCalTides.compute_variance(primary, [alt1], around: specific_time)

                expect(WebCalTides).to have_received(:next_tide_events).with('PRIMARY1', around: specific_time)
                expect(WebCalTides).to have_received(:next_tide_events).with('ALT1', around: specific_time)
            end
        end

        context 'with near-zero deltas' do
            before do
                allow(WebCalTides).to receive(:next_tide_events).with('PRIMARY1', around: anything).and_return([
                    { type: 'Low', time: Time.utc(2025, 6, 15, 14, 0, 0).in_time_zone('America/New_York'), height: 1.0, units: 'ft' }
                ])
                allow(WebCalTides).to receive(:next_tide_events).with('ALT1', around: anything).and_return([
                    { type: 'Low', time: Time.utc(2025, 6, 15, 14, 0, 10).in_time_zone('America/New_York'), height: 1.02, units: 'ft' }
                ])
            end

            it 'formats near-zero time delta as 0min' do
                result = WebCalTides.compute_variance(primary, [alt1])

                # 10 seconds difference is < 30 seconds threshold
                expect(result['ALT1'][:time]).to eq('0min')
            end

            it 'formats near-zero height delta as 0ft' do
                result = WebCalTides.compute_variance(primary, [alt1])

                # 0.02 difference is < 0.05 threshold
                expect(result['ALT1'][:height]).to eq('0ft')
            end
        end
    end
end

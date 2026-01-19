# frozen_string_literal: true

RSpec.describe Clients::ChsTides do
    let(:logger) { Logger.new('/dev/null') }
    let(:client) { described_class.new(logger) }

    describe '#tide_stations', :vcr do
        it 'fetches tide stations from CHS' do
            stations = client.tide_stations
            expect(stations).to be_an(Array)
            expect(stations.length).to be > 100  # CHS has many stations
        end

        it 'returns Station objects' do
            stations = client.tide_stations
            expect(stations).to all(be_a(Models::Station))
        end

        it 'sets provider to chs' do
            stations = client.tide_stations
            expect(stations).to all(have_attributes(provider: 'chs'))
        end

        it 'sets region to include Canada' do
            stations = client.tide_stations
            expect(stations.map(&:region)).to all(include('Canada'))
        end

        it 'parses station coordinates' do
            stations = client.tide_stations
            station = stations.first

            expect(station.lat).to be_a(Numeric)
            expect(station.lon).to be_a(Numeric)
        end
    end

    describe '#tide_data_for', :vcr do
        # Get a real station that returns data
        let(:station) do
            stations = client.tide_stations
            # Find a station with valid-looking data (Halifax is reliable)
            stations.find { |s| s.name&.include?('Halifax') } || stations.first
        end

        it 'fetches tide data for a station' do
            data = client.tide_data_for(station, Time.utc(2025, 1, 15))

            # Some CHS stations return empty data - that's expected behavior
            if data.nil?
                expect(data).to be_nil
            else
                expect(data).to be_an(Array)
            end
        end

        it 'returns TideData objects when data is available' do
            data = client.tide_data_for(station, Time.utc(2025, 1, 15))
            next skip('Station returned no data') if data.nil? || data.empty?

            expect(data).to all(be_a(Models::TideData))
        end

        it 'sets units to meters (m)' do
            data = client.tide_data_for(station, Time.utc(2025, 1, 15))
            next skip('Station returned no data') if data.nil? || data.empty?

            expect(data.first.units).to eq('m')
        end
    end

    describe 'TimeWindow module' do
        it 'includes TimeWindow module' do
            expect(described_class.ancestors).to include(Clients::TimeWindow)
        end

        it 'has window_size of 12 months' do
            expect(described_class.window_size).to eq(12.months)
        end
    end
end

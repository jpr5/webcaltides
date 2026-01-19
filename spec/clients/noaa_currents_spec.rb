# frozen_string_literal: true

RSpec.describe Clients::NoaaCurrents do
    let(:logger) { Logger.new('/dev/null') }
    let(:client) { described_class.new(logger) }

    describe '#current_stations', :vcr do
        it 'fetches current stations from NOAA API' do
            stations = client.current_stations
            expect(stations).to be_an(Array)
            expect(stations.length).to be > 50  # NOAA has many current stations
        end

        it 'returns Station objects' do
            stations = client.current_stations
            expect(stations).to all(be_a(Models::Station))
        end

        it 'sets provider to noaa' do
            stations = client.current_stations
            expect(stations).to all(have_attributes(provider: 'noaa'))
        end

        it 'includes bid (bin id) for depth-specific stations' do
            stations = client.current_stations
            station = stations.first

            expect(station.bid).to be_a(String)
            expect(station.bid).to match(/\w+_\d+/)  # format: ID_bin
        end
    end

    describe '#current_data_for', :vcr do
        # Use a well-known current station
        let(:station) do
            # Get a real station from the API to ensure valid data
            stations = client.current_stations
            stations.first
        end

        it 'fetches current data for a station' do
            data = client.current_data_for(station, Time.utc(2025, 1, 15))

            expect(data).to be_an(Array)
            expect(data.length).to be > 0
        end

        it 'returns CurrentData objects' do
            data = client.current_data_for(station, Time.utc(2025, 1, 15))
            expect(data).to all(be_a(Models::CurrentData))
        end

        it 'includes flood, ebb, and slack types' do
            data = client.current_data_for(station, Time.utc(2025, 1, 15))
            types = data.map(&:type).uniq

            # Should have at least some of these types
            expect(types).to include('flood').or include('ebb').or include('slack')
        end
    end

    describe 'TimeWindow module' do
        it 'includes TimeWindow module' do
            expect(described_class.ancestors).to include(Clients::TimeWindow)
        end

        it 'has window_size of 12 months' do
            # NOAA currents won't do more than 366 days
            expect(described_class.window_size).to eq(12.months)
        end
    end
end

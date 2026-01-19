# frozen_string_literal: true

RSpec.describe Clients::NoaaCurrents do
  let(:logger) { Logger.new('/dev/null') }
  let(:client) { described_class.new(logger) }

  describe '#current_stations' do
    context 'with mocked API response' do
      let(:stations_response) do
        {
          'stations' => [
            {
              'id' => 'ACT5546',
              'name' => 'Cape Cod Canal, East Entrance',
              'lat' => 41.7765,
              'lng' => -70.4792,
              'currbin' => 1,
              'depth' => 10,
              'type' => 'S'  # Strong (not weak)
            },
            {
              'id' => 'BOS1301',
              'name' => 'Boston Harbor Entrance',
              'lat' => 42.3275,
              'lng' => -70.8912,
              'currbin' => 1,
              'depth' => 5,
              'type' => 'S'
            }
          ]
        }.to_json
      end

      before do
        stub_request(:get, /api.tidesandcurrents.noaa.gov.*currentpredictions/)
          .to_return(status: 200, body: stations_response, headers: { 'Content-Type' => 'application/json' })
      end

      it 'fetches current stations from NOAA API' do
        stations = client.current_stations
        expect(stations).to be_an(Array)
        expect(stations.length).to eq(2)
      end

      it 'returns Station objects' do
        stations = client.current_stations
        expect(stations).to all(be_a(Models::Station))
      end

      it 'sets provider to noaa' do
        stations = client.current_stations
        expect(stations).to all(have_attributes(provider: 'noaa'))
      end
    end
  end

  describe '#current_data_for' do
    let(:station) do
      Models::Station.new(
        name: 'Cape Cod Canal',
        id: 'ACT5546',
        bid: 'ACT5546_1',
        public_id: 'ACT5546',
        provider: 'noaa',
        lat: 41.7765,
        lon: -70.4792,
        depth: 10,
        url: 'https://tidesandcurrents.noaa.gov/noaacurrents/predictions?id=ACT5546_1'
      )
    end

    let(:current_data_response) do
      {
        'current_predictions' => {
          'cp' => [
            {
              'Time' => '2025-06-15 08:30',
              'Type' => 'flood',
              'Velocity_Major' => 2.5,
              'meanFloodDir' => '045',
              'meanEbbDir' => '225',
              'Bin' => '1',
              'Depth' => '10'
            },
            {
              'Time' => '2025-06-15 12:00',
              'Type' => 'slack',
              'Velocity_Major' => 0.0,
              'Bin' => '1',
              'Depth' => '10'
            },
            {
              'Time' => '2025-06-15 15:30',
              'Type' => 'ebb',
              'Velocity_Major' => -2.8,
              'meanFloodDir' => '045',
              'meanEbbDir' => '225',
              'Bin' => '1',
              'Depth' => '10'
            }
          ]
        }
      }.to_json
    end

    before do
      stub_request(:get, /api.tidesandcurrents.noaa.gov.*currents_predictions/)
        .to_return(status: 200, body: current_data_response, headers: { 'Content-Type' => 'application/json' })
    end

    it 'fetches current data for a station' do
      data = client.current_data_for(station, Time.utc(2025, 6, 15))

      expect(data).to be_an(Array)
      expect(data.length).to eq(3)
    end

    it 'returns CurrentData objects' do
      data = client.current_data_for(station, Time.utc(2025, 6, 15))
      expect(data).to all(be_a(Models::CurrentData))
    end

    it 'parses flood, ebb, and slack types' do
      data = client.current_data_for(station, Time.utc(2025, 6, 15))

      types = data.map(&:type)
      expect(types).to include('flood', 'slack', 'ebb')
    end
  end
end

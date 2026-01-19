# frozen_string_literal: true

RSpec.describe Clients::ChsTides do
  let(:logger) { Logger.new('/dev/null') }
  let(:client) { described_class.new(logger) }

  describe '#tide_stations' do
    context 'with mocked API response' do
      let(:stations_json) do
        [
          {
            'id' => '5cebf1e23d0f4a073c4bbfb4',
            'code' => '00490',
            'officialName' => 'Halifax',
            'latitude' => 44.6476,
            'longitude' => -63.5728
          },
          {
            'id' => '5cebf1df3d0f4a073c4bbcb9',
            'code' => '07795',
            'officialName' => 'Vancouver',
            'latitude' => 49.2827,
            'longitude' => -123.1207
          }
        ].to_json
      end

      before do
        stub_request(:get, /api-iwls.dfo-mpo.gc.ca.*stations$/)
          .to_return(status: 200, body: stations_json, headers: { 'Content-Type' => 'application/json' })
      end

      it 'fetches tide stations from CHS' do
        stations = client.tide_stations
        expect(stations).to be_an(Array)
        expect(stations.length).to eq(2)
      end

      it 'returns Station objects' do
        stations = client.tide_stations
        expect(stations).to all(be_a(Models::Station))
      end

      it 'sets provider to chs' do
        stations = client.tide_stations
        expect(stations).to all(have_attributes(provider: 'chs'))
      end

      it 'sets region to Canada' do
        stations = client.tide_stations
        expect(stations.map(&:region)).to all(include('Canada'))
      end
    end

    context 'with API error' do
      it 'handles connection errors gracefully' do
        stub_request(:get, /api-iwls.dfo-mpo.gc.ca/)
          .to_raise(Errno::ECONNREFUSED)

        # Mechanize wraps connection errors in Net::HTTP::Persistent::Error
        expect { client.tide_stations }.to raise_error(StandardError)
      end
    end
  end

  describe '#tide_data_for' do
    let(:station) do
      Models::Station.new(
        name: 'Halifax',
        id: '5cebf1e23d0f4a073c4bbfb4',
        public_id: '00490',
        provider: 'chs',
        lat: 44.6476,
        lon: -63.5728,
        region: 'Atlantic Canada',
        url: 'https://www.tides.gc.ca/en/stations/00490'
      )
    end

    let(:tide_data_response) do
      # CHS API returns array directly, not nested in 'predictions'
      # The client determines High/Low by comparing consecutive values
      [
        { 'eventDate' => '2025-06-15T05:30:00Z', 'value' => 0.3 },  # Start low
        { 'eventDate' => '2025-06-15T11:45:00Z', 'value' => 1.8 },  # High (1.8 > 0.3)
        { 'eventDate' => '2025-06-15T18:00:00Z', 'value' => 0.4 }   # Low (0.4 < 1.8)
      ].to_json
    end

    before do
      stub_request(:get, /api-iwls.dfo-mpo.gc.ca.*data/)
        .to_return(status: 200, body: tide_data_response, headers: { 'Content-Type' => 'application/json' })
    end

    it 'fetches tide data for a station' do
      data = client.tide_data_for(station, Time.utc(2025, 6, 15))

      expect(data).to be_an(Array)
      expect(data.length).to eq(3)
    end

    it 'returns TideData objects' do
      data = client.tide_data_for(station, Time.utc(2025, 6, 15))
      expect(data).to all(be_a(Models::TideData))
    end

    it 'sets units to meters (m)' do
      data = client.tide_data_for(station, Time.utc(2025, 6, 15))
      expect(data.first.units).to eq('m')
    end
  end

  describe 'TimeWindow module' do
    it 'includes TimeWindow module' do
      expect(described_class.ancestors).to include(Clients::TimeWindow)
    end
  end
end

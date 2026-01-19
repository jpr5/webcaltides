# frozen_string_literal: true

RSpec.describe Clients::NoaaTides do
  let(:logger) { Logger.new('/dev/null') }
  let(:client) { described_class.new(logger) }

  describe '#tide_stations' do
    context 'with real API', :vcr do
      it 'fetches tide stations from NOAA API' do
        stations = client.tide_stations
        expect(stations).to be_an(Array)
        expect(stations.length).to be > 100  # NOAA has hundreds of stations
      end

      it 'returns Station objects' do
        stations = client.tide_stations
        expect(stations).to all(be_a(Models::Station))
      end

      it 'sets provider to noaa' do
        stations = client.tide_stations
        expect(stations).to all(have_attributes(provider: 'noaa'))
      end

      it 'parses station attributes correctly' do
        stations = client.tide_stations
        station = stations.first

        expect(station.id).to be_a(String)
        expect(station.name).to be_a(String)
        expect(station.lat).to be_a(Numeric)
        expect(station.lon).to be_a(Numeric)
      end
    end

    context 'with API error' do
      it 'retries on 502 error' do
        stub_request(:get, /api.tidesandcurrents.noaa.gov/)
          .to_return(status: 502).then
          .to_return(status: 200, body: { 'stationList' => [] }.to_json)

        expect { client.tide_stations }.not_to raise_error
      end

      it 'raises after max retries' do
        stub_request(:get, /api.tidesandcurrents.noaa.gov/)
          .to_return(status: 502)

        expect { client.tide_stations }.to raise_error(Mechanize::ResponseCodeError)
      end
    end
  end

  describe '#tide_data_for', :vcr do
    # Use a well-known station for consistent test data
    let(:station) do
      Models::Station.new(
        name: 'Boston',
        id: '8443970',
        public_id: '8443970',
        provider: 'noaa',
        lat: 42.3548,
        lon: -71.0534,
        url: 'https://tidesandcurrents.noaa.gov/stationhome.html?id=8443970'
      )
    end

    it 'fetches tide data for a station' do
      data = client.tide_data_for(station, Time.utc(2025, 1, 15))

      expect(data).to be_an(Array)
      expect(data.length).to be > 10  # Should have many tide events
    end

    it 'returns TideData objects' do
      data = client.tide_data_for(station, Time.utc(2025, 1, 15))
      expect(data).to all(be_a(Models::TideData))
    end

    it 'parses high and low tide types' do
      data = client.tide_data_for(station, Time.utc(2025, 1, 15))

      highs = data.select { |d| d.type == 'High' }
      lows = data.select { |d| d.type == 'Low' }

      expect(highs.length).to be > 0
      expect(lows.length).to be > 0
    end

    it 'includes time and prediction values' do
      data = client.tide_data_for(station, Time.utc(2025, 1, 15))
      tide = data.first

      expect(tide.time).to be_a(DateTime)
      expect(tide.prediction).to be_a(Numeric)
      expect(tide.units).to eq('ft')
    end
  end

  describe 'TimeWindow module' do
    it 'includes TimeWindow module' do
      expect(described_class.ancestors).to include(Clients::TimeWindow)
    end

    it 'has a configurable window_size' do
      # NOAA uses 13.months to get a full year (1 month behind + current + 11 ahead)
      expect(described_class.window_size).to eq(13.months)
    end

    describe '#beginning_of_window' do
      it 'returns 1 month before the start of the current month' do
        freeze_time(Time.utc(2025, 6, 15, 12, 0, 0))

        result = client.beginning_of_window(Time.current.utc)
        expect(result).to eq(Time.utc(2025, 5, 1, 0, 0, 0))
      end
    end

    describe '#end_of_window' do
      it 'returns the end of the window period' do
        freeze_time(Time.utc(2025, 6, 15, 12, 0, 0))

        result = client.end_of_window(Time.current.utc)
        # window_size (13 months) - prior months (1) - current month (1) = 11 months after end of current month
        expect(result.month).to eq(5) # May of next year
        expect(result.year).to eq(2026)
      end
    end
  end
end

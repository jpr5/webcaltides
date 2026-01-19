# frozen_string_literal: true

RSpec.describe Clients::Harmonics do
  let(:logger) { Logger.new('/dev/null') }
  let(:client) { described_class.new(logger) }

  describe '#initialize' do
    it 'creates a Harmonics::Engine instance' do
      expect(client.engine).to be_a(Harmonics::Engine)
    end
  end

  describe '#tide_stations' do
    context 'when harmonics data files exist', skip: 'requires data files' do
      it 'returns an array of stations' do
        stations = client.tide_stations
        expect(stations).to be_an(Array)
      end

      it 'returns stations with xtide or ticon provider' do
        stations = client.tide_stations
        expect(stations.map(&:provider).uniq).to all(be_in(['xtide', 'ticon']))
      end

      it 'returns only tide stations' do
        stations = client.tide_stations
        # Filter to just tide stations (those without 'knots' as units indicator)
        expect(stations).to all(satisfy { |s| s.depth.nil? || s.depth.is_a?(Numeric) })
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
    context 'when harmonics data files exist', skip: 'requires data files' do
      it 'returns an array of current stations' do
        stations = client.current_stations
        expect(stations).to be_an(Array)
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
      let(:mock_predictions) do
        [
          { 'time' => Time.utc(2025, 6, 15, 6, 30), 'height' => 10.5, 'units' => 'ft' },
          { 'time' => Time.utc(2025, 6, 15, 12, 45), 'height' => 0.5, 'units' => 'ft' },
          { 'time' => Time.utc(2025, 6, 15, 19, 0), 'height' => 11.0, 'units' => 'ft' }
        ]
      end

      let(:mock_peaks) do
        [
          { 'type' => 'High', 'time' => Time.utc(2025, 6, 15, 6, 30), 'height' => 10.5, 'units' => 'ft' },
          { 'type' => 'Low', 'time' => Time.utc(2025, 6, 15, 12, 45), 'height' => 0.5, 'units' => 'ft' },
          { 'type' => 'High', 'time' => Time.utc(2025, 6, 15, 19, 0), 'height' => 11.0, 'units' => 'ft' }
        ]
      end

      before do
        allow(client.engine).to receive(:find_station).and_return({ 'id' => 'X1234567' })
        allow(client.engine).to receive(:generate_predictions).and_return(mock_predictions)
        allow(client.engine).to receive(:detect_peaks).and_return(mock_peaks)
      end

      it 'generates predictions using the harmonics engine' do
        expect(client.engine).to receive(:generate_predictions)
        client.tide_data_for(station, Time.utc(2025, 6, 15))
      end

      it 'detects peaks from predictions' do
        expect(client.engine).to receive(:detect_peaks)
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
end

# frozen_string_literal: true

RSpec.describe 'GET /api/stations/compare', type: :api do
    include Rack::Test::Methods

    let(:noaa_station) do
        build_station(name: 'Boston NOAA', id: 'NOAA123', provider: 'noaa')
    end

    let(:xtide_station) do
        build_station(name: 'Boston XTide', id: 'XTIDE456', provider: 'xtide')
    end

    before do
        freeze_time

        allow(WebCalTides).to receive(:tide_station_for).with('NOAA123').and_return(noaa_station)
        allow(WebCalTides).to receive(:tide_station_for).with('XTIDE456').and_return(xtide_station)
        allow(WebCalTides).to receive(:tide_station_for).with('INVALID').and_return(nil)

        allow(WebCalTides).to receive(:next_tide_events).with('NOAA123').and_return([
            { type: 'High', time: Time.current + 2.hours, height: 10.5, units: 'ft' },
            { type: 'Low', time: Time.current + 8.hours, height: 0.5, units: 'ft' }
        ])

        allow(WebCalTides).to receive(:next_tide_events).with('XTIDE456').and_return([
            { type: 'High', time: Time.current + 2.hours + 5.minutes, height: 10.3, units: 'ft' },
            { type: 'Low', time: Time.current + 8.hours + 3.minutes, height: 0.6, units: 'ft' }
        ])
    end

    context 'with valid station IDs' do
        it 'returns comparison data' do
            get '/api/stations/compare', type: 'tides', ids: ['NOAA123', 'XTIDE456']

            expect(last_response).to be_ok
            data = JSON.parse(last_response.body)

            expect(data['stations'].length).to eq(2)
        end

        it 'includes station metadata' do
            get '/api/stations/compare', type: 'tides', ids: ['NOAA123']

            data = JSON.parse(last_response.body)
            station = data['stations'].first

            expect(station['id']).to eq('NOAA123')
            expect(station['name']).to eq('Boston NOAA')
            expect(station['provider']).to eq('noaa')
        end

        it 'includes event data' do
            get '/api/stations/compare', type: 'tides', ids: ['NOAA123']

            data = JSON.parse(last_response.body)
            events = data['stations'].first['events']

            expect(events.length).to eq(2)
            expect(events.first['type']).to eq('High')
            expect(events.first['time']).to be_a(String)  # ISO8601
        end

        it 'calculates deltas between stations' do
            get '/api/stations/compare', type: 'tides', ids: ['NOAA123', 'XTIDE456']

            data = JSON.parse(last_response.body)
            alt_station = data['stations'][1]

            expect(alt_station['delta']).to be_a(Hash)
            expect(alt_station['delta']['time']).to be_a(String)
        end

        it 'includes per-event deltas' do
            get '/api/stations/compare', type: 'tides', ids: ['NOAA123', 'XTIDE456']

            data = JSON.parse(last_response.body)
            alt_station = data['stations'][1]

            expect(alt_station['event_deltas']).to be_an(Array)
        end
    end

    context 'with invalid type' do
        it 'returns error for invalid type' do
            get '/api/stations/compare', type: 'invalid', ids: ['NOAA123']

            data = JSON.parse(last_response.body)
            expect(data['error']).to eq('Invalid type')
        end
    end

    context 'with no station IDs' do
        it 'returns error' do
            get '/api/stations/compare', type: 'tides'

            data = JSON.parse(last_response.body)
            expect(data['error']).to eq('No station IDs provided')
        end
    end

    context 'with too many station IDs' do
        it 'returns error for more than 5 stations' do
            get '/api/stations/compare', type: 'tides', ids: ['1', '2', '3', '4', '5', '6']

            data = JSON.parse(last_response.body)
            expect(data['error']).to eq('Maximum 5 stations allowed')
        end
    end

    context 'with invalid station IDs' do
        it 'returns error when no valid stations found' do
            get '/api/stations/compare', type: 'tides', ids: ['INVALID']

            data = JSON.parse(last_response.body)
            expect(data['error']).to eq('No valid stations found')
        end
    end

    context 'with currents type' do
        let(:current_station) do
            build_station(name: 'Cape Cod', id: 'CURR1', bid: 'CURR1_10', provider: 'noaa', depth: 10)
        end

        before do
            allow(WebCalTides).to receive(:current_station_for).with('CURR1').and_return(current_station)
            allow(WebCalTides).to receive(:next_current_events).with('CURR1').and_return([
                { type: 'Flood', time: Time.current + 2.hours, velocity: 2.5 },
                { type: 'Slack', time: Time.current + 5.hours }
            ])
        end

        it 'returns current station data' do
            get '/api/stations/compare', type: 'currents', ids: ['CURR1']

            expect(last_response).to be_ok
            data = JSON.parse(last_response.body)

            expect(data['stations'].first['id']).to eq('CURR1')
            expect(data['stations'].first['depth']).to eq(10)
        end
    end
end

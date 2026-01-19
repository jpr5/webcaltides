# frozen_string_literal: true

RSpec.describe 'GET /api/stations/:type/:id/next', type: :api do
    include Rack::Test::Methods

    describe 'tide events' do
        before do
            freeze_time

            allow(WebCalTides).to receive(:next_tide_events).with('NOAA123').and_return([
                { type: 'High', time: Time.current + 2.hours, height: 10.5, units: 'ft' },
                { type: 'Low', time: Time.current + 8.hours, height: 0.5, units: 'ft' }
            ])

            allow(WebCalTides).to receive(:next_tide_events).with('INVALID').and_return(nil)
            allow(WebCalTides).to receive(:next_tide_events).with('EMPTY').and_return([])
        end

        context 'with valid station' do
            it 'returns next tide events' do
                get '/api/stations/tides/NOAA123/next'

                expect(last_response).to be_ok
                data = JSON.parse(last_response.body)

                expect(data['events'].length).to eq(2)
            end

            it 'returns events with ISO8601 timestamps' do
                get '/api/stations/tides/NOAA123/next'

                data = JSON.parse(last_response.body)
                event = data['events'].first

                expect(event['time']).to match(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/)
            end

            it 'includes event type and height' do
                get '/api/stations/tides/NOAA123/next'

                data = JSON.parse(last_response.body)
                event = data['events'].first

                expect(event['type']).to eq('High')
                expect(event['height']).to eq(10.5)
                expect(event['units']).to eq('ft')
            end
        end

        context 'with invalid station' do
            it 'returns error' do
                get '/api/stations/tides/INVALID/next'

                data = JSON.parse(last_response.body)
                expect(data['error']).to eq('Station not found')
            end
        end

        context 'with no upcoming events' do
            it 'returns empty events array' do
                get '/api/stations/tides/EMPTY/next'

                expect(last_response).to be_ok
                data = JSON.parse(last_response.body)

                expect(data['events']).to be_empty
            end
        end
    end

    describe 'current events' do
        before do
            freeze_time

            allow(WebCalTides).to receive(:next_current_events).with('CURR123').and_return([
                { type: 'Flood', time: Time.current + 2.hours, velocity: 2.5 },
                { type: 'Slack', time: Time.current + 5.hours },
                { type: 'Ebb', time: Time.current + 8.hours, velocity: -2.8 }
            ])

            allow(WebCalTides).to receive(:next_current_events).with('INVALID').and_return(nil)
        end

        context 'with valid station' do
            it 'returns next current events' do
                get '/api/stations/currents/CURR123/next'

                expect(last_response).to be_ok
                data = JSON.parse(last_response.body)

                expect(data['events'].length).to eq(3)
            end

            it 'includes flood, slack, and ebb types' do
                get '/api/stations/currents/CURR123/next'

                data = JSON.parse(last_response.body)
                types = data['events'].map { |e| e['type'] }

                expect(types).to include('Flood', 'Slack', 'Ebb')
            end

            it 'includes velocity for flood and ebb' do
                get '/api/stations/currents/CURR123/next'

                data = JSON.parse(last_response.body)
                flood = data['events'].find { |e| e['type'] == 'Flood' }

                expect(flood['velocity']).to eq(2.5)
            end
        end

        context 'with invalid station' do
            it 'returns error' do
                get '/api/stations/currents/INVALID/next'

                data = JSON.parse(last_response.body)
                expect(data['error']).to eq('Station not found')
            end
        end
    end
end

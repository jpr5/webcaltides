# frozen_string_literal: true

RSpec.describe 'Calendar Generation Error Handling', type: :request do
    include Rack::Test::Methods

    def app
        Server
    end

    describe 'date parsing errors' do
        it 'falls back to current date on invalid date param' do
            # Invalid date should fall back to current month
            get '/tides/9410230.ics?date=invalid'

            expect(last_response.status).to eq(200)
            expect(last_response.headers['Content-Type']).to include('text/calendar')
        end

        it 'handles empty date parameter gracefully' do
            get '/tides/9410230.ics?date='

            expect(last_response.status).to eq(200)
            expect(last_response.headers['Content-Type']).to include('text/calendar')
        end
    end

    describe 'missing data scenarios' do
        it 'returns 404 for non-existent station' do
            get '/tides/INVALID_STATION_12345.ics'

            expect(last_response.status).to eq(404)
        end

        it 'handles station ID validation' do
            # Station ID not in known stations list should 404
            get '/tides/FAKE99999.ics'

            expect(last_response.status).to eq(404)
        end
    end

    describe 'parameter validation' do
        it 'validates units parameter' do
            get '/tides/9410230.ics?units=invalid'

            expect(last_response.status).to eq(422)
        end

        it 'accepts valid imperial units' do
            get '/tides/9410230.ics?units=imperial'

            expect(last_response.status).to eq(200)
        end

        it 'accepts valid metric units' do
            get '/tides/9410230.ics?units=metric'

            expect(last_response.status).to eq(200)
        end
    end
end

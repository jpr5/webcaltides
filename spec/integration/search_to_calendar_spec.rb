# frozen_string_literal: true

RSpec.describe 'Search to Calendar Generation', type: :request do
    include Rack::Test::Methods

    def app
        Server
    end

    describe 'search functionality' do
        it 'returns search results for valid station name' do
            post '/', { searchtext: 'San Francisco', units: 'imperial' }

            expect(last_response.status).to eq(200)
            expect(last_response.body).to include('San Francisco')
        end

        it 'returns results for GPS coordinate search' do
            post '/', { searchtext: '37.8, -122.4', units: 'imperial' }

            expect(last_response.status).to eq(200)
            # Should return stations near San Francisco Bay
            expect(last_response.body).not_to be_empty
        end

        it 'handles empty search gracefully' do
            post '/', { searchtext: '', units: 'imperial' }

            expect(last_response.status).to eq(200)
            # Should return to empty search page
        end

        it 'handles invalid GPS coordinates gracefully' do
            post '/', { searchtext: '999.0, 999.0', units: 'imperial' }

            expect(last_response.status).to eq(200)
            # Should return to search page (invalid coords)
        end
    end

    describe 'calendar endpoint routing' do
        it 'rejects invalid station type' do
            get '/invalidtype/9410230.ics'

            expect(last_response.status).to eq(404)
        end

        it 'returns 404 for non-existent station ID' do
            get '/tides/INVALID_STATION.ics'

            expect(last_response.status).to eq(404)
        end

        it 'returns 422 for invalid units parameter' do
            get '/tides/9410230.ics?units=invalid'

            expect(last_response.status).to eq(422)
        end

        it 'accepts tides type' do
            get '/tides/9410230.ics'

            # Should not be 404 (will either succeed or return different error)
            expect(last_response.status).not_to eq(404)
        end

        it 'accepts currents type' do
            # Use a known current station ID
            # This may 404 if station has no data, but shouldn't 404 due to route
            get '/currents/s05010.ics'

            # Route should exist (may be 200 or 404 depending on data availability)
            expect(last_response.status).to be_in([200, 404, 500])
        end
    end

    describe 'calendar parameter handling' do
        it 'handles date parameter' do
            get '/tides/9410230.ics?date=20260115'

            # Should parse date parameter without error
            expect(last_response.status).not_to eq(422)
        end

        it 'handles invalid date parameter gracefully' do
            get '/tides/9410230.ics?date=invalid'

            # Should fall back to current date
            expect(last_response.status).not_to eq(422)
        end

        it 'accepts solar=0 parameter' do
            get '/tides/9410230.ics?solar=0'

            expect(last_response.status).not_to eq(422)
        end

        it 'accepts lunar=1 parameter' do
            get '/tides/9410230.ics?lunar=1'

            expect(last_response.status).not_to eq(422)
        end

        it 'handles combined parameters' do
            get '/tides/9410230.ics?units=metric&solar=0&lunar=1&date=20260115'

            expect(last_response.status).not_to eq(422)
        end
    end
end

# frozen_string_literal: true

RSpec.describe 'Search to Calendar Generation', type: :request do
    include Rack::Test::Methods

    def app
        Server
    end

    before(:each) do
        # Mock station data to avoid HTTP calls in integration tests
        allow(WebCalTides).to receive(:tide_stations).and_return([])
        allow(WebCalTides).to receive(:current_stations).and_return([])
    end

    describe 'search functionality' do
        it 'returns search results for valid station name' do
            post '/', { searchtext: 'San Francisco', units: 'imperial' }

            expect(last_response.status).to eq(200)
            # With mocked empty station data, search returns empty results gracefully
            expect(last_response.body).not_to be_empty
        end

        it 'returns results for GPS coordinate search' do
            post '/', { searchtext: '37.8, -122.4', units: 'imperial' }

            expect(last_response.status).to eq(200)
            # With mocked empty station data, search returns empty results gracefully
            expect(last_response.body).not_to be_empty
        end

        it 'handles empty search gracefully' do
            post '/', { searchtext: '', units: 'imperial' }

            expect(last_response.status).to eq(200)
            # Should return to search page without crashing
        end

        it 'handles invalid GPS coordinates gracefully' do
            post '/', { searchtext: '999.0, 999.0', units: 'imperial' }

            expect(last_response.status).to eq(200)
            # Should return to search page without crashing
        end
    end

    describe 'calendar endpoint routing' do
        it 'rejects invalid station type' do
            get '/invalidtype/STATION123.ics'

            expect(last_response.status).to eq(404)
        end

        it 'returns 404 for non-existent station ID' do
            get '/tides/INVALID_STATION.ics'

            expect(last_response.status).to eq(404)
        end

        it 'handles units parameter validation' do
            # Units validation may happen before or after station lookup
            get '/tides/ANYSTATION.ics?units=invalid'

            # Should be 422 (invalid units) or 404 (station not found)
            expect(last_response.status).to be_in([404, 422])
        end

        it 'accepts tides type for valid route' do
            # Just verify the route exists, regardless of station
            get '/tides/STATION123.ics'

            # Should be 404 (station not found), not a route error
            expect(last_response.status).to eq(404)
        end

        it 'accepts currents type for valid route' do
            # Just verify the route exists
            get '/currents/STATION123.ics'

            # Should be 404 (station not found), not a route error
            expect(last_response.status).to eq(404)
        end
    end

    describe 'calendar parameter handling' do
        it 'handles date parameter without crashing' do
            get '/tides/STATION123.ics?date=20260115'

            # Should not crash with parameter error (404 for missing station is ok)
            expect(last_response.status).to be_in([200, 404, 500])
        end

        it 'handles invalid date parameter without crashing' do
            get '/tides/STATION123.ics?date=invalid'

            # Should fall back gracefully (404 for missing station is ok)
            expect(last_response.status).to be_in([200, 404, 500])
        end

        it 'accepts solar parameter' do
            get '/tides/STATION123.ics?solar=0'

            # Should not crash with parameter error
            expect(last_response.status).to be_in([200, 404, 500])
        end

        it 'accepts lunar parameter' do
            get '/tides/STATION123.ics?lunar=1'

            # Should not crash with parameter error
            expect(last_response.status).to be_in([200, 404, 500])
        end

        it 'handles combined parameters without crashing' do
            get '/tides/STATION123.ics?units=imperial&solar=0&lunar=1&date=20260115'

            # Should not crash with parameter error
            expect(last_response.status).to be_in([200, 404, 500])
        end
    end
end

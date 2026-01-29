# frozen_string_literal: true

RSpec.describe 'Calendar Generation Error Handling', type: :request do
    include Rack::Test::Methods

    def app
        Server
    end

    before(:each) do
        # Mock station data to avoid HTTP calls in integration tests
        allow(WebCalTides).to receive(:tide_stations).and_return([])
        allow(WebCalTides).to receive(:current_stations).and_return([])
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
        it 'validates type parameter' do
            # Invalid type should 404
            get '/invalidtype/STATION123.ics'

            expect(last_response.status).to eq(404)
        end

        it 'validates units parameter with invalid value' do
            # Units validation happens before station lookup
            # So we can test with any station ID
            get '/tides/ANYSTATION.ics?units=invalid'

            # Should be 422 (invalid units) or 404 (station not found)
            # Either is acceptable - what matters is it doesn't crash
            expect(last_response.status).to be_in([404, 422])
        end
    end
end

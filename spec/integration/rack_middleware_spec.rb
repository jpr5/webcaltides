# frozen_string_literal: true

require_relative '../spec_helper'

RSpec.describe 'Rack middleware', type: :api do
    include Rack::Test::Methods

    describe 'Rack::Deflater (response compression)' do
        # Override app to use the full Rack middleware stack from config.ru
        def app
            Rack::Builder.new do
                use Rack::Deflater
                run Server
            end
        end

        context 'when client accepts gzip encoding' do
            it 'compresses HTML responses with Content-Encoding: gzip' do
                get '/', {}, { 'HTTP_ACCEPT_ENCODING' => 'gzip' }

                expect(last_response).to be_ok
                expect(last_response.headers['content-encoding']).to eq('gzip')
            end

            it 'returns a valid gzip-encoded body' do
                get '/', {}, { 'HTTP_ACCEPT_ENCODING' => 'gzip' }

                expect(last_response).to be_ok

                # Decompress and verify it contains expected HTML
                body = Zlib::GzipReader.new(StringIO.new(last_response.body)).read
                expect(body).to include('<!DOCTYPE html>').or include('<html')
            end

            it 'compresses JSON API responses' do
                freeze_time

                allow(WebCalTides).to receive(:next_tide_events).with('NOAA123').and_return([
                    { type: 'High', time: Time.current + 2.hours, height: 10.5, units: 'ft' }
                ])

                get '/api/stations/tides/NOAA123/next', {}, { 'HTTP_ACCEPT_ENCODING' => 'gzip' }

                expect(last_response).to be_ok
                expect(last_response.headers['content-encoding']).to eq('gzip')

                body = Zlib::GzipReader.new(StringIO.new(last_response.body)).read
                data = JSON.parse(body)
                expect(data['events'].first['type']).to eq('High')
            end
        end

        context 'when client does not accept gzip encoding' do
            it 'does not compress the response' do
                get '/'

                expect(last_response).to be_ok
                expect(last_response.headers['content-encoding']).to be_nil
            end

            it 'returns readable HTML body without compression' do
                get '/'

                expect(last_response).to be_ok
                expect(last_response.body).to include('<!DOCTYPE html>').or include('<html')
            end
        end

        context 'when client accepts only deflate encoding' do
            it 'does not apply gzip encoding' do
                get '/', {}, { 'HTTP_ACCEPT_ENCODING' => 'deflate' }

                expect(last_response).to be_ok
                expect(last_response.headers['content-encoding']).not_to eq('gzip')
            end
        end
    end

    describe 'static file serving' do
        # Use the default app (Server) since Sinatra handles static files directly
        def app
            Server
        end

        context 'with known static files' do
            it 'serves robots.txt with correct content' do
                get '/robots.txt'

                expect(last_response).to be_ok
                expect(last_response.body).to include('Sitemap:')
                expect(last_response.body).to include('User-agent:')
            end

            it 'serves robots.txt with text content type' do
                get '/robots.txt'

                expect(last_response).to be_ok
                expect(last_response.content_type).to include('text/plain')
            end

            it 'serves sitemap.xml' do
                get '/sitemap.xml'

                expect(last_response).to be_ok
                expect(last_response.content_type).to include('xml')
            end

            it 'serves favicon.png as an image' do
                get '/favicon.png'

                expect(last_response).to be_ok
                expect(last_response.content_type).to include('image/png')
            end
        end

        context 'with non-existent static files' do
            it 'returns 404 for missing files' do
                get '/nonexistent-file.css'

                expect(last_response.status).to eq(404)
            end

            it 'returns 404 for directory traversal attempts' do
                get '/../Gemfile'

                expect(last_response.status).to eq(404).or eq(400)
            end
        end
    end

    describe 'parameter parsing via Rack' do
        def app
            Server
        end

        describe 'path parameters' do
            before do
                freeze_time
            end

            it 'parses :type and :id from /api/stations/:type/:id/next' do
                allow(WebCalTides).to receive(:next_tide_events).with('NOAA123').and_return([
                    { type: 'High', time: Time.current + 2.hours, height: 10.5, units: 'ft' }
                ])

                get '/api/stations/tides/NOAA123/next'

                expect(last_response).to be_ok
                expect(WebCalTides).to have_received(:next_tide_events).with('NOAA123')
            end

            it 'handles station IDs with special characters' do
                allow(WebCalTides).to receive(:next_tide_events).with('CHS-01234').and_return([
                    { type: 'High', time: Time.current + 2.hours, height: 3.2, units: 'm' }
                ])

                get '/api/stations/tides/CHS-01234/next'

                expect(last_response).to be_ok
                expect(WebCalTides).to have_received(:next_tide_events).with('CHS-01234')
            end
        end

        describe 'query parameters' do
            it 'passes query string to autocomplete endpoint' do
                allow(WebCalTides).to receive(:tide_stations).and_return([])
                allow(WebCalTides).to receive(:current_stations).and_return([])

                get '/api/stations/autocomplete', q: 'seattle'

                expect(last_response).to be_ok
                data = JSON.parse(last_response.body)
                expect(data).to have_key('results')
            end

            it 'handles URL-encoded query values' do
                allow(WebCalTides).to receive(:tide_stations).and_return([])
                allow(WebCalTides).to receive(:current_stations).and_return([])

                get '/api/stations/autocomplete', q: 'san%20francisco'

                expect(last_response).to be_ok
            end

            it 'handles empty query parameters gracefully' do
                allow(WebCalTides).to receive(:tide_stations).and_return([])
                allow(WebCalTides).to receive(:current_stations).and_return([])

                get '/api/stations/autocomplete', q: ''

                expect(last_response).to be_ok
                data = JSON.parse(last_response.body)
                expect(data['results']).to be_empty
            end

            it 'handles missing query parameters' do
                allow(WebCalTides).to receive(:tide_stations).and_return([])
                allow(WebCalTides).to receive(:current_stations).and_return([])

                get '/api/stations/autocomplete'

                expect(last_response).to be_ok
                data = JSON.parse(last_response.body)
                expect(data['results']).to be_empty
            end
        end

        describe 'array parameters' do
            it 'parses ids[] array params for compare endpoint' do
                allow(WebCalTides).to receive(:tide_station_for).and_return(nil)

                get '/api/stations/compare', type: 'tides', 'ids[]' => ['noaa:123', 'noaa:456']

                expect(last_response).to be_ok
            end
        end
    end

    describe 'Content-Type headers' do
        def app
            Server
        end

        before do
            freeze_time
        end

        describe 'JSON endpoints' do
            it 'returns application/json for autocomplete' do
                allow(WebCalTides).to receive(:tide_stations).and_return([])
                allow(WebCalTides).to receive(:current_stations).and_return([])

                get '/api/stations/autocomplete', q: 'test'

                expect(last_response.content_type).to include('application/json')
            end

            it 'returns application/json for next events' do
                allow(WebCalTides).to receive(:next_tide_events).with('NOAA123').and_return([
                    { type: 'High', time: Time.current + 2.hours, height: 10.5, units: 'ft' }
                ])

                get '/api/stations/tides/NOAA123/next'

                expect(last_response.content_type).to include('application/json')
            end

            it 'returns application/json for compare endpoint' do
                allow(WebCalTides).to receive(:tide_station_for).and_return(nil)

                get '/api/stations/compare', type: 'tides', 'ids[]' => ['noaa:123']

                expect(last_response.content_type).to include('application/json')
            end
        end

        describe 'ICS endpoints' do
            let(:test_cache_dir) { Dir.mktmpdir('webcaltides_test') }

            let(:tide_calendar) do
                cal = Icalendar::Calendar.new
                cal.event do |e|
                    e.summary = 'High Tide 10.5 ft'
                    e.dtstart = Icalendar::Values::DateTime.new(Time.utc(2025, 6, 15, 6, 30), tzid: 'GMT')
                end
                cal.publish
                cal
            end

            before do
                allow(WebCalTides).to receive(:station_ids).and_return(['NOAA123'])
                allow(WebCalTides).to receive(:tide_calendar_for).and_return(tide_calendar)
                allow(WebCalTides).to receive(:solar_calendar_for).and_return(Icalendar::Calendar.new)
                allow(Server.settings).to receive(:cache_dir).and_return(test_cache_dir)
            end

            after do
                FileUtils.rm_rf(test_cache_dir) if test_cache_dir && Dir.exist?(test_cache_dir)
            end

            it 'returns text/calendar for ICS endpoints' do
                get '/tides/NOAA123.ics'

                expect(last_response).to be_ok
                expect(last_response.content_type).to include('text/calendar')
            end

            it 'includes charset=utf-8 for ICS responses' do
                get '/tides/NOAA123.ics'

                expect(last_response).to be_ok
                expect(last_response.content_type).to include('charset=utf-8')
            end
        end

        describe 'HTML endpoints' do
            it 'returns text/html for the homepage' do
                get '/'

                expect(last_response).to be_ok
                expect(last_response.content_type).to include('text/html')
            end
        end
    end
end

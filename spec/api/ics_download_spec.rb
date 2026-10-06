# frozen_string_literal: true

RSpec.describe 'GET /:type/:station.ics', type: :api do
    include Rack::Test::Methods

    # Use a unique temp directory for each test run to avoid cache conflicts
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

    let(:current_calendar) do
        cal = Icalendar::Calendar.new
        cal.event do |e|
            e.summary = 'Flood 2.5kts'
            e.dtstart = Icalendar::Values::DateTime.new(Time.utc(2025, 6, 15, 8, 30), tzid: 'GMT')
        end
        cal.publish
        cal
    end

    before do
        freeze_time(Time.utc(2025, 6, 15))

        allow(WebCalTides).to receive(:station_ids).and_return(['NOAA123', 'CURR456'])
        allow(WebCalTides).to receive(:tide_calendar_for).and_return(tide_calendar)
        allow(WebCalTides).to receive(:current_calendar_for).and_return(current_calendar)
        allow(WebCalTides).to receive(:solar_calendar_for).and_return(Icalendar::Calendar.new)
        allow(WebCalTides).to receive(:lunar_calendar_for).and_return(Icalendar::Calendar.new)

        # Use unique temp directory to avoid cache conflicts
        allow(Server.settings).to receive(:cache_dir).and_return(test_cache_dir)
    end

    after do
        # Clean up temp directory
        FileUtils.rm_rf(test_cache_dir) if test_cache_dir && Dir.exist?(test_cache_dir)
    end

    describe 'tide calendar' do
        context 'with valid station' do
            it 'returns iCal content' do
                get '/tides/NOAA123.ics'

                expect(last_response).to be_ok
                expect(last_response.content_type).to include('text/calendar')
            end

            it 'returns valid iCal format' do
                get '/tides/NOAA123.ics'

                body = last_response.body
                expect(body).to include('BEGIN:VCALENDAR')
                expect(body).to include('END:VCALENDAR')
            end

            it 'calls tide_calendar_for with station ID' do
                get '/tides/NOAA123.ics'

                expect(WebCalTides).to have_received(:tide_calendar_for).with('NOAA123', anything)
            end
        end

        context 'with invalid station' do
            it 'returns 404' do
                get '/tides/INVALID.ics'

                expect(last_response.status).to eq(404)
            end
        end

        context 'with units parameter' do
            it 'accepts imperial units' do
                get '/tides/NOAA123.ics', units: 'imperial'

                expect(last_response).to be_ok
            end

            it 'accepts metric units' do
                get '/tides/NOAA123.ics', units: 'metric'

                expect(last_response).to be_ok
            end

            it 'rejects invalid units' do
                get '/tides/NOAA123.ics', units: 'invalid'

                expect(last_response.status).to eq(422)
            end
        end

        context 'with solar parameter' do
            it 'includes solar events by default' do
                get '/tides/NOAA123.ics'

                expect(WebCalTides).to have_received(:solar_calendar_for)
            end

            it 'excludes solar events when solar=0' do
                get '/tides/NOAA123.ics', solar: '0'

                expect(WebCalTides).not_to have_received(:solar_calendar_for)
            end

            it 'excludes solar events when solar=false' do
                get '/tides/NOAA123.ics', solar: 'false'

                expect(WebCalTides).not_to have_received(:solar_calendar_for)
            end
        end

        context 'with lunar parameter' do
            it 'excludes lunar events by default' do
                get '/tides/NOAA123.ics'

                expect(WebCalTides).not_to have_received(:lunar_calendar_for)
            end

            it 'includes lunar events when lunar=1' do
                get '/tides/NOAA123.ics', lunar: '1'

                expect(WebCalTides).to have_received(:lunar_calendar_for)
            end

            it 'includes lunar events when lunar=true' do
                get '/tides/NOAA123.ics', lunar: 'true'

                expect(WebCalTides).to have_received(:lunar_calendar_for)
            end
        end

        context 'with date parameter' do
            it 'accepts a YYYYMMDD date and generates the calendar around it' do
                get '/tides/NOAA123.ics', date: '20260929'

                expect(last_response).to be_ok
                expect(WebCalTides).to have_received(:tide_calendar_for)
                    .with('NOAA123', hash_including(around: Time.utc(2026, 9, 29)))
            end

            it 'accepts a YYYY-MM-DD date' do
                get '/tides/NOAA123.ics', date: '2026-09-29'

                expect(last_response).to be_ok
                expect(WebCalTides).to have_received(:tide_calendar_for)
                    .with('NOAA123', hash_including(around: Time.utc(2026, 9, 29)))
            end

            it 'passes the parsed date to solar and lunar calendars' do
                get '/tides/NOAA123.ics', date: '20260929', lunar: '1'

                expect(WebCalTides).to have_received(:solar_calendar_for).with(anything, around: Time.utc(2026, 9, 29))
                expect(WebCalTides).to have_received(:lunar_calendar_for).with(anything, around: Time.utc(2026, 9, 29))
            end

            it 'caches the calendar under the requested month' do
                get '/tides/NOAA123.ics', date: '20260929'

                expect(Dir.glob("#{test_cache_dir}/tides_*_NOAA123_202609_*.ics")).not_to be_empty
            end

            it 'falls back to current date for invalid date' do
                get '/tides/NOAA123.ics', date: 'invalid'

                expect(last_response).to be_ok
                expect(WebCalTides).to have_received(:tide_calendar_for)
                    .with('NOAA123', hash_including(around: Time.utc(2025, 6, 15)))
            end

            it 'falls back to current date for empty or out-of-range dates' do
                ['', '20261399'].each do |bad|
                    get '/tides/NOAA123.ics', date: bad

                    expect(last_response).to be_ok
                end
            end
        end
    end

    describe 'current calendar' do
        context 'with date parameter' do
            it 'accepts a YYYYMMDD date and generates the calendar around it' do
                get '/currents/CURR456.ics', date: '20260929'

                expect(last_response).to be_ok
                expect(WebCalTides).to have_received(:current_calendar_for)
                    .with('CURR456', around: Time.utc(2026, 9, 29))
            end
        end

        context 'with valid station' do
            it 'returns iCal content' do
                get '/currents/CURR456.ics'

                expect(last_response).to be_ok
                expect(last_response.content_type).to include('text/calendar')
            end
        end

        context 'with invalid station' do
            it 'returns 404' do
                get '/currents/INVALID.ics'

                expect(last_response.status).to eq(404)
            end
        end
    end

    describe 'invalid type' do
        it 'returns 404 for unknown type' do
            get '/unknown/NOAA123.ics'

            expect(last_response.status).to eq(404)
        end
    end
end

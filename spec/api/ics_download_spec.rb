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

    # Deliberately not midnight, so "now" and "today at UTC midnight" are distinguishable.
    let(:now) { Time.utc(2025, 6, 15, 14, 37, 12) }

    # Run under a zone far from UTC so a Date parsed as local midnight lands on a different UTC
    # instant (often a different day) than UTC midnight.  Under TZ=UTC -- as on CI -- the two are
    # the same instant, and the UTC-midnight assertions below would pass for local parsing too.
    around do |example|
        old_tz, ENV['TZ'] = ENV['TZ'], 'Pacific/Kiritimati'
        example.run
    ensure
        ENV['TZ'] = old_tz
    end

    before do
        freeze_time(now)

        allow(WebCalTides).to receive(:station_ids).and_return(['NOAA123', 'CURR456'])
        allow(WebCalTides).to receive(:tide_calendar_for).and_return(tide_calendar)
        allow(WebCalTides).to receive(:current_calendar_for).and_return(current_calendar)
        # The route looks the station up for the cache name (harmonics stations get a dataset/engine key)
        allow(WebCalTides).to receive(:tide_station_for).and_return(build_station(id: 'NOAA123', provider: 'noaa'))
        allow(WebCalTides).to receive(:current_station_for).and_return(build_station(id: 'CURR456', bid: 'CURR456', provider: 'noaa'))
        allow(WebCalTides).to receive(:solar_calendar_for).and_return(Icalendar::Calendar.new)
        allow(WebCalTides).to receive(:lunar_calendar_for).and_return(Icalendar::Calendar.new)

        # Examples freeze different months; a month change would start the background cache
        # cleanup, which deletes past-month files (including the one this request is writing).
        allow(WebCalTides).to receive(:cleanup_if_month_changed)

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
            it 'accepts a YYYYMMDD date and generates the calendar around UTC midnight of it' do
                get '/tides/NOAA123.ics', date: '20260929'

                expect(last_response).to be_ok
                expect(WebCalTides).to have_received(:tide_calendar_for)
                    .with('NOAA123', hash_including(around: Time.utc(2026, 9, 29)))
            end

            it 'passes the parsed date to solar and lunar calendars' do
                get '/tides/NOAA123.ics', date: '20260929', lunar: '1'

                expect(last_response).to be_ok
                expect(WebCalTides).to have_received(:solar_calendar_for).with(anything, around: Time.utc(2026, 9, 29))
                expect(WebCalTides).to have_received(:lunar_calendar_for).with(anything, around: Time.utc(2026, 9, 29))
            end

            it 'caches the calendar under the requested month' do
                get '/tides/NOAA123.ics', date: '20260929'

                expect(Dir.glob("#{test_cache_dir}/tides_*_NOAA123_202609_*.ics")).not_to be_empty
            end

            it 'uses the current time when no date is given' do
                get '/tides/NOAA123.ics'

                expect(last_response).to be_ok
                expect(WebCalTides).to have_received(:tide_calendar_for)
                    .with('NOAA123', hash_including(around: now))
            end

            it 'treats an empty date as no date' do
                get '/tides/NOAA123.ics', date: ''

                expect(last_response).to be_ok
                expect(WebCalTides).to have_received(:tide_calendar_for)
                    .with('NOAA123', hash_including(around: now))
            end

            it 'treats a whitespace-only date as malformed (422), not as no date' do
                get '/tides/NOAA123.ics', date: ' '

                expect(last_response.status).to eq(422)
                expect(WebCalTides).not_to have_received(:tide_calendar_for)
                expect(Dir.glob("#{test_cache_dir}/*.ics")).to be_empty
            end

            # Both rejection kinds share one plain-text 422 and one WARN naming the station.
            {
                'a malformed date'      => 'invalid',
                'an out-of-window date' => '20270701'
            }.each do |kind, bad|
                it "explains #{kind} in a plain-text 422 body and logs it with type and station" do
                    warnings = []
                    allow($LOG).to receive(:warn) { |msg| warnings << msg }

                    get '/tides/NOAA123.ics', date: bad

                    expect(last_response.status).to eq(422)
                    expect(last_response.content_type).to start_with('text/plain')
                    expect(last_response.body).to match(/YYYYMMDD/)
                    expect(warnings).to contain_exactly(a_string_including(bad.inspect, 'tides/NOAA123'))
                end
            end

            it 'caps the logged length of a long rejected date' do
                warnings = []
                allow($LOG).to receive(:warn) { |msg| warnings << msg }

                get '/tides/NOAA123.ics', date: 'x' * 2000

                expect(last_response.status).to eq(422)
                expect(warnings.size).to eq(1)
                expect(warnings.first.length).to be < 200
                expect(warnings.first).to include('2002 chars')
            end

            it 'caps the logged length of a large nested date[k]=v hash' do
                warnings = []
                allow($LOG).to receive(:warn) { |msg| warnings << msg }

                get "/tides/NOAA123.ics?#{(1..100).map { |i| "date[k#{i}]=#{'v' * 20}" }.join('&')}"

                expect(last_response.status).to eq(422)
                expect(warnings.size).to eq(1)
                expect(warnings.first.length).to be < 200
            end

            it 'treats a bare ?date (no =) as no date' do
                get '/tides/NOAA123.ics?date'

                expect(last_response).to be_ok
                expect(WebCalTides).to have_received(:tide_calendar_for)
                    .with('NOAA123', hash_including(around: now))
            end

            it 'rejects a hash date (date[k]=v) with 422 and writes no cache' do
                get '/tides/NOAA123.ics?date[k]=20260929'

                expect(last_response.status).to eq(422)
                expect(WebCalTides).not_to have_received(:tide_calendar_for)
                expect(Dir.glob("#{test_cache_dir}/*.ics")).to be_empty
            end

            # A subscription URL with a fixed date= keeps working only while the date is in the
            # window; once the window moves past it the same URL gets 422.
            it 'starts rejecting a fixed subscription date once the window moves past it' do
                get '/tides/NOAA123.ics', date: '20240601'
                expect(last_response).to be_ok

                freeze_time(now + 1.month)
                get '/tides/NOAA123.ics', date: '20240601'

                expect(last_response.status).to eq(422)
            end

            it 'rejects a non-scalar date (date[]=...) with 422 and writes no cache' do
                get '/tides/NOAA123.ics?date[]=20260929'

                expect(last_response.status).to eq(422)
                expect(WebCalTides).not_to have_received(:tide_calendar_for)
                expect(Dir.glob("#{test_cache_dir}/*.ics")).to be_empty
            end

            # Window: start of the month a year back .. end of the month two years ahead, from the
            # frozen now.  Each edge is its own example so a cached .ics can't mask another.
            shared_examples 'a date window' do |accepted, rejected|
                accepted.each do |edge, expected|
                    it "accepts #{edge} and builds the calendar around #{expected.utc.iso8601}" do
                        get '/tides/NOAA123.ics', date: edge

                        expect(last_response).to be_ok
                        expect(WebCalTides).to have_received(:tide_calendar_for)
                            .with('NOAA123', hash_including(around: expected))
                    end
                end

                rejected.each do |edge|
                    it "rejects #{edge} with 422 and writes no cache" do
                        get '/tides/NOAA123.ics', date: edge

                        expect(last_response.status).to eq(422)
                        expect(WebCalTides).not_to have_received(:tide_calendar_for)
                        expect(Dir.glob("#{test_cache_dir}/*.ics")).to be_empty
                    end
                end
            end

            context 'when now is mid-June' do
                include_examples 'a date window',
                                 { '20240601' => Time.utc(2024, 6, 1), '20270630' => Time.utc(2027, 6, 30) },
                                 %w[20240531 20270701]
            end

            context 'when now is in February of a non-leap year two years before a leap year' do
                let(:now) { Time.utc(2026, 2, 10, 9, 15) }

                include_examples 'a date window',
                                 { '20250201' => Time.utc(2025, 2, 1), '20280229' => Time.utc(2028, 2, 29) },
                                 %w[20250131 20280301]
            end

            context 'when now is the last moment of a month' do
                let(:now) { Time.utc(2026, 1, 31, 23, 59, 59) }

                include_examples 'a date window',
                                 { '20250101' => Time.utc(2025, 1, 1), '20280131' => Time.utc(2028, 1, 31) },
                                 %w[20241231 20280201]
            end

            # Each case is its own example so a cached .ics from one request can't mask another.
            {
                'garbage'                   => 'invalid',
                'impossible month/day'      => '20261399',
                'Feb 30'                    => '20260230',
                'YYYY-MM-DD'                => '2026-09-29',
                'weekday name'              => 'tue',
                'abbreviated weekday'       => 'Mon',
                'month name'                => 'may',
                'ordinal day'               => '3rd',
                'ISO week'                  => '2026-W40',
                'too few digits'            => '2026091',
                'too many digits'           => '202609290',
                'trailing junk'             => '20260929x',
                'negative year'             => '-0010101',
                'year 0001'                 => '00010101',
                'year 1999'                 => '19990101',
                'year 2100'                 => '21000101',
                'year 9999'                 => '99991231',
                'leading whitespace'        => ' 20260929',
                'trailing newline'          => "20260929\n"
            }.each do |label, bad|
                it "rejects #{label} (#{bad.inspect}) with 422 and writes no cache" do
                    get '/tides/NOAA123.ics', date: bad

                    expect(last_response.status).to eq(422)
                    expect(WebCalTides).not_to have_received(:tide_calendar_for)
                    expect(Dir.glob("#{test_cache_dir}/*.ics")).to be_empty
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

            it 'uses the current time when no date is given' do
                get '/currents/CURR456.ics'

                expect(last_response).to be_ok
                expect(WebCalTides).to have_received(:current_calendar_for).with('CURR456', around: now)
            end

            # Same window as tides, from the frozen mid-June now: each edge its own example.
            { '20240601' => Time.utc(2024, 6, 1), '20270630' => Time.utc(2027, 6, 30) }.each do |edge, expected|
                it "accepts #{edge}, just inside the window" do
                    get '/currents/CURR456.ics', date: edge

                    expect(last_response).to be_ok
                    expect(WebCalTides).to have_received(:current_calendar_for).with('CURR456', around: expected)
                end
            end

            [*%w[invalid tue 99991231 20240531 20270701], ' '].each do |bad|
                it "rejects #{bad.inspect} with 422 and writes no cache" do
                    get '/currents/CURR456.ics', date: bad

                    expect(last_response.status).to eq(422)
                    expect(WebCalTides).not_to have_received(:current_calendar_for)
                    expect(Dir.glob("#{test_cache_dir}/*.ics")).to be_empty
                end
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

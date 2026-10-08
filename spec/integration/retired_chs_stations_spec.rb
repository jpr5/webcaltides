# frozen_string_literal: true

require 'zlib'

# DFO stopped publishing high/low predictions (the wlp-hilo series) for some CHS stations; their
# data request 404s.  They're left out of the station list, and an existing subscription to one
# gets a one-event feed saying so instead of a 404.
RSpec.describe 'CHS stations without high/low predictions', :aggregate_failures do
    include Rack::Test::Methods

    def app
        Server
    end

    # In the recorded station list, none of these has a wlp-hilo series
    let(:retired) do
        {
            '5cebf1e43d0f4a073c4bc3e5' => 'Toronto',
            '5dd3064de0fdc4b9b4be6685' => 'Whycocomagh',
            '5dd3064fe0fdc4b9b4be6b3f' => 'Grand Manan Channel'
        }.freeze
    end

    let(:stations_url) { "#{Clients::ChsTides::API_URL}/stations" }
    let(:halifax)      { '5cebf1df3d0f4a073c4bbcbb' }

    # The recorded CHS /stations body (January 2026: 1570 stations, 1084 with a wlp-hilo series).
    # Its own fixture, so re-recording a client cassette doesn't change the counts here.
    def recorded_station_list
        @recorded_station_list ||= Zlib.gunzip(File.binread(File.join(__dir__, '../fixtures/chs/stations_2026_01.json.gz')))
    end

    # The listing with every station's timeSeries replaced by `value` (or removed, for :remove)
    def reshaped_station_list(value)
        JSON.parse(recorded_station_list).map { |js| value == :remove ? js.except('timeSeries') : js.merge('timeSeries' => value) }.to_json
    end

    describe Clients::ChsTides do
        let(:log)    { StringIO.new }
        let(:client) { described_class.new(Logger.new(log)) }

        context 'with the recorded station list' do
            before do
                stub_request(:get, stations_url).to_return(status: 200, body: recorded_station_list)
            end

            it 'leaves stations without a wlp-hilo series out of the station list' do
                stations = client.tide_stations

                expect(stations.map(&:id)).not_to include(*retired.keys)
                expect(stations.length).to eq(1084) # of 1570 in the recording
                expect(stations.map(&:name)).to include('Halifax')
                expect(client.station_list_degraded?).to be(false)
            end

            it 'lists the stations without a wlp-hilo series as retired, by id with their names' do
                list = client.retired_stations

                expect(list).to include(retired)
                expect(list.length).to eq(1570 - 1084)
            end
        end

        context 'when the station list is not a usable list' do
            [
                ['an HTML error page', '<html><body>Service Unavailable</body></html>'],
                ['a truncated body',   '[{"id":"5cebf1e43d0f4a073c4bc3e5","officialName":"Tor'],
                ['an error object',    '{"message":"Internal error","id":"abc"}'],
                ['an empty list',      '[]'],
                ['a list of non-objects', '["a", 1]']
            ].each do |what, body|
                it "treats #{what} as a failed fetch and logs it at error" do
                    allow(client).to receive(:get_url).and_return(body)

                    expect(client.tide_stations).to be_nil
                    expect(client.retired_stations).to be_nil
                    expect(log.string).to match(/^E, .*CHS station list/)
                end
            end
        end

        context 'when most stations have no wlp-hilo series (an upstream format change)' do
            let(:listing) { JSON.parse(recorded_station_list) }
            let(:no_hilo) { reshaped_station_list(:remove) }

            # The listing with wlp-hilo dropped from enough stations that `count` have none
            def listing_without_hilo(count)
                hilo, other = listing.partition { |js| Array(js['timeSeries']).any? { |ts| ts['code'] == 'wlp-hilo' } }
                strip = count - other.length
                (other + hilo.each_with_index.map { |js, i| i < strip ? js.except('timeSeries') : js }).to_json
            end

            it 'does not filter the station list or change the retired list, and logs it at error' do
                allow(client).to receive(:get_url).and_return(no_hilo)

                expect(client.retired_stations).to be_nil
                stations = client.tide_stations
                expect(stations.length).to eq(1570) # unfiltered: no good list to fall back to
                expect(client.station_list_degraded?).to be(true)
                expect(log.string).to match(/^E, .*wlp-hilo/)
            end

            it 'keeps the last good station list' do
                allow(client).to receive(:get_url).and_return(recorded_station_list)
                good = client.tide_stations.map(&:id)

                allow(client).to receive(:get_url).and_return(no_hilo)

                expect(client.tide_stations.map(&:id)).to eq(good)
                expect(client.station_list_degraded?).to be(true)
            end

            it 'still filters when the share without wlp-hilo is under the threshold' do
                count = (0.59 * listing.length).ceil
                allow(client).to receive(:get_url).and_return(listing_without_hilo(count))

                expect(client.tide_stations.length).to eq(listing.length - count)
                expect(client.retired_stations.length).to eq(count)
                expect(client.station_list_degraded?).to be(false)
            end

            it 'still filters when the share without wlp-hilo is exactly the threshold' do
                count = (0.6 * listing.length).floor # 942 of 1570
                allow(client).to receive(:get_url).and_return(listing_without_hilo(count))

                expect(client.tide_stations.length).to eq(listing.length - count)
                expect(client.retired_stations.length).to eq(count)
                expect(client.station_list_degraded?).to be(false)
            end

            it 'takes it as a format change one station over the threshold' do
                count = (0.6 * listing.length).floor + 1 # 943 of 1570
                allow(client).to receive(:get_url).and_return(listing_without_hilo(count))

                expect(client.retired_stations).to be_nil
                expect(client.tide_stations.length).to eq(listing.length)
                expect(client.station_list_degraded?).to be(true)
                expect(log.string).to match(/^E, .*943 of 1570 CHS stations have no wlp-hilo/)
            end
        end

        context 'when timeSeries changes shape (an upstream format change)' do
            [
                ['an object',  { 'code' => 'wlp-hilo' }],
                ['a string',   'wlp-hilo'],
                ['a list of strings', ['wlp-hilo']]
            ].each do |what, value|
                it "trips the wlp-hilo guard instead of raising when it is #{what}" do
                    allow(client).to receive(:get_url).and_return(reshaped_station_list(value))

                    expect(client.retired_stations).to be_nil
                    expect(client.tide_stations.length).to eq(1570)
                    expect(client.station_list_degraded?).to be(true)
                    expect(log.string).to match(/^E, .*wlp-hilo/)
                end
            end
        end
    end

    describe 'tide station list cache key' do
        it 'changes with the CHS station list version, so the old list is not served after deploy' do
            allow(WebCalTides).to receive(:harmonics_checksum).and_return('cafebabe')
            before = WebCalTides.tide_station_cache_file

            allow(Clients::ChsTides).to receive(:station_list_version).and_return(Clients::ChsTides.station_list_version + 1)

            expect(WebCalTides.tide_station_cache_file).not_to eq(before)
        end
    end

    context 'with a cache dir' do
        let(:chs) { instance_double(Clients::ChsTides, retired_stations: retired.dup) }

        around do |example|
            with_test_cache_dir do |dir|
                @cache_dir = dir
                WebCalTides.instance_variable_set(:@retired_tide_stations, nil)
                WebCalTides.instance_variable_set(:@retired_tide_stations_retry_at, nil)
                WebCalTides.instance_variable_set(:@retired_tide_stations_file, nil)
                example.run
            ensure
                WebCalTides.instance_variable_set(:@retired_tide_stations, nil)
                WebCalTides.instance_variable_set(:@retired_tide_stations_retry_at, nil)
                WebCalTides.instance_variable_set(:@retired_tide_stations_file, nil)
            end
        end

        before do
            allow(WebCalTides).to receive(:tide_clients).and_call_original
            allow(WebCalTides).to receive(:tide_clients).with(:chs).and_return(chs)
            allow(WebCalTides.logger).to receive(:error).and_call_original
        end

        def retired_cache_files
            Dir.glob("#{@cache_dir}/retired_tide_stations_*")
        end

        describe 'WebCalTides.retired_tide_stations' do
            it 'fetches the list once and caches it for the quarter' do
                expect(WebCalTides.retired_tide_station?('5cebf1e43d0f4a073c4bc3e5')).to be(true)
                expect(retired_cache_files.map { |f| File.basename(f) })
                    .to match([/\Aretired_tide_stations_v1_20\d\dQ\d\.json\z/])

                # A fresh process reads the cached file instead of asking DFO again
                WebCalTides.instance_variable_set(:@retired_tide_stations, nil)
                expect(WebCalTides.retired_tide_station?('5dd3064de0fdc4b9b4be6685')).to be(true)
                expect(chs).to have_received(:retired_stations).once
            end

            it 'does not cache a failed fetch, and fetches again after the retry wait' do
                freeze_time(Time.utc(2026, 10, 7, 12))
                allow(chs).to receive(:retired_stations).and_raise(Errno::ECONNREFUSED)

                expect(WebCalTides.retired_tide_station?('5cebf1e43d0f4a073c4bc3e5')).to be(false)
                expect(retired_cache_files).to be_empty
                expect(WebCalTides.logger).to have_received(:error).with(/failed to get retired CHS stations/)

                # DFO is back, but nothing is fetched until the retry wait has passed
                allow(chs).to receive(:retired_stations).and_return(retired.dup)
                Timecop.freeze(Time.utc(2026, 10, 7, 12) + WebCalTides::TIDE_STATIONS_RETRY - 1)
                expect(WebCalTides.retired_tide_station?('5cebf1e43d0f4a073c4bc3e5')).to be(false)
                expect(chs).to have_received(:retired_stations).once

                Timecop.freeze(Time.utc(2026, 10, 7, 12) + WebCalTides::TIDE_STATIONS_RETRY + 1)
                expect(WebCalTides.retired_tide_station?('5cebf1e43d0f4a073c4bc3e5')).to be(true)
                expect(chs).to have_received(:retired_stations).twice
                expect(retired_cache_files.length).to eq(1)
            end

            it 'does not cache a list that is not a Hash' do
                allow(chs).to receive(:retired_stations).and_return(['5cebf1e43d0f4a073c4bc3e5'])

                expect(WebCalTides.retired_tide_station?('5cebf1e43d0f4a073c4bc3e5')).to be(false)
                expect(retired_cache_files).to be_empty
                expect(WebCalTides.instance_variable_get(:@retired_tide_stations_retry_at)).to be_present
                expect(WebCalTides.logger).to have_received(:error).with(/failed to get retired CHS stations/)
            end

            it 'keeps the last good list when a retry fails' do
                WebCalTides.instance_variable_set(:@retired_tide_stations, retired.dup)
                WebCalTides.instance_variable_set(:@retired_tide_stations_file, WebCalTides.retired_tide_station_cache_file)
                WebCalTides.instance_variable_set(:@retired_tide_stations_retry_at, Time.current.utc - 1)
                allow(chs).to receive(:retired_stations).and_return(nil)

                expect(WebCalTides.retired_tide_station?('5cebf1e43d0f4a073c4bc3e5')).to be(true)
                expect(chs).to have_received(:retired_stations).once # the retry happened
                expect(retired_cache_files).to be_empty
                expect(WebCalTides.instance_variable_get(:@retired_tide_stations_retry_at)).to be > Time.current.utc
            end

            it 'loads the new quarter\'s list at the quarter boundary' do
                freeze_time(Time.utc(2026, 9, 30, 23, 0))
                expect(WebCalTides.retired_tide_station?('5cebf1e43d0f4a073c4bc3e5')).to be(true)

                # DFO retires another station during the quarter; the next quarter's list has it
                allow(chs).to receive(:retired_stations).and_return(retired.merge('5cebf1de3d0f4a073c4bb94e' => 'Squamish Inner'))
                Timecop.freeze(Time.utc(2026, 10, 1, 0, 1))

                expect(WebCalTides.retired_tide_station?('5cebf1de3d0f4a073c4bb94e')).to be(true)
                expect(chs).to have_received(:retired_stations).twice
                expect(retired_cache_files.map { |f| File.basename(f) })
                    .to contain_exactly('retired_tide_stations_v1_2026Q3.json', 'retired_tide_stations_v1_2026Q4.json')
            end

            ['not json', '["5cebf1e43d0f4a073c4bc3e5"]', '"a string"'].each do |content|
                it "logs and refetches a corrupt retired cache file (#{content})" do
                    File.write(WebCalTides.retired_tide_station_cache_file, content)

                    expect(WebCalTides.retired_tide_station?('5cebf1e43d0f4a073c4bc3e5')).to be(true)
                    expect(chs).to have_received(:retired_stations).once
                    expect(WebCalTides.logger).to have_received(:error).with(/unreadable retired tide station cache/)
                    expect(JSON.parse(File.read(WebCalTides.retired_tide_station_cache_file))).to eq(retired)
                end
            end

            it 'does not look up ids that are not CHS ids' do
                expect(WebCalTides.retired_tide_station?('NOAA123')).to be(false)
                expect(WebCalTides.retired_tide_station?(nil)).to be(false)
                expect(chs).not_to have_received(:retired_stations)
            end

            context 'with the real CHS client' do
                let(:chs) { Clients::ChsTides.new(Logger.new(File::NULL)) }

                it 'does not cache an unparseable DFO station list, and retries it' do
                    freeze_time(Time.utc(2026, 10, 7, 12))
                    allow(chs).to receive(:get_url).and_return('<html>502 Bad Gateway</html>')

                    expect(WebCalTides.retired_tide_station?('5cebf1e43d0f4a073c4bc3e5')).to be(false)
                    expect(retired_cache_files).to be_empty
                    expect(WebCalTides.logger).to have_received(:error).with(/failed to get retired CHS stations/)

                    allow(chs).to receive(:get_url).and_return(recorded_station_list)
                    Timecop.freeze(Time.utc(2026, 10, 7, 12) + WebCalTides::TIDE_STATIONS_RETRY + 1)
                    expect(WebCalTides.retired_tide_station?('5cebf1e43d0f4a073c4bc3e5')).to be(true)
                    expect(retired_cache_files.length).to eq(1)
                end

                it 'does not mark every station retired when the listing loses its wlp-hilo series' do
                    allow(chs).to receive(:get_url).and_return(reshaped_station_list(:remove))

                    expect(WebCalTides.retired_tide_station?(halifax)).to be(false)
                    expect(retired_cache_files).to be_empty
                end
            end
        end

        describe 'WebCalTides.fetch_tide_stations' do
            it 'counts a degraded CHS station list as incomplete, so it is not cached for the quarter' do
                chs = Clients::ChsTides.new(Logger.new(File::NULL))
                allow(chs).to receive(:get_url).and_return(reshaped_station_list(:remove))
                allow(WebCalTides).to receive(:tide_clients).and_return({ chs: chs })

                stations, complete = WebCalTides.fetch_tide_stations

                expect(stations.length).to eq(1570)
                expect(complete).to be(false)
                expect(WebCalTides.logger).to have_received(:error).with(/degraded/)
            end
        end

        describe 'GET /tides/:station.ics' do
            let(:now) { Time.utc(2026, 10, 7, 14, 37) }

            # No station list left over from another example: a retired id never needs one
            around do |example|
                saved = %i[@tide_stations @tide_stations_retry_at].to_h { |v| [v, WebCalTides.instance_variable_get(v)] }
                saved.each_key { |v| WebCalTides.instance_variable_set(v, nil) }
                example.run
            ensure
                saved.each { |v, value| WebCalTides.instance_variable_set(v, value) }
            end

            before do
                freeze_time(now)
                allow(WebCalTides).to receive(:station_ids).and_return(['NOAA123'])
                allow(WebCalTides).to receive(:cleanup_if_month_changed)
            end

            def notice_event
                cal = Icalendar::Calendar.parse(last_response.body).first
                expect(cal.events.length).to eq(1)
                cal.events.first
            end

            it 'answers a retired station with one all-day event spanning the month' do
                get '/tides/5cebf1e43d0f4a073c4bc3e5.ics'

                expect(last_response.status).to eq(200)
                expect(last_response.content_type).to include('text/calendar')

                event = notice_event
                expect(event.summary.to_s).to eq('Station retired by DFO – no tide predictions available')
                expect(event.dtstart).to be_a(Icalendar::Values::Date)
                expect(event.dtend).to be_a(Icalendar::Values::Date)
                expect(event.dtstart.to_date).to eq(Date.new(2026, 10, 1))
                expect(event.dtend.to_date).to eq(Date.new(2026, 11, 1)) # exclusive: through Oct 31
                expect(event.description.to_s).to include('no longer publishes tide predictions', 'Toronto', 'webcaltides.org')
                expect(Icalendar::Calendar.parse(last_response.body).first.x_wr_calname.first.to_s).to include('Toronto')
            end

            it 'shows a later subscriber in the month the notice as current' do
                get '/tides/5cebf1e43d0f4a073c4bc3e5.ics'

                Timecop.freeze(Time.utc(2026, 10, 31, 23, 50))
                get '/tides/5cebf1e43d0f4a073c4bc3e5.ics'

                event = notice_event
                expect(Date.new(2026, 10, 31)).to be >= event.dtstart.to_date
                expect(Date.new(2026, 10, 31)).to be < event.dtend.to_date
            end

            it 'ignores the date parameter' do
                get '/tides/5dd3064de0fdc4b9b4be6685.ics', date: '20261120'
                event = notice_event
                expect(event.dtstart.to_date).to eq(Date.new(2026, 10, 1))
                expect(event.dtend.to_date).to eq(Date.new(2026, 11, 1))

                get '/tides/5dd3064de0fdc4b9b4be6685.ics', date: '20261020'
                expect(notice_event.dtstart.to_date).to eq(Date.new(2026, 10, 1))

                expect(Dir.glob("#{@cache_dir}/*.ics").map { |f| File.basename(f) })
                    .to eq(['tides_retired_5dd3064de0fdc4b9b4be6685_202610.ics'])
            end

            it 'still rejects a malformed date parameter' do
                get '/tides/5dd3064de0fdc4b9b4be6685.ics', date: 'bogus'
                expect(last_response.status).to eq(422)
            end

            it 'caches the feed for the month' do
                allow(WebCalTides).to receive(:retired_tide_calendar_for).and_call_original

                get '/tides/5cebf1e43d0f4a073c4bc3e5.ics'
                first = last_response.body
                get '/tides/5cebf1e43d0f4a073c4bc3e5.ics', units: 'metric', lunar: '1'

                expect(last_response.body).to eq(first)
                expect(WebCalTides).to have_received(:retired_tide_calendar_for).once
                expect(Dir.glob("#{@cache_dir}/*.ics").map { |f| File.basename(f) })
                    .to eq(['tides_retired_5cebf1e43d0f4a073c4bc3e5_202610.ics'])
            end

            it 'still answers 404 for an id that never existed' do
                get '/tides/000000000000000000000000.ics'
                expect(last_response.status).to eq(404)

                get '/tides/INVALID.ics'
                expect(last_response.status).to eq(404)
            end

            it 'answers 404 for a retired id under currents' do
                get '/currents/5cebf1e43d0f4a073c4bc3e5.ics'
                expect(last_response.status).to eq(404)
            end

            it 'does not build the station list or the harmonics cache key for a retired id' do
                expect(WebCalTides).not_to receive(:station_ids)
                expect(WebCalTides).not_to receive(:tide_station_for)
                expect(WebCalTides).not_to receive(:harmonics_cache_key)

                get '/tides/5cebf1e43d0f4a073c4bc3e5.ics'
                expect(last_response.status).to eq(200)
            end

            it 'does not label a 404 as a calendar when the retired notice cannot be made' do
                allow(WebCalTides).to receive(:retired_tide_calendar_for).and_return(nil)

                get '/tides/5cebf1e43d0f4a073c4bc3e5.ics'
                expect(last_response.status).to eq(404)
                expect(last_response.content_type.to_s).not_to include('text/calendar')
            end

            it 'answers a retired station with the notice in the new quarter' do
                Timecop.freeze(Time.utc(2026, 9, 30, 23, 0))
                get '/tides/5cebf1e43d0f4a073c4bc3e5.ics'
                expect(last_response.status).to eq(200)

                allow(chs).to receive(:retired_stations).and_return(retired.merge('5cebf1de3d0f4a073c4bb94e' => 'Squamish Inner'))
                Timecop.freeze(Time.utc(2026, 10, 1, 0, 1))
                get '/tides/5cebf1de3d0f4a073c4bb94e.ics'

                expect(last_response.status).to eq(200)
                expect(notice_event.description.to_s).to include('Squamish Inner')
            end
        end

        # The real route and CHS client, with DFO's responses stubbed
        describe 'GET /tides/:station.ics with DFO stubbed' do
            let(:log) { StringIO.new }
            let(:chs) { Clients::ChsTides.new(Logger.new(log)) }
            let(:now) { Time.utc(2026, 10, 7, 14, 37) }
            let(:station_list_body) { recorded_station_list }
            let(:events) do
                [{ 'eventDate' => '2026-10-07T02:07:00Z', 'value' => 1.71 }, { 'eventDate' => '2026-10-07T08:20:00Z', 'value' => 0.42 },
                 { 'eventDate' => '2026-10-07T14:31:00Z', 'value' => 1.80 }, { 'eventDate' => '2026-10-07T20:44:00Z', 'value' => 0.35 }]
            end

            def data_url(id)
                %r{\Ahttps://api-iwls\.dfo-mpo\.gc\.ca/api/v1/stations/#{id}/data\?.*time-series-code=wlp-hilo}
            end

            around do |example|
                saved = %i[@tide_stations @tide_stations_retry_at].to_h { |v| [v, WebCalTides.instance_variable_get(v)] }
                saved.each_key { |v| WebCalTides.instance_variable_set(v, nil) }
                example.run
            ensure
                saved.each { |v, value| WebCalTides.instance_variable_set(v, value) }
            end

            before do
                freeze_time(now)
                allow(WebCalTides).to receive(:tide_clients).with(no_args).and_return({ chs: chs })
                allow(WebCalTides).to receive(:tide_clients).with('chs').and_return(chs)
                allow(WebCalTides).to receive(:tide_clients).with(:xtide).and_return(nil)
                allow(WebCalTides).to receive(:current_stations).and_return([])
                allow(WebCalTides).to receive(:harmonics_checksum).and_return('cafebabe')
                allow(WebCalTides).to receive(:cleanup_if_month_changed)
                allow(WebCalTides).to receive(:remove_tide_station).and_call_original

                stub_request(:get, stations_url).to_return(status: 200, body: station_list_body)
                stub_request(:get, data_url(halifax)).to_return(status: 200, body: events.to_json)
            end

            def tide_events
                expect(last_response.status).to eq(200)
                Icalendar::Calendar.parse(last_response.body).first.events.map { |e| e.summary.to_s }
            end

            it 'serves a healthy station' do
                get "/tides/#{halifax}.ics", solar: '0'

                expect(tide_events).to eq(['High Tide 5.61 ft', 'Low Tide 1.378 ft', 'High Tide 5.906 ft', 'Low Tide 1.148 ft'])
            end

            [
                ['an HTML page',         '<html><body>Service Unavailable</body></html>'],
                ['a JSON object',        '{"message":"Internal error","status":500}'],
                ['a list of error objects', '[{"message":"err"}]'],
                ['an event with a null value', '[{"eventDate":"2026-10-07T02:07:00Z","value":null},{"eventDate":"2026-10-07T08:20:00Z","value":0.42}]'],
                ['an event with a null eventDate', '[{"eventDate":null,"value":1.71},{"eventDate":"2026-10-07T08:20:00Z","value":0.42}]']
            ].each do |what, body|
                it "keeps a station whose data request answers 200 with #{what}, and caches nothing" do
                    stub_request(:get, data_url(halifax)).to_return(status: 200, body: body)

                    get "/tides/#{halifax}.ics", solar: '0'

                    expect(last_response.status).to eq(404)
                    expect(WebCalTides).not_to have_received(:remove_tide_station)
                    expect(WebCalTides.tide_stations.map(&:id)).to include(halifax)
                    expect(JSON.parse(File.read(WebCalTides.tide_station_cache_file)).map { |js| js['id'] }).to include(halifax)
                    expect(Dir.glob("#{@cache_dir}/tides_*#{halifax}*")).to be_empty
                    expect(log.string).to match(/^E, .*unusable CHS tide data for station #{halifax}/)

                    # DFO recovers: the station is still there to serve
                    stub_request(:get, data_url(halifax)).to_return(status: 200, body: events.to_json)
                    get "/tides/#{halifax}.ics", solar: '0'
                    expect(tide_events.length).to eq(4)
                end
            end

            context 'when timeSeries changes shape' do
                let(:station_list_body) { reshaped_station_list({ 'code' => 'wlp-hilo' }) }

                it 'still serves a healthy station from the unfiltered list' do
                    get "/tides/#{halifax}.ics", solar: '0'

                    expect(tide_events.length).to eq(4)
                    expect(log.string).to match(/^E, .*wlp-hilo/)
                end
            end

            context 'when the station list is degraded and there is no last good list' do
                let(:station_list_body) { reshaped_station_list(:remove) }

                it 'answers a retired station with the notice, from the retired list cached this quarter' do
                    File.write(WebCalTides.retired_tide_station_cache_file, retired.to_json)
                    stub_request(:get, data_url('5cebf1e43d0f4a073c4bc3e5')).to_return(status: 404, body: '')

                    get '/tides/5cebf1e43d0f4a073c4bc3e5.ics', solar: '0'

                    expect(last_response.status).to eq(200)
                    event = Icalendar::Calendar.parse(last_response.body).first.events.first
                    expect(event.summary.to_s).to eq(WebCalTides::RETIRED_TIDE_STATION_SUMMARY)
                    expect(WebCalTides.tide_stations.map(&:id)).to include('5cebf1e43d0f4a073c4bc3e5') # unfiltered
                end
            end
        end
    end
end

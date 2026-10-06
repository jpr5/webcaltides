# frozen_string_literal: true

RSpec.describe Clients::LinzTides do
    let(:logger) { Logger.new('/dev/null') }
    let(:client) { described_class.new(logger) }

    # Recorded from static.charts.linz.govt.nz on 2026-10-06, one cassette per port.  Each year file
    # is trimmed to the header and the days these examples use (Content-Length rewritten to the
    # trimmed size, recorded_at set by hand to 12:00 GMT); everything else in them is as recorded.  Because they are trimmed, a request that doesn't
    # match must fail rather than reach LINZ and append to them (VCR_RECORD=1 still re-records).
    cassette_record = ENV['VCR_RECORD'] ? :all : :none

    def on(data, date)
        day = Date.parse(date)
        data.select { |td| td.time >= day.to_datetime && td.time < (day + 1).to_datetime }
            .map { |td| [td.type, td.time.strftime('%Y-%m-%d %H:%M'), td.prediction] }
    end

    describe '#tide_stations' do
        let(:stations) { client.tide_stations }

        it 'lists the 87 LINZ standard ports with daily predictions, without a network request' do
            expect(stations.length).to eq(87)
            expect(stations).to all(be_a(Models::Station))
            expect(stations).to all(have_attributes(provider: 'linz', region: 'New Zealand', url: described_class::HOME_URL))
        end

        it 'gives every port a unique, namespaced id that stays fixed' do
            expect(stations.map(&:id)).to all(match(/\ANZ__[a-z0-9-]+\z/))
            expect(stations.map(&:id).uniq.length).to eq(87)
            expect(stations.map(&:id)).to include('NZ__auckland', 'NZ__wellington', 'NZ__lyttelton', 'NZ__port-chalmers',
                                                  'NZ__dunedin', 'NZ__whakatane', 'NZ__man-owar-bay', 'NZ__waitangi-chatham-island')
        end

        it 'maps Auckland to its LINZ port number and header position' do
            expect(stations.find { |s| s.id == 'NZ__auckland' }).to have_attributes(
                name: 'Auckland', public_id: '070', location: 'Auckland, New Zealand', lat: -36.85, lon: 174.7667
            )
        end

        it 'places the Chatham Islands and Raoul Island ports east of 180° and Scott Base in Antarctica' do
            expect(stations.find { |s| s.id == 'NZ__waitangi-chatham-island' }).to have_attributes(lat: -43.95, lon: -176.5667)
            expect(stations.find { |s| s.id == 'NZ__fishing-rock-raoul-island' }.lon).to eq(-177.9167)
            expect(stations.find { |s| s.id == 'NZ__scott-base' }.location).to eq('Scott Base, Antarctica')
        end

        it 'adds macron-free spellings, LINZ header spellings and the TICON-style "<name>, NZL", so they can be searched in plain ASCII' do
            names = ->(id) { stations.find { |s| s.id == id }.alternate_names }

            expect(names['NZ__whakatane']).to eq(['Whakatane', 'Whakatane, NZL'])
            expect(names['NZ__kaikoura']).to eq(['Kaikoura', 'Kaikoura, NZL'])
            expect(names['NZ__opotiki-wharf']).to eq(['Opotiki Wharf', 'Opotiki Wharf, NZL'])
            expect(names['NZ__halfmoon-bay-oban']).to eq(['Halfmoon Bay / Oban', 'Halfmoon Bay - Oban, NZL'])
            expect(names['NZ__town-basin']).to eq(['Town Basin - Whangarei', 'Town Basin, NZL'])
            expect(names['NZ__man-owar-bay']).to eq(["Man O' War Bay", "Man o'War Bay", "Man o'War Bay, NZL"])
            expect(names['NZ__auckland']).to eq(['Auckland, NZL'])
        end
    end

    describe '#tide_data_for' do
        before { Timecop.freeze(Time.utc(2026, 10, 6, 12)) }
        after  { Timecop.return }

        def station(slug)
            client.tide_stations.find { |s| s.id == "NZ__#{slug}" }
        end

        context 'Wellington' do
            around { |example| VCR.use_cassette('Clients_LinzTides/wellington', record: cassette_record) { example.run } }

            it 'returns TideData in metres above chart datum, linking to the LINZ tide predictions page' do
                data = client.tide_data_for(station('wellington'), Time.utc(2026, 4, 15))

                expect(data).to all(be_a(Models::TideData))
                expect(data).to all(have_attributes(units: 'm', url: described_class::HOME_URL))
                expect(data.map(&:time)).to eq(data.map(&:time).sort)
                expect(data).to all(satisfy { |td| td.time.offset.zero? })
            end

            it 'converts New Zealand daylight time (UTC+13) and standard time (UTC+12) to UTC across the April change' do
                data = client.tide_data_for(station('wellington'), Time.utc(2027, 4, 15))

                # LINZ CSV, 3 Apr 2027 (NZDT): 02:40 1.6, 08:49 0.7, 14:56 1.5, 21:00 0.8
                expect(on(data, '2027-04-02').last(2)).to eq([['High', '2027-04-02 13:40', 1.6], ['Low', '2027-04-02 19:49', 0.7]])
                # 4 Apr 2027, the day daylight time ends at 03:00: 02:21 1.6 is standard time (not bold
                # in the LINZ PDF), so is everything after it
                expect(on(data, '2027-04-03')).to eq([
                    ['High', '2027-04-03 01:56', 1.5], ['Low', '2027-04-03 08:00', 0.8],
                    ['High', '2027-04-03 14:21', 1.6], ['Low', '2027-04-03 20:30', 0.8]
                ])
                expect(on(data, '2027-04-04').first).to eq(['High', '2027-04-04 02:41', 1.5])
            end

            it 'reads a time in the repeated hour as daylight time when the tide sequence says so' do
                data = client.tide_data_for(station('wellington'), Time.utc(2026, 4, 15))

                # LINZ CSV, 5 Apr 2026: 02:20 0.7 (bold, so daylight time, in the LINZ PDF), 07:21 1.5, 13:36 0.8, 19:45 1.6
                expect(on(data, '2026-04-04').last(2)).to eq([['Low', '2026-04-04 13:20', 0.7], ['High', '2026-04-04 19:21', 1.5]])
                expect(on(data, '2026-04-05').first(2)).to eq([['Low', '2026-04-05 01:36', 0.8], ['High', '2026-04-05 07:45', 1.6]])
            end

            it 'fetches the year files covering the window, and each only once for other months' do
                client.tide_data_for(station('wellington'), Time.utc(2026, 4, 15))
                client.tide_data_for(station('wellington'), Time.utc(2026, 5, 15))

                expect(a_request(:get, 'https://static.charts.linz.govt.nz/tide-tables/maj-ports/csv/Wellington%202026.csv')).to have_been_made.once
                expect(a_request(:get, 'https://static.charts.linz.govt.nz/tide-tables/maj-ports/csv/Wellington%202027.csv')).to have_been_made.once
            end

            it 'keeps the window to 13 months (one month before)' do
                data = client.tide_data_for(station('wellington'), Time.utc(2027, 4, 15))

                # LINZ CSV, 1 Mar 2027 00:26 (NZDT) is 28 Feb UTC, before the window
                expect(data.first.time).to be >= DateTime.new(2027, 3, 1)
                # The window ends 2028-03-30 23:59:59 UTC (end of April + 11 months).  LINZ CSV, 31 Mar
                # 2028 (NZDT): 03:47 0.8 and 09:49 1.5 are 30 Mar UTC; 16:00 0.8, 22:04 1.6 and 1 Apr are after the end
                expect(on(data, '2028-03-30').last(2)).to eq([['Low', '2028-03-30 14:47', 0.8], ['High', '2028-03-30 20:49', 1.5]])
                expect(data.last.time).to eq(DateTime.new(2028, 3, 30, 20, 49))
            end
        end

        context 'Auckland' do
            around { |example| VCR.use_cassette('Clients_LinzTides/auckland', record: cassette_record) { example.run } }

            it 'converts across the September change to daylight time' do
                data = client.tide_data_for(station('auckland'), Time.utc(2026, 9, 15))

                # LINZ CSV, 27 Sep 2026 (clocks go 02:00 -> 03:00): 01:09 0.7 NZST, 08:32 3.1 NZDT, 14:28 0.6, 20:50 3.2
                expect(on(data, '2026-09-26').last(2)).to eq([['Low', '2026-09-26 13:09', 0.7], ['High', '2026-09-26 19:32', 3.1]])
                expect(on(data, '2026-09-27').first(2)).to eq([['Low', '2026-09-27 01:28', 0.6], ['High', '2026-09-27 07:50', 3.2]])
            end

            it 'joins the year files at New Year, which is still 31 December in UTC' do
                data = client.tide_data_for(station('auckland'), Time.utc(2026, 12, 15))

                # LINZ CSV: 31 Dec 2026 20:24 0.7 (2026 file), 1 Jan 2027 02:42 3.0, 08:44 0.9 (2027 file), NZDT
                expect(on(data, '2026-12-31').last(3)).to eq([['Low', '2026-12-31 07:24', 0.7], ['High', '2026-12-31 13:42', 3.0], ['Low', '2026-12-31 19:44', 0.9]])
            end

            it 'leaves out a year LINZ has not published yet (S3 403), and does not ask again for it' do
                log    = StringIO.new
                client = described_class.new(Logger.new(log))
                st     = client.tide_stations.find { |s| s.id == 'NZ__auckland' }

                data = client.tide_data_for(st, Time.utc(2029, 7, 15))
                client.tide_data_for(st, Time.utc(2029, 8, 15))

                expect(data.first.time).to be >= DateTime.new(2029, 6, 1)
                # Up to the last event LINZ published, LINZ CSV 31 Jul 2029 23:38 3.1 (NZST); the window
                # runs on into the 2030 file that isn't there
                expect([data.last.type, data.last.time.strftime('%Y-%m-%d %H:%M'), data.last.prediction]).to eq(['High', '2029-07-31 11:38', 3.1])
                expect(log.string).to match(/WARN .*403 for 2030 LINZ data of station NZ__auckland/)
                expect(a_request(:get, 'https://static.charts.linz.govt.nz/tide-tables/maj-ports/csv/Auckland%202030.csv')).to have_been_made.once
            end
        end

        context 'Chatham Islands' do
            around { |example| VCR.use_cassette('Clients_LinzTides/waitangi_chatham', record: cassette_record) { example.run } }

            it 'converts Chatham Islands time (UTC+13:45 daylight, +12:45 standard) to UTC' do
                data = client.tide_data_for(station('waitangi-chatham-island'), Time.utc(2026, 4, 15))

                # LINZ CSV, 5 Apr 2026: 02:45 0.2 (bold: Chatham daylight time), 08:08 0.8, 14:39 0.2, 20:42 0.7
                expect(on(data, '2026-04-04').last(2)).to eq([['Low', '2026-04-04 13:00', 0.2], ['High', '2026-04-04 19:23', 0.8]])
                expect(on(data, '2026-04-05').first(2)).to eq([['Low', '2026-04-05 01:54', 0.2], ['High', '2026-04-05 07:57', 0.7]])
            end
        end
    end

    describe 'credit' do
        it 'gives the LINZ attribution with the licence link, says what we changed, and disclaims navigation use on the feed' do
            expect(described_class.feed_description).to eq(
                'This work is based on Toitū Te Whenua Land Information New Zealand data which are licensed by Toitū Te Whenua ' \
                'Land Information New Zealand for re-use under the Creative Commons Attribution 4.0 International licence ' \
                '(https://creativecommons.org/licenses/by/4.0/). Source: LINZ tide predictions, ' \
                'https://www.linz.govt.nz/products-services/tides-and-tidal-streams/tide-predictions. ' \
                'Changes: times converted from New Zealand local time (Chatham Islands time for the Chatham Islands ports) to UTC; ' \
                'high and low water labels added from the order of the heights (the LINZ files do not label them); ' \
                'heights shown in metres or converted to feet; and the predictions presented as calendar events. ' \
                'NOT FOR NAVIGATION. These are LINZ website tide predictions, not the official tide tables specified in ' \
                'Maritime Rules Part 25; LINZ accepts no liability for their use.'
            )
        end

        it 'credits LINZ with the licence on every event' do
            expect(described_class.event_description).to eq(
                'Based on Toitū Te Whenua Land Information New Zealand (LINZ) data, CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). NOT FOR NAVIGATION.'
            )
        end

        it 'never calls the predictions official tide tables' do
            text = described_class.feed_description + described_class.event_description
            expect(text.scan(/official/i).length).to eq(1)
            expect(text).to include('not the official tide tables')
        end
    end

    describe 'failure handling', :aggregate_failures do
        let(:log)    { StringIO.new }
        let(:logger) { Logger.new(log).tap { |l| l.formatter = proc { |sev, _, _, msg| "#{sev} #{msg}\n" } } }
        let(:station) { client.tide_stations.find { |s| s.id == 'NZ__wellington' } }

        let(:header) { "\uFEFF071,Wellington,41°17'S,174°47'E   \r\nBased on constituent set with reference date:,01-Jan-2012\r\nLocal Std or Daylight Time,Tidal heights in metres.\r\n" }

        def year_file(*rows)
            header + rows.map { |r| "#{r}\r\n" }.join
        end

        # A row for a January day of a given year, with its events (time, height, ...) and the
        # weekday as LINZ writes it (e.g. "Fr")
        def jan(day, year, *events)
            "#{day},#{Date.new(year, 1, day).strftime('%a')[0, 2]},1,#{year},#{events.join(',')}"
        end

        let(:good_2027) { year_file(jan(1, 2027, '02:00', '1.6', '08:10', '0.7', '14:13', '1.5', '20:18', '0.8')) }

        # Year files LINZ serves (by year); a missing year is S3's 403, an exception is raised
        let(:files) { {} }

        before do
            allow(client).to receive(:get_url) do |url|
                body = files.fetch(url[/(\d{4})\.csv\z/, 1].to_i) { raise Mechanize::ResponseCodeError.new(double(code: '403'), '403') }
                raise body if body.is_a?(Exception)
                body
            end
        end

        def fetch(year_files, around: Time.utc(2027, 1, 15), now: around)
            files.replace(year_files)
            Timecop.freeze(now) { client.tide_data_for(station, around) }
        end

        def requested(year)
            have_received(:get_url).with(a_string_ending_with("#{year}.csv"))
        end

        def http_error(code)
            Mechanize::ResponseCodeError.new(double(code: code), code)
        end

        it 'reads a well-formed file' do
            data = fetch({ 2027 => good_2027 })
            expect(data.map { |td| [td.type, td.time.strftime('%m-%d %H:%M'), td.prediction] }).to eq([
                ['High', '12-31 13:00', 1.6], ['Low', '12-31 19:10', 0.7], ['High', '01-01 01:13', 1.5], ['Low', '01-01 07:18', 0.8]
            ])
        end

        it 'decides equal neighbouring heights by alternation' do
            data = fetch({ 2027 => year_file(jan(1, 2027, '00:26', '2.5', '06:49', '2.4', '08:28', '2.4', '17:57', '2.2'), jan(2, 2027, '23:41', '2.4')) })
            expect(data.map(&:type)).to eq(%w[High Low High Low High])
        end

        it 'returns nil and logs an error with the station id when the file is for another port, and backs off for that year' do
            other = good_2027.sub('071,Wellington', '070,Auckland')

            expect(fetch({ 2027 => other })).to be_nil
            expect(log.string).to match(/^ERROR .*LINZ tide data for station NZ__wellington .*unusable \(expected port 071/)
            expect(fetch({ 2027 => good_2027 })).to be_nil
            expect(client).to requested(2027).once

            # After the back-off the file is fetched again (the unusable body is not kept)
            expect(fetch({ 2027 => good_2027 }, now: Time.utc(2027, 1, 15) + described_class::UNAVAILABLE_RETRY + 1)).to be_present
            expect(client).to requested(2027).twice
        end

        it 'returns nil when the file does not say its times are local' do
            expect(fetch({ 2027 => good_2027.sub('Local Std or Daylight Time', 'UTC') })).to be_nil
            expect(log.string).to match(/^ERROR .*NZ__wellington .*unusable/)
        end

        it 'returns nil on a body that is neither a UTF-8 nor a Windows-1252 LINZ file' do
            expect(fetch({ 2027 => "\xFF\xFE\x00garbage".b })).to be_nil
            expect(log.string).to match(/^ERROR .*NZ__wellington .*unusable/)
        end

        it 'reads a year file in Windows-1252 (the degree sign in the header of 29 ports\' 2025 files)' do
            latin1 = good_2027.delete_prefix("\uFEFF").encode('Windows-1252').b
            expect(latin1.dup.force_encoding('UTF-8')).not_to be_valid_encoding

            data = fetch({ 2027 => latin1 })
            expect(data.map { |td| [td.type, td.time.strftime('%m-%d %H:%M'), td.prediction] }).to eq([
                ['High', '12-31 13:00', 1.6], ['Low', '12-31 19:10', 0.7], ['High', '01-01 01:13', 1.5], ['Low', '01-01 07:18', 0.8]
            ])
        end

        it 'lets an unusable year file fail only the windows that need that year, and backs off for that year' do
            good_2028 = good_2027.gsub(',2027,', ',2028,')
            bad_2028  = good_2028.sub('071,Wellington', '070,Auckland')
            now       = Time.utc(2027, 1, 15)

            expect(fetch({ 2027 => good_2027, 2028 => bad_2028 }, around: Time.utc(2027, 12, 15), now: now)).to be_nil
            expect(log.string).to match(/^ERROR .*LINZ tide data for station NZ__wellington .*2028\.csv unusable/)

            # The current window doesn't need 2028, so it is not blocked
            expect(fetch({ 2027 => good_2027 }, now: now)).to be_present

            # The window that needs 2028 stays blocked until the back-off ends
            expect(fetch({ 2027 => good_2027, 2028 => good_2028 }, around: Time.utc(2027, 12, 15), now: now)).to be_nil
            expect(client).to requested(2028).once
        end

        it 'returns nil and logs an error when LINZ returns no body, and backs off for that year' do
            expect(fetch({ 2026 => nil, 2027 => nil })).to be_nil
            expect(log.string).to match(/^ERROR .*got no LINZ tide data for station NZ__wellington/)
            expect(fetch({ 2026 => good_2027.gsub(',2027,', ',2026,'), 2027 => good_2027 })).to be_nil
            expect(client).to requested(2026).once
        end

        it 'returns nil and logs an error when a year file has no readable events, and backs off for that year' do
            expect(fetch({ 2027 => year_file('garbage') })).to be_nil
            expect(log.string).to match(/^ERROR .*LINZ tide data for station NZ__wellington at \S+2027\.csv has no events/)
            expect(fetch({ 2027 => good_2027 })).to be_nil
            expect(client).to requested(2027).once
        end

        it 'omits heights, but keeps the times, when the units line changes' do
            data = fetch({ 2027 => good_2027.sub('Tidal heights in metres.', 'Tidal heights in feet.') })
            expect(data.map(&:prediction)).to all(be_nil)
            expect(data.length).to eq(4)
            expect(log.string).to match(/^WARN omitting heights of LINZ data for station NZ__wellington/)
        end

        it 'skips and logs unreadable rows and events instead of failing the year' do
            data = fetch({ 2027 => year_file(jan(1, 2027, '02:00', '1.6', '25:10', '0.7', '14:13', 'x', '20:18', '0.8'), 'garbage', jan(2, 2026, '01:00', '1.0')) })
            expect(data.map { |td| td.time.strftime('%H:%M') }).to eq(%w[13:00 07:18])
            expect(log.string).to match(/^WARN skipping 4 unreadable LINZ rows\/events of 2027 for station NZ__wellington/)
        end

        it 'skips and logs a local time that does not exist (the hour skipped in September)' do
            data = fetch({ 2026 => year_file('27,Su,9,2026,01:09,0.7,02:30,3.1,08:32,0.6'), }, around: Time.utc(2026, 9, 15))
            expect(data.map { |td| td.time.strftime('%m-%d %H:%M') }).to eq(['09-26 13:09', '09-26 19:32'])
            expect(log.string).to match(/^WARN skipping LINZ event for station NZ__wellington at 2026-09-27 02:30, a local time that doesn't exist/)
        end

        it 'takes the daylight-time reading of a repeated-hour time without neighbours, and logs it' do
            data = fetch({ 2027 => year_file('4,Su,4,2027,02:21,1.6,08:30,0.8') }, around: Time.utc(2027, 4, 15))
            expect(data.map { |td| td.time.strftime('%m-%d %H:%M') }).to eq(['04-03 13:21', '04-03 20:30'])
            expect(log.string).to match(/^WARN LINZ event for station NZ__wellington .* repeated hour without neighbours/)
        end

        it 'negative-caches the window and logs an error when every year file is missing' do
            expect(fetch({})).to be_nil
            expect(fetch({})).to be_nil
            # Logged once: the second request is skipped for the window, before the year files
            expect(log.string.scan(/^ERROR !! no LINZ tide data for station NZ__wellington/).length).to eq(1)
            expect(log.string).to match(/^DEBUG skipping LINZ tide data for NZ__wellington: unavailable/)
            expect(client).to have_received(:get_url).twice   # 2026 and 2027, once each
        end

        it 'logs an HTTP error with the station id, re-raises it, and backs off' do
            expect { fetch({ 2027 => http_error('500') }) }.to raise_error(Mechanize::ResponseCodeError)
            expect(log.string).to match(/^ERROR .*LINZ tide data for station NZ__wellington .*failed \(HTTP 500\)/)
            expect(fetch({ 2027 => good_2027 })).to be_nil
            expect(client).to requested(2027).once
        end

        [SocketError.new('getaddrinfo: nodename nor servname provided'), Net::OpenTimeout.new('execution expired'),
         Errno::ECONNREFUSED.new, OpenSSL::SSL::SSLError.new('certificate verify failed'),
         Mechanize::ResponseReadError.new(EOFError.new('end of file reached'), nil, StringIO.new, URI('https://static.charts.linz.govt.nz/'), nil),
         Mechanize::ChunkedTerminationError.new(EOFError.new('end of file reached'), nil, StringIO.new, URI('https://static.charts.linz.govt.nz/'), nil)].each do |error|
            it "logs a network failure (#{error.class}) with the station id, re-raises it, and backs off" do
                expect { fetch({ 2027 => error }) }.to raise_error(error.class)
                expect(log.string).to match(/^ERROR .*LINZ tide data for station NZ__wellington unreachable \(#{Regexp.escape(error.class.name)}/)
                expect(fetch({ 2027 => good_2027 })).to be_nil
                expect(client).to requested(2027).once
            end
        end

        it 'tries again after the back-off' do
            expect { fetch({ 2027 => http_error('503') }) }.to raise_error(Mechanize::ResponseCodeError)
            expect(fetch({ 2027 => good_2027 }, around: Time.utc(2027, 1, 15) + described_class::UNAVAILABLE_RETRY + 1)).to be_present
        end

        it 'returns nil and logs an error for a station id that is not a LINZ port, and backs off' do
            other = build_station(id: 'NZ__nowhere', provider: 'linz')
            expect(client.tide_data_for(other, Time.utc(2027, 1, 15))).to be_nil
            expect(client.tide_data_for(other, Time.utc(2027, 2, 15))).to be_nil
            expect(log.string.scan(/^ERROR !! no LINZ port for station NZ__nowhere/).length).to eq(1)
            expect(log.string).to match(/^DEBUG skipping LINZ tide data for NZ__nowhere: unavailable/)
        end
    end
end

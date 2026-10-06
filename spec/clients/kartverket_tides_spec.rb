# frozen_string_literal: true

RSpec.describe Clients::KartverketTides do
    let(:logger) { Logger.new('/dev/null') }
    let(:client) { described_class.new(logger) }

    # Recorded from vannstand.kartverket.no on 2026-10-06, one cassette per request.  The Bergen
    # predictions are trimmed to the events these examples use (the first and last weeks of the
    # window); everything else in them is as recorded.  Because they are trimmed, a request that
    # doesn't match must fail rather than reach the live API and append to them (VCR_RECORD=1 still
    # re-records).
    cassette_record = ENV['VCR_RECORD'] ? :all : :none

    describe '#tide_stations' do
        around { |example| VCR.use_cassette('Clients_KartverketTides/stationlist', record: cassette_record) { example.run } }

        let(:stations) { client.tide_stations }

        it 'fetches the permanent Kartverket gauges' do
            expect(stations).to all(be_a(Models::Station))
            expect(stations.length).to eq(33)
        end

        it 'sets provider to kartverket and region to Norway' do
            expect(stations).to all(have_attributes(provider: 'kartverket', region: 'Norway'))
        end

        it 'maps Bergen to a namespaced id, its station code and its public station page' do
            bergen = stations.find { |s| s.public_id == 'BGO' }

            expect(bergen).to have_attributes(
                id: 'NO__BGO',
                name: 'Bergen',
                location: 'Bergen, Norway',
                lat: 60.398046,
                lon: 5.320487,
                url: 'https://kartverket.no/en/at-sea/se-havniva/result?latitude=60.398046&longitude=5.320487'
            )
        end

        it 'adds plain-ASCII spellings of Norwegian names, so they can be searched without æ/ø/å' do
            expect(stations.find { |s| s.public_id == 'TOS' }).to have_attributes(name: 'Tromsø', alternate_names: %w[Tromso Tromsoe])
            expect(stations.find { |s| s.public_id == 'AES' }).to have_attributes(name: 'Ålesund', alternate_names: %w[Alesund Aalesund])
            expect(stations.find { |s| s.public_id == 'OSL' }).to have_attributes(name: 'Oslo', alternate_names: [])
        end
    end

    describe '#tide_data_for' do
        around { |example| VCR.use_cassette('Clients_KartverketTides/bergen_tides', record: cassette_record) { example.run } }

        let(:station) do
            build_station(id: 'NO__BGO', public_id: 'BGO', provider: 'kartverket', lat: 60.398046, lon: 5.320487,
                          url: 'https://kartverket.no/en/at-sea/se-havniva/result?latitude=60.398046&longitude=5.320487')
        end

        before { Timecop.freeze(Time.utc(2026, 10, 6, 12)) }
        after  { Timecop.return }

        let(:data) { client.tide_data_for(station, Time.utc(2026, 10, 6)) }

        it 'returns TideData in meters, linking to the station page' do
            expect(data).to all(be_a(Models::TideData))
            expect(data).to all(have_attributes(units: 'm', url: station.url))
        end

        it 'passes Kartverket times through unchanged, as UTC, and converts chart-datum heights from cm to m' do
            # vannstand.kartverket.no, Bergen, 2026-10-01 (UTC): high 00:22 161.7 cm, low 06:14 43.2 cm,
            # high 12:50 154.8 cm, low 18:36 49.5 cm
            day = data.select { |td| td.time >= DateTime.new(2026, 10, 1) && td.time < DateTime.new(2026, 10, 2) }

            expect(day.map { |td| [td.type, td.time.strftime('%H:%M'), td.prediction] }).to eq([
                ['High', '00:22', 1.617], ['Low', '06:14', 0.432], ['High', '12:50', 1.548], ['Low', '18:36', 0.495]
            ])
            expect(day).to all(satisfy { |td| td.time.offset.zero? })
        end

        it 'covers the whole 13-month window in one request' do
            expect(data.first.time).to be >= DateTime.new(2026, 9, 1)
            expect(data.last.time).to be <= DateTime.new(2027, 9, 30, 23, 59, 59)
            expect(data.last.time).to be >= DateTime.new(2027, 9, 30)
            expect(data.map(&:time)).to eq(data.map(&:time).sort)
            expect(a_request(:get, %r{\Ahttps://vannstand\.kartverket\.no/})).to have_been_made.once
        end
    end

    describe 'credit' do
        it 'credits © Kartverket with a link and the CC BY 4.0 licence, says what we changed, and disclaims navigation use on the feed' do
            expect(described_class.feed_description).to eq(
                '© Kartverket (Norwegian Mapping Authority, Hydrographic Service), https://www.kartverket.no/, ' \
                'licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). ' \
                'Heights converted from cm above chart datum to metres or feet, and high and low waters presented as calendar events; times unchanged, in UTC. ' \
                'NOT FOR NAVIGATION. Official tide predictions of Kartverket, distributed as they are; ' \
                'Kartverket takes no responsibility for their use.'
            )
        end

        it 'credits © Kartverket with a link on every event' do
            expect(described_class.event_description).to eq(
                '© Kartverket (Norwegian Mapping Authority, Hydrographic Service), https://www.kartverket.no/, ' \
                'licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). NOT FOR NAVIGATION.'
            )
        end
    end

    describe 'failure handling', :aggregate_failures do
        let(:log)    { StringIO.new }
        let(:logger) { Logger.new(log).tap { |l| l.formatter = proc { |sev, _, _, msg| "#{sev} #{msg}\n" } } }
        let(:station) do
            build_station(id: 'NO__TST', public_id: 'TST', provider: 'kartverket', lat: 60.0, lon: 5.0,
                          url: 'https://kartverket.no/x')
        end

        def stationlist(*locations)
            "<tide><stationinfo>#{locations.join}</stationinfo></tide>"
        end

        def loc(code: 'TST', name: 'Testby', lat: '60.0', lon: '5.0')
            %(<location name="#{name}" code="#{code}" latitude="#{lat}" longitude="#{lon}" type="PERM"/>)
        end

        def wl(time, flag: 'high', value: '150.0')
            %(<waterlevel value="#{value}" time="#{time}" flag="#{flag}"/>)
        end

        def locationdata(*levels, code: 'TST', delay: '0', factor: '1.00', datum: 'CD', unit: 'cm')
            <<~XML
                <tide><locationdata>
                <location name="Testby" code="#{code}" latitude="60.0" longitude="5.0" delay="#{delay}" factor="#{factor}" obsname="Testby" obscode="TST" descr="Tides from Testby"/>
                <reflevelcode>#{datum}</reflevelcode>
                <data type="prediction" unit="#{unit}">#{levels.join}</data>
                </locationdata></tide>
            XML
        end

        def fetch(body, around: Time.utc(2027, 1, 1), now: around)
            allow(client).to receive(:get_url).and_return(body)
            Timecop.freeze(now) { client.tide_data_for(station, around) }
        end

        def http_error(code)
            Mechanize::ResponseCodeError.new(double(code: code), code)
        end

        describe '#tide_stations' do
            it 'returns nil and logs an error when Kartverket returns no body' do
                allow(client).to receive(:get_url).and_return(nil)

                expect(client.tide_stations).to be_nil
                expect(log.string).to match(/^ERROR .*Kartverket tide station list/)
            end

            {
                'not XML'          => '<html>Vedlikehold',
                'an API error'     => %(<?xml version="1.0"?>\n<error>Unrecognized command</error>),
                'a nested error'   => '<tide><stationinfo><error>Service unavailable</error></stationinfo></tide>'
            }.each do |what, body|
                it "returns nil and logs an error on #{what} instead of an empty list" do
                    allow(client).to receive(:get_url).and_return(body)

                    expect(client.tide_stations).to be_nil
                    expect(log.string).to match(/^ERROR .*Kartverket tide station list.*unusable/)
                end
            end

            it 'returns nil and logs an error when the list has no stations' do
                allow(client).to receive(:get_url).and_return(stationlist)

                expect(client.tide_stations).to be_nil
                expect(log.string).to match(/^ERROR .*Kartverket tide station list.*has no stations/)
            end

            it 'skips and logs an entry without a code or position instead of failing the whole list' do
                allow(client).to receive(:get_url).and_return(stationlist(loc, loc(code: '', name: 'Kaputt'), loc(code: 'XYZ', lat: 'n/a')))

                expect(client.tide_stations.map(&:id)).to eq(['NO__TST'])
                expect(log.string.scan(/^WARN .*skipping Kartverket station without code, name or position/).length).to eq(2)
            end
        end

        describe '#tide_data_for' do
            it 'asks for UTC times and heights above chart datum, for the whole window, at the station position' do
                fetch(locationdata(wl('2026-10-01T03:00:00+00:00')), around: Time.utc(2026, 10, 15))

                expect(client).to have_received(:get_url).with(
                    'https://vannstand.kartverket.no/tideapi.php?tide_request=locationdata&lat=60.0&lon=5.0&datatype=tab' \
                    '&refcode=cd&lang=en&tzone=0&dst=0&fromtime=2026-09-01T00%3A00&totime=2027-09-30T23%3A59'
                )
            end

            it 'normalizes any explicit offset to UTC without shifting the instant' do
                data = fetch(locationdata(wl('2027-07-01T08:29:00+02:00', flag: 'low', value: '-12.5'), wl('2027-01-01T01:13:00+00:00')))

                expect(data.map { |td| [td.type, td.time, td.prediction] }).to eq([
                    ['High', DateTime.new(2027, 1, 1, 1, 13), 1.5], ['Low', DateTime.new(2027, 7, 1, 6, 29), -0.125]
                ])
                # DateTime == compares instants only, so also check the offset itself: the calendar
                # renders the wall-clock time as TZID=GMT
                expect(data.map { |td| td.time.strftime('%Y-%m-%dT%H:%M%:z') }).to eq(['2027-01-01T01:13+00:00', '2027-07-01T06:29+00:00'])
            end

            it 'skips and logs events with an unknown flag or a timestamp without an offset' do
                data = fetch(locationdata(wl('2027-01-01T01:13:00+00:00'), wl('2027-01-01T07:00:00+00:00', flag: 'mid'), wl('2027-01-01T13:00:00')))

                expect(data.length).to eq(1)
                expect(log.string).to match(/^WARN .*skipping 2 Kartverket events for station NO__TST/)
            end

            it 'keeps events without a usable height, with one warning that counts them' do
                data = fetch(locationdata(wl('2027-01-01T01:13:00+00:00'), wl('2027-01-01T07:25:00+00:00', flag: 'low', value: ''),
                                          wl('2027-01-01T13:40:00+00:00', value: 'n/a')))

                expect(data.map(&:prediction)).to eq([1.5, nil, nil])
                expect(log.string.scan(/^WARN .*Kartverket/).length).to eq(1)
                expect(log.string).to match(/^WARN .*2 Kartverket events for station NO__TST without a usable height/)
            end

            it 'omits heights, with a warning, when they are not in cm above chart datum' do
                data = fetch(locationdata(wl('2027-01-01T01:13:00+00:00'), datum: 'MSL'))

                expect(data.map(&:prediction)).to eq([nil])
                expect(log.string).to match(/^WARN .*omitting heights .*NO__TST.*"MSL"/)
            end

            it 'accepts a gauge whose predictions Kartverket derives from another gauge with no time or height change' do
                # Sandnes (SBG) resolves to itself, with obscode Stavanger (SVG), delay 0 and factor 1.00
                data = fetch(locationdata(wl('2027-01-01T01:13:00+00:00')).sub('obscode="TST"', 'obscode="SVG"'))

                expect(data.length).to eq(1)
            end

            {
                'another gauge'   => { code: 'BGO' },
                'a delayed zone'  => { delay: '15' },
                'a scaled zone'   => { factor: '0.90' }
            }.each do |what, attrs|
                it "returns nil and logs an error when the position resolves to #{what}" do
                    expect(fetch(locationdata(wl('2027-01-01T01:13:00+00:00'), **attrs))).to be_nil
                    expect(log.string).to match(/^ERROR .*Kartverket tide data for station NO__TST.*is not for gauge TST/)
                end
            end

            it 'returns nil and logs an error on an API error' do
                expect(fetch('<tide><locationdata><error>Position outside area</error></locationdata></tide>')).to be_nil
                expect(log.string).to match(/^ERROR .*Kartverket tide data for station NO__TST.*unusable \(Position outside area\)/)
            end

            it 'returns nil and logs an error on a response that is not XML' do
                expect(fetch('<html>Feil')).to be_nil
                expect(log.string).to match(/^ERROR .*Kartverket tide data for station NO__TST.*unusable \(not XML\)/)
            end

            it 'returns nil and logs an error when there is no data in the window' do
                expect(fetch(locationdata(wl('2020-01-01T01:13:00+00:00')), around: Time.utc(2026, 10, 15))).to be_nil
                expect(log.string).to match(/^ERROR .*no Kartverket tide data for station NO__TST between 2026-09-01 and 2027-09-30/)
            end

            it 'does not ask again for a while after an error, then does' do
                allow(client).to receive(:get_url).and_return('<error>Service unavailable</error>')

                Timecop.freeze(Time.utc(2027, 1, 1, 0)) { 3.times { expect(client.tide_data_for(station, Time.utc(2027, 1, 1))).to be_nil } }
                # A station-level failure blocks the other months too
                Timecop.freeze(Time.utc(2027, 1, 1, 0, 30)) { expect(client.tide_data_for(station, Time.utc(2027, 2, 1))).to be_nil }
                expect(client).to have_received(:get_url).once

                Timecop.freeze(Time.utc(2027, 1, 1, 1, 0, 1)) { client.tide_data_for(station, Time.utc(2027, 1, 1)) }
                expect(client).to have_received(:get_url).twice
            end

            it 'does not ask again for a month without data, but still asks for other months' do
                allow(client).to receive(:get_url).and_return(locationdata)

                Timecop.freeze(Time.utc(2027, 1, 1)) do
                    2.times { client.tide_data_for(station, Time.utc(2027, 1, 1)) }
                    client.tide_data_for(station, Time.utc(2027, 2, 1))
                end
                expect(client).to have_received(:get_url).twice
            end

            it 'returns nil, logs a warning and backs off after a 404' do
                allow(client).to receive(:get_url).and_raise(http_error('404'))

                Timecop.freeze(Time.utc(2027, 1, 1)) { 2.times { expect(client.tide_data_for(station, Time.utc(2027, 1, 1))).to be_nil } }
                expect(client).to have_received(:get_url).once
                expect(log.string).to match(/^WARN 404 for station NO__TST/)
            end

            it 're-raises other HTTP errors, but still backs off' do
                allow(client).to receive(:get_url).and_raise(http_error('500'))

                Timecop.freeze(Time.utc(2027, 1, 1)) do
                    expect { client.tide_data_for(station, Time.utc(2027, 1, 1)) }.to raise_error(Mechanize::ResponseCodeError)
                    expect(client.tide_data_for(station, Time.utc(2027, 1, 1))).to be_nil
                end
                expect(client).to have_received(:get_url).once
            end

            [
                Net::ReadTimeout.new, Net::OpenTimeout.new, SocketError.new('getaddrinfo: nodename nor servname provided'),
                Errno::ECONNREFUSED.new, Errno::ECONNRESET.new, OpenSSL::SSL::SSLError.new('certificate verify failed'), EOFError.new,
                # What Mechanize raises for a truncated body, a chunked body cut short, and an undecodable one
                Mechanize::ResponseReadError.new(EOFError.new('Content-Length (900) does not match response body length (512)'), nil, nil, nil, nil),
                Mechanize::ChunkedTerminationError.new(EOFError.new('end of file reached'), nil, nil, nil, nil),
                Mechanize::Error.new('error handling content-encoding gzip: invalid compressed data -- format violated (Zlib::DataError)')
            ].each do |error|
                it "re-raises #{error.class} after get_url gives up, logs it with the station, and backs off" do
                    allow(client).to receive(:get_url).and_raise(error)

                    Timecop.freeze(Time.utc(2027, 1, 1)) do
                        expect { client.tide_data_for(station, Time.utc(2027, 1, 1)) }.to raise_error(error.class)
                        expect(client.tide_data_for(station, Time.utc(2027, 1, 1))).to be_nil
                    end
                    expect(client).to have_received(:get_url).once
                    expect(log.string).to match(/^ERROR .*Kartverket tide data for station NO__TST unreachable \(#{Regexp.escape(error.class.name)}/)
                end
            end

            it 'returns nil and logs an error when Kartverket returns no body' do
                expect(fetch(nil)).to be_nil
                expect(log.string).to match(/^ERROR .*got no Kartverket tide data/)
            end
        end
    end
end

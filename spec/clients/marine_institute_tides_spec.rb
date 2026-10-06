# frozen_string_literal: true

RSpec.describe Clients::MarineInstituteTides do
    let(:logger) { Logger.new('/dev/null') }
    let(:client) { described_class.new(logger) }

    # Recorded from erddap.marine.ie on 2026-10-06, one cassette per request.  The Dublin Port
    # predictions are trimmed to the events these examples use (the first and last days of the
    # window, and 6-8 and 23-27 October 2026 around the end of Irish summer time); everything else
    # in them is as recorded.  Because they are trimmed, a request that doesn't match must fail
    # rather than reach ERDDAP and append to them (VCR_RECORD=1 still re-records).
    cassette_record = ENV['VCR_RECORD'] ? :all : :none

    def on(data, date)
        day = Date.parse(date)
        data.select { |td| td.time >= day.to_datetime && td.time < (day + 1).to_datetime }
            .map { |td| [td.type, td.time.strftime('%Y-%m-%d %H:%M'), td.prediction] }
    end

    describe '#tide_stations' do
        around { |example| VCR.use_cassette('Clients_MarineInstituteTides/stationlist', record: cassette_record) { example.run } }

        let(:stations) { client.tide_stations }

        it 'fetches the 38 Marine Institute prediction stations' do
            expect(stations).to all(be_a(Models::Station))
            expect(stations.length).to eq(38)
            expect(stations).to all(have_attributes(provider: 'imi', region: 'Ireland', url: described_class::HOME_URL))
        end

        it 'gives every station a unique id namespaced with IE__ and the ERDDAP station id' do
            expect(stations.map(&:id)).to all(match(/\AIE__[A-Za-z0-9_]+\z/))
            expect(stations.map(&:id).uniq.length).to eq(38)
            expect(stations.map(&:id)).to include('IE__Dublin_Port', 'IE__Galway', 'IE__Ringaskiddy', 'IE__Howth', 'IE__Killybegs')
            expect(stations).to all(satisfy { |s| s.id == "IE__#{s.public_id}" })
        end

        it 'maps Dublin Port to its ERDDAP id, name, county and position' do
            expect(stations.find { |s| s.id == 'IE__Dublin_Port' }).to have_attributes(
                name: 'Dublin Port', public_id: 'Dublin_Port', location: 'Dublin Port, Co. Dublin, Ireland',
                lat: 53.34574, lon: -6.22166
            )
        end

        it 'has a chart datum offset for every listed station' do
            expect(stations.map(&:public_id)).to match_array(described_class::STATIONS.keys)
        end

        it 'adds common spellings, the county and the TICON-style "<name>, IRL", so they can be searched' do
            names = ->(id) { stations.find { |s| s.id == id }.alternate_names }

            expect(names['IE__Ringaskiddy']).to eq(['Ringaskiddy, Co. Cork', 'Ringaskiddy, IRL'])
            expect(names['IE__Buncranna']).to eq(['Buncrana', 'Buncranna, Co. Donegal', 'Buncranna, IRL'])
            expect(names['IE__Dunmore']).to eq(['Dunmore East', 'Dunmore, Co. Waterford', 'Dunmore, IRL'])
            # On the Galway/Mayo border: no county
            expect(names['IE__Killary_Harbour']).to eq(['Killary Harbour, IRL'])
            expect(stations.find { |s| s.id == 'IE__Killary_Harbour' }.location).to eq('Killary Harbour, Ireland')
        end
    end

    describe '.alternate_names' do
        it 'adds a plain-ASCII spelling of a name with fadas' do
            expect(described_class.alternate_names('Dun_Laoghaire', 'Dún Laoghaire')).to eq(['Dun Laoghaire', 'Dún Laoghaire, IRL'])
        end
    end

    describe '#tide_data_for' do
        before { Timecop.freeze(Time.utc(2026, 10, 6, 12)) }
        after  { Timecop.return }

        let(:station) do
            build_station(id: 'IE__Dublin_Port', public_id: 'Dublin_Port', provider: 'imi', lat: 53.34574, lon: -6.22166,
                          url: described_class::HOME_URL)
        end

        context 'Dublin Port' do
            around { |example| VCR.use_cassette('Clients_MarineInstituteTides/dublin_port', record: cassette_record) { example.run } }

            let(:data) { client.tide_data_for(station, Time.utc(2026, 10, 15)) }

            it 'returns TideData in metres, linking to the MI tidal predictions page' do
                expect(data).to all(be_a(Models::TideData))
                expect(data).to all(have_attributes(units: 'm', url: described_class::HOME_URL))
                expect(data.map(&:time)).to eq(data.map(&:time).sort)
            end

            it 'keeps the UTC times MI publishes, in summer time and after it ends on 25 October' do
                # ERDDAP: 2026-10-07T09:05:00Z HIGH.  Irish time was UTC+1 then, so reading it as local
                # time would give 08:05 UTC.
                expect(data.find { |td| td.time == DateTime.new(2026, 10, 7, 9, 5) }).to have_attributes(type: 'High')
                expect(data).to all(satisfy { |td| td.time.offset.zero? })
                expect(on(data, '2026-10-25').map { |type, time, _| [type, time] }).to eq([
                    ['Low', '2026-10-25 04:00'], ['High', '2026-10-25 10:45'], ['Low', '2026-10-25 16:15'], ['High', '2026-10-25 22:50']
                ])
                expect(on(data, '2026-10-26').map { |type, time, _| [type, time] }).to eq([
                    ['Low', '2026-10-26 04:35'], ['High', '2026-10-26 11:20'], ['Low', '2026-10-26 16:50'], ['High', '2026-10-26 23:25']
                ])
            end

            it 'converts heights from OD Malin to chart datum (LAT) with the station offset' do
                # ERDDAP OD Malin heights -1.569, 1.329, -1.184, 1.645, plus Dublin Port's 2.458 m.  MI's own
                # LAT heights at those times (IMI-TidePrediction Water_Level): 0.89, 3.79, 1.27, 4.10.
                expect(on(data, '2026-10-07')).to eq([
                    ['Low', '2026-10-07 02:20', 0.89], ['High', '2026-10-07 09:05', 3.79],
                    ['Low', '2026-10-07 14:40', 1.27], ['High', '2026-10-07 21:15', 4.1]
                ])
            end

            it 'covers the whole 13-month window in one request, with the query percent-encoded' do
                expect(data.first.time).to be >= DateTime.new(2026, 9, 1)
                expect(data.last.time).to be <= DateTime.new(2027, 9, 30, 23, 59, 59)
                expect(on(data, '2026-09-01').first(1)).to eq([['High', '2026-09-01 01:35', 4.22]])

                expect(a_request(:get, "#{described_class::ERDDAP_URL}.csv?time,stationID,tide_time_category,Water_Level_ODMalin" \
                                       "&stationID=%22Dublin_Port%22&time%3E=2026-09-01T00:00:00Z&time%3C=2027-09-30T23:59:59Z")).to have_been_made.once
            end
        end

        context 'a window past the published horizon' do
            around { |example| VCR.use_cassette('Clients_MarineInstituteTides/past_horizon', record: cassette_record) { example.run } }

            let(:log)    { StringIO.new }
            let(:logger) { Logger.new(log).tap { |l| l.formatter = proc { |sev, _, _, msg| "#{sev} #{msg}\n" } } }

            it 'returns nil with a warning for ERDDAP 404 (no matching rows), and does not ask again for that month' do
                Timecop.freeze(Time.utc(2030, 6, 1)) do
                    2.times { expect(client.tide_data_for(station, Time.utc(2030, 6, 1))).to be_nil }
                end
                expect(a_request(:get, /IMI_TidePrediction_HighLow\.csv/)).to have_been_made.once
                expect(log.string).to match(/^WARN 404 \(no matching rows\) from Marine Institute for station IE__Dublin_Port/)
            end
        end

        context 'a dataset ERDDAP does not know (renamed or removed)' do
            # Recorded with ERDDAP_URL pointed at a dataset id that doesn't exist: ERDDAP answers 404
            # here too, but with "Currently unknown datasetID" instead of "no matching results"
            around { |example| VCR.use_cassette('Clients_MarineInstituteTides/unknown_dataset', record: cassette_record) { example.run } }
            before { stub_const("#{described_class}::ERDDAP_URL", 'https://erddap.marine.ie/erddap/tabledap/IMI_TidePrediction_HighLow_GONE') }

            let(:log)    { StringIO.new }
            let(:logger) { Logger.new(log).tap { |l| l.formatter = proc { |sev, _, _, msg| "#{sev} #{msg}\n" } } }

            it 're-raises the 404 as an error, logs it with the station, and backs off for every month' do
                expect { client.tide_data_for(station, Time.utc(2026, 10, 15)) }.to raise_error(Mechanize::ResponseCodeError)
                expect(client.tide_data_for(station, Time.utc(2026, 11, 15))).to be_nil
                expect(a_request(:get, /IMI_TidePrediction_HighLow_GONE\.csv/)).to have_been_made.once
                expect(log.string).to match(/^ERROR .*Marine Institute tide data for station IE__Dublin_Port failed with HTTP 404 .*unknown datasetID/)
                expect(log.string).not_to match(/^WARN 404/)
            end
        end
    end

    describe 'credit' do
        it 'credits the Marine Institute with links and the CC BY 4.0 licence, says exactly what we changed, and disclaims navigation use on the feed' do
            expect(described_class.feed_description).to eq(
                'Data supplied by Marine Institute (Ireland), https://www.marine.ie/site-area/data-services/real-time-observations/tidal-predictions, ' \
                'licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). ' \
                'Changes: heights converted from metres above OD Malin to metres above chart datum (Lowest Astronomical Tide) by adding a fixed ' \
                "offset for each station, taken from MI's own IMI-TidePrediction dataset (Water_Level minus Water_Level_ODM), rounded to 0.01 m, " \
                'and shown in metres or converted to feet; high and low waters presented as calendar events; times unchanged, in UTC as MI publishes them. ' \
                'NOT FOR NAVIGATION. Tide predictions of the Marine Institute; MI accepts no responsibility for errors ' \
                'or for their use. Storm surge (atmospheric pressure and wind) is not included.'
            )
        end

        it 'says the heights were left out, not converted, for a feed without heights' do
            tides = [build_tide_data(prediction: nil, units: 'm')]
            # Without an offset, or for a unit other than metres, or with no usable value: one note true for all
            expect(described_class.feed_description(tides)).to include(
                'Changes: heights left out (MI\'s heights for this station could not be converted to chart datum)'
            )
            expect(described_class.feed_description(tides)).not_to include('no chart datum offset')
            expect(described_class.feed_description(tides)).not_to include('OD Malin')
        end

        it 'credits the Marine Institute with the licence on every event' do
            expect(described_class.event_description).to eq(
                'Data supplied by Marine Institute, CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). NOT FOR NAVIGATION.'
            )
        end
    end

    describe 'failure handling', :aggregate_failures do
        let(:log)    { StringIO.new }
        let(:logger) { Logger.new(log).tap { |l| l.formatter = proc { |sev, _, _, msg| "#{sev} #{msg}\n" } } }
        let(:station) do
            build_station(id: 'IE__Dublin_Port', public_id: 'Dublin_Port', provider: 'imi', lat: 53.3, lon: -6.2,
                          url: described_class::HOME_URL)
        end

        def stationlist(*rows, header: 'stationID,longitude,latitude')
            [header, ',degrees_east,degrees_north', *rows].join("\n") + "\n"
        end

        def highlow(*rows, header: 'time,stationID,tide_time_category,Water_Level_ODMalin', units: 'UTC,,,metres')
            [header, units, *rows].join("\n") + "\n"
        end

        def row(time = '2027-01-10T09:05:00Z', category = 'HIGH', height = '1.329', id: 'Dublin_Port')
            [time, id, category, height].join(',')
        end

        def fetch(body, around: Time.utc(2027, 1, 1), now: around)
            allow(client).to receive(:get_url).and_return(body)
            Timecop.freeze(now) { client.tide_data_for(station, around) }
        end

        def http_error(code, body = nil)
            Mechanize::ResponseCodeError.new(double(code: code, body: body), code)
        end

        # ERDDAP's 404 bodies, as erddap.marine.ie sent them on 2026-10-06
        let(:no_matching_results) { "Error {\n    code=404;\n    message=\"Not Found: Your query produced no matching results. (nRows = 0)\";\n}\n" }
        let(:unknown_dataset)     { "Error {\n    code=404;\n    message=\"Not Found: Currently unknown datasetID=IMI_TidePrediction_HighLow_BOGUS\";\n}\n" }

        describe '#tide_stations' do
            it 'returns nil (not []) and logs an error when ERDDAP returns no body' do
                allow(client).to receive(:get_url).and_return(nil)
                expect(client.tide_stations).to be_nil
                expect(log.string).to match(/^ERROR .*got no Marine Institute tide station list/)
            end

            it 'returns nil and logs an error for an unexpected header (e.g. an ERDDAP error page)' do
                allow(client).to receive(:get_url).and_return("Error {\n    code=500;\n}\n")
                expect(client.tide_stations).to be_nil
                expect(log.string).to match(/^ERROR .*tide station list .* unusable/)
            end

            it 'returns nil and logs an error when the list has no stations' do
                allow(client).to receive(:get_url).and_return(stationlist)
                expect(client.tide_stations).to be_nil
                expect(log.string).to match(/^ERROR .*has no stations/)
            end

            it 'skips and logs a row without an id or position instead of failing the whole list' do
                allow(client).to receive(:get_url).and_return(stationlist('Dublin_Port,-6.22166,53.34574', 'Bad id!,-6,53', 'Howth,,53.39'))
                expect(client.tide_stations.map(&:id)).to eq(['IE__Dublin_Port'])
                expect(log.string.scan(/^WARN skipping Marine Institute station/).length).to eq(2)
            end

            it 'returns nil (not []) and logs an error when every row is unusable' do
                allow(client).to receive(:get_url).and_return(stationlist('Bad id!,-6,53', 'Howth,,53.39'))
                expect(client.tide_stations).to be_nil
                expect(log.string).to match(/^ERROR .*tide station list .* no usable stations/)
            end

            it 'lists a station MI adds before we know its chart datum offset, with a warning' do
                allow(client).to receive(:get_url).and_return(stationlist('New_Pier,-6.0,53.0'))
                expect(client.tide_stations.map(&:id)).to eq(['IE__New_Pier'])
                expect(log.string).to match(/^WARN no chart datum offset for Marine Institute station New_Pier/)
            end
        end

        describe '#tide_data_for' do
            it 'returns nil and logs an error for an unexpected header or a time unit other than UTC' do
                expect(fetch(highlow(row, header: 'time,stationID,Water_Level_ODMalin'))).to be_nil
                expect(described_class.new(logger).tap { |c| allow(c).to receive(:get_url).and_return(highlow(row, units: 'seconds since 1970-01-01T00:00:00Z,,,metres')) }
                    .tide_data_for(station, Time.utc(2027, 1, 1))).to be_nil
                expect(log.string.scan(/^ERROR .*Marine Institute tide data for station IE__Dublin_Port .* unusable/).length).to eq(2)
            end

            it 'returns nil and logs an error when the rows are for another station' do
                expect(fetch(highlow(row, row(id: 'Howth')))).to be_nil
                expect(log.string).to match(/^ERROR .*IE__Dublin_Port .* has rows for "Howth"/)
            end

            it 'skips and logs events with an unknown type or a timestamp without Z' do
                data = fetch(highlow(row, row('2027-01-10T15:20:00Z', 'SLACK'), row('2027-01-10T21:30:00', 'HIGH')))
                expect(data.length).to eq(1)
                expect(log.string).to match(/^WARN skipping 2 Marine Institute events for station IE__Dublin_Port/)
            end

            it 'keeps events without a usable height, with one warning that counts them' do
                data = fetch(highlow(row, row('2027-01-10T15:20:00Z', 'LOW', 'NaN'), row('2027-01-10T21:30:00Z', 'HIGH', '')))
                expect(data.map(&:prediction)).to eq([3.79, nil, nil])
                expect(log.string.scan(/keeping \d+ Marine Institute events/)).to eq(['keeping 2 Marine Institute events'])
            end

            it 'omits heights, with a warning, for a station without a chart datum offset' do
                station.public_id = 'New_Pier'
                data = fetch(highlow(row(id: 'New_Pier')))
                expect(data.map(&:prediction)).to eq([nil])
                expect(log.string).to match(/^WARN omitting heights .* no chart datum offset/)
            end

            it 'omits heights, with a warning, when they are not in metres' do
                expect(fetch(highlow(row, units: 'UTC,,,feet')).map(&:prediction)).to eq([nil])
                expect(log.string).to match(/^WARN omitting heights .* unexpected unit "feet"/)
            end

            it 'returns nil and logs an error when there is no data in the window' do
                expect(fetch(highlow(row('2029-01-10T09:05:00Z')))).to be_nil
                expect(log.string).to match(/^ERROR .*no Marine Institute tide data for station IE__Dublin_Port/)
            end

            it 'returns nil and logs an error when ERDDAP returns no body' do
                expect(fetch(nil)).to be_nil
                expect(log.string).to match(/^ERROR .*got no Marine Institute tide data for station IE__Dublin_Port/)
            end

            it 'does not ask again for a while after an error, then does' do
                allow(client).to receive(:get_url).and_return("Error {\n}\n")

                Timecop.freeze(Time.utc(2027, 1, 1, 0)) { 3.times { expect(client.tide_data_for(station, Time.utc(2027, 1, 1))).to be_nil } }
                # A station-level failure blocks the other months too
                Timecop.freeze(Time.utc(2027, 1, 1, 0, 30)) { expect(client.tide_data_for(station, Time.utc(2027, 2, 1))).to be_nil }
                expect(client).to have_received(:get_url).once

                Timecop.freeze(Time.utc(2027, 1, 1, 1, 0, 1)) { client.tide_data_for(station, Time.utc(2027, 1, 1)) }
                expect(client).to have_received(:get_url).twice
            end

            it 'does not ask again for a month without data, but still asks for other months' do
                allow(client).to receive(:get_url).and_return(highlow)

                Timecop.freeze(Time.utc(2027, 1, 1)) do
                    2.times { client.tide_data_for(station, Time.utc(2027, 1, 1)) }
                    client.tide_data_for(station, Time.utc(2027, 2, 1))
                end
                expect(client).to have_received(:get_url).twice
            end

            it 'returns nil, logs a warning and backs off for that month after a 404 for no matching results' do
                allow(client).to receive(:get_url).and_raise(http_error('404', no_matching_results))

                Timecop.freeze(Time.utc(2027, 1, 1)) do
                    2.times { expect(client.tide_data_for(station, Time.utc(2027, 1, 1))).to be_nil }
                    client.tide_data_for(station, Time.utc(2027, 2, 1))
                end
                expect(client).to have_received(:get_url).twice
                expect(log.string).to match(/^WARN 404 .* for station IE__Dublin_Port/)
            end

            { 'an unknown dataset' => :unknown_dataset, 'no body' => nil, 'an unrecognised body' => '<html>Not Found</html>' }.each do |what, body|
                it "re-raises a 404 with #{what}, logs it as an error with the station, and backs off for every month" do
                    allow(client).to receive(:get_url).and_raise(http_error('404', body.is_a?(Symbol) ? send(body) : body))

                    Timecop.freeze(Time.utc(2027, 1, 1)) do
                        expect { client.tide_data_for(station, Time.utc(2027, 1, 1)) }.to raise_error(Mechanize::ResponseCodeError)
                        expect(client.tide_data_for(station, Time.utc(2027, 2, 1))).to be_nil
                    end
                    expect(client).to have_received(:get_url).once
                    expect(log.string).to match(/^ERROR .*Marine Institute tide data for station IE__Dublin_Port failed with HTTP 404/)
                    expect(log.string).not_to match(/^WARN 404/)
                end
            end

            it 're-raises other HTTP errors, logs them with the station, and backs off' do
                allow(client).to receive(:get_url).and_raise(http_error('500'))

                Timecop.freeze(Time.utc(2027, 1, 1)) do
                    expect { client.tide_data_for(station, Time.utc(2027, 1, 1)) }.to raise_error(Mechanize::ResponseCodeError)
                    expect(client.tide_data_for(station, Time.utc(2027, 1, 1))).to be_nil
                end
                expect(client).to have_received(:get_url).once
                expect(log.string).to match(/^ERROR .*Marine Institute tide data for station IE__Dublin_Port failed with HTTP 500/)
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
                    expect(log.string).to match(/^ERROR .*Marine Institute tide data for station IE__Dublin_Port unreachable \(#{Regexp.escape(error.class.name)}/)
                end
            end
        end
    end
end

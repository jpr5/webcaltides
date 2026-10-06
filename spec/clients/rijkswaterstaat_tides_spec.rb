# frozen_string_literal: true

RSpec.describe Clients::RijkswaterstaatTides do
    let(:logger) { Logger.new('/dev/null') }
    let(:client) { described_class.new(logger) }

    # Recorded from ddapi20-waterwebservices.rijkswaterstaat.nl on 2026-10-06, one cassette per
    # request (the station list: the catalogue and the prediction count that follows it).  The
    # catalogue is trimmed to the tide metadata and eleven locations (nine with high/low waters vs
    # NAP, two of them without current predictions; one offshore point vs MSL; one gauge with
    # measurements only); the Stavenisse predictions to 1 Sep 2026, 24-26 Oct 2026 (around the end
    # of Dutch summer time) and 30 Sep 2027, and the partial window to its first and last published
    # days.  Everything else is as recorded.  Because they are trimmed, a request that doesn't
    # match must fail rather than reach RWS and append to them (VCR_RECORD=1 still re-records).
    cassette_record = ENV['VCR_RECORD'] ? :all : :none

    def on(data, date)
        day = Date.parse(date)
        data.select { |td| td.time >= day.to_datetime && td.time < (day + 1).to_datetime }
            .map { |td| [td.type, td.time.strftime('%Y-%m-%d %H:%M'), td.prediction] }
    end

    def query(from, to)
        {
            Locatie: { Code: 'stavenisse' },
            AquoPlusWaarnemingMetadata: { AquoMetadata: { ProcesType: 'astronomisch', Groepering: { Code: 'GETETBRKD2' } } },
            Periode: { Begindatumtijd: from, Einddatumtijd: to }
        }.to_json
    end

    describe '#tide_stations' do
        around { |example| VCR.use_cassette('Clients_RijkswaterstaatTides/stationlist', record: cassette_record) { example.run } }
        before { Timecop.freeze(Time.utc(2026, 10, 6, 12)) }
        after  { Timecop.return }

        let(:stations) { client.tide_stations }

        it 'lists the locations with current high/low water predictions vs NAP, and only those' do
            expect(stations).to all(be_a(Models::Station))
            expect(stations.map(&:public_id)).to match_array(%w[
                ameland.nes denhelder.marsdiep harlingen.waddenzee hoekvanholland knock stavenisse vlissingen
            ])
            expect(stations).to all(have_attributes(provider: 'rws', url: described_class::HOME_URL))
        end

        it 'gives each station the country it is in as its region' do
            # Knock is the one listed gauge outside the Netherlands (Antwerpen, Prosperpolder is in the
            # catalogue but left out for having no current predictions)
            expect(stations.find { |s| s.id == 'NL__knock' }).to have_attributes(region: 'Germany')
            expect(stations.reject { |s| s.id == 'NL__knock' }).to all(have_attributes(region: 'Netherlands'))
        end

        it 'leaves out locations the catalogue lists but RWS has no current predictions for' do
            # Beerkanaal (next to Hoek van Holland) has predictions up to 2015 only, Antwerpen,
            # Prosperpolder none now either: RWS answers 204 for their window, so they could only 404
            expect(stations.map(&:public_id)).not_to include('beerkanaal', 'antwerpen.prosperpolder')
        end

        it 'asks which of them have predictions for the coming month, in one POST for all of them' do
            stations
            expect(a_request(:post, described_class::COUNT_URL).with(body: {
                LocatieLijst: %w[ameland.nes antwerpen.prosperpolder beerkanaal denhelder.marsdiep harlingen.waddenzee hoekvanholland knock stavenisse vlissingen].map { |c| { Code: c } },
                AquoMetadataLijst: [{ ProcesType: 'astronomisch', Groepering: { Code: 'GETETBRKD2' }, Typering: { Code: 'GETETTPE' } }],
                Groeperingsperiode: 'Jaar',
                Periode: { Begindatumtijd: '2026-10-06T00:00:00.000+00:00', Einddatumtijd: '2026-11-06T00:00:00.000+00:00' }
            }.to_json)).to have_been_made.once
        end

        it 'asks the catalogue for the metadata it filters on, in one POST' do
            stations
            expect(a_request(:post, described_class::CATALOGUE_URL)
                .with(body: '{"CatalogusFilter":{"Grootheden":true,"Groeperingen":true,"Hoedanigheden":true,"ProcesTypes":true}}')).to have_been_made.once
        end

        it 'gives every station a stable id: NL__ and the RWS location code' do
            expect(stations.map(&:id)).to include('NL__stavenisse', 'NL__vlissingen', 'NL__hoekvanholland', 'NL__denhelder.marsdiep', 'NL__harlingen.waddenzee')
            expect(stations).to all(satisfy { |s| s.id == "NL__#{s.public_id}" })
        end

        it 'keeps RWS names and positions, and the country of a location outside the Netherlands' do
            expect(stations.find { |s| s.id == 'NL__denhelder.marsdiep' }).to have_attributes(
                name: 'Den Helder, Marsdiep', location: 'Den Helder, Marsdiep, Netherlands', lat: 52.964359, lon: 4.74499,
                alternate_names: ['Den Helder, Marsdiep, NLD']
            )
            expect(stations.find { |s| s.id == 'NL__knock' }).to have_attributes(
                location: 'Knock, Germany', alternate_names: []
            )
        end
    end

    describe '::COUNTRIES' do
        # RWS gives no country per location; these are the three of its 101 high/low water gauges
        # outside the Netherlands (checked against the live catalogue, Oct 2026)
        it 'names the country of each gauge outside the Netherlands' do
            expect(described_class::COUNTRIES).to eq(
                'antwerpen.prosperpolder' => 'Belgium', 'knock' => 'Germany', 'pogum' => 'Germany'
            )
        end
    end

    describe '.alternate_names' do
        it 'adds a plain-ASCII spelling and the TICON-style "<name>, NLD"' do
            expect(described_class.alternate_names('xx', 'Ëemshaven')).to eq(['Eemshaven', 'Ëemshaven, NLD'])
        end
    end

    describe '#tide_data_for' do
        before { Timecop.freeze(Time.utc(2026, 10, 6, 12)) }
        after  { Timecop.return }

        let(:station) do
            build_station(id: 'NL__stavenisse', public_id: 'stavenisse', provider: 'rws', lat: 51.598, lon: 4.004,
                          url: described_class::HOME_URL)
        end

        context 'Stavenisse' do
            around { |example| VCR.use_cassette('Clients_RijkswaterstaatTides/stavenisse', record: cassette_record) { example.run } }

            let(:data) { client.tide_data_for(station, Time.utc(2026, 10, 15)) }

            it 'returns TideData in metres, linking to Waterinfo, as a complete window' do
                expect(data).to all(be_a(Models::TideData))
                expect(data).to all(have_attributes(units: 'm', url: described_class::HOME_URL))
                expect(data.map(&:time)).to eq(data.map(&:time).sort)
                expect(data).not_to respond_to(:partial?)
            end

            it 'reads the fixed +01:00 RWS uses all year as one hour ahead of UTC, in summer time and after it ends on 25 October' do
                # RWS: 2026-10-24T01:56+01:00 HW (summer time, when Dutch clocks said 02:56) and
                # 2026-10-26T03:20+01:00 HW (winter time).  Both are 1 hour ahead of UTC.
                expect(data).to all(satisfy { |td| td.time.offset.zero? })
                expect(on(data, '2026-10-24').map { |type, time, _| [type, time] }).to eq([
                    ['High', '2026-10-24 00:56'], ['Low', '2026-10-24 06:54'], ['High', '2026-10-24 13:09'], ['Low', '2026-10-24 19:14']
                ])
                expect(on(data, '2026-10-26').map { |type, time, _| [type, time] }).to eq([
                    ['High', '2026-10-26 02:20'], ['Low', '2026-10-26 08:07'], ['High', '2026-10-26 14:29'], ['Low', '2026-10-26 20:28']
                ])
            end

            it 'gives heights in metres above NAP, as RWS publishes them in centimetres' do
                expect(on(data, '2026-10-25')).to eq([
                    ['High', '2026-10-25 01:39', 1.81], ['Low', '2026-10-25 07:31', -1.15],
                    ['High', '2026-10-25 13:50', 1.7], ['Low', '2026-10-25 19:51', -1.45]
                ])
            end

            it 'covers the whole 13-month window in one request, with the period in UTC' do
                expect(on(data, '2026-09-01').first).to eq(['High', '2026-09-01 05:14', 1.74])
                expect(on(data, '2027-09-30').last).to eq(['Low', '2027-09-30 20:34', -1.56])
                expect(a_request(:post, described_class::DATA_URL)
                    .with(body: query('2026-09-01T00:00:00.000+00:00', '2027-09-30T23:59:59.000+00:00'))).to have_been_made.once
            end
        end

        context 'a window past the published horizon (RWS publishes to 31 Dec 2027)' do
            around { |example| VCR.use_cassette('Clients_RijkswaterstaatTides/partial', record: cassette_record) { example.run } }

            it 'serves what there is, marked partial so it is not cached for the month' do
                data = client.tide_data_for(station, Time.utc(2027, 6, 15))
                expect(data).to be_partial
                expect(on(data, '2027-05-01').first).to eq(['Low', '2027-05-01 04:08', -1.27])
                expect(data.last.time).to eq(DateTime.new(2027, 12, 31, 22, 37))
            end

            it 'reuses the partial window for 6 hours, then asks RWS again' do
                Timecop.freeze(Time.utc(2027, 6, 15)) { 2.times { client.tide_data_for(station, Time.utc(2027, 6, 15)) } }
                Timecop.freeze(Time.utc(2027, 6, 15, 5, 59)) { client.tide_data_for(station, Time.utc(2027, 6, 15)) }
                expect(a_request(:post, described_class::DATA_URL)).to have_been_made.once

                Timecop.freeze(Time.utc(2027, 6, 15, 6, 0, 1)) { client.tide_data_for(station, Time.utc(2027, 6, 15)) }
                expect(a_request(:post, described_class::DATA_URL)).to have_been_made.twice
            end

            it 'drops partial windows of other months older than 7 days when it keeps a new one, so the store stays bounded' do
                cassette = YAML.load_file(File.join(__dir__, '../fixtures/cassettes/Clients_RijkswaterstaatTides/partial.yml'))
                allow(client).to receive(:post_json).and_return(cassette['http_interactions'][0]['response']['body']['string'])
                months = (6..11).map { |m| Time.utc(2027, m, 15) }
                partial = -> { client.instance_variable_get(:@partial) }

                Timecop.freeze(Time.utc(2027, 6, 15)) { months.each { |m| expect(client.tide_data_for(station, m)).to be_partial } }
                expect(partial.call.size).to eq(6)

                Timecop.freeze(Time.utc(2027, 6, 15, 6, 0, 1)) { expect(client.tide_data_for(station, Time.utc(2027, 12, 15))).to be_partial }
                expect(partial.call.size).to eq(7)

                Timecop.freeze(Time.utc(2027, 6, 22, 0, 0, 1)) { expect(client.tide_data_for(station, Time.utc(2028, 1, 15))).to be_partial }
                expect(partial.call.keys).to eq(['NL__stavenisse@202712', 'NL__stavenisse@202801'])
            end
        end

        context 'a window RWS has nothing for' do
            around { |example| VCR.use_cassette('Clients_RijkswaterstaatTides/past_horizon', record: cassette_record) { example.run } }

            let(:log)    { StringIO.new }
            let(:logger) { Logger.new(log).tap { |l| l.formatter = proc { |sev, _, _, msg| "#{sev} #{msg}\n" } } }

            it 'returns nil with a warning for 204 No Content, and does not ask again for that month' do
                Timecop.freeze(Time.utc(2029, 6, 15)) do
                    2.times { expect(client.tide_data_for(station, Time.utc(2029, 6, 15))).to be_nil }
                end
                expect(a_request(:post, described_class::DATA_URL)).to have_been_made.once
                expect(log.string).to match(/^WARN no Rijkswaterstaat tide data \(empty response\) for station NL__stavenisse/)
            end
        end
    end

    describe 'credit' do
        it 'names the source, says what we changed and that heights are above NAP, and disclaims navigation use on the feed' do
            expect(described_class.feed_description).to eq(
                'Source: Rijkswaterstaat (Netherlands), astronomical tide predictions from the WaterWebservices (https://waterinfo.rws.nl), ' \
                'CC0 (https://creativecommons.org/publicdomain/zero/1.0/). ' \
                'Heights are above NAP (Normaal Amsterdams Peil, the Dutch land-survey datum), not chart datum (LAT): converted from ' \
                'centimetres to metres, or to feet. Times converted from the fixed +01:00 RWS uses all year to UTC. High and low waters ' \
                'presented as calendar events. NOT FOR NAVIGATION. Computed astronomical tide: weather (wind, air pressure) is not included. ' \
                'Rijkswaterstaat says its data service has no uptime guarantee and is not suitable for critical applications, and that use is at your own risk.'
            )
        end

        it 'attributes to Rijkswaterstaat only what it says (https://rijkswaterstaatdata.nl/waterdata/), not a liability statement' do
            expect(described_class::DISCLAIMER).not_to match(/liab/i)
            expect(described_class::DISCLAIMER).to start_with('NOT FOR NAVIGATION.')
        end

        it 'says the heights were left out, not converted, for a feed without heights' do
            tides = [build_tide_data(prediction: nil, units: 'm')]
            expect(described_class.feed_description(tides)).to include('Heights left out')
            expect(described_class.feed_description(tides)).not_to include('Heights are above NAP')
        end

        it 'names the source and the datum on every event' do
            expect(described_class.event_description(build_tide_data(prediction: 1.2, units: 'm'))).to eq(
                'Source: Rijkswaterstaat, CC0. Height above NAP, not chart datum. NOT FOR NAVIGATION.'
            )
            expect(described_class.event_description(build_tide_data(prediction: nil, units: 'm'))).to eq('Source: Rijkswaterstaat, CC0. NOT FOR NAVIGATION.')
        end

        it 'labels heights with their datum' do
            expect(described_class.height_datum).to eq('NAP')
        end
    end
    describe 'failure handling', :aggregate_failures do
        let(:log)    { StringIO.new }
        let(:logger) { Logger.new(log).tap { |l| l.formatter = proc { |sev, _, _, msg| "#{sev} #{msg}\n" } } }
        let(:station) do
            build_station(id: 'NL__stavenisse', public_id: 'stavenisse', provider: 'rws', lat: 51.598, lon: 4.004,
                          url: described_class::HOME_URL)
        end

        def metadata(id, grouping, quantity, datum, process: 'astronomisch')
            { 'AquoMetadata_MessageID' => id, 'ProcesType' => process, 'Groepering' => { 'Code' => grouping },
              'Grootheid' => { 'Code' => quantity }, 'Hoedanigheid' => { 'Code' => datum } }
        end

        def location(id, code, name = code.capitalize, lat: 51.6, lon: 4.0)
            { 'Locatie_MessageID' => id, 'Code' => code, 'Naam' => name, 'Lat' => lat, 'Lon' => lon }
        end

        def catalogue(locations, links: locations.map { |l| [250, l['Locatie_MessageID']] }, succesvol: true)
            {
                'Succesvol' => succesvol,
                'AquoMetadataLijst' => [metadata(173, 'GETETBRKD2', 'NVT', 'NVT'), metadata(250, 'GETETBRKD2', 'WATHTE', 'NAP')],
                'AquoMetadataLocatieLijst' => links.map { |m, l| { 'AquoMetaData_MessageID' => m, 'Locatie_MessageID' => l } },
                'LocatieLijst' => locations
            }.to_json
        end

        # The prediction count for the locations that have current predictions (an empty count is
        # left out, as RWS does)
        def counts(*codes)
            { 'Succesvol' => true, 'AantalWaarnemingenPerPeriodeLijst' => codes.map { |c|
                { 'AantalMetingenPerPeriodeLijst' => [{ 'AantalMetingen' => 120, 'Groeperingsperiode' => { 'Jaarnummer' => 2026 } }],
                  'Locatie' => { 'Code' => c } }
            } }.to_json
        end

        def station_list(catalogue, counts)
            allow(client).to receive(:post_json).with(described_class::CATALOGUE_URL, anything).and_return(catalogue)
            allow(client).to receive(:post_json).with(described_class::COUNT_URL, anything).and_return(counts)
            client.tide_stations
        end

        # A WaterWebservices response: the type series and the height series, sharing timestamps
        def series(events, code: 'stavenisse', datum: 'NAP', unit: 'cm', heights: true)
            types = { 'AquoMetadata' => { 'Grootheid' => { 'Code' => 'NVT' }, 'Typering' => { 'Code' => 'GETETTPE' } },
                      'Locatie' => { 'Code' => code },
                      'MetingenLijst' => events.map { |t, type, _| { 'Tijdstip' => t, 'Meetwaarde' => { 'Waarde_Alfanumeriek' => type } } } }
            cm = { 'AquoMetadata' => { 'Grootheid' => { 'Code' => 'WATHTE' }, 'Hoedanigheid' => { 'Code' => datum }, 'Eenheid' => { 'Code' => unit } },
                   'Locatie' => { 'Code' => code },
                   'MetingenLijst' => events.map { |t, _, h, q| { 'Tijdstip' => t, 'Meetwaarde' => { 'Waarde_Numeriek' => h },
                                                                    'WaarnemingMetadata' => { 'Kwaliteitswaardecode' => q || '00' } } } }
            { 'Succesvol' => true, 'WaarnemingenLijst' => heights ? [types, cm] : [types] }.to_json
        end

        # As RWS writes them: always +01:00
        def stamp(time)
            time.getlocal('+01:00').strftime('%Y-%m-%dT%H:%M:%S.000+01:00')
        end

        # The window for around = 2027-01-01, as TimeWindow computes it (2026-12-01 .. 2027-12-29)
        let(:from) { client.beginning_of_window(Time.utc(2027, 1, 1)) }
        let(:to)   { client.end_of_window(Time.utc(2027, 1, 1)) }

        # Events an hour inside both ends of that window, plus any in between
        def full(*middle)
            [[stamp(from + 1.hour), 'hoogwater', 150], *middle, [stamp(to - 1.hour), 'laagwater', -120]]
        end

        def fetch(body, around: Time.utc(2027, 1, 1), now: around)
            allow(client).to receive(:post_json).and_return(body)
            Timecop.freeze(now) { client.tide_data_for(station, around) }
        end

        def http_error(code)
            Mechanize::ResponseCodeError.new(double(code: code), code)
        end

        describe '#tide_stations' do
            [['no body', ''], ['a body that is not JSON', '<html>Service Unavailable</html>'], ['a truncated body', '{"Succesvol":true,"AquoMetadataLijst":[{']].each do |what, body|
                it "returns nil (not []) and logs an error for #{what}" do
                    allow(client).to receive(:post_json).and_return(body)
                    expect(client.tide_stations).to be_nil
                    expect(log.string).to match(/^ERROR .*Rijkswaterstaat tide station list/)
                end
            end

            it 'returns nil and logs an error when the catalogue says it failed' do
                allow(client).to receive(:post_json).and_return(catalogue([location(1, 'stavenisse')], succesvol: false))
                expect(client.tide_stations).to be_nil
                expect(log.string).to match(/^ERROR .*tide station list .* unusable/)
            end

            it 'returns nil and logs an error when no location has high/low waters vs NAP' do
                allow(client).to receive(:post_json).and_return(catalogue([location(1, 'stavenisse')], links: [[173, 1]]))
                expect(client.tide_stations).to be_nil
                expect(log.string).to match(/^ERROR .*no locations with high\/low water predictions vs NAP/)
            end

            it 'skips and logs a location without a usable code, name or position instead of failing the whole list' do
                stations = station_list(catalogue([
                    location(1, 'stavenisse'), location(2, 'Bad Code!'), location(3, 'vlissingen', ''), location(4, 'yerseke', lat: nil)
                ]), counts('stavenisse'))
                expect(stations.map(&:id)).to eq(['NL__stavenisse'])
                expect(log.string.scan(/^WARN skipping Rijkswaterstaat location/).length).to eq(3)
                expect(client).to have_received(:post_json).with(described_class::COUNT_URL, hash_including(LocatieLijst: [{ Code: 'stavenisse' }]))
            end

            it 'leaves out, and logs, the locations without current predictions' do
                stations = station_list(catalogue([location(1, 'stavenisse'), location(2, 'beerkanaal'), location(3, 'vlissingen')]),
                                        counts('vlissingen', 'stavenisse'))
                expect(stations.map(&:id)).to eq(['NL__stavenisse', 'NL__vlissingen'])
                expect(log.string).to match(/^INFO leaving out 1 Rijkswaterstaat locations? without current predictions: beerkanaal$/)
            end

            it 'does not count a location whose count is zero' do
                zero = JSON.parse(counts('stavenisse', 'beerkanaal')).tap { |c| c['AantalWaarnemingenPerPeriodeLijst'][1]['AantalMetingenPerPeriodeLijst'][0]['AantalMetingen'] = 0 }
                expect(station_list(catalogue([location(1, 'stavenisse'), location(2, 'beerkanaal')]), zero.to_json).map(&:id)).to eq(['NL__stavenisse'])
            end

            it 'returns nil (not []) and logs an error when no location has current predictions' do
                expect(station_list(catalogue([location(1, 'stavenisse'), location(2, 'beerkanaal')]), counts)).to be_nil
                expect(log.string).to match(/^ERROR .*tide station list .* no locations with current predictions/)
            end

            [['no body', ''], ['a body that is not JSON', '<html>Service Unavailable</html>'],
             ['a count that says it failed', { 'Succesvol' => false }.to_json],
             ['a count without its list', { 'Succesvol' => true }.to_json]].each do |what, body|
                it "returns nil (not the uncounted list) and logs an error for #{what} from the prediction count" do
                    expect(station_list(catalogue([location(1, 'stavenisse')]), body)).to be_nil
                    expect(log.string).to match(/^ERROR .*Rijkswaterstaat prediction count .* unusable/)
                end
            end

            it 'raises, logging it, when the prediction count POST fails' do
                allow(client).to receive(:post_json).with(described_class::CATALOGUE_URL, anything).and_return(catalogue([location(1, 'stavenisse')]))
                allow(client).to receive(:post_json).with(described_class::COUNT_URL, anything).and_call_original
                stub_request(:post, described_class::COUNT_URL).to_return(status: 404)
                expect { client.tide_stations }.to raise_error(Mechanize::ResponseCodeError)
                expect(log.string).to match(/^ERROR POST failed/)
            end

            it 'returns nil (not []) and logs an error when every location is unusable' do
                allow(client).to receive(:post_json).and_return(catalogue([location(2, 'Bad Code!'), location(4, 'yerseke', lat: nil)]))
                expect(client.tide_stations).to be_nil
                expect(log.string).to match(/^ERROR .*tide station list .* no usable locations/)
            end

            it 'returns nil, logging it, when the POST fails' do
                stub_request(:post, described_class::CATALOGUE_URL).to_return(status: 404)
                expect { client.tide_stations }.to raise_error(Mechanize::ResponseCodeError)
                expect(log.string).to match(/^ERROR POST failed/)
            end
        end

        describe '#tide_data_for' do
            it 'reads a UTF-8 body sent as binary' do
                body = series(full).b
                expect(fetch(body).length).to eq(2)
            end

            it 'returns nil and logs an error for a body that is not JSON, or is truncated' do
                expect(fetch('<html>Bad Gateway</html>')).to be_nil
                expect(fetch(series(full)[0, 200], around: Time.utc(2027, 2, 1))).to be_nil
                expect(log.string.scan(/^ERROR .*Rijkswaterstaat tide data for station NL__stavenisse unusable/).length).to eq(2)
            end

            it 'returns nil and logs an error when the response says it failed' do
                expect(fetch({ 'Succesvol' => false, 'Foutmelding' => 'x' }.to_json)).to be_nil
                expect(log.string).to match(/^ERROR .*NL__stavenisse unusable/)
            end

            it 'returns nil and logs an error when the series are for another location' do
                expect(fetch(series(full, code: 'vlissingen'))).to be_nil
                expect(log.string).to match(/^ERROR .*NL__stavenisse has a series for "vlissingen"/)
            end

            it 'returns nil and logs an error when there are no high/low water types' do
                body = JSON.parse(series(full)).tap { |d| d['WaarnemingenLijst'].shift }.to_json
                expect(fetch(body)).to be_nil
                expect(log.string).to match(/^ERROR .*NL__stavenisse has no high\/low water types/)
            end

            it 'skips and logs events with an unknown type or a timestamp without an offset, once' do
                data = fetch(series(full(['2027-01-10T15:20:00.000+01:00', 'kentering', 10], ['2027-01-10T21:30:00', 'hoogwater', 150])))
                expect(data.length).to eq(2)
                expect(log.string.scan(/skipping \d+ Rijkswaterstaat events/)).to eq(['skipping 2 Rijkswaterstaat events'])
            end

            it 'keeps events without a usable height (missing, gap code 99, out of range), with one warning that counts them' do
                data = fetch(series(full(['2027-01-10T03:00:00.000+01:00', 'laagwater', nil], ['2027-01-10T09:00:00.000+01:00', 'hoogwater', 151, '99'],
                                         ['2027-01-10T15:00:00.000+01:00', 'laagwater', -999_999_999])))
                expect(data.map(&:prediction)).to eq([1.5, nil, nil, nil, -1.2])
                expect(log.string.scan(/keeping \d+ Rijkswaterstaat events/)).to eq(['keeping 3 Rijkswaterstaat events'])
            end

            it 'omits heights, with a warning, when they are not in centimetres vs NAP' do
                expect(fetch(series(full, datum: 'MSL')).map(&:prediction)).to eq([nil, nil])
                expect(described_class.new(logger).tap { |c| allow(c).to receive(:post_json).and_return(series(full, unit: 'm')) }
                    .tide_data_for(station, Time.utc(2027, 1, 1)).map(&:prediction)).to eq([nil, nil])
                expect(log.string.scan(/^WARN omitting heights of Rijkswaterstaat data for station NL__stavenisse/).length).to eq(2)
            end

            it 'keeps the times, with a warning, when there is no height series' do
                expect(fetch(series(full, heights: false)).map(&:type)).to eq(%w[High Low])
                expect(log.string).to match(/^WARN no heights in Rijkswaterstaat data for station NL__stavenisse/)
            end

            it 'marks a window partial if its first or last event is more than a day from its edge, and not otherwise' do
                expect(fetch(series(full))).not_to respond_to(:partial?)
                late  = [[stamp(from + 1.day + 1.minute), 'hoogwater', 150], [stamp(to - 1.hour), 'laagwater', -120]]
                early = [[stamp(from + 1.hour), 'hoogwater', 150], [stamp(to - 1.day - 1.minute), 'laagwater', -120]]
                edges = [[stamp(from + 1.day - 1.second), 'hoogwater', 150], [stamp(to - 1.day + 1.second), 'laagwater', -120]]
                expect(described_class.new(logger).tap { |c| allow(c).to receive(:post_json).and_return(series(edges)) }
                    .tide_data_for(station, Time.utc(2027, 1, 1))).not_to respond_to(:partial?)
                expect(described_class.new(logger).tap { |c| allow(c).to receive(:post_json).and_return(series(late)) }
                    .tide_data_for(station, Time.utc(2027, 1, 1))).to be_partial
                expect(described_class.new(logger).tap { |c| allow(c).to receive(:post_json).and_return(series(early)) }
                    .tide_data_for(station, Time.utc(2027, 1, 1))).to be_partial
                expect(log.string.scan(/^INFO partial Rijkswaterstaat tide data for station NL__stavenisse/).length).to eq(2)
            end

            it 'returns nil and logs an error when there is no data in the window' do
                expect(fetch(series([['2029-01-10T09:05:00.000+01:00', 'hoogwater', 100]]))).to be_nil
                expect(log.string).to match(/^ERROR .*no Rijkswaterstaat tide data for station NL__stavenisse/)
            end

            it 'returns nil, with an error, for a station without a usable location code, and does not ask RWS' do
                station.public_id = '../etc'
                expect(fetch(series(full))).to be_nil
                expect(client).not_to have_received(:post_json)
                expect(log.string).to match(/^ERROR .*NL__stavenisse has an unusable location code/)
            end

            it 'does not ask again for that window for an hour after an unusable response, but still asks for other windows' do
                allow(client).to receive(:post_json).and_return('{"Succesvol":false}')

                Timecop.freeze(Time.utc(2027, 1, 1, 0)) { 3.times { expect(client.tide_data_for(station, Time.utc(2027, 1, 1))).to be_nil } }
                expect(client).to have_received(:post_json).once
                Timecop.freeze(Time.utc(2027, 1, 1, 0, 30)) { client.tide_data_for(station, Time.utc(2027, 2, 1)) }
                expect(client).to have_received(:post_json).twice

                Timecop.freeze(Time.utc(2027, 1, 1, 1, 0, 1)) { client.tide_data_for(station, Time.utc(2027, 1, 1)) }
                expect(client).to have_received(:post_json).exactly(3).times
            end

            it 're-raises HTTP errors, logs them with the station, and backs off for that window only' do
                allow(client).to receive(:post_json).and_raise(http_error('500'))

                Timecop.freeze(Time.utc(2027, 1, 1)) do
                    expect { client.tide_data_for(station, Time.utc(2027, 1, 1)) }.to raise_error(Mechanize::ResponseCodeError)
                    expect(client.tide_data_for(station, Time.utc(2027, 1, 1))).to be_nil
                    expect { client.tide_data_for(station, Time.utc(2027, 2, 1)) }.to raise_error(Mechanize::ResponseCodeError)
                end
                expect(client).to have_received(:post_json).twice
                expect(log.string).to match(/^ERROR .*Rijkswaterstaat tide data for station NL__stavenisse around 2027-01 failed with HTTP 500/)
            end

            [
                Net::ReadTimeout.new, Net::OpenTimeout.new, SocketError.new('getaddrinfo: nodename nor servname provided'),
                Errno::ECONNREFUSED.new, Errno::ECONNRESET.new, OpenSSL::SSL::SSLError.new('certificate verify failed'), EOFError.new,
                # What Mechanize raises for a truncated body, a chunked body cut short, and an undecodable one
                Mechanize::ResponseReadError.new(EOFError.new('Content-Length (900) does not match response body length (512)'), nil, nil, nil, nil),
                Mechanize::ChunkedTerminationError.new(EOFError.new('end of file reached'), nil, nil, nil, nil),
                Mechanize::Error.new('error handling content-encoding gzip: invalid compressed data -- format violated (Zlib::DataError)')
            ].each do |error|
                it "re-raises #{error.class} after retries, logs it with the station, and backs off for that window only" do
                    allow(client).to receive(:post_json).and_raise(error)

                    Timecop.freeze(Time.utc(2027, 1, 1)) do
                        expect { client.tide_data_for(station, Time.utc(2027, 1, 1)) }.to raise_error(error.class)
                        expect(client.tide_data_for(station, Time.utc(2027, 1, 1))).to be_nil
                        expect { client.tide_data_for(station, Time.utc(2027, 2, 1)) }.to raise_error(error.class)
                    end
                    expect(client).to have_received(:post_json).twice
                    expect(log.string).to match(/^ERROR .*Rijkswaterstaat tide data for station NL__stavenisse around 2027-01 unreachable \(#{Regexp.escape(error.class.name)}/)
                end
            end
        end

        describe '#tide_data_for, a partial window whose refresh fails' do
            # Past the published horizon: the window's last day has no data
            let(:late)  { [[stamp(from + 1.hour), 'hoogwater', 150], [stamp(to - 2.days), 'laagwater', -120]] }
            let(:fresh) { [[stamp(from + 1.hour), 'hoogwater', 151], [stamp(to - 1.hour), 'laagwater', -121]] }
            let(:around) { Time.utc(2027, 1, 1) }
            let(:t0)     { Time.utc(2027, 1, 1) }

            def partial_store = client.instance_variable_get(:@partial)

            def keep_partial
                allow(client).to receive(:post_json).and_return(series(late))
                Timecop.freeze(t0) { client.tide_data_for(station, around) }.tap { |d| expect(d).to be_partial }
            end

            {
                'HTTP 500'      => -> { Mechanize::ResponseCodeError.new(double(code: '500'), '500') },
                'a SocketError' => -> { SocketError.new('getaddrinfo: nodename nor servname provided') }
            }.each do |name, error|
                it "serves the expired partial data, with a warning, when the refresh fails with #{name}, and backs off" do
                    stale = keep_partial
                    allow(client).to receive(:post_json).and_raise(instance_exec(&error))

                    Timecop.freeze(t0 + 6.hours + 1.second) do
                        2.times do
                            data = client.tide_data_for(station, around)
                            expect(data).to be_partial
                            expect(data.map { |td| [td.time, td.type, td.prediction] }).to eq(stale.map { |td| [td.time, td.type, td.prediction] })
                        end
                    end
                    expect(client).to have_received(:post_json).twice
                    expect(log.string.scan(/^WARN serving stale partial Rijkswaterstaat tide data for station NL__stavenisse around 2027-01/).length).to eq(2)
                end
            end

            it 'serves the expired partial data when the refresh response is unusable or empty' do
                stale = keep_partial
                allow(client).to receive(:post_json).and_return('{"Succesvol":false}')
                Timecop.freeze(t0 + 6.hours + 1.second) { expect(client.tide_data_for(station, around).length).to eq(stale.length) }

                allow(client).to receive(:post_json).and_return('')
                Timecop.freeze(t0 + 7.hours + 2.seconds) { expect(client.tide_data_for(station, around)).to be_partial }
                expect(client).to have_received(:post_json).exactly(3).times
            end

            it 'replaces the stale data once a refresh succeeds' do
                keep_partial
                allow(client).to receive(:post_json).and_raise(SocketError.new('outage'))
                Timecop.freeze(t0 + 6.hours + 1.second) { expect(client.tide_data_for(station, around)).to be_partial }

                allow(client).to receive(:post_json).and_return(series(fresh))
                data = Timecop.freeze(t0 + 7.hours + 2.seconds) { client.tide_data_for(station, around) }
                expect(data).not_to respond_to(:partial?)
                expect(data.map(&:prediction)).to eq([1.51, -1.21])
                expect(partial_store).to be_empty
            end

            it 'keeps a stale partial window for at most 7 days, so the store stays bounded' do
                keep_partial
                allow(client).to receive(:post_json).and_raise(SocketError.new('outage'))
                Timecop.freeze(t0 + 7.days - 1.second) { expect(client.tide_data_for(station, around)).to be_partial }
                Timecop.freeze(t0 + 7.days + 1.hour) do
                    expect { client.tide_data_for(station, around) }.to raise_error(SocketError)
                end
                expect(partial_store).to be_empty
            end
        end

        describe 'the POST' do
            before { allow(client).to receive(:sleep) }

            it 'sends the query as JSON and treats 204 No Content as no data' do
                stub_request(:post, described_class::DATA_URL).with(headers: { 'Content-Type' => 'application/json' }).to_return(status: 204, body: '')
                Timecop.freeze(Time.utc(2027, 1, 1)) { expect(client.tide_data_for(station, Time.utc(2027, 1, 1))).to be_nil }
                expect(log.string).to match(/^WARN no Rijkswaterstaat tide data \(empty response\)/)
            end

            it 'retries a gateway error, then uses the answer' do
                stub_request(:post, described_class::DATA_URL).to_return({ status: 502 }, { status: 200, body: series(full) })
                Timecop.freeze(Time.utc(2027, 1, 1)) { expect(client.tide_data_for(station, Time.utc(2027, 1, 1)).length).to eq(2) }
                expect(a_request(:post, described_class::DATA_URL)).to have_been_made.twice
            end

            it 'retries timeouts, then raises; the window is backed off' do
                stub_request(:post, described_class::DATA_URL).to_timeout
                Timecop.freeze(Time.utc(2027, 1, 1)) do
                    expect { client.tide_data_for(station, Time.utc(2027, 1, 1)) }.to raise_error(Net::OpenTimeout)
                    expect(client.tide_data_for(station, Time.utc(2027, 1, 1))).to be_nil
                end
                expect(a_request(:post, described_class::DATA_URL)).to have_been_made.times(Clients::Base::MAX_RETRIES + 1)
            end
        end
    end
end

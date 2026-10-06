# frozen_string_literal: true

RSpec.describe Clients::BshTides do
    let(:logger) { Logger.new('/dev/null') }
    let(:client) { described_class.new(logger) }

    # Recorded from gezeiten.bsh.de on 2026-10-05, one cassette per endpoint.  The Cranz gauge file
    # is trimmed to the events these examples use (plus some just outside the window); everything
    # else in it is as recorded.
    describe '#tide_stations' do
        around { |example| VCR.use_cassette('Clients_BshTides/tides_overview') { example.run } }

        let(:stations) { client.tide_stations }

        it 'fetches the BSH gauge list' do
            expect(stations).to all(be_a(Models::Station))
            expect(stations.length).to be > 100  # BSH has ~170 gauges
        end

        it 'sets provider to bsh and region to Germany' do
            expect(stations).to all(have_attributes(provider: 'bsh', region: 'Germany'))
        end

        it 'maps Cranz to its BSH data file id and public station page' do
            cranz = stations.find { |s| s.public_id == '717P' }

            expect(cranz).to have_attributes(
                id: 'DE__717P',
                name: 'Hamburg, Cranz, Este-Sperrwerk, AP',
                lat: 53.53583,
                lon: 9.79167,
                url: 'https://gezeiten.bsh.de/hamburg_cranz'
            )
        end

        it 'pads only 4-character gauge numbers, so 5-character ones still map to their data file' do
            # DE_3015P_tides.json exists on gezeiten.bsh.de; DE__3015P_tides.json is a 404
            kalkgrund = stations.find { |s| s.public_id == '3015P' }

            expect(kalkgrund).to have_attributes(id: 'DE_3015P', name: 'Kalkgrund, Leuchtturm, Ostsee')
        end
    end

    describe '#tide_data_for' do
        around { |example| VCR.use_cassette('Clients_BshTides/cranz_tides') { example.run } }

        let(:station) do
            build_station(id: 'DE__717P', provider: 'bsh', url: 'https://gezeiten.bsh.de/hamburg_cranz',
                          lat: 53.53583, lon: 9.79167)
        end

        before { Timecop.freeze(Time.utc(2026, 10, 5, 12)) }
        after  { Timecop.return }

        let(:data) { client.tide_data_for(station, Time.utc(2026, 10, 5)) }

        it 'returns TideData in meters' do
            expect(data).to all(be_a(Models::TideData))
            expect(data).to all(have_attributes(units: 'm'))
        end

        it 'passes BSH times through unchanged, as UTC' do
            # gezeiten.bsh.de shows 29.09.2026 (MESZ): NW 01:40, HW 06:39, NW 13:54, HW 18:54 -- issue #49.
            # The feed has the same instants at a fixed +01:00 (e.g. HW 05:39:00+01:00).
            day = data.select { |td| td.time >= DateTime.new(2026, 9, 28, 22) && td.time < DateTime.new(2026, 9, 29, 22) }

            expect(day.map { |td| [td.type, td.time.strftime('%H:%M')] }).to eq([
                ['Low', '23:40'], ['High', '04:39'], ['Low', '11:54'], ['High', '16:54']
            ])
            expect(day).to all(satisfy { |td| td.time.offset.zero? })
        end

        it 'reports heights above chart datum (SKN)' do
            hw = data.find { |td| td.time == DateTime.new(2026, 9, 29, 4, 39) }

            # 713 cm above PNP - SKN at 312 cm above PNP
            expect(hw.prediction).to eq(4.01)
        end

        it 'limits data to the window around the requested date' do
            expect(data.first.time).to be >= DateTime.new(2026, 9, 1)
            expect(data.last.time).to be <= DateTime.new(2027, 9, 30, 23, 59, 59)
            # The cassette also holds events on 31 Aug 2026 and 1 Oct 2027 (UTC); those are dropped
            expect([data.first.time, data.last.time]).to eq([DateTime.new(2026, 9, 1, 0, 43), DateTime.new(2027, 9, 30, 22, 48)])
        end
    end

    describe 'parsing', :aggregate_failures do
        let(:station) { build_station(id: 'DE__999X', provider: 'bsh', url: 'https://gezeiten.bsh.de/x') }

        def year_json(year, level: 'PNP', skn: 300, height: 650)
            { year.to_s => {
                'SKN (ueber PNP)' => skn,
                'hwnw_prediction' => { 'level' => level, 'data' => [
                    { 'timestamp' => "#{year}-01-01 01:13:00+01:00", 'height' => height, 'type' => 'HW' },
                    { 'timestamp' => "#{year}-07-01 08:29:00+01:00", 'height' => height && height - 300, 'type' => 'NW' }
                ] }
            } }
        end

        def parse(years, around:, now: around)
            allow(client).to receive(:get_url).and_return({ 'years' => years }.to_json)
            Timecop.freeze(now) { client.tide_data_for(station, around) }
        end

        it 'normalizes the fixed +01:00 timestamps to UTC without shifting the instant, in summer too' do
            # BSH uses +01:00 all year (no DST), so a July event is 1h ahead of UTC, not 2h
            data = parse([year_json(2027)], around: Time.utc(2027, 1, 1))

            expect(data.map(&:time)).to eq([DateTime.new(2027, 1, 1, 0, 13), DateTime.new(2027, 7, 1, 7, 29)])
            expect(data.map(&:type)).to eq(%w[High Low])
        end

        it 'does not serve a year before 1 August of the previous year' do
            years = [year_json(2026), year_json(2027)]

            before_aug = parse(years, around: Time.utc(2026, 7, 1), now: Time.utc(2026, 7, 31, 23, 59))
            expect(before_aug.map { |td| td.time.year }.uniq).to eq([2026])

            from_aug = parse(years, around: Time.utc(2026, 7, 1), now: Time.utc(2026, 8, 1))
            expect(from_aug.map { |td| td.time.year }.uniq).to eq([2026, 2027])
        end

        it 'keeps SKN-level heights as-is' do
            data = parse([year_json(2027, level: 'SKN', skn: nil, height: 410)], around: Time.utc(2027, 1, 1))
            expect(data.first.prediction).to eq(4.1)
        end

        it 'omits heights the gauge does not publish or whose datum is unknown' do
            expect(parse([year_json(2027, height: nil)], around: Time.utc(2027, 1, 1)).map(&:prediction)).to eq([nil, nil])
            expect(parse([year_json(2027, skn: nil)], around: Time.utc(2027, 1, 1)).map(&:prediction)).to eq([nil, nil])
        end

        it 'tags each prediction with the BSH dataset year it was published in' do
            # 00:40 MEZ on 1 January 2027 is still 2026 in UTC, but belongs to the 2027 dataset
            year = { '2027' => { 'hwnw_prediction' => { 'level' => 'SKN', 'data' => [
                { 'timestamp' => '2027-01-01 00:40:00+01:00', 'height' => 500, 'type' => 'HW' }
            ] } } }
            data = parse([year], around: Time.utc(2026, 12, 15), now: Time.utc(2026, 12, 15))

            expect(data.map { |td| [td.time.year, td.dataset_year] }).to eq([[2026, 2027]])
        end

        it 'keeps the per-gauge BSH notices for that dataset year' do
            notices = ['Keine Gezeitenhöhen verfügbar', 'Diese Vorausberechnungen wurden im festen Zeitunterschied zu Bremen, Oslebshausen, Weser erstellt.']
            with_notice = year_json(2027).transform_values { |yd| yd.merge('notice' => notices) }
            without     = year_json(2027).transform_values { |yd| yd.merge('notice' => []) }

            expect(parse([with_notice], around: Time.utc(2027, 1, 1)).map(&:notes)).to all(eq(notices))
            expect(parse([without], around: Time.utc(2027, 1, 1)).map(&:notes)).to all(be_nil)
        end

        it 'returns nil on 404' do
            allow(client).to receive(:get_url).and_raise(Mechanize::ResponseCodeError.new(double(code: '404'), '404'))
            expect(client.tide_data_for(station, Time.utc(2027, 1, 1))).to be_nil
        end
    end

    describe 'failure handling', :aggregate_failures do
        let(:log)    { StringIO.new }
        let(:logger) { Logger.new(log).tap { |l| l.formatter = proc { |sev, _, _, msg| "#{sev} #{msg}\n" } } }
        let(:station) { build_station(id: 'DE__999X', provider: 'bsh', url: 'https://gezeiten.bsh.de/x') }

        def gauge(bshnr: '999X', name: 'Testort')
            { 'bshnr' => bshnr, 'station_name' => name, 'seo_id' => name.downcase, 'latitude' => 54.0, 'longitude' => 8.0 }
        end

        def year_json(year, data:, level: 'PNP', skn: 300, has_height: true)
            { year.to_s => { 'SKN (ueber PNP)' => skn, 'has_height' => has_height,
                             'hwnw_prediction' => { 'level' => level, 'data' => data } } }
        end

        def event(timestamp, type: 'HW', height: 650)
            { 'timestamp' => timestamp, 'height' => height, 'type' => type }
        end

        def fetch(body, around: Time.utc(2027, 1, 1), now: around)
            allow(client).to receive(:get_url).and_return(body.is_a?(String) ? body : { 'years' => body }.to_json)
            Timecop.freeze(now) { client.tide_data_for(station, around) }
        end

        def http_error(code)
            Mechanize::ResponseCodeError.new(double(code: code), code)
        end

        describe '#tide_stations' do
            it 'returns nil and logs an error when BSH returns no body' do
                allow(client).to receive(:get_url).and_return(nil)

                expect(client.tide_stations).to be_nil
                expect(log.string).to match(/^ERROR .*BSH tide station list/)
            end

            ['<html>Wartungsarbeiten</html>', { 'stations' => [] }.to_json, [].to_json].each do |body|
                it "returns nil and logs an error on a malformed overview (#{body[0, 12]}) instead of an empty list" do
                    allow(client).to receive(:get_url).and_return(body)

                    expect(client.tide_stations).to be_nil
                    expect(log.string).to match(/^ERROR .*BSH tide station list.*unparseable/)
                end
            end

            it 'returns nil and logs an error when the overview lists no gauges' do
                allow(client).to receive(:get_url).and_return({ 'gauges' => [] }.to_json)

                expect(client.tide_stations).to be_nil
                expect(log.string).to match(/^ERROR .*no gauges/)
            end

            it 'skips and logs a gauge entry without a bshnr instead of failing the whole list' do
                allow(client).to receive(:get_url).and_return({ 'gauges' => [gauge, gauge(bshnr: nil, name: 'Kaputt')] }.to_json)

                expect(client.tide_stations.map(&:id)).to eq(['DE__999X'])
                expect(log.string).to match(/^WARN .*skipping BSH gauge without bshnr.*Kaputt/)
            end
        end

        describe '#tide_data_for' do
            it 're-raises HTTP errors other than 404' do
                allow(client).to receive(:get_url).and_raise(http_error('500'))

                expect { client.tide_data_for(station, Time.utc(2027, 1, 1)) }.to raise_error(Mechanize::ResponseCodeError)
            end

            it 'returns nil and logs an error on malformed gauge JSON instead of an empty list' do
                expect(fetch('<html>Fehler</html>')).to be_nil
                expect(log.string).to match(/^ERROR .*DE__999X.*unparseable/)
            end

            it 'returns nil and logs an error when the gauge has no data in the window' do
                expect(fetch([year_json(2027, data: [])])).to be_nil
                expect(log.string).to match(/^ERROR .*no BSH tide data for station DE__999X/)
            end

            it 'does not fetch a gauge again for a while after a 404' do
                allow(client).to receive(:get_url).and_raise(http_error('404'))

                Timecop.freeze(Time.utc(2027, 1, 1, 0)) { 3.times { expect(client.tide_data_for(station, Time.utc(2027, 1, 1))).to be_nil } }
                expect(client).to have_received(:get_url).once

                Timecop.freeze(Time.utc(2027, 1, 1, 0) + described_class::UNAVAILABLE_RETRY + 1) { client.tide_data_for(station, Time.utc(2027, 1, 1)) }
                expect(client).to have_received(:get_url).twice
            end

            it 'remembers an empty window per month, so it does not hide other months of the gauge' do
                body = { 'years' => [year_json(2027, data: [event('2027-03-01 01:13:00+01:00')])] }.to_json
                allow(client).to receive(:get_url).and_return(body)

                Timecop.freeze(Time.utc(2027, 3, 1)) do
                    expect(client.tide_data_for(station, Time.utc(2020, 1, 1))).to be_nil
                    expect(client.tide_data_for(station, Time.utc(2020, 1, 1))).to be_nil
                    expect(client.tide_data_for(station, Time.utc(2027, 3, 1)).length).to eq(1)
                end
                expect(client).to have_received(:get_url).twice
            end

            it 'maps HW to High and NW to Low, and skips and logs any other type' do
                data = fetch([year_json(2027, data: [
                    event('2027-01-01 01:13:00+01:00', type: 'HW'),
                    event('2027-01-01 07:30:00+01:00', type: 'NW'),
                    event('2027-01-01 10:00:00+01:00', type: 'XX'),
                    event('2027-01-01 11:00:00+01:00', type: nil)
                ])])

                expect(data.map(&:type)).to eq(%w[High Low])
                expect(log.string).to match(/^WARN .*skipping 2 BSH events.*DE__999X.*2027/)
            end

            it 'skips and logs malformed timestamps and timestamps without a UTC offset' do
                data = fetch([year_json(2027, data: [
                    event('2027-01-01 01:13:00+01:00'),
                    event('2027-01-01 07:30:00', type: 'NW'),
                    event('kaputt'),
                    event(nil)
                ])])

                expect(data.map(&:time)).to eq([DateTime.new(2027, 1, 1, 0, 13)])
                expect(log.string).to match(/^WARN .*skipping 3 BSH events.*DE__999X.*2027/)
            end

            it 'logs when heights are published but the datum is unknown' do
                data = fetch([year_json(2027, level: 'NHN', data: [event('2027-01-01 01:13:00+01:00')])])

                expect(data.map(&:prediction)).to eq([nil])
                expect(log.string).to match(/^WARN .*2027.*DE__999X.*unknown datum.*NHN/)
            end

            it 'omits heights without logging when the gauge says it has none' do
                data = fetch([year_json(2027, level: '', has_height: false, data: [event('2027-01-01 01:13:00+01:00', height: 650)])])

                expect(data.map(&:prediction)).to eq([nil])
                expect(log.string).not_to match(/^WARN/)
            end

            it 'logs the expected skip of a not yet publishable year at debug, not warn' do
                years = [year_json(2027, data: [event('2027-01-01 01:13:00+01:00')]),
                         year_json(2028, data: [event('2028-01-01 01:13:00+01:00')])]

                data = fetch(years, around: Time.utc(2027, 1, 1))

                expect(data.map { |td| td.time.year }).to eq([2027])
                expect(log.string).not_to match(/^WARN/)
                expect(log.string).to match(/^DEBUG .*skipping 2028 BSH data/)
            end

            it 'returns nothing (uncached) for a future month whose window reaches a year that becomes publishable by then' do
                # Asked in July 2026 for August 2026: 2027 becomes publishable on 1 August 2026, so a
                # result without 2027 would be cached and served truncated for all of August
                years = [year_json(2026, data: [event('2026-08-02 01:13:00+01:00')]),
                         year_json(2027, data: [event('2027-01-01 01:13:00+01:00')])]

                expect(fetch(years, around: Time.utc(2026, 8, 1), now: Time.utc(2026, 7, 15))).to be_nil
                expect(fetch(years, around: Time.utc(2026, 8, 1), now: Time.utc(2026, 8, 1)).map { |td| td.time.year }).to eq([2026, 2027])
            end

            it 'still serves a future month when the skipped year is not publishable until after that month' do
                years = [year_json(2026, data: [event('2026-08-02 01:13:00+01:00')]),
                         year_json(2027, data: [event('2027-01-01 01:13:00+01:00')])]

                data = fetch(years, around: Time.utc(2026, 7, 1), now: Time.utc(2026, 6, 15))
                expect(data.map { |td| td.time.year }).to eq([2026])
            end
        end
    end

    describe '.attribution' do
        it 'uses the BSH source-credit format' do
            expect(described_class.attribution([2026])).to eq(
                'Datenquelle: Gezeitenvorausberechnungen ©, Bundesamt für Seeschifffahrt und Hydrographie, Hamburg, 2026'
            )
        end

        it 'spans multiple years' do
            expect(described_class.attribution([2027, 2026, 2027])).to end_with('Hamburg, 2026-2027')
        end
    end

    describe '.dataset_year' do
        it 'is the MEZ calendar year BSH files the time under, not the UTC year' do
            expect(described_class.dataset_year(DateTime.new(2026, 12, 31, 23, 40))).to eq(2027)
            expect(described_class.dataset_year(DateTime.new(2026, 12, 31, 22, 59))).to eq(2026)
        end
    end

    describe '.served_years' do
        it 'credits the years in the data window that may be published' do
            expect(described_class.served_years(now: Time.utc(2026, 7, 31, 12))).to eq([2026])
            expect(described_class.served_years(now: Time.utc(2026, 8, 1, 12))).to eq([2026, 2027])
            expect(described_class.served_years(now: Time.utc(2026, 10, 5, 12))).to eq([2026, 2027])
            # January still serves December of the previous year
            expect(described_class.served_years(now: Time.utc(2027, 1, 15, 12))).to eq([2026, 2027])
            expect(described_class.served_years(now: Time.utc(2027, 3, 15, 12))).to eq([2027])
        end
    end

    describe 'descriptions' do
        let(:credit) { 'Datenquelle: Gezeitenvorausberechnungen ©, Bundesamt für Seeschifffahrt und Hydrographie, Hamburg' }

        it 'credits an event with its dataset year and appends the gauge notices' do
            tide = build_tide_data(time: DateTime.new(2026, 12, 31, 23, 49), dataset_year: 2027,
                                   notes: ['Die Gezeitenausprägung ist sehr gering'])

            expect(described_class.event_description(tide)).to eq(
                "#{credit}, 2027. NOT FOR NAVIGATION. BSH notes (Hinweise): Die Gezeitenausprägung ist sehr gering."
            )
        end

        it 'adds no notes text when the gauge has none' do
            tide = build_tide_data(dataset_year: 2026, notes: nil)
            expect(described_class.event_description(tide)).to eq("#{credit}, 2026. NOT FOR NAVIGATION.")
        end

        it 'credits a feed with its dataset years, the disclaimer and the distinct notices' do
            tides = [
                build_tide_data(dataset_year: 2026, notes: ['Keine Gezeitenhöhen verfügbar']),
                build_tide_data(dataset_year: 2027, notes: ['Keine Gezeitenhöhen verfügbar'])
            ]

            expect(described_class.feed_description(tides)).to eq(
                "#{credit}, 2026-2027. #{described_class::DISCLAIMER} BSH notes (Hinweise): Keine Gezeitenhöhen verfügbar."
            )
        end
    end

    describe 'TimeWindow module' do
        it 'has window_size of 13 months' do
            expect(described_class.window_size).to eq(13.months)
        end
    end
end

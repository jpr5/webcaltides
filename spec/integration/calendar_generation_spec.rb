# frozen_string_literal: true

RSpec.describe WebCalTides do
    describe '.tide_calendar_for' do
        let(:station) do
            build_station(
                name: 'Boston Harbor',
                id: 'NOAA123',
                provider: 'noaa',
                lat: 42.3601,
                lon: -71.0589,
                location: 'Boston, MA'
            )
        end

        let(:tide_data) do
            [
                build_tide_data(type: 'High', prediction: 10.5, time: DateTime.new(2025, 6, 15, 6, 30)),
                build_tide_data(type: 'Low', prediction: 0.5, time: DateTime.new(2025, 6, 15, 12, 45)),
                build_tide_data(type: 'High', prediction: 11.0, time: DateTime.new(2025, 6, 15, 18, 30))
            ]
        end

        before do
            allow(described_class).to receive(:tide_station_for).and_return(station)
            allow(described_class).to receive(:tide_data_for).and_return(tide_data)
        end

        it 'returns an Icalendar::Calendar object' do
            calendar = described_class.tide_calendar_for('NOAA123')
            expect(calendar).to be_a(Icalendar::Calendar)
        end

        it 'sets calendar name to station name' do
            calendar = described_class.tide_calendar_for('NOAA123')
            expect(calendar.x_wr_calname.first.value).to eq('Boston Harbor')
        end

        it 'title-cases an all-caps station name' do
            station.name = 'BOSTON HARBOR'
            calendar = described_class.tide_calendar_for('NOAA123')
            expect(calendar.x_wr_calname.first.value).to eq('Boston Harbor')
        end

        it 'creates events for each tide' do
            calendar = described_class.tide_calendar_for('NOAA123')
            expect(calendar.events.length).to eq(3)
        end

        it 'formats high tide events correctly' do
            calendar = described_class.tide_calendar_for('NOAA123')
            high_events = calendar.events.select { |e| e.summary.to_s.include?('High') }

            expect(high_events.length).to eq(2)
            expect(high_events.first.summary.to_s).to match(/High Tide \d+\.?\d* ft/)
        end

        it 'formats low tide events correctly' do
            calendar = described_class.tide_calendar_for('NOAA123')
            low_events = calendar.events.select { |e| e.summary.to_s.include?('Low') }

            expect(low_events.length).to eq(1)
            expect(low_events.first.summary.to_s).to match(/Low Tide \d+\.?\d* ft/)
        end

        it 'sets event location' do
            calendar = described_class.tide_calendar_for('NOAA123')
            expect(calendar.events.first.location.to_s).to eq('Boston, MA')
        end

        context 'with metric units' do
            it 'converts heights to meters' do
                calendar = described_class.tide_calendar_for('NOAA123', units: 'metric')
                high_events = calendar.events.select { |e| e.summary.to_s.include?('High') }

                expect(high_events.first.summary.to_s).to include('m')
            end
        end

        context 'when station not found' do
            before do
                allow(described_class).to receive(:tide_station_for).and_return(nil)
            end

            it 'returns nil' do
                calendar = described_class.tide_calendar_for('INVALID')
                expect(calendar).to be_nil
            end
        end

        context 'with xtide/ticon provider' do
            let(:xtide_station) do
                build_station(
                    name: 'XTide Station',
                    id: 'X123',
                    provider: 'xtide',
                    lat: 42.0,
                    lon: -71.0
                )
            end

            before do
                allow(described_class).to receive(:tide_station_for).and_return(xtide_station)
            end

            it 'adds disclaimer to description' do
                calendar = described_class.tide_calendar_for('X123')
                expect(calendar.description.to_s).to include('NOT FOR NAVIGATION')
            end

            it 'keeps the harmonic disclaimer and carries no BSH or Kartverket credit' do
                calendar = described_class.tide_calendar_for('X123')
                ical     = calendar.to_ical.gsub(/\r\n[ \t]/, '')

                expect(Array(calendar.description)).to match([start_with('NOT FOR NAVIGATION. This program is distributed')])
                expect(ical).not_to include('Bundesamt')
                expect(ical).not_to include('Kartverket')
                expect(ical).not_to include('X-WR-CALDESC')
                expect(calendar.events.map(&:description)).to all(be_nil)
            end
        end

        it 'carries no BSH or Kartverket credit on an official NOAA feed' do
            calendar = described_class.tide_calendar_for('NOAA123')
            ical     = calendar.to_ical.gsub(/\r\n[ \t]/, '')

            expect(Array(calendar.description)).to be_empty
            expect(ical).not_to include('Bundesamt')
            expect(ical).not_to include('Kartverket')
            expect(ical).not_to include('X-WR-CALDESC')
            expect(calendar.events.map(&:description)).to all(be_nil)
        end
    end

    describe '.tide_calendar_for with bsh provider' do
        let(:station) do
            build_station(name: 'Hamburg, Cranz, Este-Sperrwerk, AP', id: 'DE__717P', provider: 'bsh',
                          lat: 53.53583, lon: 9.79167, location: 'Hamburg, Cranz, Este-Sperrwerk, AP, Germany')
        end

        let(:tide_data) do
            [
                build_tide_data(type: 'High', units: 'm', prediction: 4.01, time: DateTime.new(2026, 9, 29, 4, 39), dataset_year: 2026),
                # 00:49 MEZ on 1 January 2027 -- UTC year 2026, BSH dataset 2027
                build_tide_data(type: 'Low', units: 'm', prediction: nil, time: DateTime.new(2026, 12, 31, 23, 49), dataset_year: 2027,
                                notes: ['Keine Gezeitenhöhen verfügbar'])
            ]
        end

        before do
            allow(described_class).to receive(:tide_station_for).and_return(station)
            allow(described_class).to receive(:tide_data_for).and_return(tide_data)
        end

        let(:ical) { described_class.tide_calendar_for('DE__717P', units: 'metric').to_ical.gsub(/\r\n[ \t]/, '') }

        it 'credits BSH in the required format and disclaims navigation use on the feed' do
            credit = 'Datenquelle: Gezeitenvorausberechnungen ©\\, Bundesamt für Seeschifffahrt und Hydrographie\\, Hamburg\\, 2026-2027'

            expect(ical).to match(/^DESCRIPTION:#{Regexp.escape(credit)}\. NOT FOR NAVIGATION/)
            expect(ical).to match(/^X-WR-CALDESC:#{Regexp.escape(credit)}\. NOT FOR NAVIGATION/)
        end

        it 'carries the BSH gauge notices into the feed description' do
            expect(ical).to match(/^X-WR-CALDESC:.*Gewähr\)\. BSH notes \(Hinweise\): Keine Gezeitenhöhen verfügbar\.\r$/)
        end

        it 'credits BSH on every event with that event\'s dataset year and notices' do
            calendar = described_class.tide_calendar_for('DE__717P', units: 'metric')

            expect(calendar.events.map { |e| e.description.to_s }).to eq([
                'Datenquelle: Gezeitenvorausberechnungen ©, Bundesamt für Seeschifffahrt und Hydrographie, Hamburg, 2026. NOT FOR NAVIGATION.',
                'Datenquelle: Gezeitenvorausberechnungen ©, Bundesamt für Seeschifffahrt und Hydrographie, Hamburg, 2027. NOT FOR NAVIGATION. BSH notes (Hinweise): Keine Gezeitenhöhen verfügbar.'
            ])
        end

        it 'keeps the BSH event time unchanged' do
            expect(ical).to include('DTSTART;TZID=GMT:20260929T043900')
        end

        it 'omits the height when BSH publishes none' do
            calendar = described_class.tide_calendar_for('DE__717P', units: 'metric')
            expect(calendar.events.map { |e| e.summary.to_s }).to eq(['High Tide 4.01 m', 'Low Tide'])
        end
    end

    describe '.tide_calendar_for with kartverket provider' do
        let(:station) do
            build_station(name: 'Bergen', id: 'NO__BGO', public_id: 'BGO', provider: 'kartverket',
                          lat: 60.398046, lon: 5.320487, location: 'Bergen, Norway',
                          url: 'https://kartverket.no/en/at-sea/se-havniva/result?latitude=60.398046&longitude=5.320487')
        end

        let(:tide_data) do
            [
                build_tide_data(type: 'High', units: 'm', prediction: 1.617, time: DateTime.new(2026, 10, 1, 0, 22), url: station.url),
                build_tide_data(type: 'Low',  units: 'm', prediction: 0.432, time: DateTime.new(2026, 10, 1, 6, 14), url: station.url)
            ]
        end

        before do
            allow(described_class).to receive(:tide_station_for).and_return(station)
            allow(described_class).to receive(:tide_data_for).and_return(tide_data)
        end

        let(:calendar) { described_class.tide_calendar_for('NO__BGO', units: 'metric') }
        let(:ical)     { calendar.to_ical.gsub(/\r\n[ \t]/, '') }

        it 'credits © Kartverket with a link and the CC BY 4.0 licence, and disclaims navigation use on the feed' do
            credit  = '© Kartverket (Norwegian Mapping Authority\\, Hydrographic Service)\\, https://www.kartverket.no/\\, licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)'
            changes = 'Heights converted from cm above chart datum to metres or feet\\, and high and low waters presented as calendar events\\; times unchanged\\, in UTC'

            expect(ical).to match(/^DESCRIPTION:#{Regexp.escape(credit)}\. #{Regexp.escape(changes)}\. NOT FOR NAVIGATION/)
            expect(ical).to match(/^X-WR-CALDESC:#{Regexp.escape(credit)}\. #{Regexp.escape(changes)}\. NOT FOR NAVIGATION/)
        end

        it 'credits © Kartverket on every event' do
            expect(calendar.events.map { |e| e.description.to_s }).to all(eq(Clients::KartverketTides.event_description))
            expect(calendar.events.map { |e| e.description.to_s }).to all(start_with('© Kartverket'))
        end

        it 'keeps the Kartverket event times unchanged and shows heights in metres, linking each to the station page' do
            expect(ical).to include('DTSTART;TZID=GMT:20261001T002200', 'DTSTART;TZID=GMT:20261001T061400')
            expect(calendar.events.map { |e| e.summary.to_s }).to eq(['High Tide 1.617 m', 'Low Tide 0.432 m'])
            expect(calendar.events.map { |e| e.url.to_s }).to all(eq(station.url))
        end

        it 'carries no BSH credit' do
            expect(ical).not_to include('Bundesamt')
        end

        it 'names the calendar with the station name exactly as Kartverket publishes it' do
            allow(described_class).to receive(:tide_station_for).and_return(station.tap { |s| s.name = 'Ny-Ålesund' })

            expect(calendar.x_wr_calname.first.value).to eq('Ny-Ålesund')
            expect(ical).to include("X-WR-CALNAME:Ny-Ålesund\r\n")
        end

        it 'converts the heights to feet in the default (imperial) units, times unchanged' do
            calendar = described_class.tide_calendar_for('NO__BGO')

            expect(calendar.events.map { |e| e.summary.to_s }).to eq(['High Tide 5.305 ft', 'Low Tide 1.417 ft'])
            expect(calendar.to_ical).to include('DTSTART;TZID=GMT:20261001T002200', 'DTSTART;TZID=GMT:20261001T061400')
        end
    end

    describe '.tide_calendar_for with linz provider' do
        let(:station) do
            build_station(name: 'Wellington', id: 'NZ__wellington', public_id: '071', provider: 'linz',
                          lat: -41.2833, lon: 174.7833, location: 'Wellington, New Zealand', region: 'New Zealand',
                          url: Clients::LinzTides::HOME_URL)
        end

        # LINZ CSV, Wellington, 4 Apr 2027: 02:21 1.6 (NZST) and 08:30 0.8
        let(:tide_data) do
            [
                build_tide_data(type: 'High', units: 'm', prediction: 1.6, time: DateTime.new(2027, 4, 3, 14, 21), url: station.url),
                build_tide_data(type: 'Low',  units: 'm', prediction: 0.8, time: DateTime.new(2027, 4, 3, 20, 30), url: station.url)
            ]
        end

        before do
            allow(described_class).to receive(:tide_station_for).and_return(station)
            allow(described_class).to receive(:tide_data_for).and_return(tide_data)
        end

        let(:calendar) { described_class.tide_calendar_for('NZ__wellington', units: 'metric') }
        let(:ical)     { calendar.to_ical.gsub(/\r\n[ \t]/, '') }

        it 'gives the LINZ attribution with the CC BY 4.0 link, what we changed, and the disclaimer on the feed' do
            caldesc = Icalendar::Values::Text.new(Clients::LinzTides.feed_description).value_ical

            expect(ical).to include("DESCRIPTION:#{caldesc}", "X-WR-CALDESC:#{caldesc}")
            expect(ical).to match(/^X-WR-CALDESC:This work is based on Toitū Te Whenua Land Information New Zealand data .*creativecommons\.org\/licenses\/by\/4\.0.*Changes: times converted .* to UTC.*NOT FOR NAVIGATION/)
            expect(ical).not_to include('This program is distributed')
        end

        it 'credits LINZ on every event' do
            expect(calendar.events.map { |e| e.description.to_s }).to all(eq(Clients::LinzTides.event_description))
        end

        it 'shows the UTC times and heights in metres, linking each event to the LINZ predictions page' do
            expect(ical).to include('DTSTART;TZID=GMT:20270403T142100', 'DTSTART;TZID=GMT:20270403T203000')
            expect(calendar.events.map { |e| e.summary.to_s }).to eq(['High Tide 1.6 m', 'Low Tide 0.8 m'])
            expect(calendar.events.map { |e| e.url.to_s }).to all(eq(Clients::LinzTides::HOME_URL))
        end

        it 'converts the heights to feet in the default (imperial) units, times unchanged' do
            calendar = described_class.tide_calendar_for('NZ__wellington')

            expect(calendar.events.map { |e| e.summary.to_s }).to eq(['High Tide 5.249 ft', 'Low Tide 2.625 ft'])
            expect(calendar.to_ical).to include('DTSTART;TZID=GMT:20270403T142100', 'DTSTART;TZID=GMT:20270403T203000')
            expect(calendar.events.map { |e| e.description.to_s }).to all(eq(Clients::LinzTides.event_description))
        end

        it 'carries no BSH or Kartverket credit' do
            expect(ical).not_to include('Bundesamt')
            expect(ical).not_to include('Kartverket')
        end

        it 'names the calendar with the port name exactly as LINZ publishes it' do
            ['Waitangi - Chatham Island', 'Port Ōhope Wharf', 'Man o‘War Bay'].each do |name|
                allow(described_class).to receive(:tide_station_for).and_return(station.dup.tap { |s| s.name = name })

                calendar = described_class.tide_calendar_for('NZ__wellington', units: 'metric')
                expect(calendar.x_wr_calname.first.value).to eq(name)
                expect(calendar.to_ical).to include("X-WR-CALNAME:#{name}\r\n")
            end
        end
    end

    describe '.tide_calendar_for with imi (Marine Institute) provider' do
        let(:station) do
            build_station(name: 'Dublin Port', id: 'IE__Dublin_Port', public_id: 'Dublin_Port', provider: 'imi',
                          lat: 53.34574, lon: -6.22166, location: 'Dublin Port, Co. Dublin, Ireland', region: 'Ireland',
                          url: Clients::MarineInstituteTides::HOME_URL)
        end

        # MI ERDDAP, Dublin Port: 2026-10-07T09:05:00Z HIGH 1.329 and 14:40Z LOW -1.184 m OD Malin;
        # +2.458 m to chart datum
        let(:tide_data) do
            [
                build_tide_data(type: 'High', units: 'm', prediction: 3.79, time: DateTime.new(2026, 10, 7, 9, 5), url: station.url),
                build_tide_data(type: 'Low',  units: 'm', prediction: 1.27, time: DateTime.new(2026, 10, 7, 14, 40), url: station.url)
            ]
        end

        before do
            allow(described_class).to receive(:tide_station_for).and_return(station)
            allow(described_class).to receive(:tide_data_for).and_return(tide_data)
        end

        let(:calendar) { described_class.tide_calendar_for('IE__Dublin_Port', units: 'metric') }
        let(:ical)     { calendar.to_ical.gsub(/\r\n[ \t]/, '') }

        it 'gives the MI credit with the CC BY 4.0 link, what we changed, and the disclaimer on the feed' do
            caldesc = Icalendar::Values::Text.new(Clients::MarineInstituteTides.feed_description(tide_data)).value_ical

            expect(ical).to include("DESCRIPTION:#{caldesc}", "X-WR-CALDESC:#{caldesc}")
            expect(ical).to match(/^X-WR-CALDESC:Data supplied by Marine Institute .*creativecommons\.org\/licenses\/by\/4\.0.*Changes: heights converted from metres above OD Malin to metres above chart datum.*times unchanged.*NOT FOR NAVIGATION/)
            expect(ical).not_to include('This program is distributed')
        end

        it 'credits MI on every event' do
            expect(calendar.events.map { |e| e.description.to_s }).to all(eq(Clients::MarineInstituteTides.event_description))
        end

        # Through the real client: ERDDAP's response for Dublin Port (a station with a chart datum
        # offset), with the height unit row as recorded (metres) or changed to feet
        context 'with the feed built from the ERDDAP response' do
            let(:erddap_csv) do
                cassette = YAML.load_file(File.join(__dir__, '../fixtures/cassettes/Clients_MarineInstituteTides/dublin_port.yml'))
                cassette['http_interactions'].first['response']['body']['string']
            end

            def feed_for(csv)
                stub_request(:get, %r{\Ahttps://erddap\.marine\.ie/erddap/tabledap/IMI_TidePrediction_HighLow\.csv})
                    .to_return(status: 200, body: csv, headers: { 'Content-Type' => 'text/csv;charset=ISO-8859-1' })
                ical = nil
                with_test_cache_dir { ical = described_class.tide_calendar_for('IE__Dublin_Port', units: 'metric').to_ical }
                ical.gsub(/\r\n[ \t]/, '')
            end

            before do
                Timecop.freeze(Time.utc(2026, 10, 6, 12))
                allow(described_class).to receive(:tide_data_for).and_call_original
            end
            after { Timecop.return }

            it 'says the heights were converted when they are in metres' do
                ical = feed_for(erddap_csv)

                expect(ical).to include('SUMMARY:High Tide 3.79 m')
                expect(ical).to match(/^X-WR-CALDESC:.*Changes: heights converted from metres above OD Malin to metres above chart datum/)
            end

            it 'does not blame a missing chart datum offset when the heights are left out for their unit' do
                ical = feed_for(erddap_csv.sub("UTC,,,metres\n", "UTC,,,feet\n"))

                expect(ical).to include('SUMMARY:High Tide')
                expect(ical).not_to match(/SUMMARY:High Tide \d/)
                expect(ical).to match(/^X-WR-CALDESC:.*Changes: heights left out\b/)
                expect(ical).not_to include('no chart datum offset known')
                expect(ical).not_to include('converted from metres above OD Malin')
            end
        end

        it 'shows the times as MI publishes them in UTC (not shifted by Irish summer time) and heights in metres' do
            expect(ical).to include('DTSTART;TZID=GMT:20261007T090500', 'DTSTART;TZID=GMT:20261007T144000')
            expect(ical).not_to include('DTSTART;TZID=GMT:20261007T080500', 'DTSTART;TZID=GMT:20261007T100500')
            expect(calendar.events.map { |e| e.summary.to_s }).to eq(['High Tide 3.79 m', 'Low Tide 1.27 m'])
            expect(calendar.events.map { |e| e.url.to_s }).to all(eq(Clients::MarineInstituteTides::HOME_URL))
        end

        it 'converts the heights to feet in the default (imperial) units, times unchanged' do
            calendar = described_class.tide_calendar_for('IE__Dublin_Port')

            expect(calendar.events.map { |e| e.summary.to_s }).to eq(['High Tide 12.434 ft', 'Low Tide 4.167 ft'])
            expect(calendar.to_ical).to include('DTSTART;TZID=GMT:20261007T090500', 'DTSTART;TZID=GMT:20261007T144000')
            expect(calendar.events.map { |e| e.description.to_s }).to all(eq(Clients::MarineInstituteTides.event_description))
        end

        it 'carries no BSH, Kartverket or LINZ credit' do
            expect(ical).not_to include('Bundesamt')
            expect(ical).not_to include('Kartverket')
            expect(ical).not_to include('Toitū')
        end

        it 'names the calendar with the station name as MI publishes it' do
            ['Dublin Port', 'Ringaskiddy NMCI', 'Dún Laoghaire'].each do |name|
                allow(described_class).to receive(:tide_station_for).and_return(station.dup.tap { |s| s.name = name })

                calendar = described_class.tide_calendar_for('IE__Dublin_Port', units: 'metric')
                expect(calendar.x_wr_calname.first.value).to eq(name)
            end
        end
    end

    describe '.current_calendar_for' do
        let(:station) do
            build_station(
                name: 'Cape Cod Canal',
                id: 'CURR1',
                bid: 'CURR1_10',
                provider: 'noaa',
                lat: 41.7765,
                lon: -70.4792,
                url: 'https://tidesandcurrents.noaa.gov/currents'
            )
        end

        let(:current_data) do
            [
                build_current_data(type: 'flood', velocity_major: 2.5, time: DateTime.new(2025, 6, 15, 8, 30)),
                build_current_data(type: 'slack', velocity_major: 0.0, time: DateTime.new(2025, 6, 15, 12, 0)),
                build_current_data(type: 'ebb', velocity_major: -2.8, time: DateTime.new(2025, 6, 15, 15, 30))
            ]
        end

        before do
            allow(described_class).to receive(:current_station_for).and_return(station)
            allow(described_class).to receive(:current_data_for).and_return(current_data)
        end

        it 'returns an Icalendar::Calendar object' do
            calendar = described_class.current_calendar_for('CURR1_10')
            expect(calendar).to be_a(Icalendar::Calendar)
        end

        it 'creates events for each current event' do
            calendar = described_class.current_calendar_for('CURR1_10')
            expect(calendar.events.length).to eq(3)
        end

        it 'formats flood events with velocity' do
            calendar = described_class.current_calendar_for('CURR1_10')
            flood_events = calendar.events.select { |e| e.summary.to_s.include?('Flood') }

            expect(flood_events.length).to eq(1)
            expect(flood_events.first.summary.to_s).to include('kts')
        end

        it 'formats slack events' do
            calendar = described_class.current_calendar_for('CURR1_10')
            slack_events = calendar.events.select { |e| e.summary.to_s.include?('Slack') }

            expect(slack_events.length).to eq(1)
        end

        it 'formats ebb events with direction' do
            calendar = described_class.current_calendar_for('CURR1_10')
            ebb_events = calendar.events.select { |e| e.summary.to_s.include?('Ebb') }

            expect(ebb_events.length).to eq(1)
            expect(ebb_events.first.summary.to_s).to include('kts')
        end

        context 'when station not found' do
            before do
                allow(described_class).to receive(:current_station_for).and_return(nil)
            end

            it 'returns nil' do
                calendar = described_class.current_calendar_for('INVALID')
                expect(calendar).to be_nil
            end
        end

        context 'when current data is nil' do
            before do
                allow(described_class).to receive(:current_data_for).and_return(nil)
            end

            it 'returns nil' do
                calendar = described_class.current_calendar_for('CURR1_10')
                expect(calendar).to be_nil
            end
        end
    end

    describe '.solar_calendar_for' do
        let(:base_calendar) do
            cal = Icalendar::Calendar.new
            station = build_station(lat: 42.3601, lon: -71.0589)
            cal.define_singleton_method(:station) { station }
            cal.define_singleton_method(:location) { 'Boston, MA' }
            cal
        end

        before do
            # Stub timezone lookup to avoid GeoNames API calls
            allow(described_class).to receive(:timezone_for).and_return('America/New_York')
        end

        it 'adds sunrise and sunset events' do
            freeze_time(Time.utc(2025, 6, 15))

            described_class.solar_calendar_for(base_calendar, around: Time.current.utc)

            sunrise_events = base_calendar.events.select { |e| e.summary.to_s == 'Sunrise' }
            sunset_events = base_calendar.events.select { |e| e.summary.to_s == 'Sunset' }

            expect(sunrise_events).not_to be_empty
            expect(sunset_events).not_to be_empty
        end

        it 'sets location on events' do
            freeze_time(Time.utc(2025, 6, 15))

            described_class.solar_calendar_for(base_calendar, around: Time.current.utc)

            expect(base_calendar.events.first.location.to_s).to eq('Boston, MA')
        end

        context 'above the Arctic Circle' do
            let(:base_calendar) do
                cal = Icalendar::Calendar.new
                station = build_station(lat: 69.64611, lon: 18.95479) # Tromso
                cal.define_singleton_method(:station) { station }
                cal.define_singleton_method(:location) { 'Tromso, NOR' }
                cal
            end

            before do
                allow(described_class).to receive(:timezone_for).and_return('Europe/Oslo')
            end

            def solar_days(calendar, summary)
                calendar.events.select { |e| e.summary.to_s == summary }.map { |e| e.dtstart.to_date }
            end

            it 'skips sunrise and sunset on days without them (polar night, midnight sun)' do
                freeze_time(Time.utc(2025, 6, 15))

                expect {
                    described_class.solar_calendar_for(base_calendar, around: Time.current.utc)
                }.not_to raise_error

                sunrises = solar_days(base_calendar, 'Sunrise')
                sunsets  = solar_days(base_calendar, 'Sunset')

                # Polar night and midnight sun: no events
                expect(sunrises).not_to include(Date.new(2025, 12, 21), Date.new(2025, 6, 21))
                expect(sunsets).not_to include(Date.new(2025, 12, 21), Date.new(2025, 6, 21))

                # Ordinary day: both events
                expect(sunrises).to include(Date.new(2025, 9, 22))
                expect(sunsets).to include(Date.new(2025, 9, 22))

                # Transition into midnight sun: sunrise, but no sunset
                expect(sunrises).to include(Date.new(2025, 5, 18))
                expect(sunsets).not_to include(Date.new(2025, 5, 18))
            end
        end

        context 'below the Antarctic Circle' do
            let(:base_calendar) do
                cal = Icalendar::Calendar.new
                station = build_station(lat: -77.85, lon: 166.67) # McMurdo
                cal.define_singleton_method(:station) { station }
                cal.define_singleton_method(:location) { 'McMurdo Station, ATA' }
                cal
            end

            before do
                allow(described_class).to receive(:timezone_for).and_return('Antarctica/McMurdo')
            end

            def solar_days(calendar, summary)
                calendar.events.select { |e| e.summary.to_s == summary }.map { |e| e.dtstart.to_date }
            end

            it 'skips sunrise and sunset on days without them (polar night, midnight sun)' do
                freeze_time(Time.utc(2025, 6, 15))

                expect {
                    described_class.solar_calendar_for(base_calendar, around: Time.current.utc)
                }.not_to raise_error

                sunrises = solar_days(base_calendar, 'Sunrise')
                sunsets  = solar_days(base_calendar, 'Sunset')

                # Polar night (June) and midnight sun (December): no events
                expect(sunrises).not_to include(Date.new(2025, 6, 21), Date.new(2025, 12, 21))
                expect(sunsets).not_to include(Date.new(2025, 6, 21), Date.new(2025, 12, 21))

                # Ordinary day: both events
                expect(sunrises).to include(Date.new(2025, 9, 22))
                expect(sunsets).to include(Date.new(2025, 9, 22))

                # Transition out of polar night: sunset, but no sunrise
                expect(sunsets).to include(Date.new(2025, 8, 19))
                expect(sunrises).not_to include(Date.new(2025, 8, 19))
            end
        end
    end

    describe '.lunar_calendar_for' do
        let(:base_calendar) do
            cal = Icalendar::Calendar.new
            cal.define_singleton_method(:location) { 'Boston, MA' }
            cal
        end

        let(:lunar_phases) do
            [
                { datetime: DateTime.new(2025, 6, 6, 12, 0, 0), type: :full_moon },
                { datetime: DateTime.new(2025, 6, 13, 18, 0, 0), type: :last_quarter },
                { datetime: DateTime.new(2025, 6, 21, 6, 0, 0), type: :new_moon },
                { datetime: DateTime.new(2025, 6, 29, 12, 0, 0), type: :first_quarter }
            ]
        end

        before do
            allow(described_class).to receive(:lunar_phases).and_return(lunar_phases)
            allow(described_class.lunar_client).to receive(:percent_full).and_return(0.5)
        end

        it 'adds lunar phase events' do
            freeze_time(Time.utc(2025, 6, 15))

            described_class.lunar_calendar_for(base_calendar, around: Time.current.utc)

            expect(base_calendar.events).not_to be_empty
        end

        it 'creates events for all phase types' do
            freeze_time(Time.utc(2025, 6, 15))

            described_class.lunar_calendar_for(base_calendar, around: Time.current.utc)

            summaries = base_calendar.events.map { |e| e.summary.to_s }

            expect(summaries).to include('Full Moon')
            expect(summaries).to include('Last Quarter Moon')
            expect(summaries).to include('New Moon')
            expect(summaries).to include('First Quarter Moon')
        end
    end
end

# BSH terms (AGB 5(10)) want the source credit on every presentation, including the web UI
RSpec.describe 'BSH credit in the web UI', type: :api do
    let(:credit) { 'Datenquelle: Gezeitenvorausberechnungen ©, Bundesamt für Seeschifffahrt und Hydrographie, Hamburg' }

    after { Timecop.return }

    describe 'footer' do
        def footer_credit
            get '/'
            node = Nokogiri::HTML(last_response.body).at_css('footer #bsh-credit')
            # Visible without hover: neither it nor anything around it is toggled by Alpine
            expect(node.ancestors.to_a.unshift(node).select { |n| n.respond_to?(:[]) && n['x-show'] }).to be_empty
            node.text.squish
        end

        it 'shows the credit outside any hover popover, for every year served' do
            Timecop.freeze(Time.utc(2026, 10, 5, 12))
            expect(footer_credit).to eq("German tide predictions: #{credit}, 2026-2027. Das BSH übernimmt für die angegebenen Informationen keine Gewähr. Not for navigation.")
        end

        it 'credits only the current year before next year may be published' do
            Timecop.freeze(Time.utc(2026, 7, 15, 12))
            expect(footer_credit).to include("#{credit}, 2026.")
        end
    end

    describe 'provider badge' do
        let(:theme) { { accent: 'ocean' } }

        def badge(provider, has_alternatives: false)
            station = build_station(id: 'S1', provider: provider)
            html = Server.new!.send(:erb, :'partials/_provider_badge', layout: false, locals: {
                type: :tide, theme: theme, station: station, has_alternatives: has_alternatives,
                alternatives: [], sources_json: '[]'
            })
            Nokogiri::HTML.fragment(html)
        end

        before { Timecop.freeze(Time.utc(2026, 10, 5, 12)) }

        it 'puts a not-for-navigation note and the full credit next to a BSH badge' do
            html = badge('bsh')

            expect(html.at_css('.bsh-credit').text).to eq('© BSH · Not for navigation')
            expect(html.at_css('.bsh-notice')).not_to be_nil
            expect(html.text).to include('NOT FOR NAVIGATION')
            expect(html.text).to include("#{credit}, 2026-2027")
        end

        it 'does the same on the multi-source badge, shown only while BSH is selected' do
            html = badge('bsh', has_alternatives: true)

            expect(html.css('.bsh-credit').map { |n| n['x-show'] }).to eq(['/bsh/i.test(provider)', '/bsh/i.test(station.provider)'])
            expect(html.at_css('.bsh-credit').key?('x-cloak')).to be(true)
            expect(html.css('template[x-if="/bsh/i.test(provider)"] .bsh-notice')).not_to be_empty
            expect(html.text).to include("#{credit}, 2026-2027")
        end

        it 'adds no BSH credit to a NOAA badge' do
            expect(badge('noaa').to_html).not_to include('BSH')
        end
    end
end

# Kartverket's CC BY 4.0 terms want "© Kartverket", with a link, wherever its data is used
RSpec.describe 'Kartverket credit in the web UI', type: :api do
    describe 'footer' do
        it 'shows © Kartverket with links to Kartverket and the licence, outside any hover popover' do
            get '/'
            node = Nokogiri::HTML(last_response.body).at_css('footer #kartverket-credit')

            expect(node.ancestors.to_a.unshift(node).select { |n| n.respond_to?(:[]) && n['x-show'] }).to be_empty
            expect(node.text.squish).to eq('Norwegian tide predictions: © Kartverket, licensed under CC BY 4.0; heights converted to the selected units, times unchanged (UTC). Not for navigation.')
            expect(node.css('a').map { |a| [a.text, a['href']] }).to eq([
                ['© Kartverket', 'https://www.kartverket.no/'], ['CC BY 4.0', 'https://creativecommons.org/licenses/by/4.0/']
            ])
        end
    end

    describe 'provider badge' do
        def badge(provider, has_alternatives: false)
            station = build_station(id: 'S1', provider: provider)
            html = Server.new!.send(:erb, :'partials/_provider_badge', layout: false, locals: {
                type: :tide, theme: { accent: 'ocean' }, station: station, has_alternatives: has_alternatives,
                alternatives: [], sources_json: '[]'
            })
            Nokogiri::HTML.fragment(html)
        end

        it 'shows Kartverket as an official source with a not-for-navigation note and the linked credit' do
            html = badge('kartverket')

            expect(html.at_css('.badge-warning')).to be_nil
            expect(html.at_css('.kartverket-credit').text).to eq('© Kartverket · CC BY 4.0 · Not for navigation')
            expect(html.at_css('.kartverket-credit a')['href']).to eq('https://www.kartverket.no/')
            expect(html.at_css('.kartverket-notice')).not_to be_nil
            expect(html.text).to include('NOT FOR NAVIGATION')
        end

        it 'does the same on the multi-source badge, shown (and cloaked until Alpine starts) only while Kartverket is selected' do
            html = badge('kartverket', has_alternatives: true)

            expect(html.css('.kartverket-credit').map { |n| n['x-show'] }).to eq(['/kartverket/i.test(provider)', '/kartverket/i.test(station.provider)'])
            expect(html.css('.kartverket-credit')).to all(satisfy { |n| n.key?('x-cloak') })
            expect(html.css('template[x-if="/kartverket/i.test(provider)"] .kartverket-notice')).not_to be_empty
            # Styled as official, so no harmonic-source warning
            expect(html.at_css('button')[':class']).to start_with('/noaa|chs|bsh|kartverket|linz|imi|rws/i.test(provider)')
            expect(html.css('template[x-if="!/noaa|chs|bsh|kartverket|linz|imi|rws/i.test(provider)"]')).not_to be_empty
        end

        it 'says on both badges, as the CC BY change notice does, that only heights are converted and times are unchanged (UTC)' do
            [badge('kartverket'), badge('kartverket', has_alternatives: true)].each do |html|
                notes = html.css('p').map { |p| p.text.squish }.select { |t| t.start_with?('© Kartverket, CC BY 4.0.') && t.match?(/convert/i) }
                expect(notes).to eq(['© Kartverket, CC BY 4.0. Heights converted; times unchanged (UTC).'])
                expect(html.text).not_to match(/times and heights converted/i)
            end
        end

        it 'adds no Kartverket credit to NOAA or BSH badges' do
            expect(badge('noaa').to_html).not_to include('Kartverket')
            expect(badge('bsh').css('.kartverket-credit, .kartverket-notice')).to be_empty
        end
    end
end

# LINZ's CC BY 4.0 terms ask for its attribution, in writing, wherever its data is used.  LINZ says
# these are not the official tide tables under Maritime Rules Part 25, so the UI never says official.
RSpec.describe 'LINZ credit in the web UI', type: :api do
    describe 'footer' do
        it 'credits LINZ with links to its tide predictions page and the licence, outside any hover popover' do
            get '/'
            node = Nokogiri::HTML(last_response.body).at_css('footer #linz-credit')

            expect(node.ancestors.to_a.unshift(node).select { |n| n.respond_to?(:[]) && n['x-show'] }).to be_empty
            expect(node.text.squish).to eq(
                'New Zealand tide predictions: based on Toitū Te Whenua Land Information New Zealand data, licensed for re-use under CC BY 4.0; ' \
                'times converted from New Zealand local time (Chatham Islands time for the Chatham Islands ports) to UTC, ' \
                'high/low labels added, heights converted to the selected units. ' \
                'Not the official tide tables under Maritime Rules Part 25. Not for navigation.'
            )
            expect(node.css('a').map { |a| [a.text, a['href']] }).to eq([
                ['Toitū Te Whenua Land Information New Zealand', 'https://www.linz.govt.nz/products-services/tides-and-tidal-streams/tide-predictions'],
                ['CC BY 4.0', 'https://creativecommons.org/licenses/by/4.0/']
            ])
        end
    end

    describe 'provider badge' do
        def badge(provider, has_alternatives: false)
            station = build_station(id: 'S1', provider: provider)
            html = Server.new!.send(:erb, :'partials/_provider_badge', layout: false, locals: {
                type: :tide, theme: { accent: 'ocean' }, station: station, has_alternatives: has_alternatives,
                alternatives: [], sources_json: '[]'
            })
            Nokogiri::HTML.fragment(html)
        end

        it 'shows LINZ with a not-for-navigation note and the linked credit, without calling it official' do
            html = badge('linz')

            expect(html.at_css('.badge-warning')).to be_nil
            expect(html.at_css('.linz-credit').text).to eq('Toitū Te Whenua LINZ · CC BY 4.0 · Not for navigation')
            expect(html.at_css('.linz-credit a')['href']).to eq(Clients::LinzTides::HOME_URL)
            expect(html.at_css('.linz-credit')['class'].split).to include('max-w-[10.5rem]')
            expect(html.at_css('.linz-notice')).not_to be_nil
            expect(html.text).to include('NOT FOR NAVIGATION', 'not the official tide tables under Maritime Rules Part 25')
            expect(html.text.scan(/official/i).length).to eq(1)
        end

        it 'does the same on the multi-source badge, shown (and cloaked until Alpine starts) only while LINZ is selected' do
            html = badge('linz', has_alternatives: true)

            expect(html.css('.linz-credit').map { |n| n['x-show'] }).to eq(['/linz/i.test(provider)', '/linz/i.test(station.provider)'])
            expect(html.css('.linz-credit')).to all(satisfy { |n| n.key?('x-cloak') })
            # The line under the badge wraps to two lines instead of widening the badge column over the station id
            expect(html.at_css('.linz-credit')['class'].split).to include('max-w-[10.5rem]')
            expect(html.css('template[x-if="/linz/i.test(provider)"] .linz-notice')).not_to be_empty
            # Styled as an agency source, so no harmonic-source warning
            expect(html.at_css('button')[':class']).to start_with('/noaa|chs|bsh|kartverket|linz|imi|rws/i.test(provider)')
            expect(html.css('template[x-if="!/noaa|chs|bsh|kartverket|linz|imi|rws/i.test(provider)"]')).not_to be_empty
        end

        it 'adds no LINZ credit to NOAA, BSH or Kartverket badges' do
            expect(badge('noaa').to_html).not_to include('LINZ')
            expect(badge('bsh').css('.linz-credit, .linz-notice')).to be_empty
            expect(badge('kartverket').css('.linz-credit, .linz-notice')).to be_empty
        end
    end
end

# The Marine Institute's CC BY 4.0 licence asks for credit, a licence link and a note of changes
RSpec.describe 'Marine Institute credit in the web UI', type: :api do
    describe 'footer' do
        it 'credits MI with links to its tidal predictions page and the licence, and says what we changed, outside any hover popover' do
            get '/'
            node = Nokogiri::HTML(last_response.body).at_css('footer #imi-credit')

            expect(node.ancestors.to_a.unshift(node).select { |n| n.respond_to?(:[]) && n['x-show'] }).to be_empty
            expect(node.text.squish).to eq(
                'Irish tide predictions: data supplied by Marine Institute, licensed under CC BY 4.0; ' \
                'heights, where shown, converted from OD Malin to chart datum (LAT) with a fixed offset per station, and to the selected units; ' \
                'times unchanged (UTC). Storm surge not included. Not for navigation.'
            )
            # The footer serves every MI station, including those whose heights are left out
            expect(node.text.squish).not_to include('; heights converted')
            expect(node.css('a').map { |a| [a.text, a['href']] }).to eq([
                ['Marine Institute', 'https://www.marine.ie/site-area/data-services/real-time-observations/tidal-predictions'],
                ['CC BY 4.0', 'https://creativecommons.org/licenses/by/4.0/']
            ])
        end
    end

    describe 'provider badge' do
        def badge(provider, has_alternatives: false)
            station = build_station(id: 'S1', provider: provider)
            html = Server.new!.send(:erb, :'partials/_provider_badge', layout: false, locals: {
                type: :tide, theme: { accent: 'ocean' }, station: station, has_alternatives: has_alternatives,
                alternatives: [], sources_json: '[]'
            })
            Nokogiri::HTML.fragment(html)
        end

        it 'shows IMI with a not-for-navigation note and the linked credit' do
            html = badge('imi')

            expect(html.at_css('.badge-warning')).to be_nil
            expect(html.at_css('.imi-credit').text).to eq('© Marine Institute · CC BY 4.0 · Not for navigation')
            expect(html.at_css('.imi-credit a')['href']).to eq(Clients::MarineInstituteTides::HOME_URL)
            expect(html.at_css('.imi-notice')).not_to be_nil
            expect(html.at_css('.imi-credit')['class'].split).to include('max-w-[8rem]')
            expect(html.text).to include('NOT FOR NAVIGATION', 'Heights, where shown, converted from OD Malin to chart datum (LAT); times unchanged (UTC).')
            # The same badge serves stations without a chart datum offset, whose heights are left out
            expect(html.text).not_to include('. Heights converted from OD Malin')
        end

        it 'words the height change on both badges so it is true for a station with or without a chart datum offset' do
            [badge('imi'), badge('imi', has_alternatives: true)].each do |html|
                notes = html.css('p').map { |p| p.text.squish }.select { |t| t.start_with?('Data supplied by Marine Institute') && t.match?(/heights/i) }
                expect(notes).to eq(['Data supplied by Marine Institute, CC BY 4.0. Heights, where shown, converted from OD Malin to chart datum (LAT); times unchanged (UTC).'])
            end
        end

        it 'does not call MI predictions official on either badge (part of the set is modelled, not gauge-based)' do
            [badge('imi'), badge('imi', has_alternatives: true)].each do |html|
                notices = html.css('.imi-notice').map { |svg| svg.parent.text.squish }
                expect(notices).not_to be_empty
                notices.each { |t| expect(t).not_to match(/official/i) }
                notes = html.css('p').map { |p| p.text.squish }.select { |t| t.include?('MI accepts no responsibility') }
                expect(notes).to eq(['Marine Institute (Ireland) predictions. MI accepts no responsibility for errors or for their use. Storm surge is not included.'])
            end
        end

        it 'does the same on the multi-source badge, shown (and cloaked until Alpine starts) only while MI is selected' do
            html = badge('imi', has_alternatives: true)

            expect(html.css('.imi-credit').map { |n| n['x-show'] }).to eq(['/imi/i.test(provider)', '/imi/i.test(station.provider)'])
            expect(html.css('.imi-credit')).to all(satisfy { |n| n.key?('x-cloak') })
            # The line under the badge wraps to two lines instead of widening the badge column, narrower
            # than LINZ's because MI station ids run long (Tom_Clarke_Bridge) and spill toward it
            expect(html.at_css('.imi-credit')['class'].split).to include('max-w-[8rem]')
            expect(html.css('template[x-if="/imi/i.test(provider)"] .imi-notice')).not_to be_empty
            # Each source's notice is its own x-if, and the credit lines sit outside them all (inside
            # one, Alpine never renders them while another source is selected)
            expect(html.css('template[x-if*="test(provider)"] template[x-if*="test(provider)"]')).to be_empty
            expect(html.css('p[x-show$="test(provider)"]').map { |n| [n['class'].split.first, n.ancestors('template').length] }).to eq(%w[bsh-credit kartverket-credit linz-credit imi-credit rws-credit].map { |c| [c, 0] })
            # Styled as an agency source, so no harmonic-source warning
            expect(html.at_css('button')[':class']).to start_with('/noaa|chs|bsh|kartverket|linz|imi|rws/i.test(provider)')
            expect(html.css('template[x-if="!/noaa|chs|bsh|kartverket|linz|imi|rws/i.test(provider)"]')).not_to be_empty
        end

        it 'adds no MI credit to NOAA, BSH, Kartverket or LINZ badges' do
            expect(badge('noaa').to_html).not_to include('Marine Institute')
            %w[bsh kartverket linz].each { |p| expect(badge(p).css('.imi-credit, .imi-notice')).to be_empty }
        end
    end
end

RSpec.describe WebCalTides do
    describe '.tide_calendar_for with rws (Rijkswaterstaat) provider: calendar name' do
        let(:station) do
            build_station(name: 'IJmuiden, buitenhaven', id: 'NL__ijmuiden.buitenhaven', public_id: 'ijmuiden.buitenhaven', provider: 'rws',
                          lat: 52.463, lon: 4.555, location: 'IJmuiden, buitenhaven, Netherlands', region: 'Netherlands',
                          url: Clients::RijkswaterstaatTides::HOME_URL)
        end

        # RWS Stavenisse: 2026-10-24T14:09+01:00 HW 158 cm and 2026-10-26T09:07+01:00 LW -119 cm vs NAP
        # (summer time on the 24th, winter time on the 26th; RWS gives +01:00 for both)
        let(:tide_data) do
            [
                build_tide_data(type: 'High', units: 'm', prediction: 1.58, time: DateTime.new(2026, 10, 24, 13, 9), url: station.url),
                build_tide_data(type: 'Low',  units: 'm', prediction: -1.19, time: DateTime.new(2026, 10, 26, 8, 7), url: station.url)
            ]
        end

        before do
            allow(described_class).to receive(:tide_station_for).and_return(station)
            allow(described_class).to receive(:tide_data_for).and_return(tide_data)
        end

        let(:calendar) { described_class.tide_calendar_for('NL__ijmuiden.buitenhaven', units: 'metric') }
        let(:ical)     { calendar.to_ical.gsub(/\r\n[ \t]/, '') }

        it 'names the calendar with the RWS name as published (titleize would give "I Jmuiden, Buitenhaven")' do
            expect(calendar.x_wr_calname.first.value).to eq('IJmuiden, buitenhaven')
            allow(described_class).to receive(:tide_station_for).and_return(station.dup.tap { |s| s.name = 'Hoek van Holland' })
            expect(described_class.tide_calendar_for('NL__hoekvanholland').x_wr_calname.first.value).to eq('Hoek van Holland')
        end

    end
end

RSpec.describe WebCalTides do
    describe '.tide_calendar_for with rws (Rijkswaterstaat) provider: partial window' do
        let(:station) do
            build_station(name: 'IJmuiden, buitenhaven', id: 'NL__ijmuiden.buitenhaven', public_id: 'ijmuiden.buitenhaven', provider: 'rws',
                          lat: 52.463, lon: 4.555, location: 'IJmuiden, buitenhaven, Netherlands', region: 'Netherlands',
                          url: Clients::RijkswaterstaatTides::HOME_URL)
        end

        # RWS Stavenisse: 2026-10-24T14:09+01:00 HW 158 cm and 2026-10-26T09:07+01:00 LW -119 cm vs NAP
        # (summer time on the 24th, winter time on the 26th; RWS gives +01:00 for both)
        let(:tide_data) do
            [
                build_tide_data(type: 'High', units: 'm', prediction: 1.58, time: DateTime.new(2026, 10, 24, 13, 9), url: station.url),
                build_tide_data(type: 'Low',  units: 'm', prediction: -1.19, time: DateTime.new(2026, 10, 26, 8, 7), url: station.url)
            ]
        end

        before do
            allow(described_class).to receive(:tide_station_for).and_return(station)
            allow(described_class).to receive(:tide_data_for).and_return(tide_data)
        end

        let(:calendar) { described_class.tide_calendar_for('NL__ijmuiden.buitenhaven', units: 'metric') }
        let(:ical)     { calendar.to_ical.gsub(/\r\n[ \t]/, '') }

        it 'says whether it was built from a partial window' do
            expect(calendar).not_to be_partial
            allow(described_class).to receive(:tide_data_for).and_return(tide_data.dup.extend(Clients::PartialWindow))
            expect(described_class.tide_calendar_for('NL__ijmuiden.buitenhaven')).to be_partial
        end
    end

    describe '.tide_data_for with a partial window' do
        let(:station) { build_station(id: 'NL__stavenisse', public_id: 'stavenisse', provider: 'rws') }
        let(:client)  { instance_double(Clients::RijkswaterstaatTides) }
        let(:tides)   { [build_tide_data(type: 'High', units: 'm', prediction: 1.2, time: DateTime.new(2027, 12, 31, 4, 27))] }
        let(:file)    { "#{described_class.settings.cache_dir}/tides_v#{Models::TideData.version}_NL__stavenisse_202706.json" }

        around { |example| with_test_cache_dir { example.run } }

        before do
            allow(described_class).to receive(:tide_clients).and_call_original
            allow(described_class).to receive(:tide_clients).with('rws').and_return(client)
        end

        it 'serves it without caching it for the month, and fetches it again next time' do
            allow(client).to receive(:tide_data_for).and_return(tides.dup.extend(Clients::PartialWindow))

            2.times { expect(described_class.tide_data_for(station, around: Time.utc(2027, 6, 15))).to eq(tides) }
            expect(File.exist?(file)).to be(false)
            expect(client).to have_received(:tide_data_for).twice
        end

        it 'caches a complete window as before' do
            allow(client).to receive(:tide_data_for).and_return(tides)

            2.times { expect(described_class.tide_data_for(station, around: Time.utc(2027, 6, 15)).map(&:prediction)).to eq([1.2]) }
            expect(File.exist?(file)).to be(true)
            expect(client).to have_received(:tide_data_for).once
        end
    end
end

RSpec.describe 'GET /tides/:station.ics for a partial window', type: :api do
    include Rack::Test::Methods

    let(:station) { build_station(name: 'Stavenisse', id: 'NL__stavenisse', public_id: 'stavenisse', provider: 'rws', location: 'Stavenisse, Netherlands') }
    let(:tides)   { [build_tide_data(type: 'High', units: 'm', prediction: 1.2, time: DateTime.new(2027, 12, 31, 4, 27))] }
    let(:ics)     { "#{Server.settings.cache_dir}/tides_v#{Models::TideData.version}_NL__stavenisse_202706_metric_0_0.ics" }

    # The clock is frozen in 2027, which would make the route start the monthly cache cleanup;
    # keep it out of the real cache/ by using a temp cache dir and stubbing the cleanup.
    around { |example| with_test_cache_dir { example.run } }

    before do
        allow(WebCalTides).to receive(:cleanup_if_month_changed)
        allow(WebCalTides).to receive(:station_ids).and_return(['NL__stavenisse'])
        allow(WebCalTides).to receive(:tide_station_for).and_return(station)
    end

    it 'serves the feed without caching it for the month' do
        allow(WebCalTides).to receive(:tide_data_for).and_return(tides.dup.extend(Clients::PartialWindow))
        Timecop.freeze(Time.utc(2027, 6, 15)) { get '/tides/NL__stavenisse.ics', units: 'metric', solar: '0' }

        expect(last_response).to be_ok
        expect(last_response.body).to include('High Tide 1.2 m')
        expect(File.exist?(ics)).to be(false)
    end

    it 'caches a feed from a complete window as before' do
        allow(WebCalTides).to receive(:tide_data_for).and_return(tides)
        Timecop.freeze(Time.utc(2027, 6, 15)) { get '/tides/NL__stavenisse.ics', units: 'metric', solar: '0' }

        expect(last_response).to be_ok
        expect(File.exist?(ics)).to be(true)
    end

    it 'serves the RWS station id with dots in it' do
        allow(WebCalTides).to receive(:station_ids).and_return(['NL__denhelder.marsdiep'])
        allow(WebCalTides).to receive(:tide_data_for).and_return(tides.dup.extend(Clients::PartialWindow))
        Timecop.freeze(Time.utc(2027, 6, 15)) { get '/tides/NL__denhelder.marsdiep.ics', units: 'metric', solar: '0' }

        expect(last_response).to be_ok
        expect(WebCalTides).to have_received(:tide_station_for).with('NL__denhelder.marsdiep').at_least(:once)
    end
end


RSpec.describe WebCalTides do
    describe '.tide_calendar_for with rws (Rijkswaterstaat) provider' do
        let(:station) do
            build_station(name: 'IJmuiden, buitenhaven', id: 'NL__ijmuiden.buitenhaven', public_id: 'ijmuiden.buitenhaven', provider: 'rws',
                          lat: 52.463, lon: 4.555, location: 'IJmuiden, buitenhaven, Netherlands', region: 'Netherlands',
                          url: Clients::RijkswaterstaatTides::HOME_URL)
        end

        # RWS Stavenisse: 2026-10-24T14:09+01:00 HW 158 cm and 2026-10-26T09:07+01:00 LW -119 cm vs NAP
        # (summer time on the 24th, winter time on the 26th; RWS gives +01:00 for both)
        let(:tide_data) do
            [
                build_tide_data(type: 'High', units: 'm', prediction: 1.58, time: DateTime.new(2026, 10, 24, 13, 9), url: station.url),
                build_tide_data(type: 'Low',  units: 'm', prediction: -1.19, time: DateTime.new(2026, 10, 26, 8, 7), url: station.url)
            ]
        end

        before do
            allow(described_class).to receive(:tide_station_for).and_return(station)
            allow(described_class).to receive(:tide_data_for).and_return(tide_data)
        end

        let(:calendar) { described_class.tide_calendar_for('NL__ijmuiden.buitenhaven', units: 'metric') }
        let(:ical)     { calendar.to_ical.gsub(/\r\n[ \t]/, '') }

        it 'names the source, the NAP datum, what we changed and the disclaimer on the feed' do
            caldesc = Icalendar::Values::Text.new(Clients::RijkswaterstaatTides.feed_description(tide_data)).value_ical

            expect(ical).to include("DESCRIPTION:#{caldesc}", "X-WR-CALDESC:#{caldesc}")
            expect(ical).to match(/^X-WR-CALDESC:Source: Rijkswaterstaat .*CC0.*Heights are above NAP .*not chart datum.*NOT FOR NAVIGATION/)
            expect(ical).to match(/^X-WR-CALDESC:.*no uptime guarantee and is not suitable for critical applications\\?, and that use is at your own risk\./)
            expect(ical).not_to match(/liab/i)
            expect(ical).not_to include('This program is distributed')
        end

        it 'names the source and the datum on every event' do
            expect(calendar.events.map { |e| e.description.to_s }).to all(eq(Clients::RijkswaterstaatTides.event_description(tide_data.first)))
            expect(calendar.events.first.description.to_s).to include('Height above NAP')
        end

        it 'keeps the instants RWS publishes at a fixed +01:00 (no summer time shift) and labels heights in metres above NAP' do
            # 14:09+01:00 is 13:09 UTC; read as Dutch summer time (+02:00) it would be 12:09
            expect(ical).to include('DTSTART;TZID=GMT:20261024T130900', 'DTSTART;TZID=GMT:20261026T080700')
            expect(ical).not_to include('DTSTART;TZID=GMT:20261024T120900')
            expect(calendar.events.map { |e| e.summary.to_s }).to eq(['High Tide 1.58 m NAP', 'Low Tide -1.19 m NAP'])
        end

        it 'converts the heights to feet in the default (imperial) units, still labelled NAP' do
            calendar = described_class.tide_calendar_for('NL__ijmuiden.buitenhaven')
            expect(calendar.events.map { |e| e.summary.to_s }).to eq(['High Tide 5.184 ft NAP', 'Low Tide -3.904 ft NAP'])
        end

        it 'adds no NAP label or RWS credit to other providers' do
            allow(described_class).to receive(:tide_station_for).and_return(station.dup.tap { |s| s.provider = 'ticon' })
            calendar = described_class.tide_calendar_for('T1', units: 'metric')
            expect(calendar.events.map { |e| e.summary.to_s }).to eq(['High Tide 1.58 m', 'Low Tide -1.19 m'])
            expect(calendar.to_ical).not_to include('Rijkswaterstaat')
        end

    end
end

# Rijkswaterstaat's data is CC0, but the UI names the source and that heights are above NAP
RSpec.describe 'Rijkswaterstaat credit in the web UI', type: :api do
    describe 'footer' do
        it 'names RWS with links, the NAP datum and what we changed, outside any hover popover' do
            get '/'
            node = Nokogiri::HTML(last_response.body).at_css('footer #rws-credit')

            expect(node.ancestors.to_a.unshift(node).select { |n| n.respond_to?(:[]) && n['x-show'] }).to be_empty
            expect(node.text.squish).to eq(
                'Dutch tide predictions: Rijkswaterstaat astronomical tide, CC0; heights above NAP (Dutch land datum), not chart datum, ' \
                'converted from cm to the selected units; times converted from +01:00 to UTC. Weather not included. Not for navigation.'
            )
            expect(node.css('a').map { |a| [a.text, a['href']] }).to eq([
                ['Rijkswaterstaat', 'https://waterinfo.rws.nl'], ['CC0', 'https://creativecommons.org/publicdomain/zero/1.0/']
            ])
        end
    end

    describe 'provider badge' do
        def badge(provider, has_alternatives: false)
            station = build_station(id: 'S1', provider: provider)
            html = Server.new!.send(:erb, :'partials/_provider_badge', layout: false, locals: {
                type: :tide, theme: { accent: 'ocean' }, station: station, has_alternatives: has_alternatives,
                alternatives: [], sources_json: '[]'
            })
            Nokogiri::HTML.fragment(html)
        end

        it 'shows RWS with a not-for-navigation note, the NAP datum and the linked source, narrow enough to wrap' do
            html = badge('rws')

            expect(html.at_css('.badge-warning')).to be_nil
            expect(html.at_css('.rws-credit').text).to eq('Rijkswaterstaat · CC0 · Heights vs NAP · Not for navigation')
            expect(html.at_css('.rws-credit')['class']).to include('max-w-[10.5rem]')
            expect(html.at_css('.rws-credit a')['href']).to eq(Clients::RijkswaterstaatTides::HOME_URL)
            expect(html.at_css('.rws-notice')).not_to be_nil
            expect(html.text).to include('NOT FOR NAVIGATION', 'Heights are above NAP (the Dutch land datum), not chart datum.')
        end

        it 'does the same on the multi-source badge, shown (and cloaked until Alpine starts) only while RWS is selected' do
            html = badge('rws', has_alternatives: true)

            expect(html.css('.rws-credit').map { |n| n['x-show'] }).to eq(['/rws/i.test(provider)', '/rws/i.test(station.provider)'])
            expect(html.css('.rws-credit')).to all(satisfy { |n| n.key?('x-cloak') })
            expect(html.css('template[x-if="/rws/i.test(provider)"] .rws-notice')).not_to be_empty
            # The credit line sits outside every source's x-if, so Alpine renders it under the badge
            expect(html.at_css('p.rws-credit[x-show="/rws/i.test(provider)"]').ancestors('template')).to be_empty
            expect(html.at_css('button')[':class']).to start_with('/noaa|chs|bsh|kartverket|linz|imi|rws/i.test(provider)')
        end

        it 'names the datum after compared heights' do
            expect(badge('rws', has_alternatives: true).to_html).to include("(event.datum ? ' ' + event.datum : '')")
        end

        it 'adds no RWS credit to other badges' do
            expect(badge('noaa').to_html).not_to include('Rijkswaterstaat')
            %w[bsh kartverket linz imi].each { |p| expect(badge(p).css('.rws-credit, .rws-notice')).to be_empty }
        end
    end

    describe 'station card id' do
        def card(url)
            station = build_station(id: 'NL__hoekvanholland.maeslantkering.beneden.noord',
                                    public_id: 'hoekvanholland.maeslantkering.beneden.noord', provider: 'rws', url: url)
            html = Server.new!.send(:erb, :'partials/_station_card', layout: false, locals: {
                type: :tide, station: station, index: 0, theme: { accent: 'ocean', text: 'text-ocean-400' },
                map_url: nil, webcal_base: "'webcal://x'", https_base: "'https://x'",
                has_alternatives: false, alternatives: [], sources_json: '[]'
            })
            Nokogiri::HTML.fragment(html)
        end

        # A long dotted RWS id must truncate inside its column instead of running under the credit
        it 'truncates the linked id with an ellipsis, lets the arrow wrap below it and puts the full id in the title' do
            link = card('https://waterinfo.rws.nl').at_css('template[x-if="currentSource?.url?.startsWith(\'http\')"]').children.at_css('a.station-id')

            expect(link['class'].split).to include('inline-flex', 'flex-wrap', 'max-w-[calc(100%+0.5rem)]')
            expect(link[':title']).to eq('currentSource.publicId || selectedSource')
            id_span, arrow = link.css('span').to_a
            expect(id_span['class'].split).to include('min-w-0', 'truncate')
            expect(arrow['class']).to include('flex-shrink-0')
            expect(arrow.text).to eq('↗')
        end

        it 'truncates the unlinked id the same way' do
            id = card('#ticon').at_css('template[x-if="!currentSource?.url?.startsWith(\'http\')"]').children.at_css('p.station-id')

            expect(id['class'].split).to include('max-w-[calc(100%+0.5rem)]', 'truncate')
            expect(id[':title']).to eq('currentSource?.publicId || selectedSource')
        end
    end
end

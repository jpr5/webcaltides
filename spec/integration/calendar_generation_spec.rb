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
            expect(html.at_css('button')[':class']).to start_with('/noaa|chs|bsh|kartverket|linz/i.test(provider)')
            expect(html.css('template[x-if="!/noaa|chs|bsh|kartverket|linz/i.test(provider)"]')).not_to be_empty
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
            expect(html.at_css('button')[':class']).to start_with('/noaa|chs|bsh|kartverket|linz/i.test(provider)')
            expect(html.css('template[x-if="!/noaa|chs|bsh|kartverket|linz/i.test(provider)"]')).not_to be_empty
        end

        it 'adds no LINZ credit to NOAA, BSH or Kartverket badges' do
            expect(badge('noaa').to_html).not_to include('LINZ')
            expect(badge('bsh').css('.linz-credit, .linz-notice')).to be_empty
            expect(badge('kartverket').css('.linz-credit, .linz-notice')).to be_empty
        end
    end
end

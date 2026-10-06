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

            it 'keeps the harmonic disclaimer and carries no BSH credit' do
                calendar = described_class.tide_calendar_for('X123')
                ical     = calendar.to_ical.gsub(/\r\n[ \t]/, '')

                expect(Array(calendar.description)).to match([start_with('NOT FOR NAVIGATION. This program is distributed')])
                expect(ical).not_to include('Bundesamt')
                expect(ical).not_to include('X-WR-CALDESC')
                expect(calendar.events.map(&:description)).to all(be_nil)
            end
        end

        it 'carries no BSH credit on an official non-BSH feed' do
            calendar = described_class.tide_calendar_for('NOAA123')
            ical     = calendar.to_ical.gsub(/\r\n[ \t]/, '')

            expect(Array(calendar.description)).to be_empty
            expect(ical).not_to include('Bundesamt')
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

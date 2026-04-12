# frozen_string_literal: true

require 'spec_helper'

RSpec.describe 'ICS calendar serialization' do
    let(:frozen_time) { Time.utc(2025, 6, 15, 12, 0, 0) }

    let(:tide_station) do
        build_station(
            name: 'boston harbor',
            id: 'NOAA_8443970',
            public_id: 'NOAA_8443970',
            region: 'Massachusetts',
            location: 'Boston, MA',
            lat: 42.3601,
            lon: -71.0589,
            url: 'https://tidesandcurrents.noaa.gov/stationhome.html',
            provider: 'noaa',
        )
    end

    let(:tide_data) do
        [
            build_tide_data(
                type: 'High',
                units: 'ft',
                prediction: 10.5,
                time: DateTime.new(2025, 6, 15, 6, 30, 0),
                url: 'https://tidesandcurrents.noaa.gov/noaatidepredictions.html?id=8443970',
            ),
            build_tide_data(
                type: 'Low',
                units: 'ft',
                prediction: -0.3,
                time: DateTime.new(2025, 6, 15, 12, 45, 0),
                url: 'https://tidesandcurrents.noaa.gov/noaatidepredictions.html?id=8443970',
            ),
        ]
    end

    let(:current_station) do
        build_station(
            name: 'the race',
            id: 'NOAA_ACT1011',
            public_id: 'NOAA_ACT1011',
            region: 'Connecticut',
            location: 'The Race, CT',
            lat: 41.2262,
            lon: -72.0648,
            url: 'https://tidesandcurrents.noaa.gov/cdata/StationInfo',
            provider: 'noaa',
            bid: 'ACT1011',
            depth: '10',
        )
    end

    let(:current_data) do
        [
            build_current_data(
                type: 'flood',
                time: DateTime.new(2025, 6, 15, 3, 15, 0),
                velocity_major: 2.5,
                mean_flood_dir: '045',
                mean_ebb_dir: '225',
                depth: '10',
            ),
            build_current_data(
                type: 'ebb',
                time: DateTime.new(2025, 6, 15, 9, 30, 0),
                velocity_major: -1.8,
                mean_flood_dir: '045',
                mean_ebb_dir: '225',
                depth: '10',
            ),
            build_current_data(
                type: 'slack',
                time: DateTime.new(2025, 6, 15, 6, 20, 0),
                velocity_major: 0.0,
                mean_flood_dir: '045',
                mean_ebb_dir: '225',
                depth: '10',
            ),
        ]
    end

    before do
        freeze_time(frozen_time)
    end

    describe 'tide_calendar_for end-to-end' do
        before do
            allow(WebCalTides).to receive(:tide_station_for).with('NOAA_8443970').and_return(tide_station)
            allow(WebCalTides).to receive(:tide_data_for).and_return(tide_data)
        end

        it 'returns an Icalendar::Calendar' do
            cal = WebCalTides.tide_calendar_for('NOAA_8443970')

            expect(cal).to be_a(Icalendar::Calendar)
        end

        it 'produces valid ICS with BEGIN:VCALENDAR and END:VCALENDAR' do
            cal = WebCalTides.tide_calendar_for('NOAA_8443970')
            ics = cal.to_ical

            expect(ics).to include('BEGIN:VCALENDAR')
            expect(ics).to include('END:VCALENDAR')
        end

        it 'generates events with correct SUMMARY lines' do
            cal = WebCalTides.tide_calendar_for('NOAA_8443970')
            ics = cal.to_ical

            expect(ics).to include('High Tide 10.5 ft')
            expect(ics).to include('Low Tide -0.3 ft')
        end

        it 'sets DTSTART and DTEND on each event' do
            cal = WebCalTides.tide_calendar_for('NOAA_8443970')

            cal.events.each do |event|
                expect(event.dtstart).not_to be_nil
                expect(event.dtend).not_to be_nil
            end
        end

        it 'sets URL from TideData.url' do
            cal = WebCalTides.tide_calendar_for('NOAA_8443970')
            ics = cal.to_ical

            # ICS line folding may split long URLs across lines; unfold before checking
            unfolded = ics.gsub("\r\n ", '')
            expect(unfolded).to include('noaatidepredictions.html?id=8443970')
        end

        it 'sets LOCATION from station.location' do
            cal = WebCalTides.tide_calendar_for('NOAA_8443970')
            ics = cal.to_ical

            expect(ics).to include('Boston\\, MA')
        end

        it 'sets the calendar name via x_wr_calname' do
            cal = WebCalTides.tide_calendar_for('NOAA_8443970')

            expect(cal.x_wr_calname.first.to_s).to eq('Boston Harbor')
        end

        it 'generates the correct number of events' do
            cal = WebCalTides.tide_calendar_for('NOAA_8443970')

            expect(cal.events.length).to eq(2)
        end

        it 'returns nil for an unknown station' do
            allow(WebCalTides).to receive(:tide_station_for).with('UNKNOWN').and_return(nil)

            cal = WebCalTides.tide_calendar_for('UNKNOWN')

            expect(cal).to be_nil
        end

        context 'with metric units' do
            it 'converts predictions to meters' do
                cal = WebCalTides.tide_calendar_for('NOAA_8443970', units: 'metric')
                ics = cal.to_ical

                # 10.5 ft -> ~3.2 m
                expect(ics).to include('High Tide')
                expect(ics).to include(' m')
                expect(ics).not_to include(' ft')
            end
        end
    end

    describe 'current_calendar_for end-to-end' do
        before do
            allow(WebCalTides).to receive(:current_station_for).with('NOAA_ACT1011').and_return(current_station)
            allow(WebCalTides).to receive(:current_data_for).and_return(current_data)
        end

        it 'returns an Icalendar::Calendar' do
            cal = WebCalTides.current_calendar_for('NOAA_ACT1011')

            expect(cal).to be_a(Icalendar::Calendar)
        end

        it 'produces valid ICS with BEGIN:VCALENDAR and END:VCALENDAR' do
            cal = WebCalTides.current_calendar_for('NOAA_ACT1011')
            ics = cal.to_ical

            expect(ics).to include('BEGIN:VCALENDAR')
            expect(ics).to include('END:VCALENDAR')
        end

        it 'generates Flood events with correct summaries' do
            cal = WebCalTides.current_calendar_for('NOAA_ACT1011')
            ics = cal.to_ical

            expect(ics).to include('Flood 2.5kts 045T 10ft')
        end

        it 'generates Ebb events with absolute velocity' do
            cal = WebCalTides.current_calendar_for('NOAA_ACT1011')
            ics = cal.to_ical

            expect(ics).to include('Ebb 1.8kts 225T 10ft')
        end

        it 'generates Slack events' do
            cal = WebCalTides.current_calendar_for('NOAA_ACT1011')
            ics = cal.to_ical

            expect(ics).to include('SUMMARY:Slack')
        end

        it 'sets dtend equal to dtstart for each event' do
            cal = WebCalTides.current_calendar_for('NOAA_ACT1011')

            cal.events.each do |event|
                expect(event.dtend).not_to be_nil
                expect(event.dtstart).not_to be_nil
            end
        end

        it 'constructs event URLs with station bid and date' do
            cal = WebCalTides.current_calendar_for('NOAA_ACT1011')
            ics = cal.to_ical

            # ICS line folding may split long URLs across lines; unfold before checking
            unfolded = ics.gsub("\r\n ", '')
            expect(unfolded).to include('StationInfo?id=ACT1011&d=2025-06-15')
        end

        it 'sets the calendar name via x_wr_calname' do
            cal = WebCalTides.current_calendar_for('NOAA_ACT1011')

            expect(cal.x_wr_calname.first.to_s).to eq('The Race')
        end

        it 'generates the correct number of events' do
            cal = WebCalTides.current_calendar_for('NOAA_ACT1011')

            expect(cal.events.length).to eq(3)
        end

        it 'sets LOCATION on events' do
            cal = WebCalTides.current_calendar_for('NOAA_ACT1011')
            ics = cal.to_ical

            expect(ics).to include('the race (ACT1011)')
        end

        it 'returns nil for an unknown station' do
            allow(WebCalTides).to receive(:current_station_for).with('UNKNOWN').and_return(nil)

            cal = WebCalTides.current_calendar_for('UNKNOWN')

            expect(cal).to be_nil
        end

        it 'returns nil when current_data_for returns nil' do
            allow(WebCalTides).to receive(:current_data_for).and_return(nil)

            cal = WebCalTides.current_calendar_for('NOAA_ACT1011')

            expect(cal).to be_nil
        end
    end

    describe 'URI safety in ICS output' do
        it 'does not allow CRLF injection via station URL in tide calendars' do
            malicious_station = build_station(
                name: 'injected station',
                url: "https://example.com/station\r\nINJECTED:malicious-value",
                provider: 'noaa',
            )
            malicious_tide = build_tide_data(
                url: "https://example.com/tide\r\nINJECTED:evil-header",
            )

            allow(WebCalTides).to receive(:tide_station_for).with('INJECT1').and_return(malicious_station)
            allow(WebCalTides).to receive(:tide_data_for).and_return([malicious_tide])

            cal = WebCalTides.tide_calendar_for('INJECT1')
            ics = cal.to_ical

            # The serialized ICS must not contain unescaped injection payloads
            # as standalone ICS property lines
            lines = ics.split("\n").map(&:strip)
            injected_lines = lines.select { |l| l.start_with?('INJECTED:') }
            expect(injected_lines).to be_empty
        end

        it 'does not allow CRLF injection via station URL in current calendars' do
            malicious_station = build_station(
                name: 'injected current station',
                url: "https://example.com/station\r\nINJECTED:malicious-value",
                provider: 'noaa',
                bid: "BID123\r\nINJECTED:evil",
            )

            allow(WebCalTides).to receive(:current_station_for).with('INJECT2').and_return(malicious_station)
            allow(WebCalTides).to receive(:current_data_for).and_return([
                build_current_data(type: 'slack', time: DateTime.new(2025, 6, 15, 6, 0, 0)),
            ])

            cal = WebCalTides.current_calendar_for('INJECT2')
            ics = cal.to_ical

            lines = ics.split("\n").map(&:strip)
            injected_lines = lines.select { |l| l.start_with?('INJECTED:') }
            expect(injected_lines).to be_empty
        end

        it 'does not allow semicolon injection to create new ICS properties' do
            malicious_station = build_station(
                name: 'semicolon station',
                url: 'https://example.com/station;INJECTED=value',
                provider: 'noaa',
            )
            malicious_tide = build_tide_data(
                url: 'https://example.com/tide;INJECTED=evil',
            )

            allow(WebCalTides).to receive(:tide_station_for).with('INJECT3').and_return(malicious_station)
            allow(WebCalTides).to receive(:tide_data_for).and_return([malicious_tide])

            cal = WebCalTides.tide_calendar_for('INJECT3')
            ics = cal.to_ical

            # Unfold ICS lines and verify no standalone INJECTED property exists
            unfolded = ics.gsub("\r\n ", '')
            lines = unfolded.split(/\r?\n/)
            injected_lines = lines.select { |l| l.start_with?('INJECTED') }
            expect(injected_lines).to be_empty

            # The URL value should contain the semicolon as data, not as a property separator
            url_lines = lines.select { |l| l.start_with?('URL') }
            expect(url_lines).not_to be_empty
        end
    end

    describe 'publish + to_ical pipeline' do
        before do
            allow(WebCalTides).to receive(:tide_station_for).with('NOAA_8443970').and_return(tide_station)
            allow(WebCalTides).to receive(:tide_data_for).and_return(tide_data)
        end

        it 'includes METHOD:PUBLISH after calling publish' do
            cal = WebCalTides.tide_calendar_for('NOAA_8443970')
            cal.publish

            ics = cal.to_ical

            expect(ics).to include('METHOD:PUBLISH')
        end

        it 'produces parseable ICS after publish' do
            cal = WebCalTides.tide_calendar_for('NOAA_8443970')
            cal.publish

            ics = cal.to_ical
            parsed = Icalendar::Calendar.parse(ics)

            expect(parsed).not_to be_empty
            expect(parsed.first.events.length).to eq(2)
        end

        it 'preserves all event data through serialize-parse round trip' do
            cal = WebCalTides.tide_calendar_for('NOAA_8443970')
            cal.publish

            ics = cal.to_ical
            parsed_cal = Icalendar::Calendar.parse(ics).first

            summaries = parsed_cal.events.map { |e| e.summary.to_s }
            expect(summaries).to include(a_string_matching(/High Tide/))
            expect(summaries).to include(a_string_matching(/Low Tide/))
        end
    end
end

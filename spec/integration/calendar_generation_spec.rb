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

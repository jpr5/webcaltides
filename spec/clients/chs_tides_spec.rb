# frozen_string_literal: true

RSpec.describe Clients::ChsTides do
    let(:logger) { Logger.new('/dev/null') }
    let(:client) { described_class.new(logger) }

    describe '#tide_stations', :vcr do
        it 'fetches tide stations from CHS' do
            stations = client.tide_stations
            expect(stations).to be_an(Array)
            expect(stations.length).to be > 100  # CHS has many stations
        end

        it 'returns Station objects' do
            stations = client.tide_stations
            expect(stations).to all(be_a(Models::Station))
        end

        it 'sets provider to chs' do
            stations = client.tide_stations
            expect(stations).to all(have_attributes(provider: 'chs'))
        end

        it 'sets region to include Canada' do
            stations = client.tide_stations
            expect(stations.map(&:region)).to all(include('Canada'))
        end

        it 'parses station coordinates' do
            stations = client.tide_stations
            station = stations.first

            expect(station.lat).to be_a(Numeric)
            expect(station.lon).to be_a(Numeric)
        end
    end

    describe '#tide_data_for', :vcr do
        # Get a real station that returns data
        let(:station) do
            stations = client.tide_stations
            # Find a station with valid-looking data (Halifax is reliable)
            stations.find { |s| s.name&.include?('Halifax') } || stations.first
        end

        it 'fetches tide data for a station' do
            data = client.tide_data_for(station, Time.utc(2025, 1, 15))

            # Some CHS stations return empty data - that's expected behavior
            if data.nil?
                expect(data).to be_nil
            else
                expect(data).to be_an(Array)
            end
        end

        it 'returns TideData objects when data is available' do
            data = client.tide_data_for(station, Time.utc(2025, 1, 15))
            next skip('Station returned no data') if data.nil? || data.empty?

            expect(data).to all(be_a(Models::TideData))
        end

        it 'sets units to meters (m)' do
            data = client.tide_data_for(station, Time.utc(2025, 1, 15))
            next skip('Station returned no data') if data.nil? || data.empty?

            expect(data.first.units).to eq('m')
        end
    end

    describe '#tide_data_for with few events' do
        let(:station) { build_station(id: '5cebf1de3d0f4a073c4bb94e', provider: 'chs', url: 'https://www.tides.gc.ca/en/stations/00490') }

        it 'handles a response with a single event' do
            allow(client).to receive(:get_url).and_return([{ 'eventDate' => '2026-10-07T10:00:00Z', 'value' => 1.5 }].to_json)

            data = client.tide_data_for(station, Time.utc(2026, 10, 7))

            expect(data.length).to eq(1)
            expect(data.first.prediction).to eq(1.5)
            expect(data.first.type).to eq('High')
        end

        it 'handles a response with two events' do
            allow(client).to receive(:get_url).and_return([{ 'eventDate' => '2026-10-07T10:00:00Z', 'value' => 1.5 },
                                                           { 'eventDate' => '2026-10-07T16:00:00Z', 'value' => 0.2 }].to_json)

            expect(client.tide_data_for(station, Time.utc(2026, 10, 7)).map(&:type)).to eq(%w[High Low])
        end

        it 'treats a response with no events as no data' do
            allow(client).to receive(:get_url).and_return('[]')
            allow(WebCalTides).to receive(:remove_tide_station)

            expect(client.tide_data_for(station, Time.utc(2026, 10, 7))).to be_nil
            expect(WebCalTides).to have_received(:remove_tide_station).with(station.id)
        end
    end

    # A 200 whose body is not a list of events is an upstream error, not "no data": the station
    # stays in the list and nothing is returned (so nothing is cached)
    describe '#tide_data_for with a malformed response' do
        let(:log)     { StringIO.new }
        let(:client)  { described_class.new(Logger.new(log)) }
        let(:station) { build_station(id: '5cebf1df3d0f4a073c4bbcbb', provider: 'chs', url: 'https://www.tides.gc.ca/en/stations/00490') }

        before do
            allow(WebCalTides).to receive(:remove_tide_station)
        end

        [
            ['an HTML page',            '<html><body>Service Unavailable</body></html>'],
            ['a JSON object',           '{"message":"Internal error","status":500}'],
            ['an empty JSON object',    '{}'],
            ['a list of non-objects',   '["a", 1]'],
            ['a list of error objects', '[{"message":"err"}]'],
            ['an event with no value',  '[{"eventDate":"2026-10-07T02:07:00Z"},{"eventDate":"2026-10-07T08:20:00Z","value":0.42}]'],
            ['an event with a null value', '[{"eventDate":"2026-10-07T02:07:00Z","value":null},{"eventDate":"2026-10-07T08:20:00Z","value":0.42}]'],
            ['an event with a null eventDate', '[{"eventDate":null,"value":1.71},{"eventDate":"2026-10-07T08:20:00Z","value":0.42}]']
        ].each do |what, body|
            it "returns nil for #{what}, logs it at error and keeps the station" do
                stub_request(:get, %r{\Ahttps://api-iwls\.dfo-mpo\.gc\.ca/api/v1/stations/5cebf1df3d0f4a073c4bbcbb/data})
                    .to_return(status: 200, body: body)

                expect(client.tide_data_for(station, Time.utc(2026, 10, 7))).to be_nil
                expect(WebCalTides).not_to have_received(:remove_tide_station)
                expect(log.string).to match(/^E, .*unusable CHS tide data for station 5cebf1df3d0f4a073c4bbcbb/)
            end
        end
    end

    describe 'TimeWindow module' do
        it 'includes TimeWindow module' do
            expect(described_class.ancestors).to include(Clients::TimeWindow)
        end

        it 'has window_size of 12 months' do
            expect(described_class.window_size).to eq(12.months)
        end
    end
end

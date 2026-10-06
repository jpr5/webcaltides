# frozen_string_literal: true

RSpec.describe 'GET /api/stations/compare', type: :api do
    include Rack::Test::Methods

    let(:noaa_station) do
        build_station(name: 'Boston NOAA', id: 'NOAA123', provider: 'noaa')
    end

    let(:xtide_station) do
        build_station(name: 'Boston XTide', id: 'XTIDE456', provider: 'xtide')
    end

    before do
        freeze_time

        allow(WebCalTides).to receive(:tide_station_for).with('NOAA123').and_return(noaa_station)
        allow(WebCalTides).to receive(:tide_station_for).with('XTIDE456').and_return(xtide_station)
        allow(WebCalTides).to receive(:tide_station_for).with('INVALID').and_return(nil)

        allow(WebCalTides).to receive(:next_tide_events).with('NOAA123').and_return([
            { type: 'High', time: Time.current + 2.hours, height: 10.5, units: 'ft' },
            { type: 'Low', time: Time.current + 8.hours, height: 0.5, units: 'ft' }
        ])

        allow(WebCalTides).to receive(:next_tide_events).with('XTIDE456').and_return([
            { type: 'High', time: Time.current + 2.hours + 5.minutes, height: 10.3, units: 'ft' },
            { type: 'Low', time: Time.current + 8.hours + 3.minutes, height: 0.6, units: 'ft' }
        ])
    end

    context 'with valid station IDs' do
        it 'returns comparison data' do
            get '/api/stations/compare', type: 'tides', ids: ['NOAA123', 'XTIDE456']

            expect(last_response).to be_ok
            data = JSON.parse(last_response.body)

            expect(data['stations'].length).to eq(2)
        end

        it 'includes station metadata' do
            get '/api/stations/compare', type: 'tides', ids: ['NOAA123']

            data = JSON.parse(last_response.body)
            station = data['stations'].first

            expect(station['id']).to eq('NOAA123')
            expect(station['name']).to eq('Boston NOAA')
            expect(station['provider']).to eq('noaa')
        end

        it 'includes event data' do
            get '/api/stations/compare', type: 'tides', ids: ['NOAA123']

            data = JSON.parse(last_response.body)
            events = data['stations'].first['events']

            expect(events.length).to eq(2)
            expect(events.first['type']).to eq('High')
            expect(events.first['time']).to be_a(String)  # ISO8601
        end

        it 'calculates deltas between stations' do
            get '/api/stations/compare', type: 'tides', ids: ['NOAA123', 'XTIDE456']

            data = JSON.parse(last_response.body)
            alt_station = data['stations'][1]

            expect(alt_station['delta']).to be_a(Hash)
            expect(alt_station['delta']['time']).to be_a(String)
        end

        it 'includes per-event deltas' do
            get '/api/stations/compare', type: 'tides', ids: ['NOAA123', 'XTIDE456']

            data = JSON.parse(last_response.body)
            alt_station = data['stations'][1]

            expect(alt_station['event_deltas']).to be_an(Array)
        end
    end

    context 'when a station publishes no heights (e.g. a times-only BSH gauge)' do
        let(:bsh_station)   { build_station(name: 'Hörnum BSH', id: 'DE__726A', provider: 'bsh') }
        let(:ticon_station) { build_station(name: 'Hörnum TICON', id: 'T726', provider: 'ticon') }

        before do
            allow(WebCalTides).to receive(:tide_station_for).with('DE__726A').and_return(bsh_station)
            allow(WebCalTides).to receive(:tide_station_for).with('T726').and_return(ticon_station)

            allow(WebCalTides).to receive(:next_tide_events).with('DE__726A').and_return([
                { type: 'High', time: Time.current + 2.hours, height: nil, units: nil },
                { type: 'Low',  time: Time.current + 8.hours, height: nil, units: nil }
            ])
            allow(WebCalTides).to receive(:next_tide_events).with('T726').and_return([
                { type: 'High', time: Time.current + 2.hours + 12.minutes, height: 3.8, units: 'm' },
                { type: 'Low',  time: Time.current + 8.hours + 4.minutes,  height: 0.6, units: 'm' }
            ])
        end

        it 'reports time deltas but no height delta when the primary has no heights' do
            get '/api/stations/compare', type: 'tides', ids: ['DE__726A', 'T726']

            alt = JSON.parse(last_response.body)['stations'][1]

            expect(alt['event_deltas'].map { |d| d['time'] }).to eq(['+12min', '+4min'])
            expect(alt['event_deltas'].map { |d| d['raw_value'] }).to eq([nil, nil])
            expect(alt['event_deltas'].map { |d| d['units'] }).to eq([nil, nil])
            expect(alt['delta']).to eq('time' => '+12min', 'raw_value' => nil, 'units' => nil)
        end

        it 'reports time deltas but no height delta when the alternative has no heights' do
            get '/api/stations/compare', type: 'tides', ids: ['T726', 'DE__726A']

            alt = JSON.parse(last_response.body)['stations'][1]

            expect(alt['event_deltas'].map { |d| d['time'] }).to eq(['-12min', '-4min'])
            expect(alt['event_deltas'].map { |d| d['raw_value'] }).to eq([nil, nil])
            expect(alt['delta']).to eq('time' => '-12min', 'raw_value' => nil, 'units' => nil)
        end
    end

    context 'with invalid type' do
        it 'returns error for invalid type' do
            get '/api/stations/compare', type: 'invalid', ids: ['NOAA123']

            data = JSON.parse(last_response.body)
            expect(data['error']).to eq('Invalid type')
        end
    end

    context 'with no station IDs' do
        it 'returns error' do
            get '/api/stations/compare', type: 'tides'

            data = JSON.parse(last_response.body)
            expect(data['error']).to eq('No station IDs provided')
        end
    end

    context 'with too many station IDs' do
        it 'returns error for more than 5 stations' do
            get '/api/stations/compare', type: 'tides', ids: ['1', '2', '3', '4', '5', '6']

            data = JSON.parse(last_response.body)
            expect(data['error']).to eq('Maximum 5 stations allowed')
        end
    end

    context 'with invalid station IDs' do
        it 'returns error when no valid stations found' do
            get '/api/stations/compare', type: 'tides', ids: ['INVALID']

            data = JSON.parse(last_response.body)
            expect(data['error']).to eq('No valid stations found')
        end
    end

    context 'with currents type' do
        let(:current_station) do
            build_station(name: 'Cape Cod', id: 'CURR1', bid: 'CURR1_10', provider: 'noaa', depth: 10)
        end

        before do
            allow(WebCalTides).to receive(:current_station_for).with('CURR1').and_return(current_station)
            allow(WebCalTides).to receive(:next_current_events).with('CURR1').and_return([
                { type: 'Flood', time: Time.current + 2.hours, velocity: 2.5 },
                { type: 'Slack', time: Time.current + 5.hours }
            ])
        end

        it 'returns current station data' do
            get '/api/stations/compare', type: 'currents', ids: ['CURR1']

            expect(last_response).to be_ok
            data = JSON.parse(last_response.body)

            expect(data['stations'].first['id']).to eq('CURR1')
            expect(data['stations'].first['depth']).to eq(10)
        end
    end
end

RSpec.describe 'GET /api/stations/compare with heights above different datums', type: :api do
    include Rack::Test::Methods

    before do
        freeze_time
        allow(WebCalTides).to receive(:tide_station_for).with('NL__stavenisse').and_return(build_station(id: 'NL__stavenisse', provider: 'rws'))
        allow(WebCalTides).to receive(:tide_station_for).with('T1').and_return(build_station(id: 'T1', provider: 'ticon'))
        allow(WebCalTides).to receive(:next_tide_events).with('NL__stavenisse').and_return([
            { type: 'High', time: Time.current + 2.hours, height: 1.58, units: 'm', datum: 'NAP' }
        ])
        allow(WebCalTides).to receive(:next_tide_events).with('T1').and_return([
            { type: 'High', time: Time.current + 2.hours + 20.minutes, height: 3.1, units: 'm' }
        ])
    end

    it 'compares the times but not RWS heights above NAP with chart datum heights' do
        get '/api/stations/compare', type: 'tides', ids: ['NL__stavenisse', 'T1']
        data = JSON.parse(last_response.body)

        expect(data['stations'][0]['events'][0]['datum']).to eq('NAP')
        expect(data['stations'][1]['event_deltas']).to eq([{ 'type' => 'High', 'time' => '+20min', 'raw_value' => nil, 'units' => nil }])
    end
end

RSpec.describe WebCalTides do
    describe '.next_tide_events for a Rijkswaterstaat station' do
        let(:tides) do
            [build_tide_data(type: 'High', units: 'm', prediction: 1.58, time: DateTime.new(2026, 10, 24, 13, 9)),
             build_tide_data(type: 'Low',  units: 'm', prediction: -1.19, time: DateTime.new(2026, 10, 24, 19, 14))]
        end

        before do
            allow(described_class).to receive(:tide_data_for).and_return(tides)
            allow(described_class).to receive(:timezone_for).and_return('Europe/Amsterdam')
        end

        it 'names the NAP datum on RWS events only' do
            allow(described_class).to receive(:tide_station_for).and_return(build_station(id: 'NL__stavenisse', provider: 'rws'))
            Timecop.freeze(Time.utc(2026, 10, 24, 12)) do
                expect(described_class.next_tide_events('NL__stavenisse').map { |e| e[:datum] }).to eq(%w[NAP NAP])
            end

            allow(described_class).to receive(:tide_station_for).and_return(build_station(id: 'T1', provider: 'ticon'))
            Timecop.freeze(Time.utc(2026, 10, 24, 12)) do
                expect(described_class.next_tide_events('T1')).to all(satisfy { |e| !e.key?(:datum) })
            end
        end
    end

    describe '.compute_variance with heights above different datums' do
        let(:rws)   { build_station(id: 'NL__stavenisse', provider: 'rws') }
        let(:ticon) { build_station(id: 'T1', provider: 'ticon') }
        let(:other) { build_station(id: 'T2', provider: 'ticon') }

        before do
            freeze_time
            allow(described_class).to receive(:next_tide_events).with('NL__stavenisse', anything).and_return([{ type: 'High', time: Time.current, height: 1.58, units: 'm', datum: 'NAP' }])
            allow(described_class).to receive(:next_tide_events).with('T1', anything).and_return([{ type: 'High', time: Time.current + 20.minutes, height: 3.1, units: 'm' }])
            allow(described_class).to receive(:next_tide_events).with('T2', anything).and_return([{ type: 'High', time: Time.current + 5.minutes, height: 3.0, units: 'm' }])
        end

        it 'gives the time delta but no height delta between NAP and chart datum heights' do
            expect(described_class.compute_variance(rws, [ticon])).to eq('T1' => { time: '+20min', height: nil })
        end

        it 'still compares heights above the same datum' do
            expect(described_class.compute_variance(other, [ticon])).to eq('T1' => { time: '+15min', height: '+0.1m' })
        end
    end
end

# frozen_string_literal: true

RSpec.describe WebCalTides do
    describe '.find_tide_stations' do
        before do
            # Mock the tide_stations method to return predictable test data
            allow(described_class).to receive(:tide_stations).and_return([
                build_station(name: 'Boston Harbor', id: 'NOAA123', region: 'Massachusetts, USA', public_id: 'BOS'),
                build_station(name: 'Boston Inner Harbor', id: 'NOAA124', region: 'Massachusetts, USA', public_id: 'BOSI'),
                build_station(name: 'Portland', id: 'NOAA456', region: 'Maine, USA', public_id: 'PORT'),
                build_station(name: 'Halifax', id: 'CHS001', region: 'Nova Scotia, Canada', public_id: 'HAL')
            ])
        end

        context 'with name search' do
            it 'finds stations by name' do
                results = described_class.find_tide_stations(by: ['boston'])
                expect(results.length).to eq(2)
                expect(results.map(&:name)).to all(include('Boston'))
            end

            it 'is case insensitive' do
                results = described_class.find_tide_stations(by: ['BOSTON'])
                expect(results.length).to eq(2)
            end

            it 'finds stations by partial name' do
                results = described_class.find_tide_stations(by: ['port'])
                expect(results.length).to eq(1)
                expect(results.first.name).to eq('Portland')
            end
        end

        context 'with region search' do
            it 'finds stations by region' do
                results = described_class.find_tide_stations(by: ['massachusetts'])
                expect(results.length).to eq(2)
            end

            it 'finds Canadian stations' do
                results = described_class.find_tide_stations(by: ['canada'])
                expect(results.length).to eq(1)
                expect(results.first.name).to eq('Halifax')
            end
        end

        context 'with ID search' do
            it 'finds stations by exact ID' do
                results = described_class.find_tide_stations(by: ['NOAA123'])
                expect(results.length).to eq(1)
                expect(results.first.id).to eq('NOAA123')
            end

            it 'finds stations by public_id' do
                results = described_class.find_tide_stations(by: ['bos'])
                expect(results.length).to eq(2)
            end
        end

        context 'with multiple search terms' do
            it 'requires all terms to match' do
                results = described_class.find_tide_stations(by: ['boston', 'inner'])
                expect(results.length).to eq(1)
                expect(results.first.name).to eq('Boston Inner Harbor')
            end

            it 'returns empty when terms conflict' do
                results = described_class.find_tide_stations(by: ['boston', 'portland'])
                expect(results).to be_empty
            end
        end

        context 'with nil/empty input' do
            it 'returns all stations for nil' do
                results = described_class.find_tide_stations(by: nil)
                expect(results.length).to eq(4)
            end

            it 'returns all stations for empty string' do
                results = described_class.find_tide_stations(by: [''])
                expect(results.length).to eq(4)
            end
        end
    end

    describe '.find_current_stations' do
        before do
            allow(described_class).to receive(:current_stations).and_return([
                build_station(name: 'Cape Cod Canal', id: 'CURR1', bid: 'CURR1_10', region: 'Massachusetts, USA'),
                build_station(name: 'Boston Harbor Entrance', id: 'CURR2', bid: 'CURR2_15', region: 'Massachusetts, USA'),
                build_station(name: 'Portland Head', id: 'CURR3', bid: 'CURR3_20', region: 'Maine, USA')
            ])
        end

        context 'with name search' do
            it 'finds current stations by name' do
                results = described_class.find_current_stations(by: ['cape'])
                expect(results.length).to eq(1)
                expect(results.first.name).to eq('Cape Cod Canal')
            end
        end

        context 'with BID search' do
            it 'finds stations by bid prefix' do
                results = described_class.find_current_stations(by: ['curr1'])
                expect(results.length).to eq(1)
            end
        end
    end

    describe '.find_tide_stations_by_gps' do
        before do
            allow(described_class).to receive(:tide_stations).and_return([
                build_station(name: 'Boston', id: 'BOS', lat: 42.3601, lon: -71.0589),
                build_station(name: 'Portland', id: 'PORT', lat: 43.6615, lon: -70.2553),
                build_station(name: 'Halifax', id: 'HAL', lat: 44.6476, lon: -63.5728)
            ])
        end

        it 'finds stations within radius' do
            # Search near Boston
            results = described_class.find_tide_stations_by_gps(42.36, -71.06, within: 10, units: 'mi')

            expect(results.length).to eq(1)
            expect(results.first.name).to eq('Boston')
        end

        it 'returns multiple stations in larger radius' do
            # Search near Boston with larger radius
            results = described_class.find_tide_stations_by_gps(42.36, -71.06, within: 150, units: 'mi')

            expect(results.length).to eq(2)  # Boston and Portland
        end

        it 'supports metric units' do
            results = described_class.find_tide_stations_by_gps(42.36, -71.06, within: 20, units: 'km')
            expect(results.length).to eq(1)
        end
    end

    describe '.group_search_results' do
        let(:noaa_boston) { build_station(name: 'Boston', provider: 'noaa', lat: 42.36, lon: -71.06) }
        let(:xtide_boston) { build_station(name: 'Boston', provider: 'xtide', lat: 42.3601, lon: -71.0601) }
        let(:portland) { build_station(name: 'Portland', provider: 'noaa', lat: 43.66, lon: -70.25) }

        it 'groups and returns StationGroup objects' do
            groups = described_class.group_search_results([noaa_boston, xtide_boston, portland])

            expect(groups.length).to eq(2)
            expect(groups).to all(be_a(described_class::StationGroup))
        end

        it 'sets primary station correctly' do
            groups = described_class.group_search_results([xtide_boston, noaa_boston])

            expect(groups.first.primary.provider).to eq('noaa')
        end
    end
end

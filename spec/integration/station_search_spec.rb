# frozen_string_literal: true

RSpec.describe WebCalTides do
    describe '.find_tide_stations with LINZ ports' do
        let(:linz)  { Clients::LinzTides.new(Logger.new('/dev/null')).tide_stations }
        let(:ticon) { build_station(name: 'Auckland, NZL', id: 'T9162534', region: 'NZL', public_id: 'T9162534', provider: 'ticon') }

        before { allow(described_class).to receive(:tide_stations).and_return(linz + [ticon]) }

        def ids(*tokens)
            described_class.find_tide_stations(by: tokens).map(&:id)
        end

        it 'finds a port by its macronised name, its plain-ASCII spelling, or LINZ header spelling' do
            expect(ids('whakatāne')).to eq(['NZ__whakatane'])
            expect(ids('whakatane')).to eq(['NZ__whakatane'])
            expect(ids('kaikoura')).to eq(['NZ__kaikoura'])
            expect(ids('oban')).to eq(['NZ__halfmoon-bay-oban'])
        end

        it 'finds the LINZ port alongside TICON with an "NZ" or "NZL" qualifier, and with "New Zealand"' do
            expect(ids('auckland', 'nz')).to eq(['NZ__auckland', 'T9162534'])
            expect(ids('auckland', 'nzl')).to eq(['NZ__auckland', 'T9162534'])
            expect(ids('auckland', 'new', 'zealand')).to eq(['NZ__auckland'])
        end
    end

    describe '.find_tide_stations with Marine Institute stations' do
        let(:imi) do
            VCR.use_cassette('Clients_MarineInstituteTides/stationlist', record: :none) do
                Clients::MarineInstituteTides.new(Logger.new('/dev/null')).tide_stations
            end
        end
        let(:ticon)  { build_station(name: 'Dublin Port, IRL', id: 'Tc4beed3', region: 'IRL', public_id: 'Tc4beed3', provider: 'ticon') }
        let(:noaa)   { build_station(name: 'CORKSCREW SLOUGH,S.F.BAY', id: '9414505', region: 'San Francisco Bay', public_id: '9414505') }

        before { allow(described_class).to receive(:tide_stations).and_return([noaa] + imi + [ticon]) }

        def ids(*tokens)
            described_class.find_tide_stations(by: tokens).map(&:id)
        end

        it 'finds a station by name, by common spelling, and by county' do
            expect(ids('killybegs')).to eq(['IE__Killybegs'])
            expect(ids('buncrana')).to eq(['IE__Buncranna'])
            expect(ids('cork', 'ireland')).to eq(%w[IE__Ballycotton IE__Castletownbere IE__Crosshaven IE__Kinsale IE__Ringaskiddy IE__Union_Hall])
        end

        it 'finds the MI station alongside TICON with an "IRL" qualifier, and with "Ireland"' do
            expect(ids('dublin', 'port', 'irl')).to eq(['IE__Dublin_Port', 'Tc4beed3'])
            expect(ids('dublin', 'port', 'ireland')).to eq(['IE__Dublin_Port'])
        end
    end

    describe '.find_tide_stations with Rijkswaterstaat stations' do
        let(:rws) do
            VCR.use_cassette('Clients_RijkswaterstaatTides/stationlist', record: :none) do
                Clients::RijkswaterstaatTides.new(Logger.new('/dev/null')).tide_stations
            end
        end
        let(:ticon) do
            [['Stavenisse, NLD', 'Tdb6d7c0'], ['Vlissingen, NLD', 'Tf72e951'], ['Hoek van Holland, NLD', 'T3ee6a65'],
             ['Den Helder, NLD', 'Tf2bcd2c'], ['Harlingen, NLD', 'T51a982b']].map do |name, id|
                build_station(name: name, id: id, region: 'NLD', location: name, public_id: id, provider: 'ticon')
            end
        end

        # The station list request carries today's date, so replay it on the day it was recorded
        before { Timecop.freeze(Time.utc(2026, 10, 6, 12)) }
        after  { Timecop.return }

        # As the app builds the list: agency sources before the harmonic ones
        before { allow(described_class).to receive(:tide_stations).and_return(rws + ticon) }

        def ids(*tokens)
            described_class.find_tide_stations(by: tokens).map(&:id)
        end

        it 'finds the RWS station before TICON by place name, including names RWS qualifies ("Den Helder, Marsdiep")' do
            expect(ids('stavenisse')).to eq(%w[NL__stavenisse Tdb6d7c0])
            expect(ids('vlissingen')).to eq(%w[NL__vlissingen Tf72e951])
            expect(ids('hoek', 'van', 'holland')).to eq(%w[NL__hoekvanholland T3ee6a65])
            expect(ids('den', 'helder')).to eq(%w[NL__denhelder.marsdiep Tf2bcd2c])
            expect(ids('harlingen')).to eq(%w[NL__harlingen.waddenzee T51a982b])
        end

        it 'finds the RWS station alongside TICON with an "NLD" qualifier, and with "Netherlands"' do
            expect(ids('stavenisse', 'nld')).to eq(%w[NL__stavenisse Tdb6d7c0])
            expect(ids('stavenisse', 'netherlands')).to eq(%w[NL__stavenisse])
        end
    end

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

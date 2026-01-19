# frozen_string_literal: true

RSpec.describe WebCalTides, '.group_stations_by_proximity' do
    describe 'station grouping' do
        let(:noaa_station) do
            build_station(
                name: 'Boston Harbor',
                id: 'NOAA123',
                provider: 'noaa',
                lat: 42.3601,
                lon: -71.0589
            )
        end

        let(:xtide_station) do
            build_station(
                name: 'Boston Harbor',
                id: 'X1234567',
                provider: 'xtide',
                lat: 42.3602,  # Very close (within 200m)
                lon: -71.0590
            )
        end

        let(:distant_station) do
            build_station(
                name: 'Portland',
                id: 'NOAA456',
                provider: 'noaa',
                lat: 43.6615,  # Far away (different city)
                lon: -70.2553
            )
        end

        context 'with stations within grouping threshold' do
            it 'groups nearby stations together' do
                groups = described_class.group_stations_by_proximity([noaa_station, xtide_station])

                expect(groups.length).to eq(1)
                expect(groups.first.primary).to eq(noaa_station)
                expect(groups.first.alternatives).to include(xtide_station)
            end

            it 'selects primary based on provider hierarchy' do
                # NOAA should be preferred over XTide
                groups = described_class.group_stations_by_proximity([xtide_station, noaa_station])

                expect(groups.first.primary.provider).to eq('noaa')
            end
        end

        context 'with stations beyond grouping threshold' do
            it 'keeps distant stations separate' do
                groups = described_class.group_stations_by_proximity([noaa_station, distant_station])

                expect(groups.length).to eq(2)
            end
        end

        context 'with empty input' do
            it 'returns empty array' do
                groups = described_class.group_stations_by_proximity([])
                expect(groups).to eq([])
            end

            it 'handles nil input' do
                groups = described_class.group_stations_by_proximity(nil)
                expect(groups).to eq([])
            end
        end

        context 'with custom threshold' do
            it 'respects custom distance threshold' do
                # 50m threshold should still group these (they're about 10m apart)
                groups = described_class.group_stations_by_proximity(
                    [noaa_station, xtide_station],
                    threshold_m: 50
                )
                expect(groups.length).to eq(1)
            end

            it 'separates stations with very small threshold' do
                # 1m threshold should separate them
                groups = described_class.group_stations_by_proximity(
                    [noaa_station, xtide_station],
                    threshold_m: 1
                )
                expect(groups.length).to eq(2)
            end
        end

        context 'with current stations and depth matching' do
            let(:shallow_current) do
                build_station(
                    name: 'Boston Current',
                    id: 'CURR1',
                    bid: 'CURR1_10',
                    provider: 'noaa',
                    lat: 42.3601,
                    lon: -71.0589,
                    depth: 10
                )
            end

            let(:deep_current) do
                build_station(
                    name: 'Boston Current',
                    id: 'CURR2',
                    bid: 'CURR2_50',
                    provider: 'noaa',
                    lat: 42.3602,
                    lon: -71.0590,
                    depth: 50
                )
            end

            it 'groups by location only when match_depth is false' do
                groups = described_class.group_stations_by_proximity(
                    [shallow_current, deep_current],
                    match_depth: false
                )
                expect(groups.length).to eq(1)
            end

            it 'separates by depth when match_depth is true' do
                groups = described_class.group_stations_by_proximity(
                    [shallow_current, deep_current],
                    match_depth: true
                )
                expect(groups.length).to eq(2)
            end
        end
    end

    describe 'StationGroup' do
        let(:primary) { build_station(name: 'Primary', id: 'P1', provider: 'noaa') }
        let(:alternative) { build_station(name: 'Alt', id: 'A1', provider: 'xtide') }

        it 'reports has_alternatives? correctly' do
            group_with = described_class::StationGroup.new(primary: primary, alternatives: [alternative], deltas: {})
            group_without = described_class::StationGroup.new(primary: primary, alternatives: [], deltas: {})

            expect(group_with.has_alternatives?).to be true
            expect(group_without.has_alternatives?).to be false
        end

        it 'converts to hash' do
            group = described_class::StationGroup.new(primary: primary, alternatives: [alternative], deltas: {})
            hash = group.to_h

            expect(hash[:primary]).to eq(primary)
            expect(hash[:alternatives]).to eq([alternative])
            expect(hash[:deltas]).to eq({})
        end
    end

    describe 'provider hierarchy' do
        it 'defines PROVIDER_HIERARCHY constant' do
            expect(described_class::PROVIDER_HIERARCHY).to eq(%w[noaa chs xtide ticon])
        end

        it 'prefers NOAA over CHS' do
            noaa = build_station(provider: 'noaa', lat: 42.0, lon: -71.0)
            chs = build_station(provider: 'chs', lat: 42.0001, lon: -71.0001)

            groups = described_class.group_stations_by_proximity([chs, noaa])
            expect(groups.first.primary.provider).to eq('noaa')
        end

        it 'prefers CHS over XTide' do
            chs = build_station(provider: 'chs', lat: 42.0, lon: -71.0)
            xtide = build_station(provider: 'xtide', lat: 42.0001, lon: -71.0001)

            groups = described_class.group_stations_by_proximity([xtide, chs])
            expect(groups.first.primary.provider).to eq('chs')
        end

        it 'prefers XTide over TICON' do
            xtide = build_station(provider: 'xtide', lat: 42.0, lon: -71.0)
            ticon = build_station(provider: 'ticon', lat: 42.0001, lon: -71.0001)

            groups = described_class.group_stations_by_proximity([ticon, xtide])
            expect(groups.first.primary.provider).to eq('xtide')
        end
    end
end

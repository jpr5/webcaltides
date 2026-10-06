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
            expect(described_class::PROVIDER_HIERARCHY).to eq(%w[noaa chs bsh kartverket linz xtide ticon])
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

        it 'prefers BSH over TICON' do
            # Cranz: BSH gauge 717P and TICON T310a3db are ~15m apart
            bsh = build_station(provider: 'bsh', id: 'DE__717P', lat: 53.53583, lon: 9.79167)
            ticon = build_station(provider: 'ticon', id: 'T310a3db', lat: 53.53593513, lon: 9.79152582)

            groups = described_class.group_stations_by_proximity([ticon, bsh])
            expect(groups.length).to eq(1)
            expect(groups.first.primary.provider).to eq('bsh')
            expect(groups.first.alternatives.map(&:provider)).to eq(['ticon'])
        end

        it 'ranks BSH first even for a gauge that publishes no heights' do
            # Deliberate trade-off (see PROVIDER_HIERARCHY): BSH's official times win over
            # harmonic heights, so a times-only BSH gauge is still the primary.  Ranking is by
            # provider alone and never looks at predictions.
            expect(described_class).not_to receive(:next_tide_events)

            bsh = build_station(provider: 'bsh', id: 'DE__726A', lat: 54.7586, lon: 8.2975)
            xtide = build_station(provider: 'xtide', lat: 54.7587, lon: 8.2976)
            ticon = build_station(provider: 'ticon', lat: 54.7585, lon: 8.2974)

            groups = described_class.group_stations_by_proximity([ticon, xtide, bsh])
            expect(groups.length).to eq(1)
            expect(groups.first.primary.provider).to eq('bsh')
            expect(groups.first.alternatives.map(&:provider)).to eq(%w[xtide ticon])
        end

        it 'prefers Kartverket over TICON' do
            # Bergen: Kartverket gauge BGO and TICON T01f7ba9 share the same coordinates
            kartverket = build_station(provider: 'kartverket', id: 'NO__BGO', lat: 60.398046, lon: 5.320487)
            ticon      = build_station(provider: 'ticon', id: 'T01f7ba9', lat: 60.398046, lon: 5.320487)

            groups = described_class.group_stations_by_proximity([ticon, kartverket])
            expect(groups.length).to eq(1)
            expect(groups.first.primary.provider).to eq('kartverket')
            expect(groups.first.alternatives.map(&:provider)).to eq(['ticon'])
        end

        it 'registers Kartverket as a tide client' do
            expect(described_class.tide_clients(:kartverket)).to be_a(Clients::KartverketTides)
        end

        it 'prefers LINZ over TICON' do
            # Auckland: LINZ port 070 and TICON T9162534 share the same (rounded) coordinates
            linz  = build_station(provider: 'linz', id: 'NZ__auckland', lat: -36.85, lon: 174.7667)
            ticon = build_station(provider: 'ticon', id: 'T9162534', lat: -36.85, lon: 174.767)

            groups = described_class.group_stations_by_proximity([ticon, linz])
            expect(groups.length).to eq(1)
            expect(groups.first.primary.provider).to eq('linz')
            expect(groups.first.alternatives.map(&:provider)).to eq(['ticon'])
        end

        it 'registers LINZ as a tide client' do
            expect(described_class.tide_clients(:linz)).to be_a(Clients::LinzTides)
        end

        it 'prefers XTide over TICON' do
            xtide = build_station(provider: 'xtide', lat: 42.0, lon: -71.0)
            ticon = build_station(provider: 'ticon', lat: 42.0001, lon: -71.0001)

            groups = described_class.group_stations_by_proximity([ticon, xtide])
            expect(groups.first.primary.provider).to eq('xtide')
        end
    end
end

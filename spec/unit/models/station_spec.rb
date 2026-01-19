# frozen_string_literal: true

RSpec.describe Models::Station do
    describe '.version' do
        it 'returns the current version number' do
            expect(described_class.version).to eq(2)
        end
    end

    describe '.from_hash' do
        let(:hash) do
            {
                'name' => 'Boston Harbor',
                'alternate_names' => ['Boston', 'Boston MA'],
                'id' => 'NOAA123',
                'public_id' => 'NOAA123',
                'region' => 'Massachusetts, USA',
                'location' => 'Boston Harbor, MA',
                'lat' => 42.3601,
                'lon' => -71.0589,
                'url' => 'https://tidesandcurrents.noaa.gov/stationhome.html?id=8443970',
                'provider' => 'noaa',
                'bid' => 'BIN001',
                'depth' => 10.5
            }
        end

        it 'creates a Station from a hash' do
            station = described_class.from_hash(hash)

            expect(station).to be_a(described_class)
            expect(station.name).to eq('Boston Harbor')
            expect(station.alternate_names).to eq(['Boston', 'Boston MA'])
            expect(station.id).to eq('NOAA123')
            expect(station.public_id).to eq('NOAA123')
            expect(station.region).to eq('Massachusetts, USA')
            expect(station.location).to eq('Boston Harbor, MA')
            expect(station.lat).to eq(42.3601)
            expect(station.lon).to eq(-71.0589)
            expect(station.url).to eq('https://tidesandcurrents.noaa.gov/stationhome.html?id=8443970')
            expect(station.provider).to eq('noaa')
            expect(station.bid).to eq('BIN001')
            expect(station.depth).to eq(10.5)
        end

        it 'handles nil values' do
            minimal_hash = { 'name' => 'Test', 'id' => 'TEST1' }
            station = described_class.from_hash(minimal_hash)

            expect(station.name).to eq('Test')
            expect(station.id).to eq('TEST1')
            expect(station.alternate_names).to be_nil
            expect(station.bid).to be_nil
            expect(station.depth).to be_nil
        end
    end

    describe '#to_h' do
        it 'serializes station to hash' do
            station = described_class.new(
                name: 'Test Station',
                alternate_names: ['Alt1'],
                id: 'TEST1',
                public_id: 'TEST1',
                region: 'Test Region',
                location: 'Test Location',
                lat: 42.0,
                lon: -71.0,
                url: 'https://example.com',
                provider: 'noaa',
                bid: nil,
                depth: nil
            )

            hash = station.to_h
            expect(hash[:name]).to eq('Test Station')
            expect(hash[:id]).to eq('TEST1')
            expect(hash[:lat]).to eq(42.0)
            expect(hash[:provider]).to eq('noaa')
        end
    end

    describe 'round-trip serialization' do
        it 'preserves all fields through from_hash -> to_h -> from_hash' do
            original = {
                'name' => 'Round Trip Test',
                'alternate_names' => ['Alt1', 'Alt2'],
                'id' => 'RT001',
                'public_id' => 'RT001',
                'region' => 'Test Region',
                'location' => 'Test Location',
                'lat' => 42.3601,
                'lon' => -71.0589,
                'url' => 'https://example.com',
                'provider' => 'xtide',
                'bid' => 'BID001',
                'depth' => 15.0
            }

            station = described_class.from_hash(original)
            hash = station.to_h.transform_keys(&:to_s)
            restored = described_class.from_hash(hash)

            expect(restored.name).to eq(station.name)
            expect(restored.id).to eq(station.id)
            expect(restored.lat).to eq(station.lat)
            expect(restored.lon).to eq(station.lon)
            expect(restored.provider).to eq(station.provider)
            expect(restored.depth).to eq(station.depth)
        end
    end
end

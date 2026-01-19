# frozen_string_literal: true

RSpec.describe Models::CurrentData do
    describe '.version' do
        it 'returns the current version number' do
            expect(described_class.version).to eq(2)
        end
    end

    describe '.from_hash' do
        context 'with API-style keys' do
            let(:api_hash) do
                {
                    'Bin' => '1',
                    'Type' => 'flood',
                    'meanFloodDir' => '045',
                    'meanEbbDir' => '225',
                    'Time' => '2025-06-15T10:30:00+00:00',
                    'Depth' => '10',
                    'Velocity_Major' => 2.5,
                    'Url' => 'https://example.com/current'
                }
            end

            it 'creates CurrentData from API hash' do
                current = described_class.from_hash(api_hash)

                expect(current).to be_a(described_class)
                expect(current.bin).to eq('1')
                expect(current.type).to eq('flood')
                expect(current.mean_flood_dir).to eq('045')
                expect(current.mean_ebb_dir).to eq('225')
                expect(current.time).to be_a(DateTime)
                expect(current.depth).to eq('10')
                expect(current.velocity_major).to eq(2.5)
                expect(current.url).to eq('https://example.com/current')
            end
        end

        context 'with cached/internal keys' do
            let(:cached_hash) do
                {
                    'bin' => '2',
                    'type' => 'ebb',
                    'mean_flood_dir' => '090',
                    'mean_ebb_dir' => '270',
                    'time' => '2025-06-15T16:45:00+00:00',
                    'depth' => '25',
                    'velocity_major' => -1.8,
                    'url' => 'https://example.com/current'
                }
            end

            it 'creates CurrentData from cached hash' do
                current = described_class.from_hash(cached_hash)

                expect(current.bin).to eq('2')
                expect(current.type).to eq('ebb')
                expect(current.mean_flood_dir).to eq('090')
                expect(current.mean_ebb_dir).to eq('270')
                expect(current.depth).to eq('25')
                expect(current.velocity_major).to eq(-1.8)
            end
        end

        it 'parses string time to DateTime' do
            hash = { 'time' => '2025-12-25T00:00:00Z', 'type' => 'slack' }
            current = described_class.from_hash(hash)

            expect(current.time).to be_a(DateTime)
            expect(current.time.month).to eq(12)
            expect(current.time.day).to eq(25)
        end

        it 'handles slack current type' do
            hash = { 'type' => 'slack', 'time' => '2025-06-15T12:00:00Z' }
            current = described_class.from_hash(hash)

            expect(current.type).to eq('slack')
        end
    end

    describe '#to_h' do
        it 'serializes CurrentData to hash' do
            current = described_class.new(
                bin: '1',
                type: 'flood',
                mean_flood_dir: '045',
                mean_ebb_dir: '225',
                time: DateTime.new(2025, 6, 15, 10, 30, 0),
                depth: '10',
                velocity_major: 2.5,
                url: 'https://example.com'
            )

            hash = current.to_h
            expect(hash[:bin]).to eq('1')
            expect(hash[:type]).to eq('flood')
            expect(hash[:velocity_major]).to eq(2.5)
        end
    end

    describe 'round-trip serialization' do
        it 'preserves all fields through from_hash -> to_h -> from_hash' do
            original = {
                'bin' => '3',
                'type' => 'flood',
                'mean_flood_dir' => '135',
                'mean_ebb_dir' => '315',
                'time' => '2025-08-20T08:15:00+00:00',
                'depth' => '50',
                'velocity_major' => 3.2,
                'url' => 'https://example.com/current'
            }

            current = described_class.from_hash(original)
            hash = current.to_h.transform_keys(&:to_s)
            hash['time'] = hash['time'].iso8601
            restored = described_class.from_hash(hash)

            expect(restored.bin).to eq(current.bin)
            expect(restored.type).to eq(current.type)
            expect(restored.mean_flood_dir).to eq(current.mean_flood_dir)
            expect(restored.mean_ebb_dir).to eq(current.mean_ebb_dir)
            expect(restored.depth).to eq(current.depth)
            expect(restored.velocity_major).to eq(current.velocity_major)
        end
    end
end

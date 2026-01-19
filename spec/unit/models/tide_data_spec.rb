# frozen_string_literal: true

RSpec.describe Models::TideData do
    describe '.version' do
        it 'returns the current version number' do
            expect(described_class.version).to eq(1)
        end
    end

    describe '.from_hash' do
        let(:hash) do
            {
                'type' => 'High',
                'prediction' => 10.5,
                'time' => '2025-06-15T12:30:00+00:00',
                'url' => 'https://example.com/tide',
                'units' => 'ft'
            }
        end

        it 'creates a TideData from a hash' do
            tide = described_class.from_hash(hash)

            expect(tide).to be_a(described_class)
            expect(tide.type).to eq('High')
            expect(tide.prediction).to eq(10.5)
            expect(tide.time).to be_a(DateTime)
            expect(tide.time.year).to eq(2025)
            expect(tide.time.month).to eq(6)
            expect(tide.time.day).to eq(15)
            expect(tide.time.hour).to eq(12)
            expect(tide.time.min).to eq(30)
            expect(tide.url).to eq('https://example.com/tide')
            expect(tide.units).to eq('ft')
        end

        it 'parses ISO 8601 datetime strings' do
            hash['time'] = '2025-12-31T23:59:59Z'
            tide = described_class.from_hash(hash)

            expect(tide.time.year).to eq(2025)
            expect(tide.time.month).to eq(12)
            expect(tide.time.day).to eq(31)
            expect(tide.time.hour).to eq(23)
            expect(tide.time.min).to eq(59)
        end

        it 'handles Low tide type' do
            hash['type'] = 'Low'
            hash['prediction'] = 0.5
            tide = described_class.from_hash(hash)

            expect(tide.type).to eq('Low')
            expect(tide.prediction).to eq(0.5)
        end
    end

    describe '#to_h' do
        it 'serializes TideData to hash' do
            tide = described_class.new(
                type: 'High',
                prediction: 10.5,
                time: DateTime.new(2025, 6, 15, 12, 30, 0),
                url: 'https://example.com',
                units: 'ft'
            )

            hash = tide.to_h
            expect(hash[:type]).to eq('High')
            expect(hash[:prediction]).to eq(10.5)
            expect(hash[:time]).to be_a(DateTime)
            expect(hash[:units]).to eq('ft')
        end
    end

    describe 'round-trip serialization' do
        it 'preserves all fields through from_hash -> to_h -> from_hash' do
            original = {
                'type' => 'High',
                'prediction' => 8.75,
                'time' => '2025-07-04T14:00:00+00:00',
                'url' => 'https://example.com/tide',
                'units' => 'm'
            }

            tide = described_class.from_hash(original)
            # Convert DateTime to string for comparison
            hash = tide.to_h.transform_keys(&:to_s)
            hash['time'] = hash['time'].iso8601
            restored = described_class.from_hash(hash)

            expect(restored.type).to eq(tide.type)
            expect(restored.prediction).to eq(tide.prediction)
            expect(restored.units).to eq(tide.units)
            expect(restored.time.year).to eq(tide.time.year)
            expect(restored.time.month).to eq(tide.time.month)
            expect(restored.time.day).to eq(tide.time.day)
        end
    end
end

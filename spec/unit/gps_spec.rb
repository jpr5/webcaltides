# frozen_string_literal: true

RSpec.describe WebCalTides::GPS do
    describe '.normalize' do
        # Tolerance for coordinate comparison (approximately 20 meters)
        let(:tolerance) { 0.0002 }

        context 'with decimal degrees' do
            it 'parses "40.7128° N, 74.0060° W"' do
                result = described_class.normalize("40.7128° N, 74.0060° W")
                expect(result[:latitude]).to be_within(tolerance).of(40.7128)
                expect(result[:longitude]).to be_within(tolerance).of(-74.006)
            end

            it 'parses "-33.8688, 151.2093" (plain decimal)' do
                result = described_class.normalize("-33.8688, 151.2093")
                expect(result[:latitude]).to be_within(tolerance).of(-33.8688)
                expect(result[:longitude]).to be_within(tolerance).of(151.2093)
            end

            it 'parses "37.7749 N, 122.4194 W"' do
                result = described_class.normalize("37.7749 N, 122.4194 W")
                expect(result[:latitude]).to be_within(tolerance).of(37.7749)
                expect(result[:longitude]).to be_within(tolerance).of(-122.4194)
            end

            it 'parses "51.0, -0.0" (zero longitude)' do
                result = described_class.normalize("51.0, -0.0")
                expect(result[:latitude]).to be_within(tolerance).of(51.0)
                expect(result[:longitude]).to be_within(tolerance).of(0.0)
            end
        end

        context 'with degrees, minutes, seconds (DMS)' do
            it 'parses "51°30\'26\"N, 0°7\'39\"W"' do
                result = described_class.normalize("51°30'26\"N, 0°7'39\"W")
                expect(result[:latitude]).to be_within(tolerance).of(51.507222)
                expect(result[:longitude]).to be_within(tolerance).of(-0.1275)
            end

            it 'parses "48°51\'29.5\"N, 2°17\'40.2\"E"' do
                result = described_class.normalize("48°51'29.5\"N, 2°17'40.2\"E")
                expect(result[:latitude]).to be_within(tolerance).of(48.858194)
                expect(result[:longitude]).to be_within(tolerance).of(2.2945)
            end

            it 'parses "33°52\'7.7\"S, 151°12\'33.5\"E" (southern hemisphere)' do
                result = described_class.normalize("33°52'7.7\"S, 151°12'33.5\"E")
                expect(result[:latitude]).to be_within(tolerance).of(-33.868806)
                expect(result[:longitude]).to be_within(tolerance).of(151.209306)
            end
        end

        context 'with degrees and decimal minutes (DM)' do
            it 'parses "51°30.5\'N, 0°7.65\'W"' do
                result = described_class.normalize("51°30.5'N, 0°7.65'W")
                expect(result[:latitude]).to be_within(tolerance).of(51.508333)
                expect(result[:longitude]).to be_within(tolerance).of(-0.1275)
            end
        end

        context 'with various whitespace and formatting' do
            it 'parses "40 42 46.1 N, 74 0 21.6 W" (space-separated)' do
                result = described_class.normalize("40 42 46.1 N, 74 0 21.6 W")
                expect(result[:latitude]).to be_within(tolerance).of(40.712806)
                expect(result[:longitude]).to be_within(tolerance).of(-74.006)
            end

            it 'parses "40 42 46.1, -74 0 21.6" (no hemisphere indicators)' do
                result = described_class.normalize("40 42 46.1, -74 0 21.6")
                expect(result[:latitude]).to be_within(tolerance).of(40.712806)
                expect(result[:longitude]).to be_within(tolerance).of(-74.006)
            end

            it 'parses "40°42\'46.1\" N 74°0\'21.6\" W" (no comma)' do
                result = described_class.normalize("40°42'46.1\" N 74°0'21.6\" W")
                expect(result[:latitude]).to be_within(tolerance).of(40.712806)
                expect(result[:longitude]).to be_within(tolerance).of(-74.006)
            end
        end

        context 'with European decimal comma format' do
            it 'parses "51,86982°  5,31011°" (comma as decimal separator)' do
                result = described_class.normalize("51,86982°  5,31011°")
                expect(result[:latitude]).to be_within(tolerance).of(51.86982)
                expect(result[:longitude]).to be_within(tolerance).of(5.31011)
            end

            it 'parses "51,86982° N  5,31011° W" (European with hemispheres)' do
                result = described_class.normalize("51,86982° N  5,31011° W")
                expect(result[:latitude]).to be_within(tolerance).of(51.86982)
                expect(result[:longitude]).to be_within(tolerance).of(-5.31011)
            end

            it 'parses "40,7128° N, 74,0060° W" (European with comma separator)' do
                result = described_class.normalize("40,7128° N, 74,0060° W")
                expect(result[:latitude]).to be_within(tolerance).of(40.7128)
                expect(result[:longitude]).to be_within(tolerance).of(-74.006)
            end

            it 'parses "48,8584° N, 2,2945° E" (Paris in European format)' do
                result = described_class.normalize("48,8584° N, 2,2945° E")
                expect(result[:latitude]).to be_within(tolerance).of(48.8584)
                expect(result[:longitude]).to be_within(tolerance).of(2.2945)
            end
        end

        context 'with UTF-8 degree symbol variations' do
            it 'parses decimal degrees with UTF-8 degree symbol followed by hemisphere' do
                result = described_class.normalize("51.87757° N 5.30811° W")
                expect(result[:latitude]).to be_within(tolerance).of(51.87757)
                expect(result[:longitude]).to be_within(tolerance).of(-5.30811)
            end

            it 'parses decimal degrees with degree symbol but no comma separator' do
                result = described_class.normalize("40.7128° N 74.0060° W")
                expect(result[:latitude]).to be_within(tolerance).of(40.7128)
                expect(result[:longitude]).to be_within(tolerance).of(-74.006)
            end

            it 'parses lowercase hemisphere indicators' do
                result = described_class.normalize("37.7749° n, 122.4194° w")
                expect(result[:latitude]).to be_within(tolerance).of(37.7749)
                expect(result[:longitude]).to be_within(tolerance).of(-122.4194)
            end
        end

        context 'with edge cases' do
            it 'parses "0°0\'0\"N, 0°0\'0\"E" (origin)' do
                result = described_class.normalize("0°0'0\"N, 0°0'0\"E")
                expect(result[:latitude]).to be_within(tolerance).of(0.0)
                expect(result[:longitude]).to be_within(tolerance).of(0.0)
            end

            it 'parses "90°0\'0\"S, 180°0\'0\"W" (extreme south pole)' do
                result = described_class.normalize("90°0'0\"S, 180°0'0\"W")
                expect(result[:latitude]).to be_within(tolerance).of(-90.0)
                expect(result[:longitude]).to be_within(tolerance).of(-180.0)
            end
        end

        context 'with DMS format output' do
            it 'returns DMS formatted string' do
                result = described_class.normalize("40.7128, -74.006")
                expect(result[:dms]).to match(/\d+°\d+'[\d.]+\"[NS], \d+°\d+'[\d.]+\"[EW]/)
            end

            it 'returns correct hemisphere indicators' do
                result = described_class.normalize("-33.8688, 151.2093")
                expect(result[:dms]).to include('S')
                expect(result[:dms]).to include('E')
            end
        end

        context 'with invalid input' do
            it 'returns origin coordinates for unparseable text' do
                # GPS.normalize is permissive - unparseable text defaults to 0,0
                result = described_class.normalize("not a coordinate")
                expect(result[:latitude]).to eq(0.0)
                expect(result[:longitude]).to eq(0.0)
            end

            it 'raises error for empty string' do
                expect { described_class.normalize("") }.to raise_error(RuntimeError, /could not/)
            end
        end
    end
end

RSpec.describe WebCalTides, '.parse_gps' do
    context 'with valid coordinates' do
        it 'parses decimal format' do
            result = WebCalTides.parse_gps("40.7128, -74.006")
            expect(result).to eq(['40.7128', '-74.006'])
        end

        it 'parses DMS format via GPS.normalize' do
            result = WebCalTides.parse_gps("40°42'46.1\" N, 74°0'21.6\" W")
            expect(result).not_to be_nil
            expect(result.length).to eq(2)
            expect(result[0].to_f).to be_within(0.01).of(40.7128)
            expect(result[1].to_f).to be_within(0.01).of(-74.006)
        end

        it 'parses decimal degrees with UTF-8 degree symbol and hemispheres' do
            result = WebCalTides.parse_gps("51.87757° N 5.30811° W")
            expect(result).not_to be_nil
            expect(result.length).to eq(2)
            expect(result[0].to_f).to be_within(0.01).of(51.87757)
            expect(result[1].to_f).to be_within(0.01).of(-5.30811)
        end

        it 'parses European comma decimal format' do
            result = WebCalTides.parse_gps("51,86982° N 5,31011° W")
            expect(result).not_to be_nil
            expect(result.length).to eq(2)
            expect(result[0].to_f).to be_within(0.01).of(51.86982)
            expect(result[1].to_f).to be_within(0.01).of(-5.31011)
        end

        it 'parses European format without hemispheres' do
            result = WebCalTides.parse_gps("51,86982°  5,31011°")
            expect(result).not_to be_nil
            expect(result.length).to eq(2)
            expect(result[0].to_f).to be_within(0.01).of(51.86982)
            expect(result[1].to_f).to be_within(0.01).of(5.31011)
        end
    end

    context 'with invalid coordinates' do
        it 'returns nil for empty input' do
            result = WebCalTides.parse_gps("")
            expect(result).to be_nil
        end

        it 'returns nil for out-of-range latitude' do
            result = WebCalTides.parse_gps("91.0, -74.0")
            expect(result).to be_nil
        end

        it 'returns nil for out-of-range longitude' do
            result = WebCalTides.parse_gps("40.0, 181.0")
            expect(result).to be_nil
        end
    end

    context 'fallback parser and edge cases' do
        it 'uses fallback regex parser when GPS.normalize raises exception' do
            # Force GPS.normalize to raise an exception
            allow(WebCalTides::GPS).to receive(:normalize).and_raise(RuntimeError.new('test error'))

            # Fallback should handle decimal format
            result = WebCalTides.parse_gps("40.7128, -74.006")
            expect(result).to eq(['40.7128', '-74.006'])
        end

        it 'handles degree symbol with fallback parser after GPS.normalize fails' do
            # Force GPS.normalize to raise an exception
            allow(WebCalTides::GPS).to receive(:normalize).and_raise(RuntimeError.new('test error'))

            # Fallback should handle degree symbol format: 1°2.3
            result = WebCalTides.parse_gps("40°42.77, -74°0.36")
            expect(result).not_to be_nil
            expect(result[0].to_f).to be_within(0.01).of(40.7128)
            expect(result[1].to_f).to be_within(0.01).of(-74.006)
        end

        it 'converts DMS to decimal with fallback parser' do
            # Force GPS.normalize to raise an exception
            allow(WebCalTides::GPS).to receive(:normalize).and_raise(RuntimeError.new('test error'))

            # Fallback should convert "40°42.77N" to decimal
            # Note: In fallback logic, N/W are positive, S/E are negative
            result = WebCalTides.parse_gps("40°42.77N, 74°0.36W")
            expect(result).not_to be_nil
            expect(result[0].to_f).to be_within(0.01).of(40.7128)  # N stays positive
            expect(result[1].to_f).to be_within(0.01).of(74.006)   # W stays positive in fallback
        end

        it 'rejects latitude > 90' do
            result = WebCalTides.parse_gps("91.0, -74.0")
            expect(result).to be_nil
        end

        it 'rejects latitude < -90' do
            result = WebCalTides.parse_gps("-91.0, -74.0")
            expect(result).to be_nil
        end

        it 'rejects longitude > 180' do
            result = WebCalTides.parse_gps("40.0, 181.0")
            expect(result).to be_nil
        end

        it 'rejects longitude < -180' do
            result = WebCalTides.parse_gps("40.0, -181.0")
            expect(result).to be_nil
        end

        it 'accepts boundary values (90, -90, 180, -180)' do
            # Latitude boundaries
            result1 = WebCalTides.parse_gps("90.0, 0.0")
            expect(result1).to eq(['90.0', '0.0'])

            result2 = WebCalTides.parse_gps("-90.0, 0.0")
            expect(result2).to eq(['-90.0', '0.0'])

            # Longitude boundaries
            result3 = WebCalTides.parse_gps("0.0, 180.0")
            expect(result3).to eq(['0.0', '180.0'])

            result4 = WebCalTides.parse_gps("0.0, -180.0")
            expect(result4).to eq(['0.0', '-180.0'])
        end

        it 'handles valid decimal coordinates with space separator' do
            # Test that simple decimal coordinates work with space separator
            result = WebCalTides.parse_gps("51.86982 -5.30811")
            expect(result).not_to be_nil
            expect(result.length).to eq(2)
            expect(result[0].to_f).to be_within(0.01).of(51.86982)
            expect(result[1].to_f).to be_within(0.01).of(-5.30811)
        end

        it 'handles space-only separator (no comma)' do
            result = WebCalTides.parse_gps("51.87757 -5.30811")
            expect(result).to eq(['51.87757', '-5.30811'])
        end

        it 'handles fallback with cardinal directions (S/E negative conversion)' do
            # Force GPS.normalize to raise an exception
            allow(WebCalTides::GPS).to receive(:normalize).and_raise(RuntimeError.new('test error'))

            # S and E should convert to negative
            result = WebCalTides.parse_gps("33°52.13S, 151°12.56E")
            expect(result).not_to be_nil
            expect(result[0].to_f).to be < 0  # South = negative
            expect(result[1].to_f).to be < 0  # East = negative (based on fallback logic)
        end
    end
end

# frozen_string_literal: true

RSpec.describe Harmonics::Engine, '#detect_peaks' do
    let(:logger) { Logger.new('/dev/null') }
    let(:engine) { described_class.new(logger, 'spec/fixtures/cache') }

    describe 'peak detection' do
        context 'with simple sine wave data' do
            let(:predictions) do
                # Generate a simple sine wave with known peaks at 90 and 270 degrees
                (0..360).step(10).map do |deg|
                    time = Time.utc(2025, 6, 15, 0, 0, 0) + (deg * 60)
                    height = 5.0 + 3.0 * Math.sin(deg * Math::PI / 180.0)
                    { 'time' => time, 'height' => height, 'units' => 'ft' }
                end
            end

            it 'detects high and low peaks' do
                peaks = engine.detect_peaks(predictions, step_seconds: 600)

                highs = peaks.select { |p| p['type'] == 'High' }
                lows = peaks.select { |p| p['type'] == 'Low' }

                expect(highs).not_to be_empty
                expect(lows).not_to be_empty
            end

            it 'returns peaks with expected structure' do
                peaks = engine.detect_peaks(predictions, step_seconds: 600)

                expect(peaks).to all(include('type', 'height', 'time', 'units'))
                expect(peaks.map { |p| p['type'] }).to all(be_in(['High', 'Low']))
            end

            it 'identifies high peaks at local maxima' do
                peaks = engine.detect_peaks(predictions, step_seconds: 600)
                highs = peaks.select { |p| p['type'] == 'High' }

                # High peak should be around 8.0 (5.0 + 3.0)
                highs.each do |high|
                    expect(high['height']).to be_within(0.5).of(8.0)
                end
            end

            it 'identifies low peaks at local minima' do
                peaks = engine.detect_peaks(predictions, step_seconds: 600)
                lows = peaks.select { |p| p['type'] == 'Low' }

                # Low peak should be around 2.0 (5.0 - 3.0)
                lows.each do |low|
                    expect(low['height']).to be_within(0.5).of(2.0)
                end
            end
        end

        context 'with empty predictions' do
            it 'returns empty array' do
                peaks = engine.detect_peaks([])
                expect(peaks).to eq([])
            end
        end

        context 'with insufficient data points' do
            it 'returns empty array for single point' do
                single = [{ 'time' => Time.utc(2025, 6, 15), 'height' => 5.0, 'units' => 'ft' }]
                peaks = engine.detect_peaks(single)
                expect(peaks).to eq([])
            end

            it 'returns empty array for two points' do
                two = [
                    { 'time' => Time.utc(2025, 6, 15, 0), 'height' => 5.0, 'units' => 'ft' },
                    { 'time' => Time.utc(2025, 6, 15, 1), 'height' => 6.0, 'units' => 'ft' }
                ]
                peaks = engine.detect_peaks(two)
                expect(peaks).to eq([])
            end
        end

        context 'with pre-existing peak data' do
            it 'returns data as-is if already contains type field' do
                existing_peaks = [
                    { 'type' => 'High', 'time' => Time.utc(2025, 6, 15, 6), 'height' => 10.0, 'units' => 'ft' },
                    { 'type' => 'Low', 'time' => Time.utc(2025, 6, 15, 12), 'height' => 2.0, 'units' => 'ft' }
                ]

                result = engine.detect_peaks(existing_peaks)
                expect(result).to eq(existing_peaks)
            end
        end

        context 'with monotonic data' do
            it 'returns empty array for strictly increasing data' do
                increasing = (0..10).map do |i|
                    { 'time' => Time.utc(2025, 6, 15, i), 'height' => i.to_f, 'units' => 'ft' }
                end
                peaks = engine.detect_peaks(increasing)
                expect(peaks).to eq([])
            end

            it 'returns empty array for strictly decreasing data' do
                decreasing = (0..10).map do |i|
                    { 'time' => Time.utc(2025, 6, 15, i), 'height' => (10 - i).to_f, 'units' => 'ft' }
                end
                peaks = engine.detect_peaks(decreasing)
                expect(peaks).to eq([])
            end
        end

        context 'with parabolic refinement' do
            it 'refines peak times using parabolic interpolation' do
                # Create data with a peak that's not exactly at a sample point
                # The parabolic fit should shift the peak time slightly
                data = [
                    { 'time' => Time.utc(2025, 6, 15, 0, 0), 'height' => 5.0, 'units' => 'ft' },
                    { 'time' => Time.utc(2025, 6, 15, 0, 1), 'height' => 7.0, 'units' => 'ft' },
                    { 'time' => Time.utc(2025, 6, 15, 0, 2), 'height' => 8.0, 'units' => 'ft' },  # Peak area
                    { 'time' => Time.utc(2025, 6, 15, 0, 3), 'height' => 7.5, 'units' => 'ft' },
                    { 'time' => Time.utc(2025, 6, 15, 0, 4), 'height' => 6.0, 'units' => 'ft' }
                ]

                peaks = engine.detect_peaks(data, step_seconds: 60)
                expect(peaks.length).to eq(1)

                # The refined peak should be close to but not exactly at 0:02
                peak = peaks.first
                expect(peak['type']).to eq('High')
                expect(peak['height']).to be > 7.9
            end
        end
    end
end

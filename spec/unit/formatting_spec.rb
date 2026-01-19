# frozen_string_literal: true

RSpec.describe WebCalTides do
  describe '.format_time_delta' do
    context 'with small differences' do
      it 'returns "0min" for less than 30 seconds' do
        expect(described_class.format_time_delta(0)).to eq('0min')
        expect(described_class.format_time_delta(15)).to eq('0min')
        expect(described_class.format_time_delta(29)).to eq('0min')
        expect(described_class.format_time_delta(-20)).to eq('0min')
      end
    end

    context 'with minute-scale differences' do
      it 'formats positive minutes with plus sign' do
        expect(described_class.format_time_delta(300)).to eq('+5min')
        expect(described_class.format_time_delta(1800)).to eq('+30min')
        expect(described_class.format_time_delta(3540)).to eq('+59min')  # 59 min stays in minutes
      end

      it 'formats negative minutes without explicit sign (negative is implicit)' do
        expect(described_class.format_time_delta(-60)).to eq('-1min')
        expect(described_class.format_time_delta(-300)).to eq('-5min')
      end

      it 'rounds to nearest minute' do
        expect(described_class.format_time_delta(90)).to eq('+2min')  # 1.5 min rounds to 2
        expect(described_class.format_time_delta(75)).to eq('+1min')  # 1.25 min rounds to 1
      end
    end

    context 'with hour-scale differences' do
      it 'formats hours and minutes for large differences' do
        expect(described_class.format_time_delta(3600)).to eq('+1hr')
        expect(described_class.format_time_delta(3660)).to eq('+1hr1min')
        expect(described_class.format_time_delta(7200)).to eq('+2hr')
        expect(described_class.format_time_delta(5400)).to eq('+1hr30min')
      end

      it 'handles negative hours' do
        expect(described_class.format_time_delta(-3600)).to eq('-1hr')
        expect(described_class.format_time_delta(-5400)).to eq('-2hr30min')  # Ruby integer division
      end
    end
  end

  describe '.format_height_delta' do
    context 'with negligible differences' do
      it 'returns "0ft" for differences less than 0.05' do
        expect(described_class.format_height_delta(0.0)).to eq('0ft')
        expect(described_class.format_height_delta(0.04)).to eq('0ft')
        expect(described_class.format_height_delta(-0.04)).to eq('0ft')
      end
    end

    context 'with positive differences' do
      it 'formats with plus sign and units' do
        expect(described_class.format_height_delta(0.5)).to eq('+0.5ft')
        expect(described_class.format_height_delta(1.2)).to eq('+1.2ft')
        expect(described_class.format_height_delta(0.1)).to eq('+0.1ft')
      end
    end

    context 'with negative differences' do
      it 'formats with minus sign and units' do
        expect(described_class.format_height_delta(-0.5)).to eq('-0.5ft')
        expect(described_class.format_height_delta(-1.2)).to eq('-1.2ft')
      end
    end

    context 'with custom units' do
      it 'uses metric units when specified' do
        expect(described_class.format_height_delta(0.5, 'm')).to eq('+0.5m')
        expect(described_class.format_height_delta(-0.3, 'm')).to eq('-0.3m')
      end
    end
  end

  describe '.convert_depth_to_correct_units' do
    context 'when units match' do
      it 'returns the value unchanged' do
        expect(described_class.convert_depth_to_correct_units(10, 'ft', 'ft')).to eq(10)
        expect(described_class.convert_depth_to_correct_units(5.5, 'm', 'm')).to eq(5.5)
      end
    end

    context 'when converting meters to feet' do
      it 'multiplies by 3.28084' do
        result = described_class.convert_depth_to_correct_units(10, 'm', 'ft')
        expect(result).to be_within(0.001).of(32.808)
      end

      it 'handles decimal values' do
        result = described_class.convert_depth_to_correct_units(1.5, 'm', 'ft')
        expect(result).to be_within(0.001).of(4.921)
      end
    end

    context 'when converting feet to meters' do
      it 'divides by 3.28084' do
        result = described_class.convert_depth_to_correct_units(10, 'ft', 'm')
        expect(result).to be_within(0.001).of(3.048)
      end

      it 'handles decimal values' do
        result = described_class.convert_depth_to_correct_units(6.5, 'ft', 'm')
        expect(result).to be_within(0.001).of(1.981)
      end
    end

    context 'with string input' do
      it 'converts string values to float' do
        result = described_class.convert_depth_to_correct_units('10', 'm', 'ft')
        expect(result).to be_within(0.001).of(32.808)
      end
    end
  end
end

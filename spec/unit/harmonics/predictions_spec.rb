# frozen_string_literal: true

RSpec.describe Harmonics::Engine do
    let(:logger) { Logger.new('/dev/null') }

    describe 'astronomical calculations' do
        let(:engine) { described_class.new(logger, 'spec/fixtures/cache') }

        describe '#parse_meridian' do
            it 'returns 0 for nil input' do
                expect(engine.send(:parse_meridian, nil)).to eq(0.0)
            end

            it 'returns 0 for blank input' do
                expect(engine.send(:parse_meridian, '')).to eq(0.0)
            end

            it 'returns 0 for \\N input' do
                expect(engine.send(:parse_meridian, '\N')).to eq(0.0)
            end

            it 'parses positive meridian' do
                expect(engine.send(:parse_meridian, '05:00:00')).to be_within(0.01).of(5.0)
            end

            it 'parses negative meridian' do
                expect(engine.send(:parse_meridian, '-05:00:00')).to be_within(0.01).of(-5.0)
            end

            it 'handles minutes component' do
                expect(engine.send(:parse_meridian, '05:30:00')).to be_within(0.01).of(5.5)
            end
        end
    end

    describe 'file paths' do
        it 'has default XTIDE_FILE path' do
            expect(Harmonics::Engine::XTIDE_FILE).to include('latest-xtide.tcd')
        end

        it 'has default TICON_FILE path' do
            expect(Harmonics::Engine::TICON_FILE).to include('latest-ticon.json')
        end
    end

    describe '#initialize' do
        it 'initializes with logger and cache directory' do
            engine = described_class.new(logger, '/tmp/test_cache')

            expect(engine.logger).to eq(logger)
            expect(engine.stations_cache).to eq({})
            expect(engine.speeds).to eq({})
        end

        it 'uses ENV vars for data file paths if set' do
            original_xtide = ENV['XTIDE_FILE']
            original_ticon = ENV['TICON_FILE']

            ENV['XTIDE_FILE'] = '/custom/xtide.sql'
            ENV['TICON_FILE'] = '/custom/ticon.json'

            engine = described_class.new(logger)

            expect(engine.xtide_file).to eq('/custom/xtide.sql')
            expect(engine.ticon_file).to eq('/custom/ticon.json')

            ENV['XTIDE_FILE'] = original_xtide
            ENV['TICON_FILE'] = original_ticon
        end
    end
end

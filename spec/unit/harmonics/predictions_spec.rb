# frozen_string_literal: true

RSpec.describe Harmonics::Engine do
    let(:logger) { Logger.new('/dev/null') }

    describe 'astronomical calculations' do
        let(:engine) { described_class.new(logger, 'spec/fixtures/cache') }

        describe 'trigonometric helpers' do
            it 'calculates sind correctly' do
                expect(engine.send(:sind, 0)).to be_within(0.0001).of(0.0)
                expect(engine.send(:sind, 90)).to be_within(0.0001).of(1.0)
                expect(engine.send(:sind, 180)).to be_within(0.0001).of(0.0)
                expect(engine.send(:sind, 270)).to be_within(0.0001).of(-1.0)
            end

            it 'calculates cosd correctly' do
                expect(engine.send(:cosd, 0)).to be_within(0.0001).of(1.0)
                expect(engine.send(:cosd, 90)).to be_within(0.0001).of(0.0)
                expect(engine.send(:cosd, 180)).to be_within(0.0001).of(-1.0)
                expect(engine.send(:cosd, 270)).to be_within(0.0001).of(0.0)
            end

            it 'calculates asind correctly' do
                expect(engine.send(:asind, 0)).to be_within(0.0001).of(0.0)
                expect(engine.send(:asind, 1)).to be_within(0.0001).of(90.0)
                expect(engine.send(:asind, -1)).to be_within(0.0001).of(-90.0)
            end

            it 'calculates acosd correctly' do
                expect(engine.send(:acosd, 1)).to be_within(0.0001).of(0.0)
                expect(engine.send(:acosd, 0)).to be_within(0.0001).of(90.0)
                expect(engine.send(:acosd, -1)).to be_within(0.0001).of(180.0)
            end
        end

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

    describe 'constants' do
        it 'defines OBLIQUITY' do
            expect(Harmonics::Engine::OBLIQUITY).to be_within(0.1).of(23.45)
        end

        it 'defines LUNAR_INCLINATION' do
            expect(Harmonics::Engine::LUNAR_INCLINATION).to be_within(0.1).of(5.145)
        end

        it 'defines base constituents' do
            expect(Harmonics::Engine::BASES).to be_a(Hash)
            expect(Harmonics::Engine::BASES.keys).to include('M2', 'S2', 'K1', 'O1')
        end

        it 'defines constituent order' do
            expect(Harmonics::Engine::BASES_ORDER).to be_an(Array)
            expect(Harmonics::Engine::BASES_ORDER).to include('O1', 'K1', 'P1', 'M2', 'S2')
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

    describe 'constituent definitions' do
        let(:engine) { described_class.new(logger, 'spec/fixtures/cache') }

        it 'BASES constituents have v and u arrays' do
            # All BASES constituents should have v/u arrays for nodal factor calculations
            described_class::BASES.each do |name, definition|
                expect(definition).to have_key('v'), "#{name} missing 'v' array"
                expect(definition).to have_key('u'), "#{name} missing 'u' array"
                expect(definition['v']).to be_an(Array).and(have_attributes(length: 6))
                expect(definition['u']).to be_an(Array).and(have_attributes(length: 7))
            end
        end

        it 'can calculate nodal factors for BASES constituents' do
            # Verify that calculate_basic_factors works for all BASES constituents
            # This tests the core calculation without needing TCD files
            described_class::BASES.each do |name, definition|
                expect {
                    arg_start = engine.send(:astronomical_arguments, Time.utc(2026, 1, 1, 12, 0, 0))
                    arg_mid = engine.send(:astronomical_arguments, Time.utc(2026, 7, 1, 12, 0, 0))
                    result = engine.send(:calculate_basic_factors, definition, arg_start, arg_mid)

                    expect(result).to have_key('f')
                    expect(result).to have_key('u')
                    expect(result).to have_key('V0')
                }.not_to raise_error, "Failed for constituent #{name}"
            end
        end

        it 'loaded constituent definitions must have v/u arrays to calculate nodal factors' do
            # This tests the bug: if @constituent_definitions has a Basic constituent
            # without v/u arrays, calculate_basic_factors will fail
            bad_definition = {
                'type' => 'Basic',
                'speed' => 28.9841042,
                'f_formula' => 78
                # Missing 'v' and 'u' arrays
            }

            arg_start = engine.send(:astronomical_arguments, Time.utc(2026, 1, 1, 12, 0, 0))
            arg_mid = engine.send(:astronomical_arguments, Time.utc(2026, 7, 1, 12, 0, 0))

            # Should raise NoMethodError because v_coeffs will be nil
            expect {
                engine.send(:calculate_basic_factors, bad_definition, arg_start, arg_mid)
            }.to raise_error(NoMethodError, /undefined method `\[\]' for nil/)
        end
    end
end

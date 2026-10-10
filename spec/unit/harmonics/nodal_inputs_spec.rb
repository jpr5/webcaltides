# frozen_string_literal: true

require 'tmpdir'

# The engine side of the computed nodal corrections: input checks before
# NodalSchureman is called, and cache files from older engine versions.
RSpec.describe Harmonics::Engine, 'computed nodal corrections' do
    let(:logger) { Logger.new('/dev/null') }

    around do |ex|
        saved = ENV['HARMONICS_NODAL']
        ex.run
    ensure
        saved.nil? ? ENV.delete('HARMONICS_NODAL') : ENV['HARMONICS_NODAL'] = saved
    end

    def engine(mode, dir)
        ENV['HARMONICS_NODAL'] = mode
        described_class.new(logger, dir)
    end

    poison = { 'M2' => { 'f' => 50.0, 'u' => 90.0, 'V0' => 90.0 } }

    # v5 cache files were written by the libcongen-derived code that
    # NodalSchureman replaced, so v6 must never serve them.
    describe 'cache files from engine v5' do
        it 'are not served in legacy mode' do
            Dir.mktmpdir do |dir|
                File.write("#{dir}/nodal_factors_v5_legacy_2026_7_2_0.0_h12.json", poison.to_json)
                factors = engine('legacy', dir).send(:get_nodal_factors, 2026, 7, 2, 0.0, 12)
                expect(factors['M2']['f']).to be_within(0.1).of(1.0)
            end
        end

        it 'are not served in tcd mode for a year outside the TCD table' do
            Dir.mktmpdir do |dir|
                File.write("#{dir}/nodal_factors_v5_tcd_2101_7_2_0.0_h12.json", poison.to_json)
                factors = engine('tcd', dir).send(:get_nodal_factors, 2101, 7, 2, 0.0, 12)
                expect(factors['M2']['f']).to be_within(0.1).of(1.0)
            end
        end

        it 'are not served for tcd tables either' do
            Dir.mktmpdir do |dir|
                e = engine('tcd', dir)
                File.write("#{dir}/nodal_factors_v5_tcd_t#{e.source_files_checksum.split('_').first}_2026_0.0.json", poison.to_json)
                expect(e.send(:get_nodal_factors, 2026, 7, 2, 0.0, 12)['M2']['f']).to be_within(0.1).of(1.0)
            end
        end

        it 'do not share a cache key with v6 output' do
            expect(described_class::ENGINE_VERSION).to eq(6)
            expect(described_class.cache_key_component('tcd')).to eq('hA6tcd')
            expect(described_class.cache_key_component('legacy')).to eq('hA6legacy')
        end
    end

    # v5 station caches were written by the libcongen-derived code, which
    # stored its BASES fields ('v', 'u', 'f_formula') in each of the 13
    # constituents' definitions. Such a file must be rebuilt, not loaded.
    describe 'station cache from engine v5' do
        it 'is not loaded; the stations are parsed again without the BASES fields' do
            Dir.mktmpdir do |dir|
                first = engine('tcd', dir)
                first.stations
                written = Dir.glob("#{dir}/xtide_stations_*.json")
                expect(written.size).to eq(1)
                data = JSON.parse(File.read(written.first))
                data['constituent_definitions']['M2'].merge!('v' => [2, -2, 2, 0, 0, 0], 'u' => [2, -2, 0, 0, 0, 0, 0], 'f_formula' => 78)
                File.delete(written.first)
                old = "#{dir}/xtide_stations_v5_#{first.source_files_checksum}.json"
                File.write(old, data.to_json)

                fresh = engine('tcd', dir)
                fresh.stations
                expect(fresh.instance_variable_get(:@constituent_definitions)['M2'].keys).to match_array(%w[type speed])
                current = fresh.stations_cache_file
                expect(current).not_to eq(old)
                expect(File.read(current)).not_to include('f_formula')
            end
        end
    end

    # The removed code built the instant with Time#local, which raised for an
    # hour outside 0..24; it truncated 12.5 to 12, parsed "12" as 12, raised
    # for "12.7" and treated nil as 0 (see check_nodal_instant).
    # NodalSchureman accepts any hour, so get_nodal_factors checks the hour and
    # the date first: in every mode, and whether or not the result is cached.
    describe 'nodal hour' do
        def hour_error(hour)
            /nodal hour must be an Integer in 0\.\.24, got #{Regexp.escape(hour.inspect)}/
        end

        %w[legacy tcd].each do |mode|
            [2101, 2026].each do |year|
                [30, 25, -1, 12.5, '12'].each do |hour|
                    it "raises ArgumentError for hour #{hour.inspect} (#{mode} mode, year #{year}) before calling NodalSchureman" do
                        Dir.mktmpdir do |dir|
                            e = engine(mode, dir)
                            expect(NodalSchureman).not_to receive(:compute)
                            expect { e.send(:get_nodal_factors, year, 7, 2, 0.0, hour) }.to raise_error(ArgumentError, hour_error(hour))
                        end
                    end
                end
            end
        end

        %w[legacy tcd].each do |mode|
            it "raises for hour \"12\" with a warm cache for hour 12 (#{mode} mode, in memory and on disk)" do
                Dir.mktmpdir do |dir|
                    warm = engine(mode, dir)
                    [2026, 2101].each { |y| warm.send(:get_nodal_factors, y, 7, 2, 0.0, 12) }
                    [warm, engine(mode, dir)].each do |e|
                        [2026, 2101].each do |y|
                            expect { e.send(:get_nodal_factors, y, 7, 2, 0.0, '12') }.to raise_error(ArgumentError, hour_error('12'))
                        end
                    end
                end
            end
        end

        %w[legacy tcd].each do |mode|
            it "raises from generate_predictions in #{mode} mode for 2026 (scripts/predict.rb --nodal-hour 30)" do
                Dir.mktmpdir do |dir|
                    e = engine(mode, dir)
                    e.stations
                    e.stations_cache['RNHOUR'] = { 'constituents' => [{ 'name' => 'M2', 'amp' => 1.0, 'phase' => 0.0 }],
                                                   'datum_offset' => 0.0, 'meridian' => '00:00:00', 'units' => 'meters' }
                    at = Time.utc(2026, 7, 2, 12)
                    expect { e.generate_predictions('RNHOUR', at, at, nodal_hour: 30) }.to raise_error(ArgumentError, hour_error(30))
                end
            end
        end

        [0, 23, 24].each do |hour|
            it "accepts hour #{hour}, as the removed code did" do
                Dir.mktmpdir do |dir|
                    factors = engine('legacy', dir).send(:get_nodal_factors, 2026, 7, 2, 0.0, hour)
                    expect(factors.keys).to match_array(NodalSchureman::CONSTITUENTS)
                end
            end
        end

        # Time#local(y, m, d, 24) is 00:00 the next day; so is NodalSchureman's
        # instant for u and f. V0 stays that of the requested year: Dec 31 at
        # hour 24 keeps year Y's V0, while Jan 1 at hour 0 of Y+1 takes Y+1's.
        it 'gives Dec 31 hour 24 the u and f of Jan 1 00:00 and the V0 of its own year' do
            Dir.mktmpdir do |dir|
                e = engine('legacy', dir)
                at24 = e.send(:get_nodal_factors, 2026, 12, 31, 0.0, 24)
                next0 = e.send(:get_nodal_factors, 2027, 1, 1, 0.0, 0)
                same0 = e.send(:get_nodal_factors, 2026, 12, 31, 0.0, 0)
                uf = ->(fs) { fs.transform_values { |nf| nf.values_at('f', 'u') } }
                v0 = ->(fs) { fs.transform_values { |nf| nf['V0'] } }
                expect(uf[at24]).to eq(uf[next0])
                expect(v0[at24]).to eq(v0[same0])
                expect(v0[at24]['M2']).not_to eq(v0[next0]['M2'])
            end
        end

        # The hour sets the instant of u and f: hour 6 must give NodalSchureman's
        # values for hour 6, which differ from those for hour 0.
        %w[legacy tcd].each do |mode|
            it "uses the nodal hour (#{mode} mode, year 2101: hour 6 differs from hour 0)" do
                Dir.mktmpdir do |dir|
                    e = engine(mode, dir)
                    at6 = e.send(:get_nodal_factors, 2101, 7, 2, 0.0, 6)
                    at0 = e.send(:get_nodal_factors, 2101, 7, 2, 0.0, 0)
                    want6 = NodalSchureman.compute(2101, month: 7, day: 2, hour: 6, shift_hours: 0.0)
                    expect(at6.transform_values { |nf| nf['u'] }).to eq(want6.transform_values { |r| r[:u] })
                    expect(at6.transform_values { |nf| nf['f'] }).to eq(want6.transform_values { |r| r[:f] })
                    expect((at6['M2']['u'] - at0['M2']['u']).abs).to be > 1e-6
                    expect((at6['K1']['f'] - at0['K1']['f']).abs).to be > 1e-9
                end
            end
        end
    end

    describe 'date' do
        %w[legacy tcd].each do |mode|
            # 2100 and 1900 are not Gregorian leap years (both are Julian ones),
            # and both are inside the TCD table.
            [[2026, 2, 30], [2026, 4, 31], [2026, 13, 1], [2027, 2, 29], [2101, 2, 30], [2100, 2, 29], [1900, 2, 29]].each do |y, m, d|
                it "raises a clear ArgumentError for #{y}-#{m}-#{d} (#{mode} mode) before calling NodalSchureman" do
                    Dir.mktmpdir do |dir|
                        e = engine(mode, dir)
                        expect(NodalSchureman).not_to receive(:compute)
                        expect { e.send(:get_nodal_factors, y, m, d, 0.0, 12) }
                            .to raise_error(ArgumentError, /no such date: #{y}-#{m}-#{d}/)
                    end
                end
            end
        end

        it 'accepts 29 February in a leap year' do
            Dir.mktmpdir do |dir|
                expect(engine('legacy', dir).send(:get_nodal_factors, 2028, 2, 29, 0.0, 12).keys)
                    .to match_array(NodalSchureman::CONSTITUENTS)
            end
        end
    end
end

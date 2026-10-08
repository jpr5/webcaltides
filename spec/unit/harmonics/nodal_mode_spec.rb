# frozen_string_literal: true

require 'tmpdir'

# Per-year TCD nodal corrections for every constituent, behind
# HARMONICS_NODAL=tcd|legacy, with versioned engine cache files.
RSpec.describe Harmonics::Engine, 'nodal mode' do
    let(:logger) { Logger.new('/dev/null') }
    let(:fixtures) { File.expand_path('../../fixtures/harmonics', __dir__) }
    let(:fmt) { ->(t) { t.utc.strftime('%Y-%m-%dT%H:%M:%S.%N') } }

    around do |ex|
        saved = ENV['HARMONICS_NODAL']
        ex.run
    ensure
        saved.nil? ? ENV.delete('HARMONICS_NODAL') : ENV['HARMONICS_NODAL'] = saved
    end

    def with_mode(mode)
        mode.nil? ? ENV.delete('HARMONICS_NODAL') : ENV['HARMONICS_NODAL'] = mode
        yield
    end

    def events(engine, id, t0, t1)
        engine.generate_peaks_optimized(id, t0, t1).map { |p| [fmt[p['time']], p['type'], p['height'], p['units']] }
    end

    describe '.nodal_mode' do
        it 'defaults to tcd' do
            with_mode(nil) { expect(described_class.nodal_mode).to eq('tcd') }
            with_mode('') { expect(described_class.nodal_mode).to eq('tcd') }
        end

        it 'accepts tcd and legacy, ignoring case and spaces' do
            with_mode(' Legacy ') { expect(described_class.nodal_mode).to eq('legacy') }
            with_mode('TCD') { expect(described_class.nodal_mode).to eq('tcd') }
        end

        it 'falls back to tcd on any other value, logging it once at ERROR' do
            log = StringIO.new
            engine = with_mode('legcy') { described_class.new(Logger.new(log), Dir.mktmpdir) }
            expect(engine.nodal_mode).to eq('tcd')
            expect(engine.cache_key_component).to eq("hA#{described_class::ENGINE_VERSION}tcd")
            errors = log.string.lines.grep(/ERROR/)
            expect(errors.size).to eq(1)
            expect(errors.first).to include('"legcy"').and include('using tcd')
            with_mode('xtide') { expect(described_class.nodal_mode).to eq('tcd') }
        end

        it 'is captured by the engine at construction' do
            with_mode('legacy') { expect(described_class.new(logger, Dir.mktmpdir).nodal_mode).to eq('legacy') }
        end
    end

    describe '.cache_key_component' do
        it 'names the engine version and nodal mode' do
            with_mode(nil) { expect(described_class.cache_key_component).to eq("hA#{described_class::ENGINE_VERSION}tcd") }
            with_mode('legacy') { expect(described_class.cache_key_component).to eq("hA#{described_class::ENGINE_VERSION}legacy") }
        end

        it 'on an engine, names the mode it resolved at construction, not the current ENV' do
            engine = with_mode('legacy') { described_class.new(logger, Dir.mktmpdir) }
            with_mode('tcd') { expect(engine.cache_key_component).to eq("hA#{described_class::ENGINE_VERSION}legacy") }
        end

        it 'has no _20dddd token that the monthly cleanup could take for a datestamp' do
            %w[tcd legacy].each do |m|
                with_mode(m) { expect("_#{described_class.cache_key_component}_").not_to match(/_(20\d{4})[_.]/) }
            end
        end
    end

    describe 'cache files' do
        let(:t0) { Time.utc(2026, 12, 30) }
        let(:t1) { Time.utc(2027, 1, 2) }

        it 'bumps the station cache to v4' do
            expect(described_class::CACHE_VERSION).to eq(4)
            expect(described_class.new(logger, 'x').stations_cache_file).to match(%r{\Ax/xtide_stations_v4_\h{8}_\h{8}\.json\z})
        end

        it 'writes tcd nodal files when HARMONICS_NODAL is invalid' do
            Dir.mktmpdir do |dir|
                with_mode('legcy') { events(described_class.new(logger, dir), 'T7a94f47', t0, t1) }
                nodal = Dir.children(dir).grep(/\Anodal_factors_/)
                expect(nodal).not_to be_empty
                expect(nodal).to all(start_with('nodal_factors_v4_tcd_'))
            end
        end

        %w[tcd legacy].each do |mode|
            it "writes only versioned #{mode} nodal files in a fresh cache dir" do
                Dir.mktmpdir do |dir|
                    with_mode(mode) { events(described_class.new(logger, dir), 'T7a94f47', t0, t1) }
                    nodal = Dir.children(dir).grep(/\Anodal_factors_/)
                    expect(nodal).not_to be_empty
                    expect(nodal).to all(start_with("nodal_factors_v4_#{mode}_"))
                    expect(Dir.children(dir).grep(/\Axtide_stations_/)).to all(start_with('xtide_stations_v4_'))
                end
            end
        end

        it 'writes one tcd nodal file per year' do
            Dir.mktmpdir do |dir|
                with_mode('tcd') { events(described_class.new(logger, dir), 'T7a94f47', t0, t1) }
                tcd_sum = described_class.new(logger, dir).source_files_checksum.split('_').first
                expect(Dir.children(dir).grep(/\Anodal_factors_/).sort).to eq(%W[nodal_factors_v4_tcd_t#{tcd_sum}_2026_0.0.json nodal_factors_v4_tcd_t#{tcd_sum}_2027_0.0.json])
            end
        end

        # The TCD file supplies the per-year tables, so a different TCD file
        # (different checksum) must never read another file's nodal tables.
        it 'never reads tcd nodal tables written for a different TCD file' do
            Dir.mktmpdir do |dir|
                with_mode('tcd') do
                    events(described_class.new(logger, dir), 'T7a94f47', t0, t1)
                    written = Dir.children(dir).grep(/\Anodal_factors_/).map { |f| "#{dir}/#{f}" }
                    expect(written.size).to eq(2)

                    other = described_class.new(logger, dir)
                    allow(other).to receive(:source_files_checksum).and_return('deadbeef_deadbeef')
                    allow(File).to receive(:read).and_call_original
                    events(other, 'T7a94f47', t0, t1)
                    written.each { |f| expect(File).not_to have_received(:read).with(f, any_args) }
                    expect(Dir.children(dir).grep(/\Anodal_factors_.*deadbeef/).size).to eq(2)
                end
            end
        end

        # A prod-like dir holds unversioned nodal files and the v2 and v3 station
        # caches written by older code (v3 holds a current's name depth as its
        # datum offset). Poison them: they must never be read, and the output
        # must equal a run from an empty dir.
        %w[tcd legacy].each do |mode|
            it "never reads pre-v4 cache files (#{mode})" do
                Dir.mktmpdir do |empty|
                    Dir.mktmpdir do |prod|
                        stale = []
                        (Date.new(2026, 12, 29)..Date.new(2027, 1, 3)).each do |d|
                            f = "#{prod}/nodal_factors_#{d.year}_#{d.month}_#{d.day}_0.0_h12.json"
                            stale << f
                            File.write(f, { 'M2' => { 'f' => 50.0, 'u' => 90.0, 'V0' => 90.0 } }.to_json)
                        end
                        checksum = described_class.new(logger, prod).source_files_checksum
                        %w[v2 v3].each do |v|
                            f = "#{prod}/xtide_stations_#{v}_#{checksum}.json"
                            stale << f
                            File.write(f, '{"poisoned": ')
                        end
                        allow(File).to receive(:read).and_call_original

                        with_mode(mode) do
                            expected = events(described_class.new(logger, empty), 'X49eee41', t0, t1)
                            expect(events(described_class.new(logger, prod), 'X49eee41', t0, t1)).to eq(expected)
                        end
                        stale.each { |s| expect(File).not_to have_received(:read).with(s, any_args) }
                        expect(Dir.children(prod)).to include(a_string_starting_with('xtide_stations_v4_'))
                    end
                end
            end
        end

        # One tcd nodal file serves every station for a year, so a truncated
        # file must be a cache miss, not an error.
        it 'treats a truncated tcd nodal file as a miss, logs it, and rewrites it' do
            Dir.mktmpdir do |empty|
                Dir.mktmpdir do |dir|
                    with_mode('tcd') do
                        fresh = described_class.new(logger, empty)
                        expected = events(fresh, 'T7a94f47', t0, t1)
                        engine = described_class.new(Logger.new(log = StringIO.new), dir)
                        bad = engine.send(:tcd_nodal_cache_file, 2026, 0.0)
                        File.write(bad, '{"M2": {"f": 1.0')
                        expect(events(engine, 'T7a94f47', t0, t1)).to eq(expected)
                        expect(log.string).to match(/WARN.*#{Regexp.escape(bad)}/)
                        expect(JSON.parse(File.read(bad))).to eq(JSON.parse(File.read(fresh.send(:tcd_nodal_cache_file, 2026, 0.0))))
                    end
                end
            end
        end

        # The station cache is written on the first boot after a CACHE_VERSION or data change.  A
        # write cut short (e.g. a redeploy killing the process) must not break every later boot.
        it 'writes the station cache via a temp file in the same dir and a rename' do
            Dir.mktmpdir do |dir|
                allow(File).to receive(:rename).and_call_original
                allow(File).to receive(:write).and_call_original
                engine = described_class.new(logger, dir)
                engine.stations
                target = engine.stations_cache_file
                expect(File).to have_received(:rename).with(a_string_starting_with("#{target}.tmp."), target)
                expect(File).not_to have_received(:write).with(target, anything)
                expect(Dir.children(dir).grep(/\.tmp\./)).to be_empty
            end
        end

        it 'treats a truncated station cache as a miss, logs it, and rebuilds it' do
            Dir.mktmpdir do |dir|
                good = described_class.new(logger, dir)
                expected = good.stations
                file = good.stations_cache_file
                full = File.read(file)
                File.write(file, full[0, full.size / 2])

                engine = described_class.new(Logger.new(log = StringIO.new), dir)
                expect(engine.stations).to eq(expected)
                expect(engine.station_data('X2d7f27f')).to include('ref_key', 'flood_begins')
                expect(log.string).to match(/ERROR.*unreadable station cache #{Regexp.escape(file)}/)
                expect(File.read(file)).to eq(full)
            end
        end

        # A parse fills the station cache one station at a time, so a lookup that only waits while
        # the cache is empty can see a partial cache, find no station and predict nothing.
        it 'waits for the station load before a lookup, even when the station cache is partly filled' do
            engine = described_class.new(logger, Dir.mktmpdir)
            engine.stations_cache['PARTIAL'] = { 'constituents' => [] }
            loaded = { 'name' => 'Loaded', 'constituents' => [] }
            allow(engine).to receive(:stations) { engine.stations_cache['LATE'] = loaded; [] }

            expect(engine.station_data('LATE')).to eq(loaded)
            expect(engine).to have_received(:stations)
        end

        it 'writes nodal files via a temp file in the same dir and a rename' do
            Dir.mktmpdir do |dir|
                allow(File).to receive(:rename).and_call_original
                allow(File).to receive(:write).and_call_original
                engine = described_class.new(logger, dir)
                with_mode('tcd') { events(engine, 'T7a94f47', t0, t1) }
                target = engine.send(:tcd_nodal_cache_file, 2026, 0.0)
                expect(File).to have_received(:rename).with(a_string_starting_with("#{target}.tmp."), target)
                expect(File).not_to have_received(:write).with(target, anything)
                expect(Dir.children(dir).grep(/\.tmp\./)).to be_empty
            end
        end
    end

    # Legacy-mode output for 2 XTide and 2 TICON stations across the 2026/2027 year boundary.
    # The tide stations are the output of commit e441ec9 (before per-year TCD nodal
    # corrections).  The current station X0730150_90 is e441ec9 output with its datum offset
    # corrected from the name depth (90) to the TCD value (-0.067): every velocity is
    # 90.067 lower and peak times differ by under 1 microsecond.
    #
    # Raw hourly heights come out of libm sin/cos, which differ by an ulp or
    # two between platforms (glibc on Linux vs macOS gave deltas up to 4.4e-16),
    # so they are compared within 1e-9. That is far below the 3-decimal
    # precision of any published height and far below the smallest gap between
    # tcd output and this fixture (5e-4), so it still pins legacy output.
    describe 'legacy mode' do
        def match_hourly(golden)
            match(golden.map { |t, h| [t, be_within(1e-9).of(h)] })
        end

        it 'reproduces e441ec9 output, with the corrected datum for X0730150_90 (peaks exactly, hourly heights to 1e-9)' do
            golden = JSON.parse(File.read("#{fixtures}/legacy_e441ec9_events.json"))
            t0, t1 = golden['window'].map { |w| Time.parse("#{w} UTC") }
            Dir.mktmpdir do |dir|
                with_mode('legacy') do
                    engine = described_class.new(logger, dir)
                    golden['stations'].each do |id, g|
                        hourly = engine.generate_predictions(id, Time.utc(2026, 12, 31, 12), Time.utc(2027, 1, 1, 12), step_seconds: 3600)
                        expect(events(engine, id, t0, t1)).to eq(g['peaks']), "peaks differ for #{id}"
                        expect(hourly.map { |p| [fmt[p['time']], p['height']] }).to match_hourly(g['hourly']), "heights differ for #{id}"
                    end
                end
            end
        end
    end

    # Engine control: NOAA's own harmonic constants for Boston (8443970) must
    # reproduce NOAA's own high/low predictions. Only the nodal treatment can
    # make them differ.
    describe 'engine control vs NOAA (Boston, Nov-Dec 2026)' do
        let(:noaa) { JSON.parse(File.read("#{fixtures}/noaa_8443970_boston.json")) }
        let(:names) { { 'LAM2' => 'LDA2', 'RHO' => 'RHO1', 'SIGMA1' => 'SIG1' } }

        def control_errors(mode)
            with_mode(mode) do
                Dir.mktmpdir do |dir|
                    engine = described_class.new(logger, dir)
                    engine.stations
                    consts = noaa['harcon'].map { |n, a, ph| { 'name' => names[n] || n, 'amp' => a, 'phase' => ph } }
                    consts.select! { |c| engine.speeds[c['name']] }
                    engine.stations_cache['RNBOS'] = { 'constituents' => consts, 'datum_offset' => 0.0, 'meridian' => '00:00:00', 'units' => 'meters' }
                    peaks = engine.generate_peaks_optimized('RNBOS', Time.utc(2026, 10, 31, 18), Time.utc(2027, 1, 1, 6))
                    noaa['hilo'].map do |t, v, ty|
                        rt = Time.parse("#{t} UTC")
                        type = ty == 'H' ? 'High' : 'Low'
                        c = peaks.select { |p| p['type'] == type }.min_by { |p| (p['time'] - rt).abs }
                        [(c['time'] - rt).abs / 60.0, (c['height'] - v).abs * 100]
                    end
                end
            end
        end

        it 'tcd mode matches NOAA to within 0.5 min and 0.5 cm on average' do
            errs = control_errors('tcd')
            expect(errs.size).to eq(236)
            expect(errs.sum(&:first) / errs.size).to be <= 0.5
            expect(errs.sum(&:last) / errs.size).to be <= 0.5
        end

        it 'legacy mode is measurably off (the defect tcd mode fixes)' do
            errs = control_errors('legacy')
            expect(errs.sum(&:first) / errs.size).to be > 5.0
        end
    end
end

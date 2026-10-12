# frozen_string_literal: true

require_relative 'case_oracle'

module OTCVectors
    class Generator < OTCCaseOracle::Runner
        TOLERANCES = { 'height_m' => 1e-5, 'time_s' => 1, 'velocity_ms' => 1e-5,
                       'direction_deg' => 0.01, 'direction_min_speed_ms' => 0.01 }.freeze

        def initialize(release:, run_dir:, output:, only: nil, instant: nil, strict_source: nil)
            @release_path, @output, @only = release, output, only
            @release = JSON.parse(File.read(release))
            @extra_instant = self.instant(instant) if instant
            builder = File.join(ROOT, 'scripts/otc/vectors/build_vectors_release.py')
            log, status = Open3.capture2e('uv', 'run', '--project', File.join(ROOT, 'scripts/otc'),
                                        'python', builder, '--run-dir', run_dir, '--verify-entry', release)
            raise OTCCaseOracle::ProofError, log unless status.success?
            @strict_source = strict_source || STRICT_SOURCE
            require @strict_source
            @engine = Harmonics::Engine.allocate
            @engine.instance_variable_set(:@xtide_file, File.join(ROOT, 'data/harmonics-dwf-20251228-free.tcd'))
            @checks = { 'problems' => [], 'hand_sums' => [], 'bands' => [] }
            @vectors = []
            @stations = @release.fetch('stations').to_h { |station| [station.fetch('station_id'), station] }
            @sets = @release.fetch('stations').flat_map { |station| station.fetch('constant_sets') }
            @table = @release.fetch('astro_tables').fetch(0)
        end

        def fixture(_case)
            @release
        end

        def instant(text)
            raise OTCCaseOracle::ProofError, "vector instant must be UTC Z: #{text.inspect}" unless text.is_a?(String) && text.end_with?('Z')
            time = Time.iso8601(text).utc
            raise OTCCaseOracle::ProofError, 'vector instant must be a whole second' unless time.to_r.denominator == 1
            time
        end

        # Vectors deliberately permit the half-second rounding band; Tier A
        # event times allow one second. Bands 1, 2 and 4 still reject windows.
        def rounding_check(*)
            nil
        end

        def fixed_instants
            points = [1990, 1997, 2000, 2010, 2020, 2032, 2040, 2052, 2060].flat_map do |year|
                24.times.map { |n| Time.utc(year, 6, 15) + n * 3 * 3600 }
            end
            points.concat(248.times.map { |n| Time.utc(2010, 3, 1) + n * 3 * 3600 })
            points.concat([Time.utc(1999, 12, 31, 23, 59, 59), Time.utc(2000), Time.utc(2000, 2, 29, 12),
                           Time.utc(2052, 12, 31, 23, 59, 59), Time.utc(2053)])
            points << @extra_instant if @extra_instant
            points.uniq.map(&:iso8601)
        end

        def datum_term(set)
            datum = set.fetch('datum')
            datum.fetch('msl_offset_m') - datum.fetch('named').fetch(datum.fetch('chart_datum'))
        end

        def metadata(station, set, method:, datum: nil, bin: nil, reference: nil, reference_bin: nil, omitted: nil)
            levels = method == 'harmonic' ? set['datum'] : station.dig('subordinate_offsets', 'datum')
            name = datum && (datum == 'chart' ? levels&.fetch('chart_datum', nil) : datum)
            name = 'mean_level' if datum == 'mean_level'
            basis = levels&.dig('basis', name) || {}
            { 'station_id' => station.fetch('station_id'), 'timezone' => station.fetch('timezone'),
              'method' => method, 'set_id' => set.fetch('set_id'), 'convention_id' => set.fetch('convention_id'),
              'astro_table_id' => @table.fetch('astro_table_id'), 'reference_station_id' => reference,
              'reference_bin' => reference_bin, 'datum' => datum, 'datum_name' => name,
              'datum_kind' => basis['kind'], 'datum_uncertainty_m' => basis['uncertainty_m'],
              'bin_number' => bin, 'omitted_event_types' => omitted, 'tz' => nil, 'tz_version' => nil,
              'release' => @release.fetch('release').fetch('datestamp'), 'algorithm' => 'otc-predict-1' }
        end

        def add_vector(id, op, request, entry, native_args, meta, bands: false)
            strict = { 'entry' => entry, 'args' => native_args.merge('astro' => @table.fetch('astro_table_id')) }
            args = arguments(@release, strict)
            list = items(entry, compute(entry, args), args)
            if bands
                old_problems = @checks['problems'].length
                event_bands(id, {}, { 'strict' => strict }, args, @table)
                if @checks['problems'].length > old_problems
                    raise OTCCaseOracle::ProofError, @checks['problems'][old_problems..].join('; ')
                end
            elsif entry == 'predict_strict'
                check_numeric(id, {}, { 'strict' => strict }, @release, args, list)
            end
            list.each do |item|
                %w[height_m velocity_major_ms velocity_minor_ms speed_ms velocity_ms].each do |key|
                    item[key] = item[key].round(7) if item[key].is_a?(Numeric)
                end
            end
            @vectors << { 'id' => id, 'op' => op, 'args' => request, 'expect' => { 'prediction' => meta.merge('items' => list) } }
        end

        def windows
            [Time.utc(2010, 3, 1), Time.utc(2024, 8, 1), Time.utc(2052, 12, 15)]
        end

        def event_windows(label)
            windows.each_with_index do |base, index|
                accepted = false
                8.times do |attempt|
                    start = base + attempt * 86400
                    stop = start + 31 * 86400
                    before = @checks['bands'].length
                    problems_before = @checks['problems'].length
                    begin
                        yield(index, start.iso8601, stop.iso8601)
                        accepted = true
                        break
                    rescue OTCCaseOracle::ProofError => error
                        raise unless @checks['problems'].length > problems_before
                        @checks['bands'].slice!(before..)
                        @checks['problems'].slice!(problems_before..)
                        warn "REPLACE #{label} window #{index}: #{error.message}"
                    end
                end
                raise OTCCaseOracle::ProofError, "#{label}: no 31-day window outside bands 1, 2, 4" unless accepted
            end
        end

        def harmonic_tide(station, set)
            sid = station.fetch('station_id')
            cons = { '$set' => set.fetch('set_id') }
            meta = metadata(station, set, method: 'harmonic', datum: 'mean_level')
            add_vector("#{sid}/heights", 'heights_at', { 'station_id' => sid, 'times' => fixed_instants, 'datum' => 'mean_level' },
                       'predict_strict', { 'constituents' => cons, 'times' => fixed_instants, 'datum_term' => 0.0 }, meta)
            event_windows(sid) do |index, start, stop|
                add_vector("#{sid}/extremes/#{index}", 'extremes', { 'station_id' => sid, 'start' => start, 'stop' => stop, 'datum' => 'mean_level' },
                           'extremes_strict', { 'constituents' => cons, 'start' => start, 'stop' => stop, 'datum_term' => 0.0 }, meta, bands: true)
            end
        end

        def harmonic_current(station, set)
            sid = station.fetch('station_id')
            set.fetch('current_bins').each do |bin|
                number = bin.fetch('bin')
                native_bin = { '$bin' => [set.fetch('set_id'), number] }
                meta = metadata(station, set, method: 'harmonic', bin: number)
                add_vector("#{sid}/currents/#{number}", 'currents',
                           { 'station_id' => sid, 'start' => '2010-03-01T00:00:00Z', 'stop' => '2010-04-01T00:00:00Z', 'step_s' => 10800, 'bin_number' => number },
                           'currents_strict', { 'bin' => native_bin, 'times' => 248.times.map { |n| (Time.utc(2010, 3, 1) + n * 10800).iso8601 } }, meta)
                event_windows("#{sid}/#{number}") do |index, start, stop|
                    add_vector("#{sid}/events/#{number}/#{index}", 'current_events',
                               { 'station_id' => sid, 'start' => start, 'stop' => stop, 'bin_number' => number },
                               'current_events_strict', { 'bin' => native_bin, 'start' => start, 'stop' => stop }, meta, bands: true)
                end
            end
        end

        def subordinate_tide(station)
            sid = station.fetch('station_id')
            offset = station.fetch('subordinate_offsets')
            reference = offset.fetch('reference_station_id')
            set = @stations.fetch(reference).fetch('constant_sets').fetch(0)
            folded = offset['time_offset_high_min'] == offset['time_offset_low_min'] && offset['height_offset_high'] == offset['height_offset_low']
            method = folded ? 'folded_offsets' : 'offsets'
            meta = metadata(station, set, method: method, datum: 'chart', reference: reference)
            base = { 'ref_constituents' => { '$set' => set.fetch('set_id') }, 'chart_datum_term' => datum_term(set),
                     'offsets' => { '$offsets' => sid }, 'datum_shift' => 0.0 }
            if folded
                add_vector("#{sid}/folded-heights", 'heights_at', { 'station_id' => sid, 'times' => fixed_instants, 'datum' => 'chart' },
                           'folded_heights', base.merge('times' => fixed_instants), meta)
            end
            event_windows(sid) do |index, start, stop|
                add_vector("#{sid}/extremes/#{index}", 'extremes', { 'station_id' => sid, 'start' => start, 'stop' => stop, 'datum' => 'chart' },
                           folded ? 'folded_extremes' : 'subordinate_extremes_strict', base.merge('start' => start, 'stop' => stop), meta, bands: true)
            end
        end

        def subordinate_current(station)
            sid = station.fetch('station_id')
            station.fetch('current_offsets').each do |offset|
                number, reference, reference_bin = offset.values_at('bin', 'reference_station_id', 'reference_bin')
                set = @stations.fetch(reference).fetch('constant_sets').fetch(0)
                omitted = Harmonics::Strict::CURRENT_OFFSET_KEYS.filter_map do |type, (adjustment, ratio)|
                    type if offset[adjustment].nil? || (ratio && offset[ratio].nil?)
                end
                meta = metadata(station, set, method: 'offsets', bin: number, reference: reference, reference_bin: reference_bin, omitted: omitted)
                event_windows("#{sid}/#{number}") do |index, start, stop|
                    add_vector("#{sid}/events/#{number}/#{index}", 'current_events',
                               { 'station_id' => sid, 'start' => start, 'stop' => stop, 'bin_number' => number },
                               'subordinate_current_events_strict', { 'ref_bin' => { '$bin' => [set.fetch('set_id'), reference_bin] },
                                  'offset' => { '$current_offset' => [sid, number] }, 'start' => start, 'stop' => stop }, meta, bands: true)
                end
            end
        end

        def run
            selected = @release.fetch('stations').select do |station|
                !@only || station['station_id'] == @only || station.dig('aliases', 'noaa') == @only
            end
            raise OTCCaseOracle::ProofError, 'station filter selected no vectors' if selected.empty?
            selected.each do |station|
                set = station.fetch('constant_sets').first
                if set
                    set['quantity'] == 'current' ? harmonic_current(station, set) : harmonic_tide(station, set)
                elsif station['subordinate_offsets']
                    subordinate_tide(station)
                else
                    subordinate_current(station)
                end
            end
            document = { 'vectors_version' => 1, 'format_version' => @release.fetch('format_version'),
                         'release_sha256' => Digest::SHA256.file(@release_path).hexdigest,
                         'strict_source_sha256' => Digest::SHA256.file(@strict_source).hexdigest,
                         'astro_table_id' => @table.fetch('astro_table_id'), 'tcd_sha256' => @table.fetch('tables_sha256'),
                         'complete' => @only.nil?, 'tolerances' => TOLERANCES, 'vectors' => @vectors, 'checks' => @checks }
            File.write(@output, JSON.pretty_generate(document) + "\n")
            puts "PASS: #{@vectors.length} vectors, #{@checks['bands'].length} accepted band checks"
        end
    end
end

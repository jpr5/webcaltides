# frozen_string_literal: true

require 'optparse'
require 'csv'
require_relative 'lib/case_oracle'

module OTCTierB
    class Comparison
        def initialize(input:, out:, only: nil, mutant: nil, strict_source: nil, xtide_report: nil)
            @input, @out, @only, @mutant = input, out, only, mutant
            @document = JSON.parse(File.read(input))
            raise ArgumentError, 'unsupported Tier B input format' unless @document['format'] == 'otc-tier-b-1'
            @oracle = OTCCaseOracle::Runner.new(calls: input, fixtures: File.dirname(input),
                                                out: out, strict_source: strict_source)
            @xtide_report = xtide_report
            add_xtide_rows if xtide_report
        end

        def add_xtide_rows
            report = JSON.parse(File.read(@xtide_report))
            raise ArgumentError, 'XTide compared different inputs' unless report.fetch('input_sha256') == Digest::SHA256.file(@input).hexdigest
            report.fetch('curves').each do |curve|
                path = curve.fetch('csv')
                raise ArgumentError, "XTide curve bytes changed: #{path}" unless Digest::SHA256.file(path).hexdigest == curve.fetch('sha256')
                reference = CSV.read(path, headers: true).map do |point|
                    { 'time' => Time.at(Integer(point.fetch('time_t'))).utc.iso8601,
                      'height_m' => Float(point.fetch('Value/unit')) }
                end
                window = @document.fetch('window')
                reference.select! { |point| point['time'] >= window.fetch('start') && point['time'] < window.fetch('stop') }
                raise ArgumentError, 'empty XTide curve' if reference.empty?
                call = curve.fetch('call').reject { |key, _value| %w[start stop].include?(key) }
                call['times'] = reference.map { |point| point['time'] }
                @document.fetch('rows') << { 'id' => "xtide/#{curve.fetch('station')}/folded-heights", 'source' => 'xtide',
                                            'station' => curve.fetch('station'), 'kind' => 'heights',
                                            'call' => { 'entry' => 'folded_heights', 'args' => call },
                                            'reference' => reference, 'limits' => { 'height_max_m' => 0.02 } }
            end
        end

        def arguments(row)
            call = Marshal.load(Marshal.dump(row.fetch('call').fetch('args')))
            table = Marshal.load(Marshal.dump(@document.fetch('astro_table')))
            if @mutant == 'raw-kartverket-phase' && row['source'] == 'kartverket'
                call.fetch('constituents').each do |c|
                    c['phase_deg'] = (c.fetch('phase_deg') + c.fetch('speed_deg_per_hour')) % 360
                end
            elsif @mutant == 'missing-used-row'
                name = call.fetch('constituents').first.fetch('name')
                table.fetch('constituents').delete(name)
            end
            call['astro'] = Harmonics::Strict::AstroTable.from_h(table)
            %w[start stop].each { |key| call[key] = Time.iso8601(call[key]) if call[key] }
            call['times'] = call['times'].map { |stamp| Time.iso8601(stamp) } if call['times']
            call
        end

        def mean(values)
            values.sum / values.length.to_f
        end

        def numeric(value)
            raise ArgumentError, "non-finite comparison value #{value.inspect}" unless value.is_a?(Numeric) && value.finite?
            value
        end

        def sample_metrics(row, model)
            reference = row.fetch('reference')
            raise ArgumentError, "sample counts differ: #{model.size}/#{reference.size}" unless model.size == reference.size
            errors, details = [], []
            model.zip(reference).each do |a, b|
                raise ArgumentError, "sample times differ: #{a['time']}/#{b['time']}" unless a.fetch('time') == b.fetch('time')
                if row['kind'] == 'direction'
                    next if numeric(b.fetch('speed_ms')) < row.fetch('limits').fetch('minimum_speed_ms')
                    angle = (numeric(a.fetch('direction_deg')) - numeric(b.fetch('direction_deg'))).abs % 360
                    error = [angle, 360 - angle].min
                else
                    error = (numeric(a.fetch('height_m')) - numeric(b.fetch('height_m'))).abs
                end
                errors << error
                details << { 'time' => b['time'], 'absolute_error' => error, 'model' => a, 'official' => b }
            end
            raise ArgumentError, 'no comparable samples' if errors.empty?
            metrics = if row['kind'] == 'direction'
                          { 'direction_max_deg' => errors.max, 'direction_mae_deg' => mean(errors) }
                      else
                          { 'height_max_m' => errors.max, 'height_mae_m' => mean(errors) }
                      end
            [metrics.merge('samples' => errors.length), details]
        end

        def event_type(kind)
            return 'slack' if kind.start_with?('slack_before_')
            { 'max_flood' => 'flood', 'max_ebb' => 'ebb' }.fetch(kind, kind)
        end

        def event_metrics(row, model)
            reference = row.fetch('reference')
            used, details, missed = {}, [], []
            candidates = reference.each_with_index.flat_map do |official, ref_index|
                model.each_with_index.filter_map do |item, index|
                    next if event_type(item.fetch('type')) != official.fetch('type')
                    delta = (Time.iso8601(item.fetch('time')) - Time.iso8601(official.fetch('time'))).abs
                    [delta, ref_index, index] if delta <= 90 * 60
                end
            end
            assignments = {}
            candidates.sort.each do |delta, ref_index, index|
                next if assignments.key?(ref_index) || used[index]
                assignments[ref_index] = [delta, index]
                used[index] = true
            end
            times, heights, velocities, slack_times, peak_times = [], [], [], [], []
            reference.each_with_index do |official, ref_index|
                pair = assignments[ref_index]
                unless pair
                    missed << official
                    next
                end
                delta, index = pair
                item = model[index]
                times << delta
                alternatives = candidates.count { |_delta, candidate_ref, candidate_index| candidate_ref == ref_index && candidate_index != index }
                detail = { 'official' => official, 'model' => item, 'time_error_s' => delta,
                           'alternative_model_candidates' => alternatives }
                if row['kind'] == 'events'
                    if official['type'] == 'slack'
                        slack_times << delta
                    else
                        peak_times << delta
                        error = (numeric(item.fetch('velocity_ms')) - numeric(official.fetch('velocity_ms'))).abs
                        velocities << error
                        detail['velocity_error_ms'] = error
                    end
                else
                    error = (numeric(item.fetch('height_m')) - numeric(official.fetch('height_m'))).abs
                    heights << error
                    detail['height_error_m'] = error
                end
                details << detail
            end
            extra = model.each_with_index.filter_map { |item, index| item unless used[index] }
            metrics = { 'official_events' => reference.length, 'model_events' => model.length,
                        'matched_events' => times.length, 'missed' => missed.length, 'extra' => extra.length,
                        'time_max_s' => times.max || 0, 'time_mae_s' => times.empty? ? 0 : mean(times),
                        'height_max_m' => heights.max || 0, 'velocity_max_ms' => velocities.max || 0,
                        'slack_time_max_s' => slack_times.max || 0, 'peak_time_max_s' => peak_times.max || 0 }
            [metrics, { 'paired' => details, 'missed' => missed, 'extra' => extra,
                        'pairing' => { 'policy' => 'globally closest same-type time pairs; one use per event',
                                       'window_s' => 90 * 60,
                                       'ambiguity' => 'secondary extrema can share candidate windows; unmatched records are retained' } }]
        end

        def compare(row)
            args = arguments(row)
            entry = row.fetch('call').fetch('entry')
            result = @oracle.compute(entry, args)
            model = @oracle.items(entry, result, args)
            metrics, details = %w[heights direction].include?(row['kind']) ? sample_metrics(row, model) : event_metrics(row, model)
            violations = row.fetch('limits').filter_map do |key, limit|
                next if key == 'minimum_speed_ms'
                actual = metrics.fetch(key)
                "#{key}=#{actual} exceeds #{limit}" if actual > limit
            end
            if row.fetch('exact_events', false)
                %w[missed extra].each { |key| violations << "#{metrics[key]} #{key} events" if metrics.fetch(key, 0).positive? }
            end
            { 'id' => row.fetch('id'), 'kind' => row['kind'], 'limits' => row['limits'],
              'metrics' => metrics, 'violations' => violations, 'passed' => violations.empty?, 'details' => details }
        rescue Harmonics::Strict::Error => error
            { 'id' => row.fetch('id'), 'passed' => false, 'error' => { 'code' => error.code, 'message' => error.message } }
        end

        def run
            rows = @document.fetch('rows').select { |row| !@only || @only.match?(row.fetch('id')) }
            raise ArgumentError, 'no Tier B row selected' if rows.empty?
            reports = rows.map do |row|
                report = compare(row)
                puts "#{report['passed'] ? 'PASS' : 'FAIL'} #{row['id']} #{JSON.generate(report['metrics'] || report['error'])}"
                $stdout.flush
                report
            end
            result = { 'format' => 'otc-tier-b-comparison-1', 'passed' => reports.all? { |row| row['passed'] },
                       'mutant' => @mutant, 'input_sha256' => Digest::SHA256.file(@input).hexdigest,
                       'strict_source_sha256' => Digest::SHA256.file(@oracle.instance_variable_get(:@strict_source)).hexdigest,
                       'tables_sha256' => @document.fetch('astro_table').fetch('tables_sha256'),
                       'xtide_report_sha256' => @xtide_report && Digest::SHA256.file(@xtide_report).hexdigest, 'rows' => reports }
            FileUtils.mkdir_p(@out)
            File.write(File.join(@out, 'comparison.json'), JSON.pretty_generate(result) + "\n")
            result
        end
    end

    class CoverageGate
        PINNED = {
            input: '399c62b47acc3fd38b78205829db51392191caea575e63be167f446aadf5d2f1',
            comparison: '30f78c762b4b282d5a0801c59bdb268622498d75aa34bc83cd9b7aa7221bef6d',
            provenance: '9586605c9708968a5a9573d9ca875ba6100dd730d8e2bdfafe9d0270564d62e1',
            inventory: '28c1d1391164a736add779dd92e61210fe2d0e633085a4e893d19fd379c9dcd1'
        }.freeze
        FAILED = %w[
            noaa-currents/BOS1101:1/events noaa-currents/BOS1101:15/events
            noaa-currents/PUG1501:1/events noaa-currents/PUG1501:6/events
            noaa-currents/ACT0091:1/events kartverket/BGO/extremes
        ].freeze
        PREFIX = 'OTC metric finding: '

        def initialize(input:, comparison:, release:, provenance:, inventory:, out:, projection: false)
            @paths = { input: input, comparison: comparison, provenance: provenance, inventory: inventory }
            @release_path, @out, @projection = release, out, projection
            @mappings, @labels, @violations = [], [], []
        end

        def require_value(condition, message)
            raise ArgumentError, message unless condition
        end

        def digest(path)
            Digest::SHA256.file(path).hexdigest
        end

        def evidence(suffix)
            matches = @provenance.fetch('artifacts').select { |item| item.fetch('path').end_with?("/#{suffix}") }
            require_value(matches.length == 1, "ambiguous or missing evidence #{suffix}")
            item = matches.first
            require_value(digest(item.fetch('path')) == item.fetch('sha256'), "evidence hash changed: #{suffix}")
            item
        end

        def load_inputs
            @paths.each do |key, path|
                require_value(path && digest(path) == PINNED.fetch(key), "#{key} hash differs from approved comparison evidence")
            end
            @input, @comparison, @provenance, @inventory = @paths.values.map { |path| JSON.parse(File.read(path)) }
            @release = JSON.parse(File.read(@release_path))
            identity = JSON.parse(File.read(evidence('release-identities.json').fetch('path')))
            require_value(digest(identity.fetch('release_path')) == identity.fetch('release_sha256'), 'original release hash changed')
            @original = JSON.parse(File.read(identity.fetch('release_path')))
            require_value(@comparison.fetch('passed') == false, 'comparison FAIL ledger was renamed PASS')
            rows = @comparison.fetch('rows')
            require_value(rows.length == 32 && rows.count { |row| row.fetch('passed') } == 26, 'incomplete scientific row census')
            require_value(rows.reject { |row| row.fetch('passed') }.map { |row| row.fetch('id') } == FAILED,
                          'new or missing scientific failure')
            require_value(@comparison.fetch('input_sha256') == PINNED.fetch(:input), 'comparison input binding changed')
            (@input.fetch('inputs') + @input.fetch('official_pins')).each do |item|
                require_value(digest(item.fetch('path')) == item.fetch('sha256'), "original input bytes changed: #{item['path']}")
            end
            strict = File.expand_path('../../../lib/harmonics_strict.rb', __dir__)
            require_value(digest(strict) == @comparison.fetch('strict_source_sha256'), 'strict mathematics changed')
            @xtide = JSON.parse(File.read(evidence('xtide-comparison/xtide-report.json').fetch('path')))
            require_value(digest(evidence('xtide-comparison/xtide-report.json').fetch('path')) == @comparison.fetch('xtide_report_sha256'),
                          'same-input XTide evidence changed')
            @xtide.fetch('curves').each do |curve|
                require_value(digest(curve.fetch('csv')) == curve.fetch('sha256'), 'same-input XTide curve hash changed')
            end
            verify_astronomy
        end

        def verify_astronomy
            expected = @input.fetch('astro_table')
            tables = @release.fetch('astro_tables').select { |table| table.fetch('astro_table_id') == expected.fetch('astro_table_id') }
            require_value(tables.length == 1, 'missing or ambiguous accepted astronomical table')
            actual = tables.first
            expected.each do |key, value|
                next if key == 'constituents'
                require_value(actual.fetch(key) == value, "astronomical #{key} changed")
            end
            expected.fetch('constituents').each do |name, row|
                require_value(actual.fetch('constituents').fetch(name) == row, "astronomical values changed: #{name}")
            end
        end

        def original_subject(source, upstream)
            alias_key = source == 'noaa-currents' ? 'noaa' : source
            matches = @original.fetch('stations').select do |station|
                station.fetch('constant_sets').any? { |set| set['source'] == source && set['source_record_id'] == upstream } ||
                    station.fetch('aliases', {})[alias_key] == upstream
            end
            require_value(matches.length == 1, "unmapped original #{source}/#{upstream}")
            matches.first
        end

        def released_station(id)
            matches = @release.fetch('stations').select { |station| station.fetch('station_id') == id }
            require_value(matches.length == 1, "unmapped or ambiguous published station #{id}")
            matches.first
        end

        def published_set(station, source, upstream)
            matches = station.fetch('constant_sets').select { |set| set['source'] == source && set['source_record_id'] == upstream }
            require_value(matches.length == 1, "unmapped or ambiguous published set #{source}/#{upstream}")
            set = matches.first
            original = @original.fetch('stations').find { |item| item['station_id'] == station['station_id'] }
            previous = original.fetch('constant_sets').find { |item| item['set_id'] == set.fetch('set_id') }
            require_value(previous, "published set identity changed: #{set['set_id']}")
            %w[datum convention_id time_base quantity].each do |key|
                require_value(set[key] == previous[key], "source values changed: #{set['set_id']} #{key}")
            end
            set
        end

        def original_identity(source, upstream)
            matches = @inventory.fetch('identities').select do |identity|
                identity['source'] == source && identity['upstream_record_id'] == upstream && identity['granularity'] == 'native_source_set'
            end
            require_value(matches.length == 1, "unmapped or ambiguous original source tuple #{source}/#{upstream}")
            matches.first
        end

        def unchanged(actual, expected, subject)
            require_value(actual == expected, "source values changed: #{subject}")
        end

        def map_subject(row)
            source = row.fetch('source')
            upstream, bin_text = row.fetch('station').split(':')
            original = original_subject(source, upstream)
            station = released_station(original.fetch('station_id'))
            args = row.fetch('call').fetch('args')
            identity = original_identity(source, upstream)
            mapping = { 'row_id' => row.fetch('id'), 'source' => source, 'source_record_id' => upstream,
                        'source_version' => identity.fetch('version'), 'station_id' => station.fetch('station_id'),
                        'set_id' => nil, 'bin_id' => bin_text && Integer(bin_text), 'values_preserved' => true }
            if args.key?('ref_constituents')
                offsets = station.fetch('subordinate_offsets')
                ref_original = original_subject('noaa', row.fetch('reference_station'))
                ref = released_station(ref_original.fetch('station_id'))
                set = published_set(ref, 'noaa', row.fetch('reference_station'))
                expected_offsets = args.fetch('offsets').merge('reference_station_id' => ref.fetch('station_id'))
                unchanged(offsets, expected_offsets, row['id'])
                unchanged(set.fetch('constituents'), args.fetch('ref_constituents'), row['id'])
                unchanged(set.fetch('datum').fetch('msl_offset_m'), args.fetch('chart_datum_term'), row['id'])
                mapping.merge!('reference_station_id' => ref['station_id'], 'reference_set_id' => set['set_id'])
            elsif args.key?('ref_bin')
                expected_offset = args.fetch('offset')
                ref_original = original_subject(source, expected_offset.fetch('reference_station_id'))
                ref = released_station(ref_original.fetch('station_id'))
                set = published_set(ref, source, expected_offset.fetch('reference_station_id'))
                offsets = station.fetch('current_offsets').select { |offset| offset.fetch('bin') == mapping.fetch('bin_id') }
                require_value(offsets.length == 1, "missing or ambiguous current offset #{row['id']}")
                unchanged(offsets.first, expected_offset.merge('reference_station_id' => ref.fetch('station_id')), row['id'])
                bin = set.fetch('current_bins').find { |item| item.fetch('bin') == expected_offset.fetch('reference_bin') }
                unchanged(bin, args.fetch('ref_bin'), row['id'])
                mapping.merge!('reference_station_id' => ref['station_id'], 'reference_set_id' => set['set_id'],
                               'reference_bin_id' => expected_offset.fetch('reference_bin'))
            else
                set = published_set(station, source, upstream)
                mapping['set_id'] = set.fetch('set_id')
                if args.key?('bin')
                    bin = set.fetch('current_bins').find { |item| item.fetch('bin') == mapping.fetch('bin_id') }
                    unchanged(bin, args.fetch('bin'), row['id'])
                else
                    unchanged(set.fetch('constituents'), args.fetch('constituents'), row['id'])
                    unchanged(set.fetch('datum').fetch('msl_offset_m'), args.fetch('datum_term'), row['id']) if source == 'jma'
                end
            end
            @mappings << mapping
            mapping
        end

        def expected_label(row, mapping)
            expected = mapping.reject { |key, _value| %w[values_preserved reference_set_id].include?(key) }
            expected.merge!('finding' => 'model/reference event disagreement', 'result' => 'FAIL',
                            'window_start' => @input.fetch('window').fetch('start'),
                            'window_end' => @input.fetch('window').fetch('stop'),
                            'comparison_sha256' => PINNED.fetch(:comparison), 'provenance_sha256' => PINNED.fetch(:provenance))
            { 'brackets_sha256' => 'native-root-brackets.json', 'identity_mapping_sha256' => 'release-identities.json',
              'normalization_sha256' => 'normalization-checks.json', 'pairing_red_sha256' => 'pairing-red.log',
              'pairing_green_sha256' => 'pairing-green.log' }.each do |key, suffix|
                expected[key] = evidence(suffix).fetch('sha256')
            end
            metrics = row.fetch('metrics')
            if row.fetch('kind') == 'events'
                expected.merge!('metric' => 'event_count', 'native_value' => metrics.fetch('model_events'),
                                'official_value' => metrics.fetch('official_events'), 'bound' => 0,
                                'event_extra' => metrics.fetch('extra'), 'event_missed' => metrics.fetch('missed'))
            else
                worst = row.fetch('details').fetch('paired').max_by { |pair| pair.fetch('time_error_s') }
                heights = @comparison.fetch('rows').find { |item| item['id'] == 'kartverket/BGO/heights' }
                expected.merge!('metric' => 'time_max_s', 'native_value' => metrics.fetch('time_max_s'),
                                'official_value' => nil, 'bound' => row.fetch('limits').fetch('time_max_s'),
                                'native_time' => worst.fetch('model').fetch('time'), 'official_time' => worst.fetch('official').fetch('time'),
                                'passing_metrics' => [{ 'row_id' => heights['id'], 'metric' => 'height_max_m',
                                                        'native_value' => heights.fetch('metrics').fetch('height_max_m'),
                                                        'bound' => heights.fetch('limits').fetch('height_max_m'),
                                                        'samples' => heights.fetch('metrics').fetch('samples'), 'result' => 'PASS' }])
            end
            if mapping['source_record_id'] == 'BOS1101' && mapping['bin_id'] == 15
                expected['slack_brackets_sha256'] = evidence('native-slack-bracket.json').fetch('sha256')
            elsif mapping['source_record_id'] == 'PUG1501' && mapping['bin_id'] == 1
                expected['weak_peak_sha256'] = evidence('PUG1501-b1-missing-official-peak-series.json').fetch('sha256')
            end
            expected
        end

        def verify_label(row, mapping)
            identity = original_identity(mapping.fetch('source'), mapping.fetch('source_record_id'))
            records = @release.fetch('stations').flat_map do |station|
                station.fetch('source_records', []).filter_map do |record|
                    [station, record] if record['source'] == mapping['source'] && record['source_record_id'] == mapping['source_record_id'] &&
                                        record['source_version'] == identity.fetch('version')
                end
            end
            require_value(records.length == 1, "missing source record or duplicate tuple #{row['id']}")
            station, record = records.first
            require_value(station.fetch('station_id') == mapping.fetch('station_id'), "source record mapped to wrong station #{row['id']}")
            provenance = record.fetch('provenance')
            require_value(provenance.fetch('inventory_sha256') == PINNED.fetch(:inventory), "source inventory provenance changed #{row['id']}")
            require_value(provenance.fetch('input_sha256').sort == identity.fetch('input_sha256').sort, "original input provenance changed #{row['id']}")
            linked = record.fetch('published_set_ids')
            expected_links = mapping['set_id'] ? [mapping['set_id']] : []
            require_value(linked == expected_links, "wrong published set or offset link #{row['id']}")
            require_value(record.fetch('disposition') == 'retained', "source disposition changed #{row['id']}")
            detail = record.fetch('detail')
            require_value(!detail.match?(/hourly heights?\s+(?:fail|failed|fails|failure)/i), "blanket BGO hourly height failure claim #{row['id']}") if mapping['source_record_id'] == 'BGO'
            labels = detail.lines.filter_map { |line| JSON.parse(line.delete_prefix(PREFIX)) if line.start_with?(PREFIX) }
            require_value(labels.all? { |label| FAILED.include?(label.fetch('row_id')) }, "unevidenced failed metric label #{row['id']}")
            matches = labels.select { |label| label.fetch('row_id') == row.fetch('id') }
            require_value(matches.length == 1, "missing metric label or duplicate row #{row['id']}")
            expected = expected_label(row, mapping)
            differing = (expected.keys | matches.first.keys).select { |key| matches.first[key] != expected[key] }
            require_value(differing.empty?, "metric label differs for #{row['id']}: #{differing.join(', ')}")
            @labels << matches.first
        end

        def run
            begin
                load_inputs
                rows = @input.fetch('rows') + @input.fetch('folded_calls').map do |folded|
                    { 'id' => "xtide/#{folded.fetch('station')}/folded-heights", 'source' => 'noaa',
                      'station' => folded.fetch('station'), 'reference_station' => folded.fetch('reference_station'),
                      'call' => { 'args' => folded.fetch('call') } }
                end
                require_value(rows.map { |row| row.fetch('id') } == @comparison.fetch('rows').map { |row| row.fetch('id') },
                              'missing or reordered comparison row')
                rows.each { |row| map_subject(row) }
                @comparison.fetch('rows').reject { |row| row.fetch('passed') }.each do |row|
                    verify_label(row, @mappings.find { |mapping| mapping['row_id'] == row['id'] })
                end
            rescue ArgumentError, KeyError, Errno::ENOENT => error
                @violations << error.message
            end
            report = { 'format' => 'otc-tier-b-coverage-1', 'passed' => @violations.empty?,
                       'artifact_kind' => @projection ? 'source-derived-labelled-projection' : 'release-candidate',
                       'release_sha256' => digest(@release_path), 'comparison_sha256' => digest(@paths.fetch(:comparison)),
                       'approved_comparison_sha256' => PINNED.fetch(:comparison),
                       'scientific_passed' => @comparison && @comparison.fetch('passed'),
                       'scientific_passes' => @comparison && @comparison.fetch('rows').count { |row| row.fetch('passed') },
                       'scientific_failures' => @comparison && @comparison.fetch('rows').count { |row| !row.fetch('passed') },
                       'mappings' => @mappings, 'labels' => @labels, 'violations' => @violations }
            FileUtils.mkdir_p(@out)
            File.write(File.join(@out, 'coverage.json'), JSON.pretty_generate(report) + "\n")
            puts "#{report['passed'] ? 'PASS' : 'FAIL'} coverage-and-label #{@violations.join('; ')}"
            report
        end
    end
end

if $PROGRAM_NAME == __FILE__
    options = {}
    OptionParser.new do |parser|
        parser.on('--input PATH') { |value| options[:input] = value }
        parser.on('--out PATH') { |value| options[:out] = value }
        parser.on('--only REGEXP') { |value| options[:only] = Regexp.new(value) }
        parser.on('--strict-source PATH') { |value| options[:strict_source] = value }
        parser.on('--xtide-report PATH') { |value| options[:xtide_report] = value }
        parser.on('--coverage-release PATH') { |value| options[:coverage_release] = value }
        parser.on('--comparison PATH') { |value| options[:comparison] = value }
        parser.on('--provenance PATH') { |value| options[:provenance] = value }
        parser.on('--inventory PATH') { |value| options[:inventory] = value }
        parser.on('--projection') { options[:projection] = true }
        parser.on('--mutant NAME', %w[raw-kartverket-phase missing-used-row]) { |value| options[:mutant] = value }
    end.parse!
    begin
        %i[input out].each { |key| raise ArgumentError, "missing --#{key}" unless options[key] }
        if options.key?(:coverage_release)
            release = options.delete(:coverage_release)
            exit(OTCTierB::CoverageGate.new(**options, release: release).run.fetch('passed') ? 0 : 1)
        end
        exit(OTCTierB::Comparison.new(**options).run.fetch('passed') ? 0 : 1)
    rescue ArgumentError, KeyError, OTCCaseOracle::ProofError => error
        warn "FAIL: #{error.message}"
        exit 1
    end
end

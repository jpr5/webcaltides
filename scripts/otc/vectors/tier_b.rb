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
end

if $PROGRAM_NAME == __FILE__
    options = {}
    OptionParser.new do |parser|
        parser.on('--input PATH') { |value| options[:input] = value }
        parser.on('--out PATH') { |value| options[:out] = value }
        parser.on('--only REGEXP') { |value| options[:only] = Regexp.new(value) }
        parser.on('--strict-source PATH') { |value| options[:strict_source] = value }
        parser.on('--xtide-report PATH') { |value| options[:xtide_report] = value }
        parser.on('--mutant NAME', %w[raw-kartverket-phase missing-used-row]) { |value| options[:mutant] = value }
    end.parse!
    begin
        %i[input out].each { |key| raise ArgumentError, "missing --#{key}" unless options[key] }
        exit(OTCTierB::Comparison.new(**options).run.fetch('passed') ? 0 : 1)
    rescue ArgumentError, KeyError, OTCCaseOracle::ProofError => error
        warn "FAIL: #{error.message}"
        exit 1
    end
end

# frozen_string_literal: true

require 'json'
require 'digest'
require 'fileutils'
require 'time'
require 'open3'
require 'tzinfo'

module OTCCaseOracle
    class ProofError < StandardError; end

    class Runner
        ROOT = File.expand_path('../../../..', __dir__)
        STRICT_SOURCE = File.join(ROOT, 'lib/harmonics_strict.rb')
        attr_reader :checks

        def initialize(calls:, fixtures:, out:, filter: nil, strict_source: nil)
            @calls_path, @fixtures, @out, @filter = calls, fixtures, out, filter
            @strict_source = strict_source || STRICT_SOURCE
            require @strict_source
            @calls = JSON.parse(File.read(calls))
            @engine = Harmonics::Engine.allocate
            @engine.instance_variable_set(:@xtide_file, File.join(ROOT, 'data/harmonics-dwf-20251228-free.tcd'))
            @documents, @fixture_hashes, @results = {}, {}, {}
            @checks = { 'problems' => [], 'outcomes' => [], 'hand_sums' => [], 'comparisons' => [],
                        'scans' => [], 'bands' => [], 'deferred_bands_remaining' => [] }
        end

        def fixture(c)
            key = [c.fetch('root'), c.fetch('release')]
            @documents[key] ||= begin
                path = File.join(@fixtures, key[0], "OTC_#{key[1]}.json")
                @fixture_hashes[File.join(key[0], "OTC_#{key[1]}.json")] = Digest::SHA256.file(path).hexdigest
                JSON.parse(File.read(path))
            end
        end

        def lookup(doc, reference)
            case reference.keys
            when ['$set']
                doc.fetch('stations').flat_map { |s| s.fetch('constant_sets', []) }
                   .find { |s| s['set_id'] == reference['$set'] }.fetch('constituents')
            when ['$bin']
                sid, number = reference['$bin']
                doc.fetch('stations').flat_map { |s| s.fetch('constant_sets', []) }
                   .find { |s| s['set_id'] == sid }.fetch('current_bins').find { |b| b['bin'] == number } ||
                    raise(ProofError, "missing bin #{reference.inspect}")
            when ['$offsets']
                doc.fetch('stations').find { |s| s['station_id'] == reference['$offsets'] }.fetch('subordinate_offsets')
            when ['$current_offset']
                sid, number = reference['$current_offset']
                doc.fetch('stations').find { |s| s['station_id'] == sid }.fetch('current_offsets')
                   .find { |b| b['bin'] == number } || raise(ProofError, "missing offset #{reference.inspect}")
            else
                raise ProofError, "unknown fixture reference #{reference.inspect}"
            end
        end

        def instant(text)
            raise ProofError, "oracle instant must have UTC Z: #{text.inspect}" unless text.is_a?(String) && text.end_with?('Z')
            Time.at(Time.iso8601(text).to_i).utc
        end

        def arguments(doc, strict)
            strict.fetch('args').to_h do |key, value|
                resolved = case key
                           when 'astro'
                               table = doc.fetch('astro_tables').find { |t| t['astro_table_id'] == value }
                               raise ProofError, "missing named fixture table #{value}" unless table
                               Harmonics::Strict::AstroTable.from_h(table)
                           when 'times' then value.map { |t| instant(t) }
                           when 'start', 'stop' then instant(value)
                           else value.is_a?(Hash) && value.keys.any? { |k| k.start_with?('$') } ? lookup(doc, value) : value
                           end
                [key, resolved]
            end
        end

        def compute(entry, args)
            astro = args.fetch('astro')
            case entry
            when 'predict_strict'
                @engine.predict_strict(args.fetch('constituents'), args.fetch('times'), datum_term: args.fetch('datum_term'), astro: astro)
            when 'extremes_strict'
                @engine.extremes_strict(args.fetch('constituents'), args.fetch('start'), args.fetch('stop'), datum_term: args.fetch('datum_term'), astro: astro)
            when 'currents_strict'
                @engine.currents_strict(args.fetch('bin'), args.fetch('times'), astro: astro)
            when 'current_events_strict'
                @engine.current_events_strict(args.fetch('bin'), args.fetch('start'), args.fetch('stop'), astro: astro)
            when 'subordinate_extremes_strict'
                @engine.subordinate_extremes_strict(args.fetch('ref_constituents'), args['chart_datum_term'], args.fetch('offsets'),
                                                  args.fetch('start'), args.fetch('stop'), datum_shift: args.fetch('datum_shift'), astro: astro)
            when 'subordinate_current_events_strict'
                @engine.subordinate_current_events_strict(args.fetch('ref_bin'), args.fetch('offset'), args.fetch('start'), args.fetch('stop'), astro: astro)
            when 'folded_heights', 'folded_extremes'
                fold = @engine.subordinate_folded_strict(args.fetch('ref_constituents'), args['chart_datum_term'], args.fetch('offsets'),
                                                       datum_shift: args.fetch('datum_shift'), astro: astro)
                if entry == 'folded_heights'
                    @engine.predict_strict(fold.fetch('constituents'), args.fetch('times'), datum_term: fold.fetch('datum_term'), astro: astro)
                else
                    @engine.extremes_strict(fold.fetch('constituents'), args.fetch('start'), args.fetch('stop'), datum_term: fold.fetch('datum_term'), astro: astro)
                end
            else raise ProofError, "unsupported strict entry #{entry}"
            end
        end

        def whole_time(time)
            raise ProofError, 'strict oracle returned a fractional instant' unless time.to_r.denominator == 1
            time.utc.iso8601
        end

        def items(_entry, result, args)
            result = result.fetch('events') if result.is_a?(Hash)
            result.each_with_index.map do |value, index|
                item = if value.is_a?(Numeric)
                           { 'time' => whole_time(args.fetch('times')[index]), 'height_m' => value }
                       else
                           value.to_h do |key, field|
                               [key == 'height' ? 'height_m' : key, field.is_a?(Time) ? whole_time(field) : field]
                           end
                       end
                item['time'] ||= whole_time(args.fetch('times')[index])
                item.merge('local_time' => nil, 'utc_offset_s' => nil)
            end
        end

        def localize(items, local)
            return items unless local
            zone = TZInfo::Timezone.get(local.fetch('zone'))
            converted = items.map do |item|
                time = Time.iso8601(item.fetch('time'))
                offset = zone.period_for_utc(time).observed_utc_offset
                item.merge('local_time' => time.getlocal(offset).strftime('%Y-%m-%dT%H:%M:%S%:z'), 'utc_offset_s' => offset)
            end
            local.fetch('check', []).each do |wanted|
                actual = converted.find { |item| item['time'] == wanted['time'] }
                raise ProofError, "local conversion differs: #{wanted.inspect}" unless actual && wanted.all? { |k, v| actual[k] == v }
            end
            converted
        end

        def check_outcome(label, step, list)
            outcome = step['outcome']
            return unless outcome
            relevant = outcome['types'] ? list.select { |item| outcome['types'].include?(item['type']) } : list
            if outcome.key?('count') && relevant.length != outcome['count']
                raise ProofError, "#{label}: expected count #{outcome['count']}, actual #{relevant.length}"
            end
            if outcome['items']
                observed = relevant.map { |item| item.slice('time', 'type') }
                raise ProofError, "#{label}: handwritten outcome differs: #{observed.inspect}" unless observed == outcome['items']
            end
            @checks['outcomes'] << { 'step' => label, 'count' => relevant.length, 'pass' => true }
        end

        def compare_items(label, left, right, time_s: 0, height_m: 1e-9)
            raise ProofError, "#{label}: item counts differ #{left.size}/#{right.size}" unless left.size == right.size
            left.zip(right).each do |a, b|
                a.each do |key, value|
                    actual = b[key]
                    okay = if key == 'time'
                               (instant(value).to_i - instant(actual).to_i).abs <= time_s
                           elsif value.is_a?(Numeric) && actual.is_a?(Numeric)
                               (value - actual).abs <= height_m
                           else
                               value == actual
                           end
                    raise ProofError, "#{label}: #{key} differs #{value.inspect}/#{actual.inspect}" unless okay
                end
            end
        end

        def step(c, s, index)
            label = "#{c.fetch('id')}:#{index}"
            doc = fixture(c)
            strict = s['strict']
            if s['swap_of']
                validate_exception(c, s, doc) if c['band_exception']
                from = s.fetch('swap_of')
                positive_case = @calls.fetch('cases').find { |item| item['id'] == from.fetch('case') }
                raise ProofError, "negative mirror names an unknown source #{from}" unless positive_case
                original = fixture(positive_case)
                raise ProofError, 'negative mirror changed its astronomical table' unless doc.fetch('astro_tables') == original.fetch('astro_tables')
                source = @results.fetch(from.fetch('case')).fetch(from.fetch('step')).fetch('items')
                list = source.map do |item|
                    item.merge('type' => item['type'] == 'high' ? 'low' : 'high', 'height_m' => -item.fetch('height_m'))
                end
                check_outcome(label, s, list)
                return { 'source' => 'negative_mirror', 'items' => list }
            end
            return { 'source' => 'authored_interface', 'expect' => s['expect'] } unless strict
            if strict.key?('same_as_step')
                source = @results.fetch(c.fetch('id')).fetch(strict.fetch('same_as_step')).fetch('items')
                return { 'source' => 'strict_reuse', 'items' => localize(source, s['local']) }
            end
            args = arguments(doc, strict)
            result = compute(strict.fetch('entry'), args)
            if s.dig('expect', 'error') && !c['id'].start_with?('not-built/')
                raise ProofError, "#{label}: expected refusal #{s['expect']['error']}, native oracle returned values"
            end
            list = items(strict['entry'], result, args)
            check_outcome(label, s, list)
            if s['compare']
                comparison = s.fetch('compare')
                other = arguments(doc, comparison)
                other_items = items(comparison['entry'], compute(comparison['entry'], other), other)
                compare_items(label, list, other_items, time_s: comparison.fetch('time_s'), height_m: comparison.fetch('height_m'))
                @checks['comparisons'] << { 'step' => label, 'pass' => true }
            end
            scientific_checks(label, c, s, doc, args, list)
            { 'source' => 'strict', 'items' => list }
        rescue Harmonics::Strict::Error => error
            raise ProofError, "#{label}: unexpected native refusal #{error.code}: #{error.message}" unless error.code == s.dig('expect', 'error')
            { 'source' => 'strict_refusal', 'error' => error.code }
        end

        def scientific_checks(label, c, s, doc, args, list)
            # Implemented by the independent numerical checks; no SDK code is used.
            check_numeric(label, c, s, doc, args, list)
        end

        def run
            raise ProofError, "output directory already contains files: #{@out}" if Dir.exist?(@out) && !Dir.empty?(@out)
            selected = @calls.fetch('cases').select { |c| !@filter || @filter.match?(c.fetch('id')) }
            raise ProofError, 'case filter selected no cases' if selected.empty?
            selected.each do |c|
                @results[c.fetch('id')] = []
                c.fetch('steps').each_with_index { |s, i| @results[c['id']] << step(c, s, i) }
                c.fetch('steps').each_with_index do |s, i|
                    next unless s.key?('same_items_as_step')
                    compare_items("#{c['id']}:#{i}", @results[c['id']].fetch(s['same_items_as_step']).fetch('items'),
                                  @results[c['id']].fetch(i).fetch('items'))
                end
            end
            deferred = @calls.fetch('band_coverage').fetch('deferred')
            applicable = deferred.select { |item| !@filter || @filter.match?(item.fetch('case_id')) }
            proved = applicable.select do |item|
                label = "#{item.fetch('case_id')}:#{item.fetch('step')}"
                round = @checks['bands'].any? { |check| check['step'] == label && check['bands'] == [3] && check['pass'] }
                search = @checks['bands'].any? do |check|
                    (check['step'] == label || check['step'].start_with?(label + '/')) && check['bands'] == [1, 2, 4] && check['pass']
                end
                round && search
            end
            @checks['deferred_bands_remaining'] = applicable - proved
            raise ProofError, "deferred native band proof missing #{@checks['deferred_bands_remaining'].inspect}" unless @checks['deferred_bands_remaining'].empty?
            raise ProofError, @checks['problems'].join("\n") unless @checks['problems'].empty?
            @checks['scientific_gate_ready'] = !@filter && selected.size == @calls.fetch('cases').size
            @checks['band_coverage'] = {
                'scientific_gate_ready' => @checks['scientific_gate_ready'],
                'authored_deferred_steps' => deferred.map { |item| item.slice('case_id', 'step', 'root') },
                'proved_deferred_steps' => proved.map { |item| item.slice('case_id', 'step', 'root').merge('bands' => [1, 2, 3, 4]) },
                'deferred_steps_remaining' => @checks['deferred_bands_remaining']
            }
            sha, status = Open3.capture2('git', '-C', ROOT, 'rev-parse', 'HEAD')
            raise ProofError, 'cannot identify oracle commit' unless status.success?
            binding = { 'calls_sha256' => Digest::SHA256.file(@calls_path).hexdigest,
                        'fixture_sha256' => @fixture_hashes.sort.to_h,
                        'strict_source_sha256' => Digest::SHA256.file(@strict_source).hexdigest,
                        'oracle_commit' => sha.strip, 'calls_version' => @calls.fetch('calls_version'),
                        'ruby_version' => RUBY_VERSION }
            crosscheck_bytes = JSON.generate(binding.merge(@checks)) + "\n"
            output = binding.merge('scientific_gate_ready' => @checks['scientific_gate_ready'],
                                   'band_coverage' => @checks['band_coverage'],
                                   'crosscheck_sha256' => Digest::SHA256.hexdigest(crosscheck_bytes), 'cases' => selected.map do |c|
                { 'id' => c.fetch('id'), 'steps' => @results.fetch(c['id']), 'band_exception' => c['band_exception'] }
            end)
            FileUtils.mkdir_p(@out)
            File.write(File.join(@out, 'predict_expected.json'), JSON.generate(output) + "\n")
            File.write(File.join(@out, 'crosscheck.json'), crosscheck_bytes)
            puts "PASS #{selected.size} cases / #{@results.values.sum(&:size)} steps; #{binding['calls_sha256']}"
        end
    end
end

require_relative 'numerical_checks'

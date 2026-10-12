# frozen_string_literal: true

require 'json'
require 'digest'
require 'open3'
require 'rbconfig'
require 'fileutils'
require_relative 'case_oracle'

module OTCCaseOracle
    module MutationProof
        def self.replaced(source, before, after)
            raise ProofError, "mutation anchor absent: #{before}" unless source.include?(before)
            source.sub(before, after)
        end

        def self.sources(source)
            {
                '8' => replaced(source,
                    '@terms = terms.map { |name, amp, phase| [table.row(name), amp.to_f, phase.to_f] }',
                    '@terms = terms.filter_map { |name, amp, phase| begin; [table.row(name), amp.to_f, phase.to_f]; rescue Error => error; raise unless error.code == "unsupported_constituent"; nil; end }'),
                '8b' => replaced(source, 'nullable(offset, adj, what)', '(nullable(offset, adj, what) || 0)'),
                '8c' => replaced(source,
                    'Strict.non_negative(Strict.number(row.speed, "speed of #{row.name}"), "speed of #{row.name}") unless row.speed.nil?',
                    'Strict.number(row.speed, "speed of #{row.name}") unless row.speed.nil?'),
                '9' => replaced(source,
                    'subdivide(f, bound.call(year), year, a.to_f, b.to_f, fa, fb, 0, out)',
                    'bracket(f, year, a.to_f, b.to_f, fa, fb, out)'),
                '9a' => replaced(replaced(source,
                    'def filter_extrema(cands, &more_extreme)',
                    "def filter_extrema(cands, &more_extreme)\n            return cands"),
                    'def filter_slacks(cands)', "def filter_slacks(cands)\n            return cands"),
                '9b' => replaced(source, 'cluster[2 * ((cluster.size - 1) / 4)]', 'cluster[(cluster.size - 1) / 2]'),
                '9c' => replaced(source,
                    'def reconcile_new_years!(cands, g0, gn, f, value, &keep_left)',
                    "def reconcile_new_years!(cands, g0, gn, f, value, &keep_left)\n            return cands"),
                '9d-error' => replaced(source, ' + 2 * e + ROOT_FREE_ABS', ' + ROOT_FREE_ABS'),
                '9d-margin' => replaced(source,
                    's > m * hours * (1 + ROOT_FREE_REL) + 2 * e + ROOT_FREE_ABS * [1.0, m].max',
                    's > m * hours'),
                '10' => replaced(source, 'kind == :down ? fm.positive? : fm.negative?', 'kind == :down ? fm >= 0 : fm <= 0'),
                '11' => replaced(source, 'g0 = (start / STEP_S).floor * STEP_S - PAD_S', 'g0 = (start / STEP_S).floor * STEP_S - PAD_S + 180'),
                'own-table-omitted' => replaced(source,
                    'Strict.heights(astro, constituents, times, datum_term)',
                    'Strict.heights(strict_astro_table, constituents, times, datum_term)')
            }
        end

        def self.difference(base, changed, time_s: 0, numeric: 1e-9)
            base_cases = base.fetch('cases').to_h { |c| [c.fetch('id'), c.fetch('steps')] }
            changed.fetch('cases').each do |c|
                c.fetch('steps').zip(base_cases.fetch(c.fetch('id'))).each_with_index do |(actual, wanted), i|
                    label = "#{c.fetch('id')}:#{i}"
                    return label if actual.keys != wanted.keys || actual['error'] != wanted['error']
                    next unless wanted['items']
                    return label unless actual['items'].size == wanted['items'].size
                    actual['items'].zip(wanted['items']).each do |a, b|
                        return label unless a.keys == b.keys
                        a.each do |key, value|
                            other = b[key]
                            okay = if key == 'time'
                                       (Time.iso8601(value).to_i - Time.iso8601(other).to_i).abs <= time_s
                                   elsif value.is_a?(Numeric) && other.is_a?(Numeric)
                                       (value - other).abs <= numeric
                                   else
                                       value == other
                                   end
                            return "#{label}/#{key}" unless okay
                        end
                    end
                end
            end
            nil
        end

        def self.run(calls, fixtures, out, baseline)
            raise ProofError, "mutation output is nonempty #{out}" if Dir.exist?(out) && !Dir.empty?(out)
            FileUtils.mkdir_p(out)
            original = File.binread(Runner::STRICT_SOURCE)
            table = []
            sources(original).each do |name, source|
                path = File.join(out, "strict-#{name}.rb")
                File.write(path, source)
                run_dir = File.join(out, name)
                command = [RbConfig.ruby, File.expand_path('../run_cases.rb', __dir__),
                           '--calls', calls, '--fixtures', fixtures, '--out', run_dir, '--strict-source', path]
                log, status = Open3.capture2e(*command)
                File.write(File.join(out, "#{name}.log"), log)
                permitted = %w[10 11].include?(name)
                difference = status.success? && self.difference(baseline, JSON.parse(File.read(File.join(run_dir, 'predict_expected.json'))),
                                                               time_s: permitted ? 1 : 0)
                rejected = !status.success? && log.start_with?('FAIL:')
                pass = permitted ? status.success? && !difference : rejected || difference
                table << { 'mutation' => name, 'expected' => permitted ? 'within_tolerance' : 'killed',
                           'exit' => status.exitstatus, 'source_sha256' => Digest::SHA256.hexdigest(source),
                           'differing_step' => difference, 'pass' => !!pass, 'output' => log.lines.first.to_s.strip }
                puts "#{pass ? 'PASS' : 'FAIL'} mutation #{name}: exit #{status.exitstatus} #{difference}"
            end
            result = { 'calls_sha256' => Digest::SHA256.file(calls).hexdigest,
                       'strict_source_sha256' => Digest::SHA256.hexdigest(original), 'mutations' => table }
            File.write(File.join(out, 'mutation-evidence.json'), JSON.generate(result) + "\n")
            raise ProofError, 'required mutation proof failed' unless table.all? { |row| row['pass'] }
        end
    end
end

if $PROGRAM_NAME == __FILE__
    begin
        calls, fixtures, out, baseline = ARGV
        raise ArgumentError, 'calls fixtures out baseline required' unless baseline
        OTCCaseOracle::MutationProof.run(calls, fixtures, out, JSON.parse(File.read(baseline)))
    rescue OTCCaseOracle::ProofError, ArgumentError => error
        warn "FAIL: #{error.message}"
        exit 1
    end
end

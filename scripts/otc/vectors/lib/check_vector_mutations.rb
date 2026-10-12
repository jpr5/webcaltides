# frozen_string_literal: true

require 'rbconfig'
require_relative 'vector_generator'

module OTCVectors
    module MutationProof
        def self.replace(source, before, after)
            raise OTCCaseOracle::ProofError, "mutation anchor count is not one: #{before}" unless source.scan(before).length == 1
            source.sub(before, after)
        end

        def self.sources(source)
            {
                '1' => replace(source, 'Strict.number(row.f[i], "f of #{row.name} in #{year}")',
                               '(Strict.number(row.f[i], "f of #{row.name} in #{year}") * (row.name == "M2" && year == 2010 ? 1.0001 : 1))'),
                '2' => replace(source, 'Strict.number(row.v0u[i], "V0+u of #{row.name} in #{year}")',
                               '(Strict.number(row.v0u[i], "V0+u of #{row.name} in #{year}") + (row.name == "M2" && year == 2010 ? 0.01 : 0))'),
                '3' => replace(source, 'a, f = @table.values(row, year)', 'a, f = @table.values(row, year - 1)'),
                '4' => replace(source, '(x / 360.0).floor', '(x / 360.0).truncate'),
                '5' => replace(source, 'u = b[:mean_major] + b[:major].value(t, year)', 'u = b[:major].value(t, year)'),
                '6' => replace(source, 'theta_minor = (b[:azimuth] + minor_sign * 90.0)', 'theta_minor = (b[:azimuth] - minor_sign * 90.0)'),
                '7' => replace(source, "height = (o[:type] == 'R' ? c[:value] * k : c[:value] + k) + shift",
                               "height = (o[:type] == 'R' ? (c[:value] - datum_term) * k + datum_term : c[:value] + k) + shift"),
                '8a' => replace(source, "reduce_deg(c['phase_deg'] + speed * dh)", "reduce_deg(c['phase_deg'] - speed * dh)")
            }
        end

        def self.difference(base, changed)
            by_id = changed.fetch('vectors').to_h { |vector| [vector.fetch('id'), vector] }
            base.fetch('vectors').each do |vector|
                id = vector.fetch('id')
                actual = by_id[id]
                return { 'vector' => id, 'field' => 'missing vector' } unless actual
                wanted, got = vector.dig('expect', 'prediction'), actual.dig('expect', 'prediction')
                return { 'vector' => id, 'field' => 'metadata' } unless wanted.reject { |k, _| k == 'items' } == got.reject { |k, _| k == 'items' }
                return { 'vector' => id, 'field' => 'count' } unless wanted['items'].size == got['items'].size
                wanted['items'].zip(got['items']).each_with_index do |(item, other), index|
                    return { 'vector' => id, 'field' => 'item keys' } unless item.keys == other.keys
                    item.each do |field, value|
                        actual_value = other[field]
                        tolerance = if field == 'time'
                                        1
                                    elsif field == 'direction_deg'
                                        0.01
                                    else
                                        1e-5
                                    end
                        delta = if field == 'time'
                                    (Time.iso8601(value) - Time.iso8601(actual_value)).abs
                                elsif field == 'direction_deg' && value.is_a?(Numeric) && actual_value.is_a?(Numeric)
                                    return { 'vector' => id, 'item' => index, 'field' => field, 'actual' => actual_value,
                                             'reason' => 'direction outside [0,360)' } unless actual_value >= 0 && actual_value < 360
                                    next if item.fetch('speed_ms', 1.0) < 0.01
                                    gap = (value - actual_value).abs % 360
                                    [gap, 360 - gap].min
                                elsif value.is_a?(Numeric) && actual_value.is_a?(Numeric)
                                    (value - actual_value).abs
                                else
                                    value == actual_value ? 0 : Float::INFINITY
                                end
                        if delta > tolerance
                            return { 'vector' => id, 'item' => index, 'field' => field, 'expected' => value,
                                     'actual' => actual_value, 'difference' => delta.finite? ? delta : 'type/value', 'tolerance' => tolerance }
                        end
                    end
                end
            end
            nil
        end

        def self.run(release, run_dir, out, baseline)
            raise OTCCaseOracle::ProofError, "mutation output is nonempty: #{out}" if Dir.exist?(out) && !Dir.empty?(out)
            FileUtils.mkdir_p(out)
            original = File.read(OTCCaseOracle::Runner::STRICT_SOURCE)
            pinned = JSON.parse(File.read(baseline))
            raise OTCCaseOracle::ProofError, 'mutation baseline is not a complete native run' unless pinned.fetch('complete')
            rows = sources(original).map do |name, source|
                path = File.join(out, "strict-#{name}.rb")
                File.write(path, source)
                result = File.join(out, "vectors-#{name}.json")
                command = [RbConfig.ruby, File.expand_path('../generate.rb', __dir__), '--release', release,
                           '--run-dir', run_dir, '--output', result, '--strict-source', path]
                log, status = Open3.capture2e(*command)
                File.write(File.join(out, "#{name}.log"), log)
                diff = difference(pinned, JSON.parse(File.read(result))) if status.success?
                refusal = !status.success? && log.include?('FAIL:')
                row = { 'mutation' => name, 'exit' => status.exitstatus, 'source_sha256' => Digest::SHA256.hexdigest(source),
                        'differing_value' => diff, 'native_refusal' => refusal ? log.strip : nil, 'pass' => !!(diff || refusal) }
                row['constituent'] = 'M2' if %w[1 2].include?(name)
                row['year'] = 2010 if %w[1 2].include?(name)
                puts "#{row['pass'] ? 'PASS' : 'FAIL'} mutation #{name}: exit #{status.exitstatus} #{diff}"
                row
            end
            document = { 'vectors_sha256' => Digest::SHA256.file(baseline).hexdigest,
                         'release_sha256' => Digest::SHA256.file(release).hexdigest,
                         'strict_source_sha256' => Digest::SHA256.hexdigest(original), 'mutations' => rows }
            File.write(File.join(out, 'mutation-evidence.json'), JSON.pretty_generate(document) + "\n")
            raise OTCCaseOracle::ProofError, 'required vector mutation survived' unless rows.all? { |row| row['pass'] }
        end
    end
end

if $PROGRAM_NAME == __FILE__
    begin
        release, run_dir, out, baseline = ARGV
        raise ArgumentError, 'release run-dir out baseline required' unless baseline
        OTCVectors::MutationProof.run(release, run_dir, out, baseline)
    rescue OTCCaseOracle::ProofError, ArgumentError => error
        warn "FAIL: #{error.message}"
        exit 1
    end
end

#!/usr/bin/env ruby
# Explicit delta selection and binding of pinned country-research outputs.
# This tool never performs network requests or changes scientific inputs.
require 'digest'
require 'fileutils'
require 'json'
require 'optparse'
require 'pathname'

options = {}
OptionParser.new do |parser|
    parser.banner = 'Usage: refresh_country_resolutions.rb --manifest PATH --manifest-sha256 SHA --requests PATH [--cache PATH --cache-sha256 SHA] [--backend-results PATH --backend-results-sha256 SHA --output PATH]'
    %w[manifest manifest-sha256 requests cache cache-sha256 backend-results backend-results-sha256 output].each do |name|
        parser.on("--#{name} VALUE") { |value| options[name] = value }
    end
end.parse!
%w[manifest manifest-sha256 requests].each { |name| raise "--#{name} required" unless options[name] }
[%w[cache cache-sha256], %w[backend-results backend-results-sha256 output]].each do |names|
    raise "#{names.join('/')} must be supplied together" if names.any? { |name| options[name] } && !names.all? { |name| options[name] }
end

def pinned(path, expected)
    raise "invalid SHA-256: #{path}" unless expected.match?(/\A[0-9a-f]{64}\z/)
    raw = File.binread(path)
    raise "SHA-256 mismatch: #{path}" unless Digest::SHA256.hexdigest(raw) == expected
    [JSON.parse(raw), raw]
end

def canonical(value)
    case value
    when Hash then value.keys.sort.to_h { |key| [key, canonical(value[key])] }
    when Array then value.map { |item| canonical(item) }
    else value
    end
end

def raw_key(row)
    [row.fetch('source'), row.fetch('source_record_id'), row.fetch('upstream_raw_sha256')]
end

def evidence_files(value, root, found = {})
    if value.is_a?(Hash)
        name = value['path'] || value['evidence_file']
        if name && value['sha256']
            raise 'evidence path must be relative' if Pathname.new(name).absolute?
            path = File.realpath(File.join(root, name))
            raise 'evidence path escapes bundle' unless path.start_with?(File.realpath(root) + '/')
            expected = value.fetch('sha256')
            raise 'conflicting evidence hash' if found[name] && found[name][1] != expected
            unless found[name]
                raise "evidence hash mismatch: #{name}" unless Digest::SHA256.file(path).hexdigest == expected
                found[name] = [path, expected]
            end
        end
        value.each_value { |child| evidence_files(child, root, found) }
    elsif value.is_a?(Array)
        value.each { |child| evidence_files(child, root, found) }
    end
    found
end

manifest, = pinned(options.fetch('manifest'), options.fetch('manifest-sha256'))
old, old_raw = options['cache'] ? pinned(options['cache'], options['cache-sha256']) : [nil, nil]
old_entries = {}
(old ? old.fetch('entries') : []).each do |entry|
    key = raw_key(entry)
    raise 'duplicate cached raw key' if old_entries.key?(key)
    old_entries[key] = entry
end
requests = {}
units = 0
manifest.fetch('stations').each do |station|
    station.fetch('source_units').each do |unit|
        units += 1
        field = unit.fetch('upstream_raw_hash_fields').fetch(0)
        original = unit.fetch('original_metadata').fetch(field)
        digest = Digest::SHA256.hexdigest(JSON.generate(canonical({field => original})))
        raise 'source manifest raw hash mismatch' unless digest == unit.fetch('upstream_raw_sha256')
        key = raw_key(unit)
        next if old_entries.key?(key)
        request = requests[key] ||= {'source' => key[0], 'source_record_id' => key[1], 'upstream_raw_sha256' => key[2],
            'upstream_raw_hash_fields' => [field], 'station_ids' => [], 'source_versions' => [],
            'input_member_sha256' => [], 'assembled_position' => {'latitude' => station['lat'], 'longitude' => station['lon']}}
        raise 'raw key has conflicting assembled positions' unless request['assembled_position'] == {'latitude' => station['lat'], 'longitude' => station['lon']}
        request['station_ids'] |= [station.fetch('station_id')]
        request['source_versions'] |= [unit.fetch('source_version')]
        request['input_member_sha256'] |= unit.fetch('provenance').fetch('input_sha256')
    end
end
File.write(options.fetch('requests'), JSON.pretty_generate({'format_version' => 1,
    'manifest_sha256' => options.fetch('manifest-sha256'), 'cache_sha256' => options['cache-sha256'],
    'source_units' => units, 'new_or_changed_raw_keys' => requests.size, 'requests' => requests.values}) + "\n")

if options['backend-results']
    backend, = pinned(options['backend-results'], options.fetch('backend-results-sha256'))
    raise 'backend source manifest differs' unless backend.fetch('manifest_sha256') == options.fetch('manifest-sha256')
    methods = %w[marine_zone_territory_iso3 marine_zone_plus_official_country_code_crosswalk marine_zone_plus_station_registry_disambiguation official_source_plus_country_code_registry maritime_zone_ambiguous_after_source_lookup]
    resolved = {}
    backend.fetch('entries').each do |entry|
        raise 'unapproved resolution method' unless methods.include?(entry.fetch('method'))
        key = raw_key(entry)
        raise 'duplicate backend raw key' if resolved.key?(key)
        resolved[key] = entry
    end
    raise 'backend must cover every requested raw key' unless requests.keys.all? { |key| resolved.key?(key) }
    requested_results = resolved.select { |key, _| requests.key?(key) }
    requested_results.each do |key, entry|
        raise 'backend changed raw fields or coordinate basis' unless entry.fetch('upstream_raw_hash_fields') == requests[key]['upstream_raw_hash_fields'] && entry.fetch('assembled_position') == requests[key]['assembled_position']
    end
    output = File.expand_path(options.fetch('output'))
    output_root = File.dirname(output)
    FileUtils.mkdir_p(output_root)
    # Verify and copy actual descriptor bytes, preserving relative paths/hashes.
    bundles = [[backend, File.dirname(File.expand_path(options.fetch('backend-results')))]]
    bundles << [old, File.dirname(File.expand_path(options['cache']))] if old
    bundles.each do |table, root|
        evidence_files(table, root).each do |name, (source, digest)|
            destination = File.join(output_root, name)
            FileUtils.mkdir_p(File.dirname(destination))
            if File.exist?(destination)
                raise "output evidence collision: #{name}" unless Digest::SHA256.file(destination).hexdigest == digest
            else
                FileUtils.cp(source, destination)
            end
        end
    end
    if requests.empty? && old_raw
        File.binwrite(output, old_raw)
    else
        combined = old_entries.merge(requested_results)
        result = backend.merge('entries' => combined.values,
            'refresh_binding' => {'manifest_sha256' => options.fetch('manifest-sha256'),
                'backend_results_sha256' => options.fetch('backend-results-sha256'),
                'bound_new_or_changed_raw_keys' => requested_results.size, 'cache_entries' => combined.size,
                'network_calls' => 0})
        File.write(output, JSON.pretty_generate(result) + "\n")
    end
    puts JSON.generate({'bound_new_or_changed_keys' => requested_results.size,
        'output' => output, 'sha256' => Digest::SHA256.file(output).hexdigest})
end
puts JSON.generate({'source_units' => units, 'new_or_changed_raw_keys' => requests.size, 'network_calls' => 0})

#!/usr/bin/env ruby
# frozen_string_literal: true

##
## Fetch how NOAA predicts each of its tide prediction stations, and write it
## to data/noaa_station_types.json (NOAA id => "harmonic" or "subordinate").
##
## The harmonics engine reads that file when an XTide reference station and its
## "(sub)" twin share an id: it keeps the one that matches NOAA's method for the
## station (see Harmonics::Engine#store_station_data). The engine never calls NOAA.
##
## NOAA's station list marks each station with "type": "R" (a harmonic station,
## no reference_id) or "S" (a subordinate station, predicted by offsets from its
## reference_id).
##
## Usage: ruby scripts/build_noaa_station_types.rb           # write the file
##        ruby scripts/build_noaa_station_types.rb --check   # compare only
##
## --check is run monthly by .github/workflows/harmonics-data-monitor.yml.
## Exit codes: 0 = written, or the file is up to date, 1 = (--check) NOAA's
## types differ from the file, 2 = the fetch or parse failed, or the script
## itself failed.
##

require 'net/http'
require 'json'
require 'uri'

module NoaaStationTypes
    URL = 'https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json?type=tidepredictions'
    OUTPUT_PATH = File.expand_path('../data/noaa_station_types.json', __dir__)

    TYPES = { 'R' => 'harmonic', 'S' => 'subordinate' }.freeze

    EXIT_OK           = 0
    EXIT_CHANGED      = 1
    EXIT_CHECK_FAILED = 2

    RETRY_DELAY = 5

    class CheckError < StandardError; end

    module_function

    def fetch(url)
        attempts = 0
        begin
            attempts += 1
            uri = URI(url)
            response = Net::HTTP.start(uri.host, uri.port, use_ssl: uri.scheme == 'https',
                                       open_timeout: 15, read_timeout: 60) do |http|
                http.get(uri.request_uri, 'User-Agent' => 'webcaltides-harmonics-monitor')
            end
            raise CheckError, "HTTP #{response.code} from #{url}" unless response.is_a?(Net::HTTPSuccess)

            response.body.to_s
        rescue StandardError => e
            if attempts < 2
                sleep RETRY_DELAY
                retry
            end
            raise if e.is_a?(CheckError)

            raise CheckError, "#{e.class}: #{e.message}"
        end
    end

    # NOAA id => "harmonic" or "subordinate", sorted by id.
    def parse(body)
        parsed = begin
            JSON.parse(body)
        rescue JSON::ParserError => e
            raise CheckError, "unparseable NOAA response: #{e.message}"
        end
        stations = parsed['stations'] if parsed.is_a?(Hash)
        raise CheckError, 'unexpected NOAA response shape' unless stations.is_a?(Array) && stations.any?
        if parsed['count'].is_a?(Integer) && parsed['count'] != stations.size
            raise CheckError, "NOAA returned #{stations.size} of #{parsed['count']} stations"
        end

        types = {}
        stations.each do |s|
            id = s['id'] if s.is_a?(Hash)
            type = TYPES[s['type']] if s.is_a?(Hash)
            raise CheckError, "unexpected station #{s.inspect[0, 120]}" unless id.is_a?(String) && !id.empty? && type
            raise CheckError, "station #{id} listed twice with different types" if types[id] && types[id] != type

            types[id] = type
        end
        types.sort.to_h
    end

    def render(types)
        "#{JSON.pretty_generate(types)}\n"
    end

    def read_file(path)
        JSON.parse(File.read(path))
    rescue Errno::ENOENT
        {}
    end

    def changes(old, new)
        added   = new.keys - old.keys
        removed = old.keys - new.keys
        changed = (new.keys & old.keys).reject { |id| old[id] == new[id] }
        lines = []
        lines << "#{added.size} added (#{added.first(10).join(', ')}#{added.size > 10 ? ', ...' : ''})" if added.any?
        lines << "#{removed.size} removed (#{removed.first(10).join(', ')}#{removed.size > 10 ? ', ...' : ''})" if removed.any?
        changed.each { |id| lines << "#{id}: #{old[id]} -> #{new[id]}" }
        lines
    end

    def run(argv: ARGV, env: ENV, out: $stdout, err: $stderr, path: OUTPUT_PATH)
        check = argv.include?('--check')
        types = parse(fetch(URL))
        diff = changes(read_file(path), types)
        counts = types.values.tally.sort.map { |t, n| "#{n} #{t}" }.join(', ')

        message = if diff.empty?
            "up to date, #{types.size} stations (#{counts})"
        elsif check
            "CHANGED, run ruby scripts/build_noaa_station_types.rb and commit #{File.basename(path)}: #{diff.join('; ')}"
        else
            File.write(path, render(types))
            "wrote #{File.basename(path)}, #{types.size} stations (#{counts}): #{diff.join('; ')}"
        end
        out.puts "NOAA station types: #{message}"

        if (summary = env['GITHUB_STEP_SUMMARY']) && !summary.empty?
            File.open(summary, 'a') { |f| f.puts '## NOAA station types', '', "- #{message}" }
        end

        check && !diff.empty? ? EXIT_CHANGED : EXIT_OK
    rescue StandardError => e
        # Ruby exits 1 on an uncaught error, which would read as a change.
        err.puts "NOAA station types: CHECK FAILED: #{e.class}: #{e.message}"
        EXIT_CHECK_FAILED
    end
end

exit NoaaStationTypes.run if $PROGRAM_NAME == __FILE__

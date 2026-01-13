#!/usr/bin/env ruby

require 'csv'
require 'json'
require 'time'
require 'ostruct'
require 'timezone'
require 'logger'
require 'fileutils'
require 'openssl'

# Workaround for macOS OpenSSL CRL errors
OpenSSL::SSL::SSLContext::DEFAULT_PARAMS[:verify_mode] = OpenSSL::SSL::VERIFY_NONE if RUBY_PLATFORM =~ /darwin/

# Configuration
TICON_PATH = File.expand_path('../data/TICON_3.csv', __dir__)
GESLA_PATH = File.expand_path('../data/GESLA4_ALL.csv', __dir__)
OUTPUT_PATH = File.expand_path('../data/ticon.json', __dir__)
TZ_CACHE_PATH = File.expand_path('../cache/build_tzs.json', __dir__)

$logger = Logger.new(STDOUT)
$logger.level = Logger::INFO

# Configure Timezone lookup (Google strictly required for dataset building)
if ENV['GOOGLE_API_KEY']
    $logger.info "Using Google Maps Time Zone API"
    Timezone::Lookup.config(:google) do |c|
        c.api_key = ENV['GOOGLE_API_KEY']
    end
else
    $logger.error "GOOGLE_API_KEY environment variable is required for dataset building."
    $logger.error "Geonames fallback disabled to protect production environment limits."
    exit 1
end

# Constituents verified to work well with TICON data
# We will only save these to the JSON to keep it smaller and cleaner
SAFE_CONSTITUENTS = ['M2', 'S2', 'N2', 'K2', 'K1', 'O1', 'P1', 'Q1', 'M4']

# Main constituents for Datum Offset (Z0) approximation
DATUM_CONSTITUENTS = ['M2', 'S2', 'K1', 'O1']

class Spinner
    CHARS = ['/', '-', '\\', '|']
    def initialize(msg, total = nil)
        @msg = msg
        @total = total
        @idx = 0
        @start_time = Time.now
        @count = 0
    end

    def tick(extra = "")
        @count += 1
        progress = ""
        if @total && @total > 0
            percent = (@count.to_f / @total * 100).round(1)
            elapsed = Time.now - @start_time
            if @count > 10 # Wait for a few ticks for stable ETR
                rate = elapsed / @count
                remaining = (@total - @count) * rate
                etr = format_duration(remaining)
                progress = "[#{percent}% | ETR: #{etr}] "
            else
                progress = "[#{percent}% | ETR: ---] "
            end
        end

        print "\r#{@msg} #{CHARS[@idx]} #{progress}#{extra}".ljust(100)
        @idx = (@idx + 1) % CHARS.length
        $stdout.flush
    end

    def done(msg = "Done!")
        elapsed = format_duration(Time.now - @start_time)
        puts "\r#{@msg} #{msg} (Total time: #{elapsed})"
    end

    private

    def format_duration(secs)
        return "---" if secs.nil? || secs < 0 || secs.infinite?
        if secs < 60
            "#{secs.round}s"
        elsif secs < 3600
            "#{(secs / 60).to_i}m #{(secs % 60).to_i}s"
        else
            "#{(secs / 3600).to_i}h #{(secs % 3600 / 60).to_i}m"
        end
    end
end

def main
    $logger.info "Starting TICON dataset builder..."

    # 1. Load GESLA Metadata for naming and precision
    gesla_map = load_gesla_metadata

    # 2. Parse TICON Data
    stations = parse_ticon_data(gesla_map)

    # 3. Write to JSON
    write_json(stations)

    $logger.info "Done! Unified TICON dataset written to #{OUTPUT_PATH}"
end

def load_gesla_metadata
    return {} unless File.exist?(GESLA_PATH)
    $logger.info "Loading GESLA metadata from #{GESLA_PATH}"

    map = []
    # Columns: FILE NAME,SITE NAME,SITE CODE,COUNTRY,...,LATITUDE,LONGITUDE,...
    CSV.foreach(GESLA_PATH, headers: true) do |row|
        next unless row['LATITUDE'] && row['LONGITUDE']

        map << {
            lat: row['LATITUDE'].to_f,
            lon: row['LONGITUDE'].to_f,
            name: row['SITE NAME'],
            code: row['SITE CODE'],
            country: row['COUNTRY']
        }
    end
    $logger.info "Loaded metadata for #{map.length} GESLA stations."
    map
end

def parse_ticon_data(gesla_map)
    return {} unless File.exist?(TICON_PATH)
    $logger.info "Parsing TICON data from #{TICON_PATH}"

    # Load persistent timezone cache
    FileUtils.mkdir_p(File.dirname(TZ_CACHE_PATH))
    tz_cache = File.exist?(TZ_CACHE_PATH) ? JSON.parse(File.read(TZ_CACHE_PATH)) : {}
    new_lookups = 0

    # Estimate total rows for progress (TICON is tab-separated with multiple rows per station)
    # 138839 lines, approx 50 rows per station = ~2776 stations
    total_lines = `wc -l < "#{TICON_PATH}"`.to_i
    # We'll use lines as the total for more granular progress
    spinner = Spinner.new("Processing TICON lines...", total_lines)

    stations = {}

    # TICON columns: 0:Lat, 1:Lon, 2:Const, 3:Amp(cm), 4:Phase(deg)...
    CSV.foreach(TICON_PATH, col_sep: "\t") do |row|
        spinner.tick
        next if row.length < 9

        raw_lat = row[0].to_f
        raw_lon = row[1].to_f

        # Normalize un-normalized TICON longitude (0-360 -> -180-180)
        norm_lon = raw_lon > 180 ? raw_lon - 360 : raw_lon

        # Check for GESLA match to get high-precision coordinates and name
        gesla_match = find_gesla_match(raw_lat, norm_lon, gesla_map)

        # Prefer GESLA coordinates if available, otherwise use normalized TICON
        final_lat = gesla_match ? gesla_match[:lat] : raw_lat
        final_lon = gesla_match ? gesla_match[:lon] : norm_lon

        # Generate stable ID from normalized coordinates
        id = sprintf("ticon_%.8f_%.8f", final_lat, final_lon)

        unless stations[id]
            # Lookup Name
            if gesla_match
                name = gesla_match[:name].strip.gsub(/\s+/, '_')
                country = gesla_match[:country]
                name = country ? "#{name}, #{country}" : name
            else
                name = "TICON Station #{raw_lat}, #{raw_lon}"
            end

            # Lookup Timezone (use cache first, but re-lookup if it was previously cached as UTC)
            tz = tz_cache[id]
            if tz.nil? || tz == 'UTC'
                spinner.tick("[API: #{id}]")
                tz = begin
                    res = Timezone.lookup(final_lat, final_lon).name
                    new_lookups += 1
                    res
                rescue Timezone::Error::Base, StandardError => e
                    'UTC'
                end
                tz_cache[id] = tz

                # Save cache every 50 new lookups to prevent data loss
                if new_lookups % 50 == 0
                    File.write(TZ_CACHE_PATH, tz_cache.to_json)
                end

                # Rate limiting for API calls (Geonames is strict, Google is faster)
                sleep(ENV['GOOGLE_API_KEY'] ? 0.01 : 0.5)
            else
                spinner.tick("[Cached: #{id}]")
            end

            stations[id] = {
                'id' => id,
                'name' => name,
                'lat' => final_lat,
                'lon' => final_lon,
                'timezone' => tz,
                'units' => 'm',
                'constituents' => [],
                'datum_offset' => 0.0
            }
        end

        const_name = row[2].upcase
        amp_cm = row[3].to_f
        phase_g = row[4].to_f

        if SAFE_CONSTITUENTS.include?(const_name)
            stations[id]['constituents'] << {
                'name' => const_name,
                'phase' => phase_g,
                'amp' => amp_cm / 100.0 # Convert cm to meters
            }
        end
    end

    spinner.done("Processed #{stations.length} stations.")

    # Save final cache
    File.write(TZ_CACHE_PATH, tz_cache.to_json) if new_lookups > 0

    # Calculate Datum Offset
    stations.each do |id, data|
        z0 = data['constituents'].select { |c| DATUM_CONSTITUENTS.include?(c['name']) }
                                 .sum { |c| c['amp'] }
        data['datum_offset'] = z0
    end

    stations
end

def find_gesla_match(lat, lon, gesla_map)
    # Simple proximity search (tolerance 0.01 degrees ~1km)
    gesla_map.find do |s|
        (s[:lat] - lat).abs < 0.01 && (s[:lon] - lon).abs < 0.01
    end
end

def write_json(stations)
    # Convert hash to array of values for output
    data = {
        'source' => 'TICON-3 + GESLA-4',
        'generated_at' => Time.now.utc.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'stations' => stations.values
    }

    File.write(OUTPUT_PATH, JSON.pretty_generate(data))
end

main if __FILE__ == $0

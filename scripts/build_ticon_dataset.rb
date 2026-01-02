#!/usr/bin/env ruby

require 'csv'
require 'json'
require 'time'
require 'ostruct'
require 'timezone'
require 'logger'

# Configuration
TICON_PATH = File.expand_path('../data/TICON_3.csv', __dir__)
GESLA_PATH = File.expand_path('../data/GESLA4_ALL.csv', __dir__)
OUTPUT_PATH = File.expand_path('../data/ticon.json', __dir__)

# Configure Timezone lookup (matching server.rb)
Timezone::Lookup.config(:geonames) do |c|
    c.username = ENV['USER']
end

# Constituents verified to work well with TICON data
# We will only save these to the JSON to keep it smaller and cleaner
SAFE_CONSTITUENTS = ['M2', 'S2', 'N2', 'K2', 'K1', 'O1', 'P1', 'Q1', 'M4']

# Main constituents for Datum Offset (Z0) approximation
DATUM_CONSTITUENTS = ['M2', 'S2', 'K1', 'O1']

$logger = Logger.new(STDOUT)
$logger.level = Logger::INFO

def main
    $logger.info "Starting TICON dataset builder..."

    # 1. Load GESLA Metadata for naming
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

    stations = {}

    # TICON columns: 0:Lat, 1:Lon, 2:Const, 3:Amp(cm), 4:Phase(deg)...
    CSV.foreach(TICON_PATH, col_sep: "\t") do |row|
        next if row.length < 9

        lat = row[0].to_f
        lon = row[1].to_f

        # Normalize longitude (0-360 -> -180-180)
        norm_lon = lon > 180 ? lon - 360 : lon

        id = sprintf("ticon_%.4f_%.4f", lat, lon)

        unless stations[id]
            # Lookup Name
            name = find_station_name(lat, norm_lon, gesla_map)

            # Lookup Timezone
            # Using Timezone gem (Geonames API)
            tz = begin
                Timezone.lookup(lat, norm_lon).name
            rescue Timezone::Error::Base, StandardError => e
                # Only log warnings for non-ocean/invalid zone errors if verbose
                # $logger.warn "Timezone lookup failed for #{lat}, #{norm_lon}: #{e.message}"
                'UTC'
            end

            # Rate limiting for API calls
            sleep 0.05

            stations[id] = {
                'id' => id,
                'name' => name,
                'lat' => lat,
                'lon' => lon,
                'timezone' => tz,
                'units' => 'm',
                'constituents' => [],
                'datum_offset' => 0.0
            }

            if stations.length % 50 == 0
                print "."
                $stdout.flush
            end
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

    # Calculate Datum Offset
    stations.each do |id, data|
        z0 = data['constituents'].select { |c| DATUM_CONSTITUENTS.include?(c['name']) }
                                 .sum { |c| c['amp'] }
        data['datum_offset'] = z0
    end

    $logger.info "Processed #{stations.length} TICON stations."
    stations
end

def find_station_name(lat, lon, gesla_map)
    # Simple proximity search (tolerance 0.01 degrees ~1km)
    match = gesla_map.find do |s|
        (s[:lat] - lat).abs < 0.01 && (s[:lon] - lon).abs < 0.01
    end

    if match
        name = match[:name].strip.gsub(/\s+/, '_')
        country = match[:country]
        country ? "#{name}, #{country}" : name
    else
        "TICON Station #{lat}, #{lon}"
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

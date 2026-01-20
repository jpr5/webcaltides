#!/usr/bin/env ruby

# Precomputes region data for NOAA current stations by finding the nearest
# NOAA tide station (which has region info) for each current station.
#
# NOTE: This is now done automatically as part of the quarterly station cache
# refresh. This script is provided for manual regeneration if needed.
#
# Output: cache/noaa_current_regions_{quarter}.json

require 'bundler/setup'
require 'active_support/all'
require 'json'
require 'logger'
require 'geocoder'

require_relative '../clients/noaa_tides'
require_relative '../clients/noaa_currents'

OUTPUT_PATH = File.expand_path('../cache/noaa_current_regions.json', __dir__)

$logger = Logger.new(STDOUT).tap do |log|
    log.formatter = proc { |s, d, _, m| "#{d.strftime("%Y-%m-%d %H:%M:%S")} #{s} #{m}\n" }
end
$logger.level = Logger::INFO

def main
    $logger.info "fetching NOAA tide stations..."
    tide_client = Clients::NoaaTides.new($logger)
    tide_stations = tide_client.tide_stations
    $logger.info "loaded #{tide_stations.length} NOAA tide stations"

    $logger.info "fetching NOAA current stations..."
    current_client = Clients::NoaaCurrents.new($logger)
    current_stations = current_client.current_stations
    $logger.info "loaded #{current_stations.length} NOAA current stations"

    # Filter to stations that need enrichment (region is generic "United States")
    stations_to_enrich = current_stations.select { |s| s.region == 'United States' }
    $logger.info "#{stations_to_enrich.length} current stations need region enrichment"

    # Build mapping: station_id -> region
    mapping = {}
    total = stations_to_enrich.length
    enriched = 0

    stations_to_enrich.each_with_index do |cs, idx|
        next unless cs.lat && cs.lon

        # Find closest tide station
        closest = tide_stations.min_by do |ts|
            next Float::INFINITY unless ts.lat && ts.lon
            Geocoder::Calculations.distance_between([cs.lat, cs.lon], [ts.lat, ts.lon])
        end

        if closest&.region && closest.region != 'United States'
            mapping[cs.id] = closest.region
            enriched += 1
        end

        # Progress indicator
        if (idx + 1) % 500 == 0 || idx + 1 == total
            $logger.info "processed #{idx + 1}/#{total} stations (#{enriched} enriched)"
        end
    end

    $logger.info "writing mapping to #{OUTPUT_PATH}"
    File.write(OUTPUT_PATH, JSON.pretty_generate({
        'generated_at' => Time.now.utc.iso8601,
        'tide_station_count' => tide_stations.length,
        'current_station_count' => current_stations.length,
        'enriched_count' => enriched,
        'regions' => mapping
    }))

    $logger.info "done, mapped #{enriched} current stations to regions"
end

main

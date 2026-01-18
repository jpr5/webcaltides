#!/usr/bin/env ruby
##
## Compare XTide vs TICON accuracy against NOAA reference stations
##
## Usage: bundle exec ruby scripts/compare_harmonic_sources.rb
##

require 'bundler/setup'
require 'logger'
require 'json'

$LOG = Logger.new(STDOUT).tap do |log|
  log.formatter = proc { |s, d, _, m| "#{d.strftime("%Y-%m-%d %H:%M:%S")} #{s} #{m}\n" }
  log.level = Logger::INFO
end

# Minimal server settings stub
module Server
  def self.settings
    OpenStruct.new(
      cache_dir: File.expand_path('../../cache', __FILE__),
      root: File.expand_path('../..', __FILE__)
    )
  end
end

require_relative '../webcaltides'

# Reference NOAA stations to test (diverse geographic regions)
REFERENCE_STATIONS = [
  { name: "Seattle", noaa_id: "9447130", lat: 47.6025, lon: -122.3392 },
  { name: "San Francisco", noaa_id: "9414290", lat: 37.8063, lon: -122.4659 },
  { name: "Port Townsend", noaa_id: "9444900", lat: 48.1129, lon: -122.7595 },
  { name: "Astoria", noaa_id: "9439040", lat: 46.2073, lon: -123.7683 },
  { name: "Los Angeles", noaa_id: "9410660", lat: 33.7200, lon: -118.2720 },
  { name: "New York (Battery)", noaa_id: "8518750", lat: 40.7006, lon: -74.0142 },
  { name: "Boston", noaa_id: "8443970", lat: 42.3539, lon: -71.0503 },
  { name: "Miami", noaa_id: "8723214", lat: 25.7687, lon: -80.1317 },
]

PROXIMITY_THRESHOLD = 0.1  # ~11km in degrees

def find_harmonic_stations_near(lat, lon, provider)
  all_stations = WebCalTides.tide_stations

  all_stations.select do |s|
    s.provider == provider &&
    (s.lat - lat).abs < PROXIMITY_THRESHOLD &&
    (s.lon - lon).abs < PROXIMITY_THRESHOLD
  end
end

def get_predictions(station, around)
  return nil unless station

  data = WebCalTides.tide_data_for(station, around: around)
  return nil unless data && data.any?

  # Filter to high/low events only
  data.select { |d| d.type.in?(['High', 'Low']) }
      .sort_by(&:time)
end

def calculate_errors(noaa_events, harmonic_events)
  return nil unless noaa_events && harmonic_events && noaa_events.any? && harmonic_events.any?

  time_errors = []
  height_errors = []

  noaa_events.each do |noaa|
    # Find closest matching harmonic event of same type within 3 hours
    match = harmonic_events.find do |h|
      h.type == noaa.type && (h.time - noaa.time).abs < 3.hours
    end

    next unless match

    time_diff = (match.time - noaa.time).to_f  # seconds
    height_diff = match.prediction - noaa.prediction  # feet or meters

    time_errors << time_diff
    height_errors << height_diff
  end

  return nil if time_errors.empty?

  {
    count: time_errors.length,
    time_mean: (time_errors.sum / time_errors.length).round(1),
    time_rms: Math.sqrt(time_errors.map { |e| e**2 }.sum / time_errors.length).round(1),
    time_max: time_errors.map(&:abs).max.round(1),
    height_mean: (height_errors.sum / height_errors.length).round(3),
    height_rms: Math.sqrt(height_errors.map { |e| e**2 }.sum / height_errors.length).round(3),
    height_max: height_errors.map(&:abs).max.round(3)
  }
end

def format_seconds(s)
  sign = s < 0 ? "-" : "+"
  s = s.abs
  if s >= 60
    "#{sign}#{(s / 60).round(1)}min"
  else
    "#{sign}#{s.round(0)}s"
  end
end

puts "=" * 80
puts "XTide vs TICON Accuracy Comparison"
puts "Reference: NOAA Tide Predictions"
puts "=" * 80
puts

around = Time.current.utc
results = []

REFERENCE_STATIONS.each do |ref|
  puts "\n#{ref[:name]} (NOAA: #{ref[:noaa_id]})"
  puts "-" * 40

  # Find NOAA station (ID is just the number, not prefixed)
  noaa_station = WebCalTides.tide_stations.find { |s| s.id == ref[:noaa_id] && s.provider == 'noaa' }
  unless noaa_station
    puts "  NOAA station not found, skipping"
    next
  end

  # Get NOAA predictions
  noaa_events = get_predictions(noaa_station, around)
  unless noaa_events && noaa_events.any?
    puts "  No NOAA predictions available, skipping"
    next
  end
  puts "  NOAA: #{noaa_events.length} events"

  # Find XTide stations near this location
  xtide_stations = find_harmonic_stations_near(ref[:lat], ref[:lon], 'xtide')
  ticon_stations = find_harmonic_stations_near(ref[:lat], ref[:lon], 'ticon')

  puts "  XTide stations nearby: #{xtide_stations.length}"
  puts "  TICON stations nearby: #{ticon_stations.length}"

  # Compare XTide
  xtide_station = xtide_stations.first
  if xtide_station
    xtide_events = get_predictions(xtide_station, around)
    if xtide_events && xtide_events.any?
      xtide_errors = calculate_errors(noaa_events, xtide_events)
      if xtide_errors
        puts "  XTide (#{xtide_station.name}):"
        puts "    Matched events: #{xtide_errors[:count]}"
        puts "    Time error:   mean=#{format_seconds(xtide_errors[:time_mean])}, RMS=#{format_seconds(xtide_errors[:time_rms])}, max=#{format_seconds(xtide_errors[:time_max])}"
        puts "    Height error: mean=#{xtide_errors[:height_mean].round(2)}ft, RMS=#{xtide_errors[:height_rms].round(2)}ft, max=#{xtide_errors[:height_max].round(2)}ft"
        results << { location: ref[:name], provider: 'xtide', errors: xtide_errors }
      end
    else
      puts "  XTide: no predictions available"
    end
  else
    puts "  XTide: no station found"
  end

  # Compare TICON
  ticon_station = ticon_stations.first
  if ticon_station
    ticon_events = get_predictions(ticon_station, around)
    if ticon_events && ticon_events.any?
      ticon_errors = calculate_errors(noaa_events, ticon_events)
      if ticon_errors
        puts "  TICON (#{ticon_station.name}):"
        puts "    Matched events: #{ticon_errors[:count]}"
        puts "    Time error:   mean=#{format_seconds(ticon_errors[:time_mean])}, RMS=#{format_seconds(ticon_errors[:time_rms])}, max=#{format_seconds(ticon_errors[:time_max])}"
        puts "    Height error: mean=#{ticon_errors[:height_mean].round(2)}ft, RMS=#{ticon_errors[:height_rms].round(2)}ft, max=#{ticon_errors[:height_max].round(2)}ft"
        results << { location: ref[:name], provider: 'ticon', errors: ticon_errors }
      end
    else
      puts "  TICON: no predictions available"
    end
  else
    puts "  TICON: no station found"
  end
end

# Summary
puts "\n" + "=" * 80
puts "SUMMARY"
puts "=" * 80

xtide_results = results.select { |r| r[:provider] == 'xtide' }
ticon_results = results.select { |r| r[:provider] == 'ticon' }

if xtide_results.any?
  avg_time_rms = xtide_results.map { |r| r[:errors][:time_rms] }.sum / xtide_results.length
  avg_height_rms = xtide_results.map { |r| r[:errors][:height_rms] }.sum / xtide_results.length
  puts "\nXTide (#{xtide_results.length} stations):"
  puts "  Average Time RMS:   #{format_seconds(avg_time_rms)}"
  puts "  Average Height RMS: #{avg_height_rms.round(3)}ft"
end

if ticon_results.any?
  avg_time_rms = ticon_results.map { |r| r[:errors][:time_rms] }.sum / ticon_results.length
  avg_height_rms = ticon_results.map { |r| r[:errors][:height_rms] }.sum / ticon_results.length
  puts "\nTICON (#{ticon_results.length} stations):"
  puts "  Average Time RMS:   #{format_seconds(avg_time_rms)}"
  puts "  Average Height RMS: #{avg_height_rms.round(3)}ft"
end

if xtide_results.any? && ticon_results.any?
  puts "\nRECOMMENDATION:"
  xtide_time = xtide_results.map { |r| r[:errors][:time_rms] }.sum / xtide_results.length
  ticon_time = ticon_results.map { |r| r[:errors][:time_rms] }.sum / ticon_results.length
  xtide_height = xtide_results.map { |r| r[:errors][:height_rms] }.sum / xtide_results.length
  ticon_height = ticon_results.map { |r| r[:errors][:height_rms] }.sum / ticon_results.length

  if xtide_time < ticon_time && xtide_height < ticon_height
    puts "  XTide is more accurate overall"
  elsif ticon_time < xtide_time && ticon_height < ticon_height
    puts "  TICON is more accurate overall"
  else
    puts "  Mixed results - XTide better for #{xtide_time < ticon_time ? 'timing' : 'heights'}, TICON better for #{ticon_time < xtide_time ? 'timing' : 'heights'}"
  end
end

puts

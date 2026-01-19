#!/usr/bin/env ruby
##
## Compare XTide vs TICON accuracy against NOAA reference stations
##
## Usage: bundle exec ruby scripts/compare_harmonic_sources.rb [tides|currents|all]
##

require 'bundler/setup'
require 'logger'
require 'json'

$LOG = Logger.new(STDOUT).tap do |log|
  log.formatter = proc { |s, d, _, m| "#{d.strftime("%Y-%m-%d %H:%M:%S")} #{s} #{m}\n" }
  log.level = Logger::INFO
end

require_relative '../webcaltides'

# Reference NOAA tide stations (diverse geographic regions)
REFERENCE_TIDE_STATIONS = [
  { name: "Seattle", noaa_id: "9447130", lat: 47.6025, lon: -122.3392 },
  { name: "San Francisco", noaa_id: "9414290", lat: 37.8063, lon: -122.4659 },
  { name: "Port Townsend", noaa_id: "9444900", lat: 48.1129, lon: -122.7595 },
  { name: "Astoria", noaa_id: "9439040", lat: 46.2073, lon: -123.7683 },
  { name: "Los Angeles", noaa_id: "9410660", lat: 33.7200, lon: -118.2720 },
  { name: "New York (Battery)", noaa_id: "8518750", lat: 40.7006, lon: -74.0142 },
  { name: "Boston", noaa_id: "8443970", lat: 42.3539, lon: -71.0503 },
  { name: "Miami", noaa_id: "8723214", lat: 25.7687, lon: -80.1317 },
]

# Reference NOAA current stations (major tidal current locations)
REFERENCE_CURRENT_STATIONS = [
  { name: "Golden Gate", noaa_id: "SFB1203", lat: 37.8117, lon: -122.4650 },
  { name: "The Race (Long Island Sound)", noaa_id: "ACT4996", lat: 41.2267, lon: -72.0650 },
  { name: "Chesapeake Bay Entrance", noaa_id: "cb0102", lat: 36.9983, lon: -76.0133 },
  { name: "Puget Sound (Admiralty Inlet)", noaa_id: "PUG1515", lat: 48.1583, lon: -122.7567 },
  { name: "Tampa Bay Entrance", noaa_id: "TPA0801", lat: 27.5850, lon: -82.7417 },
  { name: "Delaware Bay Entrance", noaa_id: "db0101", lat: 38.7833, lon: -75.0667 },
]

PROXIMITY_THRESHOLD = 0.15  # ~17km in degrees (currents may be more spread out)

def format_seconds(s)
  sign = s < 0 ? "-" : "+"
  s = s.abs
  if s >= 60
    "#{sign}#{(s / 60).round(1)}min"
  else
    "#{sign}#{s.round(0)}s"
  end
end

#
# TIDE COMPARISON
#

def find_harmonic_tide_stations_near(lat, lon, provider)
  WebCalTides.tide_stations.select do |s|
    s.provider == provider &&
    (s.lat - lat).abs < PROXIMITY_THRESHOLD &&
    (s.lon - lon).abs < PROXIMITY_THRESHOLD
  end
end

def get_tide_predictions(station, around)
  return nil unless station

  data = WebCalTides.tide_data_for(station, around: around)
  return nil unless data && data.any?

  # Filter to high/low events only
  data.select { |d| d.type.in?(['High', 'Low']) }
      .sort_by(&:time)
end

def calculate_tide_errors(noaa_events, harmonic_events)
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
    value_mean: (height_errors.sum / height_errors.length).round(3),
    value_rms: Math.sqrt(height_errors.map { |e| e**2 }.sum / height_errors.length).round(3),
    value_max: height_errors.map(&:abs).max.round(3)
  }
end

def run_tide_comparison
  puts "=" * 80
  puts "TIDE COMPARISON: XTide vs TICON"
  puts "Reference: NOAA Tide Predictions"
  puts "=" * 80

  around = Time.current.utc
  results = []

  REFERENCE_TIDE_STATIONS.each do |ref|
    puts "\n#{ref[:name]} (NOAA: #{ref[:noaa_id]})"
    puts "-" * 40

    # Find NOAA station
    noaa_station = WebCalTides.tide_stations.find { |s| s.id == ref[:noaa_id] && s.provider == 'noaa' }
    unless noaa_station
      puts "  NOAA station not found, skipping"
      next
    end

    # Get NOAA predictions
    noaa_events = get_tide_predictions(noaa_station, around)
    unless noaa_events && noaa_events.any?
      puts "  No NOAA predictions available, skipping"
      next
    end
    puts "  NOAA: #{noaa_events.length} events"

    # Find harmonic stations near this location
    xtide_stations = find_harmonic_tide_stations_near(ref[:lat], ref[:lon], 'xtide')
    ticon_stations = find_harmonic_tide_stations_near(ref[:lat], ref[:lon], 'ticon')

    puts "  XTide stations nearby: #{xtide_stations.length}"
    puts "  TICON stations nearby: #{ticon_stations.length}"

    # Compare XTide
    xtide_station = xtide_stations.first
    if xtide_station
      xtide_events = get_tide_predictions(xtide_station, around)
      if xtide_events && xtide_events.any?
        xtide_errors = calculate_tide_errors(noaa_events, xtide_events)
        if xtide_errors
          puts "  XTide (#{xtide_station.name}):"
          puts "    Matched events: #{xtide_errors[:count]}"
          puts "    Time error:   mean=#{format_seconds(xtide_errors[:time_mean])}, RMS=#{format_seconds(xtide_errors[:time_rms])}, max=#{format_seconds(xtide_errors[:time_max])}"
          puts "    Height error: mean=#{xtide_errors[:value_mean].round(2)}ft, RMS=#{xtide_errors[:value_rms].round(2)}ft, max=#{xtide_errors[:value_max].round(2)}ft"
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
      ticon_events = get_tide_predictions(ticon_station, around)
      if ticon_events && ticon_events.any?
        ticon_errors = calculate_tide_errors(noaa_events, ticon_events)
        if ticon_errors
          puts "  TICON (#{ticon_station.name}):"
          puts "    Matched events: #{ticon_errors[:count]}"
          puts "    Time error:   mean=#{format_seconds(ticon_errors[:time_mean])}, RMS=#{format_seconds(ticon_errors[:time_rms])}, max=#{format_seconds(ticon_errors[:time_max])}"
          puts "    Height error: mean=#{ticon_errors[:value_mean].round(2)}ft, RMS=#{ticon_errors[:value_rms].round(2)}ft, max=#{ticon_errors[:value_max].round(2)}ft"
          results << { location: ref[:name], provider: 'ticon', errors: ticon_errors }
        end
      else
        puts "  TICON: no predictions available"
      end
    else
      puts "  TICON: no station found"
    end
  end

  print_summary(results, "TIDE", "Height")
end

#
# CURRENT COMPARISON
#

def find_harmonic_current_stations_near(lat, lon, provider)
  WebCalTides.current_stations.select do |s|
    s.provider == provider &&
    (s.lat - lat).abs < PROXIMITY_THRESHOLD &&
    (s.lon - lon).abs < PROXIMITY_THRESHOLD
  end
end

def get_current_predictions(station, around)
  return nil unless station

  data = WebCalTides.current_data_for(station, around: around)
  return nil unless data && data.any?

  # Filter to flood/ebb events (skip slack for velocity comparison)
  data.select { |d| d.type.downcase.in?(['flood', 'ebb']) }
      .sort_by(&:time)
end

def calculate_current_errors(noaa_events, harmonic_events)
  return nil unless noaa_events && harmonic_events && noaa_events.any? && harmonic_events.any?

  time_errors = []
  velocity_errors = []

  noaa_events.each do |noaa|
    noaa_type = noaa.type.downcase
    # Find closest matching harmonic event of same type within 3 hours
    match = harmonic_events.find do |h|
      h.type.downcase == noaa_type && (h.time - noaa.time).abs < 3.hours
    end

    next unless match
    next unless noaa.velocity_major && match.velocity_major

    time_diff = (match.time - noaa.time).to_f  # seconds
    velocity_diff = match.velocity_major.to_f - noaa.velocity_major.to_f  # knots

    time_errors << time_diff
    velocity_errors << velocity_diff
  end

  return nil if time_errors.empty?

  {
    count: time_errors.length,
    time_mean: (time_errors.sum / time_errors.length).round(1),
    time_rms: Math.sqrt(time_errors.map { |e| e**2 }.sum / time_errors.length).round(1),
    time_max: time_errors.map(&:abs).max.round(1),
    value_mean: (velocity_errors.sum / velocity_errors.length).round(3),
    value_rms: Math.sqrt(velocity_errors.map { |e| e**2 }.sum / velocity_errors.length).round(3),
    value_max: velocity_errors.map(&:abs).max.round(3)
  }
end

def run_current_comparison
  puts "=" * 80
  puts "CURRENT COMPARISON: XTide vs TICON"
  puts "Reference: NOAA Current Predictions"
  puts "=" * 80

  around = Time.current.utc
  results = []

  REFERENCE_CURRENT_STATIONS.each do |ref|
    puts "\n#{ref[:name]} (NOAA: #{ref[:noaa_id]})"
    puts "-" * 40

    # Find NOAA station - current stations use bid, not id
    noaa_station = WebCalTides.current_stations.find do |s|
      s.provider == 'noaa' && (s.id == ref[:noaa_id] || s.bid&.start_with?(ref[:noaa_id]))
    end
    unless noaa_station
      puts "  NOAA station not found, skipping"
      next
    end

    # Get NOAA predictions
    noaa_events = get_current_predictions(noaa_station, around)
    unless noaa_events && noaa_events.any?
      puts "  No NOAA predictions available, skipping"
      next
    end
    puts "  NOAA: #{noaa_events.length} flood/ebb events"

    # Find harmonic stations near this location
    xtide_stations = find_harmonic_current_stations_near(ref[:lat], ref[:lon], 'xtide')
    ticon_stations = find_harmonic_current_stations_near(ref[:lat], ref[:lon], 'ticon')

    puts "  XTide stations nearby: #{xtide_stations.length}"
    puts "  TICON stations nearby: #{ticon_stations.length}"

    # Compare XTide
    xtide_station = xtide_stations.first
    if xtide_station
      xtide_events = get_current_predictions(xtide_station, around)
      if xtide_events && xtide_events.any?
        xtide_errors = calculate_current_errors(noaa_events, xtide_events)
        if xtide_errors
          puts "  XTide (#{xtide_station.name}):"
          puts "    Matched events: #{xtide_errors[:count]}"
          puts "    Time error:     mean=#{format_seconds(xtide_errors[:time_mean])}, RMS=#{format_seconds(xtide_errors[:time_rms])}, max=#{format_seconds(xtide_errors[:time_max])}"
          puts "    Velocity error: mean=#{xtide_errors[:value_mean].round(2)}kn, RMS=#{xtide_errors[:value_rms].round(2)}kn, max=#{xtide_errors[:value_max].round(2)}kn"
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
      ticon_events = get_current_predictions(ticon_station, around)
      if ticon_events && ticon_events.any?
        ticon_errors = calculate_current_errors(noaa_events, ticon_events)
        if ticon_errors
          puts "  TICON (#{ticon_station.name}):"
          puts "    Matched events: #{ticon_errors[:count]}"
          puts "    Time error:     mean=#{format_seconds(ticon_errors[:time_mean])}, RMS=#{format_seconds(ticon_errors[:time_rms])}, max=#{format_seconds(ticon_errors[:time_max])}"
          puts "    Velocity error: mean=#{ticon_errors[:value_mean].round(2)}kn, RMS=#{ticon_errors[:value_rms].round(2)}kn, max=#{ticon_errors[:value_max].round(2)}kn"
          results << { location: ref[:name], provider: 'ticon', errors: ticon_errors }
        end
      else
        puts "  TICON: no predictions available"
      end
    else
      puts "  TICON: no station found"
    end
  end

  print_summary(results, "CURRENT", "Velocity")
end

#
# SUMMARY
#

def print_summary(results, type, value_label)
  puts "\n" + "=" * 80
  puts "#{type} SUMMARY"
  puts "=" * 80

  xtide_results = results.select { |r| r[:provider] == 'xtide' }
  ticon_results = results.select { |r| r[:provider] == 'ticon' }

  unit = type == "TIDE" ? "ft" : "kn"

  if xtide_results.any?
    avg_time_rms = xtide_results.map { |r| r[:errors][:time_rms] }.sum / xtide_results.length
    avg_value_rms = xtide_results.map { |r| r[:errors][:value_rms] }.sum / xtide_results.length
    puts "\nXTide (#{xtide_results.length} stations):"
    puts "  Average Time RMS:     #{format_seconds(avg_time_rms)}"
    puts "  Average #{value_label} RMS: #{avg_value_rms.round(3)}#{unit}"
  end

  if ticon_results.any?
    avg_time_rms = ticon_results.map { |r| r[:errors][:time_rms] }.sum / ticon_results.length
    avg_value_rms = ticon_results.map { |r| r[:errors][:value_rms] }.sum / ticon_results.length
    puts "\nTICON (#{ticon_results.length} stations):"
    puts "  Average Time RMS:     #{format_seconds(avg_time_rms)}"
    puts "  Average #{value_label} RMS: #{avg_value_rms.round(3)}#{unit}"
  end

  if xtide_results.any? && ticon_results.any?
    puts "\nRECOMMENDATION:"
    xtide_time = xtide_results.map { |r| r[:errors][:time_rms] }.sum / xtide_results.length
    ticon_time = ticon_results.map { |r| r[:errors][:time_rms] }.sum / ticon_results.length
    xtide_value = xtide_results.map { |r| r[:errors][:value_rms] }.sum / xtide_results.length
    ticon_value = ticon_results.map { |r| r[:errors][:value_rms] }.sum / ticon_results.length

    if xtide_time <= ticon_time && xtide_value <= ticon_value
      puts "  XTide is more accurate overall for #{type.downcase}s"
    elsif ticon_time <= xtide_time && ticon_value <= xtide_value
      puts "  TICON is more accurate overall for #{type.downcase}s"
    else
      time_winner = xtide_time < ticon_time ? 'XTide' : 'TICON'
      value_winner = xtide_value < ticon_value ? 'XTide' : 'TICON'
      puts "  Mixed results - #{time_winner} better for timing, #{value_winner} better for #{value_label.downcase}"
    end
  elsif xtide_results.empty? && ticon_results.empty?
    puts "\nNo harmonic stations found near reference locations."
  end

  puts
end

#
# MAIN
#

mode = ARGV[0] || 'all'

case mode.downcase
when 'tides', 'tide'
  run_tide_comparison
when 'currents', 'current'
  run_current_comparison
when 'all'
  run_tide_comparison
  puts "\n\n"
  run_current_comparison
else
  puts "Usage: #{$0} [tides|currents|all]"
  exit 1
end

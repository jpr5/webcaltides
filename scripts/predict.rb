#!/usr/bin/env ruby

require 'logger'
require 'active_support/all'
require 'mechanize'
require 'nokogiri'
require 'optparse'
require_relative '../lib/harmonics_engine'

# Mock settings for WebCalTides
module WebCalTides
    def self.settings
        Struct.new(:cache_dir).new('cache')
    end
end

class TideForecastScraper
    def initialize(logger)
        @logger = logger
        @agent = Mechanize.new
        @agent.user_agent_alias = 'Mac Safari'
    end

    def fetch_tides(slug, date)
        url = "https://www.tide-forecast.com/locations/#{slug}/tides/latest"
        @logger.debug "Fetching live data from #{url}"

        begin
            page = @agent.get(url)
            doc = Nokogiri::HTML(page.body)

            day_name = date.strftime("%A")
            day_num = date.day.to_s
            day_num_padded = date.strftime("%d")
            month_name = date.strftime("%B")

            tide_events = []

            header = doc.css('h4').find do |h|
                t = h.text
                t.include?(day_name) && t.include?(month_name) && (t.include?(" #{day_num} ") || t.include?(" #{day_num_padded} "))
            end

            if header
                container = header.next_element
                container = container.next_element while container && !container.classes.include?('tide-day__tables')

                if container
                    table = container.at_css('table.tide-day-tides')
                    if table
                        table.css('tr').each do |row|
                            next if row.at_css('th')
                            cells = row.css('td')
                            next if cells.length < 3

                            type_text = cells[0].text.strip
                            time_text = cells[1].at_css('b')&.text&.strip
                            height_text = cells[2].at_css('.js-two-units-length-value__primary')&.text&.strip || cells[2].text.strip

                            next unless time_text && height_text

                            type = type_text.split(' ').first
                            val = height_text.to_f
                            unit = height_text.match(/[a-z]+/i)&.[](0) || "ft"

                            tide_events << {
                                type: type,
                                time_str: time_text,
                                height: val,
                                units: unit
                            }
                        end
                    end
                end
            end
            tide_events
        rescue => e
            @logger.error "Failed to fetch from tide-forecast: #{e.message}"
            []
        end
    end
end

class HarmonicsTestRunner
    LOCATION_POOL = [
        { name: "Kwajalein", slug: "Kwajalein-Atoll-Namur-Island-Marshall-Islands" },
        { name: "Malakal Harbor", slug: "Malakal-Harbor-Palau-Islands-Caroline-Islands" },
        { name: "Wake Island", slug: "Wake-Island-Pacific-Ocean" },
        { name: "Chuuk", slug: "Moen-Island-Chuuk-FederatedStatesofMicronesia" },
        { name: "Johnston Atoll", slug: "Johnston-Atoll-Pacific-Ocean" },
        { name: "Sand Island, Midway Islands", slug: "Sand-Island-Midway-Islands" },
        { name: "Apra Harbor, Guam", slug: "Apra-Harbor-Guam" },
        { name: "Pago Pago, American Samoa", slug: "Pago-Pago-American-Samoa" },
        { name: "Honolulu", slug: "Honolulu-Oahu-Hawaii" },
        { name: "Hilo", slug: "Hilo-Hilo-Bay-Hawaii" },
        { name: "Port Allen", slug: "Port-Allen-Hanapepe-Bay-Kauai-Island-Hawaii" },
        { name: "Nawiliwili", slug: "Nawiliwili-Nawiliwili-Harbor-Kauai-Island-Hawaii" },
        { name: "Kawaihae", slug: "Kawaihae-Hawaii-Island-Hawaii" },
        { name: "Majuro Atoll", slug: "Majuro-Atoll-Marshall-Islands" },
        { name: "Bikini Atoll", slug: "Bikini-Atoll-Marshall-Islands" },
        { name: "Koror", slug: "Koror-Palau-Islands" },
        { name: "Tanapag Harbor, Saipan", slug: "Tanapag-Harbor-Saipan-Island-Marianas" },
        { name: "Pago Bay, Guam", slug: "Pago-Bay-Guam" },
        { name: "Eniirikku Island, Bikini Atoll", slug: "Eniirikku-Island-Bikini-Atoll-Marshall-Islands" },
        { name: "Jaluit Atoll", slug: "Jaluit-Atoll-SE-Pass-Marshall-Islands" },
        { name: "Sydney (Fort Denison)", slug: "Sydney-Australia", id: "ticon_-33.85500000_151.22700000" },
        { name: "Tokyo", slug: "Tokyo-Japan", id: "ticon_35.64861667_139.77000000" },
        { name: "San Francisco", slug: "San-Francisco-California", id: "ticon_37.80700000_-122.46500000" },
        { name: "Seattle", slug: "Seattle-Washington", id: "ticon_47.60194400_-122.33916700" }
    ]

    def initialize(logger, engine, scraper)
        @logger = logger
        @engine = engine
        @scraper = scraper
    end

    def run_tests(locations, stations)
        locations.each do |loc|
            search_term = loc[:name]
            puts "\n" + "=" * 100
            puts "Testing: #{search_term}"

            # Match by ID first, then by name
            station = nil
            if loc[:id]
                station = stations.find { |s| s['id'] == loc[:id] }
                unless station
                    puts "Debug: Could not find station with ID '#{loc[:id]}'"
                    # Try fuzzy matching coords if it looks like a ticon ID
                    if loc[:id].start_with?('ticon_')
                        parts = loc[:id].sub('ticon_', '').split('_')
                        if parts.length == 2
                            lat_target = parts[0].to_f
                            lon_target = parts[1].to_f
                            puts "Debug: Attempting coordinate match for #{lat_target}, #{lon_target}..."
                            station = stations.find do |s|
                                s['lat'] && (s['lat'] - lat_target).abs < 0.0001 &&
                                s['lon'] && (s['lon'] - lon_target).abs < 0.0001
                            end
                            puts "Debug: Coordinate match found: #{station['id']}" if station
                        end
                    end
                end
            else
                station = stations.find { |s| s['name'] && s['name'].include?(search_term) }
            end

            if !station
                puts "No stations found matching '#{search_term}'"
                next
            end

            puts "Station:  #{station['name']} (ID: #{station['id']})"
            puts "Timezone: #{station['timezone']}"

            tz = ActiveSupport::TimeZone[station['timezone']] || ActiveSupport::TimeZone['UTC']
            start_time = tz.now.beginning_of_day + 1.day
            end_time = start_time + 24.hours

            puts "\nComparing data for #{start_time.strftime('%A %-d %B %Y %Z')}..."

            predicted_peaks = @engine.detect_peaks(@engine.generate_predictions(station['id'], start_time, end_time))
            live_peaks = @scraper.fetch_tides(loc[:slug], start_time)

            compare_and_display(predicted_peaks, live_peaks, tz, start_time)
        end
    end

    private

    def compare_and_display(predicted_peaks, live_peaks, tz, reference_date)
        puts "\n| Event | Predicted (Our Engine)      | Live (Tide-Forecast)      | Difference            |"
        puts "| :---  | :---                        | :---                      | :---                  |"

        all_events = []
        remaining_live = live_peaks.dup

        predicted_peaks.each do |pred|
            closest_live = remaining_live.find do |live|
                next unless live[:type] == pred['type']
                live_time = tz.parse(live[:time_str], reference_date)
                (live_time - pred['time']).abs < 2.hours
            end

            all_events << { pred: pred, live: closest_live }
            remaining_live.delete(closest_live) if closest_live
        end

        remaining_live.each do |live|
            all_events << { pred: nil, live: live }
        end

        all_events.sort_by! do |e|
            if e[:pred]
                e[:pred]['time']
            else
                tz.parse(e[:live][:time_str], reference_date)
            end
        end

        all_events.each do |event|
            pred, live = event[:pred], event[:live]
            type = (pred ? pred['type'] : (live ? live[:type] : "?")).ljust(5)

            p_height = pred ? pred['height'] : 0.0
            p_units = pred ? pred['units'] : "ft"

            display_p_height = p_height
            p_display_units = p_units

            if p_units == 'm' && (live && live[:units] == 'ft')
                display_p_height = (p_height * 3.28084).round(3)
                p_display_units = "ft (conv)"
            end

            pred_str = "---"
            if pred
                pred_str = "#{pred['time'].in_time_zone(tz).strftime('%H:%M')} (#{display_p_height} #{p_display_units})"
            end

            live_str = live ? "#{live[:time_str]} (#{live[:height]} #{live[:units]})" : "---"

            diff_str = "---"
            if pred && live
                live_time = tz.parse(live[:time_str], reference_date)
                time_diff = ((pred['time'] - live_time) / 60.0).round(1)

                # Compare in live units
                actual_p_in_live_units = p_units == 'm' && live[:units] == 'ft' ? (p_height * 3.28084) : p_height
                height_diff = (actual_p_in_live_units - live[:height]).round(3)

                diff_str = "#{time_diff > 0 ? '+' : ''}#{time_diff}m / #{height_diff > 0 ? '+' : ''}#{height_diff} #{live[:units]}"
            end
            puts "| #{type} | #{pred_str.ljust(27)} | #{live_str.ljust(25)} | #{diff_str.ljust(21)} |"
        end
    end
end

options = { random: false, name: nil }
OptionParser.new do |opts|
    opts.banner = "Usage: scripts/test_harmonics.rb [options]"
    opts.on("-r", "--random", "Pick a random 5 test locations from the pool") { options[:random] = true }
    opts.on("-n", "--name NAME", "Only test locations matching this name") { |n| options[:name] = n }
    opts.on("-f", "--filter", "Filter to Top 8 constituents only") { options[:filter] = true }
    opts.on("-h", "--help", "Prints this help") { puts opts; exit }
end.parse!

logger = Logger.new(STDOUT).tap do |log|
    log.formatter = proc { |s, d, _, m| "#{d.strftime("%Y-%m-%d %H:%M:%S")} #{s} #{m}\n" }
end
logger.level = Logger::INFO

cache_dir = File.expand_path('../cache', __dir__)

engine = Harmonics::Engine.new(logger, cache_dir)
scraper = TideForecastScraper.new(logger)

puts "Loading stations from #{engine.harmonics_file}..."
stations = engine.stations
puts "Loaded #{stations.length} stations."

# Filter constituents if requested
if options[:filter]
    puts "Filtering stations to Top 8 constituents only..."
    top_8 = ['M2', 'S2', 'N2', 'K2', 'K1', 'O1', 'P1', 'Q1']
    stations.each do |s|
        # We need to update the cache entry in the engine
        # This is a bit hacky as we're reaching into the engine's state via the station object reference if possible
        # But engine.stations returns a list of metadata hashes. The actual data is in @stations_cache.
        # So we need to access the private cache or rely on the fact that we can't easily modify it globally here without engine support.

        # Actually, the engine exposes `stations` which is just metadata.
        # The predictions use `generate_predictions` which looks up in `@stations_cache`.
        # We need to hack the engine instance to filter.

        cache = engine.instance_variable_get(:@stations_cache)
        if cache[s['id']]
            cache[s['id']]['constituents'].select! { |c| top_8.include?(c['name']) }
        end
    end
end

test_locations = if options[:name]
    HarmonicsTestRunner::LOCATION_POOL.select { |l| l[:name].downcase.include?(options[:name].downcase) }
elsif options[:random]
    HarmonicsTestRunner::LOCATION_POOL.sample(5)
else
    HarmonicsTestRunner::LOCATION_POOL.first(5)
end

if test_locations.empty?
    puts "No locations in pool matching '#{options[:name]}'"
    exit 1
end

runner = HarmonicsTestRunner.new(logger, engine, scraper)
runner.run_tests(test_locations, stations)

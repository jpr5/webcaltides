#!/usr/bin/env ruby

require 'logger'
require 'active_support/all'
require 'mechanize'
require 'nokogiri'
require 'optparse'
require_relative '../lib/harmonics_engine'
require_relative '../clients/noaa_tides'

class NoaaTideFetcher
    def initialize(logger)
        @logger = logger
        @client = Clients::NoaaTides.new(logger)
    end

    def fetch_tides(loc, date, _tz)
        station_id = loc[:noaa_id]
        unless station_id
            @logger.warn "No NOAA station id for #{loc[:name]}"
            return []
        end

        range_start = date.utc
        range_end = (date + 24.hours).utc

        station = Models::Station.new(
            id: station_id,
            name: loc[:name],
            url: Clients::NoaaTides::PUBLIC_STATION_URL % [station_id],
            provider: 'noaa'
        )

        data = @client.tide_data_for(station, date) || []
        data.filter_map do |entry|
            entry_time = entry.time.to_time.utc
            next if entry_time < range_start || entry_time > range_end

            {
                type: entry.type,
                time: entry_time,
                height: entry.prediction,
                units: entry.units
            }
        end
    end
end

class TideForecastScraper
    def initialize(logger)
        @logger = logger
        @agent = Mechanize.new
        @agent.user_agent_alias = 'Mac Safari'
    end

    def fetch_tides(loc, date, _tz)
        slug = loc[:slug]
        return [] unless slug

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
        { name: "Sydney (Fort Denison)", slug: "Sydney-Australia", id: "Tb07b950" },
        { name: "Tokyo", slug: "Tokyo-Japan", id: "Tffa2a40" },
        { name: "San Francisco", slug: "San-Francisco-California", id: "X973a1be", noaa_id: "9414290" },
        { name: "Seattle", slug: "Seattle-Washington", id: "X4b5d76b", noaa_id: "9447130" }
    ]

    def initialize(logger, engine, live_fetcher, live_label)
        @logger = logger
        @engine = engine
        @live_fetcher = live_fetcher
        @live_label = live_label
    end

    def run_tests(locations, stations)
        locations.each do |loc|
            search_term = loc[:name]
            puts "\n" + "=" * 100
            puts "Testing: #{search_term}"

            # Match by ID first, then by name
            station = nil
            if loc[:id]
                station = @engine.find_station(loc[:id])
                unless station
                    puts "Debug: Could not find station with ID '#{loc[:id]}'"
                    # Try fuzzy matching coords if it looks like a T icon ID
                    if loc[:id].start_with?('T')
                        # No longer supporting coordinate-based fuzzy matching for short hashes
                        # but keeping the block structure for future logic if needed.
                        puts "Debug: Hash IDs do not support coordinate extraction."
                    elsif loc[:id].start_with?('ticon_')
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

            prediction_options = {
                step_seconds: @options[:step_seconds],
                meridian_override: @options[:meridian_override],
                meridian_from_timezone: @options[:meridian_from_timezone],
                nodal_hour: @options[:nodal_hour]
            }.compact

            predictions = @engine.generate_predictions(station['id'], start_time, end_time, prediction_options)
            predicted_peaks = @engine.detect_peaks(predictions, step_seconds: prediction_options.fetch(:step_seconds, 60))
            live_peaks = @live_fetcher.fetch_tides(loc, start_time, tz)

            compare_and_display(predicted_peaks, live_peaks, tz, start_time)
        end
    end

    private

    def compare_and_display(predicted_peaks, live_peaks, tz, reference_date)
        puts "\n| Event | Predicted (Our Engine)      | Live (#{@live_label})       | Difference            |"
        puts "| :---  | :---                        | :---                      | :---                  |"

        all_events = []
        remaining_live = live_peaks.dup

        predicted_peaks.each do |pred|
            closest_live = remaining_live.find do |live|
                next unless live[:type] == pred['type']
                live_time = live[:time] ? live[:time] : tz.parse(live[:time_str], reference_date)
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
                e[:live][:time] ? e[:live][:time] : tz.parse(e[:live][:time_str], reference_date)
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

            live_time = nil
            if live
                live_time = live[:time] ? live[:time] : tz.parse(live[:time_str], reference_date)
            end
            live_time_str = live_time ? live_time.in_time_zone(tz).strftime('%H:%M') : nil
            live_str = live ? "#{live_time_str} (#{live[:height]} #{live[:units]})" : "---"

            diff_str = "---"
            if pred && live && live_time
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

options = {
    random: false,
    name: nil,
    source: 'forecast',
    step_seconds: 60,
    meridian_override: nil,
    meridian_from_timezone: false,
    nodal_hour: 12
}
OptionParser.new do |opts|
    opts.banner = "Usage: scripts/predict.rb [options]"
    opts.on("-r", "--random", "Pick a random 5 test locations from the pool") { options[:random] = true }
    opts.on("-n", "--name NAME", "Only test locations matching this name") { |n| options[:name] = n }
    opts.on("-f", "--filter", "Filter to Top 8 constituents only") { options[:filter] = true }
    opts.on("-s", "--source SOURCE", "Live source: forecast or noaa (default forecast)") { |s| options[:source] = s }
    opts.on("--step-seconds SECONDS", Float, "Prediction sampling interval (default 60)") { |v| options[:step_seconds] = v }
    opts.on("--meridian HOURS", Float, "Override meridian offset hours (e.g. -8)") { |v| options[:meridian_override] = v }
    opts.on("--meridian-from-tz", "Use timezone offset as meridian") { options[:meridian_from_timezone] = true }
    opts.on("--nodal-hour HOUR", Integer, "Hour for nodal factor calculation (default 12)") { |v| options[:nodal_hour] = v }
    opts.on("-h", "--help", "Prints this help") { puts opts; exit }
end.parse!

logger = Logger.new(STDOUT).tap do |log|
    log.formatter = proc { |s, d, _, m| "#{d.strftime("%Y-%m-%d %H:%M:%S")} #{s} #{m}\n" }
end
logger.level = Logger::INFO

cache_dir = File.expand_path('../cache', __dir__)

engine = Harmonics::Engine.new(logger, cache_dir)

source = options[:source].to_s.downcase
case source
when 'forecast', 'tide-forecast', 'tideforecast'
    live_fetcher = TideForecastScraper.new(logger)
    live_label = 'Tide-Forecast'
when 'noaa'
    live_fetcher = NoaaTideFetcher.new(logger)
    live_label = 'NOAA'
else
    puts "Unknown source '#{options[:source]}'. Use 'forecast' or 'noaa'."
    exit 1
end

begin
    puts "Loading stations from #{engine.xtide_file}..."
    stations = engine.stations
    puts "Loaded #{stations.length} stations."
rescue Harmonics::Engine::MissingSourceFilesError => e
    warn e.message
    exit 1
end

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

runner = HarmonicsTestRunner.new(logger, engine, live_fetcher, live_label)
runner.instance_variable_set(:@options, options)
runner.run_tests(test_locations, stations)

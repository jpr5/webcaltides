##
## Primary library of functions.  Included on Server.
##
#
# FIXME: Code runs under a threaded server (puma), but is not threadsafe. 🤷‍♂️
# I like my patterns more than the odds of it happening.  My code is *tight*, yo! 😂
#

require 'dotenv/load'

require 'bundler/setup'
Bundler.require(:default, ENV['RACK_ENV'] || 'development')

require_relative 'gps'
require_relative 'clients/base'
require_relative 'clients/noaa_tides'
require_relative 'clients/chs_tides'
require_relative 'clients/noaa_currents'
require_relative 'clients/harmonics'
require_relative 'clients/lunar'


module WebCalTides

    extend self

    include Clients::TimeWindow

    # Configuration constants
    STATION_GROUPING_DISTANCE_M = 200  # Meters threshold for grouping nearby stations

    # Priority order for selecting primary source when multiple providers cover the same location.
    # Official sources (NOAA, CHS) are preferred over harmonic-based predictions.
    #
    # XTide vs TICON (Jan 2026, scripts/compare_harmonic_sources.rb):
    # - Tides: Same timing RMS (~4min), but XTide height RMS 1.56ft vs TICON 3.49ft (2.2x better)
    # - Currents: TICON has no coverage in US waters; XTide is the only harmonic option
    PROVIDER_HIERARCHY = %w[noaa chs xtide ticon].freeze

    def settings; return Server.settings; end
    def logger; $LOG; end

    ##
    ## Clients
    ##

    def tide_clients(provider = nil)
        @tide_clients ||= begin
            @@harmonics ||= Clients::Harmonics.new(logger)
            {
                noaa:  Clients::NoaaTides.new(logger),
                chs:   Clients::ChsTides.new(logger),
                xtide: @@harmonics,
                ticon: @@harmonics
            }
        end

        provider ? @tide_clients[provider.to_sym] : @tide_clients
    end

    def current_clients(provider = nil)
        @current_clients ||= begin
            @@harmonics ||= Clients::Harmonics.new(logger)
            {
                noaa:  Clients::NoaaCurrents.new(logger),
                xtide: @@harmonics,
                ticon: @@harmonics
            }
        end

        provider ? @current_clients[provider.to_sym] : @current_clients
    end

    def lunar_client
        @lunar_client ||= Clients::Lunar.new(logger)
    end

    ##
    ## Util
    ##

    def convert_depth_to_correct_units(val, curr_units, desired_units)
        if desired_units == curr_units
            val
        elsif desired_units == 'ft' # convert to feet
            (val.to_f * 3.28084).round(3)
        else # convert to meters
            (val.to_f / 3.28084).round(3)
        end
    end

    # Should handle most (mal)formed inputs using georuby gem.
    def parse_gps(str)
        begin
            str = GPS.normalize(str)[:decimal]
        rescue
            # Fallback to our own original implementation
            # Handles decimal (-)X.YYY or deg/min/sec format:
            # Supported deg/min/sec format:
            #     "1°2.3" or "1'2.3" with explicit negative
            #     "1°2.3N" or "1'2.3W" with implicit negative (S+E -> -)

            if str.match(/\d[°']/) # leave out " because that's also for search
                str = str # blindly fix NSEW, no-op if DNE
                    .gsub(/(\d['°])(\s*)/, '\1') # remove any space b/w deg
                    .gsub(/([^\s])\s+([NSEW])/, '\1\2') # remove any space b/w cardinal
                    .gsub(/([^\s]+)[SE]/, '-\1') # if SE exists, remove + convert to -
                    .gsub(/([^\s]+)[NW]/, '\1') # if NW exists, remove + ignore (+)
                    .gsub(/([-]*)(\d+)['°]\s*(\d+)\.(\d+)/) do |m| # Convert to decimal
                        $1 + ($2.to_f + $3.to_f/60 + $4.to_f/3600).to_s
                    end
            end
        end

        # Hopefully in decimal form now... if it passes validation.
        res = str.split(/[, ]+/)

        return nil if res.length != 2 or
                      res.any? { |s| s.scan(/^[\d\.-]+$/).empty? } or
                     !res[0].to_f.between?(-90,90) or
                     !res[1].to_f.between?(-180,180)
        return res
    end

    def timezone_for(lat, long)
        filename = "#{settings.cache_dir}/tzs.json"

        unless @tzcache
            logger.debug "initializing #{filename}"
            if File.exist?(filename)
                logger.debug "reading #{filename}"
                json = File.read(filename)
                logger.debug "parsing #{filename}"
                @tzcache = JSON.parse(json) rescue {}
            else
                @tzcache = {}
            end
        end

        lat = lat.to_f
        long = long.to_f

        # The timezone gem requires longitude in -180..180, but TICON uses 0..360.
        if long > 180.0 || long < -180.0
            old_long = long
            while long > 180.0; long -= 360.0; end
            while long < -180.0; long += 360.0; end
            logger.debug "normalized longitude #{old_long} => #{long} for timezone lookup"
        end

        key = "#{lat} #{long}"

        return @tzcache[key] ||= begin
            logger.debug "looking up tz for GPS #{key}"

            tz = nil
            i = 0
            begin
                i += 1
                tz = Timezone.lookup(lat, long)
            rescue Timezone::Error::InvalidZone
                # Use default UTC
            rescue Timezone::Error::GeoNames => e
                logger.error "GeoNames lookup failed for #{key}: #{e.message}"
                if i < 3
                    sleep(i)
                    retry
                end
            rescue => e
                logger.error "Timezone lookup failed for #{key}: #{e.message}"
            end

            if tz.nil?
                logger.warn "Timezone.lookup returned nil for #{key}, defaulting to UTC"
                res = 'UTC'
            else
                res = tz.name
            end

            logger.debug "GPS #{key} => #{res}"

            @tzcache[key] = res

            logger.debug "updating tzcache at #{filename}"
            File.write(filename, @tzcache.to_json)

            res
        end
    end

    def station_ids
        ids = tide_stations.map(&:id) + current_stations.map(&:bid)

        # Add all keys from the XTide engine cache to support aliased/merged IDs
        xtide = tide_clients(:xtide)
        if xtide.respond_to?(:engine)
            xtide.engine.stations # Ensure stations are loaded
            ids += xtide.engine.stations_cache.keys
        end

        ids.uniq.compact
    end

    ##
    ## Station Grouping & Deduplication
    ##

    # Represents a group of nearby stations from different providers
    StationGroup = Struct.new(:primary, :alternatives, :deltas, keyword_init: true) do
        def has_alternatives?
            alternatives && alternatives.any?
        end

        def to_h
            {
                primary: primary,
                alternatives: alternatives || [],
                deltas: deltas || {}
            }
        end
    end

    # Groups stations by proximity (within STATION_GROUPING_DISTANCE_M meters)
    # Returns array of StationGroup objects with primary and alternatives
    def group_stations_by_proximity(stations, threshold_m: STATION_GROUPING_DISTANCE_M, match_depth: false)
        return [] if stations.nil? || stations.empty?

        groups = []
        threshold_km = threshold_m / 1000.0

        stations.each do |station|
            # Find existing group within threshold distance
            existing_group = groups.find do |group|
                ref_station = group.first
                next false unless ref_station.lat && ref_station.lon && station.lat && station.lon

                # For current stations, also require matching depth
                if match_depth
                    # Normalize depth comparison (both nil, or both same value)
                    ref_depth = ref_station.respond_to?(:depth) ? ref_station.depth : nil
                    sta_depth = station.respond_to?(:depth) ? station.depth : nil
                    next false unless ref_depth == sta_depth
                end

                distance_km = Geocoder::Calculations.distance_between(
                    [ref_station.lat, ref_station.lon],
                    [station.lat, station.lon],
                    units: :km
                )
                distance_km <= threshold_km
            end

            if existing_group
                existing_group << station
            else
                groups << [station]
            end
        end

        # Convert raw groups to StationGroup objects with primary selection
        groups.map { |g| select_primary_and_alternatives(g) }
    end

    # Given a group of stations, selects primary based on provider hierarchy
    # and returns a StationGroup with primary, alternatives, and (empty) deltas
    def select_primary_and_alternatives(group)
        sorted = group.sort_by do |station|
            provider = (station.provider || 'unknown').downcase
            PROVIDER_HIERARCHY.index(provider) || 999
        end

        StationGroup.new(
            primary: sorted.first,
            alternatives: sorted[1..] || [],
            deltas: {}  # Populated lazily via compute_variance
        )
    end

    # Computes time and height deltas between primary and each alternative
    # Returns hash: { "alt_station_id" => { time: "+4min", height: "-0.2ft" }, ... }
    def compute_variance(primary, alternatives, around: Time.current.utc)
        return {} if alternatives.nil? || alternatives.empty?

        primary_events = next_tide_events(primary.id, around: around)
        return {} unless primary_events && primary_events.any?

        primary_next = primary_events.first

        alternatives.each_with_object({}) do |alt, deltas|
            alt_events = next_tide_events(alt.id, around: around)
            next unless alt_events && alt_events.any?

            alt_next = alt_events.first

            # Calculate deltas
            time_diff_seconds = (alt_next[:time].to_time - primary_next[:time].to_time).to_i
            height_diff = (alt_next[:height].to_f - primary_next[:height].to_f).round(2)
            height_units = primary_next[:units] || 'ft'

            deltas[alt.id] = {
                time: format_time_delta(time_diff_seconds),
                height: format_height_delta(height_diff, height_units)
            }
        end
    end

    # Formats time delta in seconds to human-readable string
    def format_time_delta(seconds)
        return "0min" if seconds.abs < 30  # Less than 30 seconds = essentially same time

        sign = seconds >= 0 ? "+" : ""
        minutes = (seconds / 60.0).round

        if minutes.abs >= 60
            hours = minutes / 60
            mins = minutes.abs % 60
            mins_str = mins > 0 ? "#{mins}min" : ""
            "#{sign}#{hours}hr#{mins_str}"
        else
            "#{sign}#{minutes}min"
        end
    end

    # Formats height delta with sign and units
    def format_height_delta(diff, units = 'ft')
        return "0#{units}" if diff.abs < 0.05

        sign = diff >= 0 ? "+" : ""
        "#{sign}#{diff}#{units}"
    end

    # Groups search results and optionally computes variance
    # Set compute_deltas: false for faster initial search results
    def group_search_results(stations, compute_deltas: false, match_depth: false, around: Time.current.utc)
        groups = group_stations_by_proximity(stations, match_depth: match_depth)

        if compute_deltas
            groups.each do |group|
                next unless group.has_alternatives?
                group.deltas = compute_variance(group.primary, group.alternatives, around: around)
            end
        end

        groups
    end

    ##
    ## Tides
    ##

    # Cache quarterly / every three months
    def tide_station_cache_file
        now = Time.current.utc
        datestamp = now.strftime("%YQ#{now.quarter}")
        "#{settings.cache_dir}/tide_stations_v#{Models::Station.version}_#{datestamp}.json"
    end

    def cache_tide_stations(at:tide_station_cache_file, stations:[])
        # stations: is used in the re-cache scenario
        tide_clients.values.uniq.each { |c| stations.concat(c.tide_stations) } if stations.empty?

        logger.debug "storing tide station list at #{at}"
        File.write(at, stations.map(&:to_h).to_json )

        return stations.length > 0
    end

    def tide_stations
        cache_file = tide_station_cache_file
        File.exist?(cache_file) || cache_tide_stations(at:cache_file) && @tide_stations = nil

        return @tide_stations ||= begin
            logger.debug "reading #{cache_file}"
            json = File.read(cache_file)

            logger.debug "parsing tide station list"

            data = JSON.parse(json) rescue []
            data.map { |js| Models::Station.from_hash(js) }
        end
    end

    # This is primarily for CHS tide stations, whose metadata is such a broken mess as to not
    # reliably indicate, in any way, whether the station is producing tide data or not.  See
    # chs_tides.rb for details.

    def remove_tide_station(station_id)
        @tide_stations.delete_if { |s| s.id == station_id }
        cache_tide_stations(stations:@tide_stations)
    end

    def tide_station_for(id)
        return nil if id.blank?
        station = tide_stations.find { |s| s.id == id }
        return station if station

        # Fallback to looking in the XTide engine cache for aliased/merged IDs
        xtide = tide_clients(:xtide)
        if xtide.respond_to?(:engine)
            xtide.engine.stations # Ensure stations are loaded
            if data = xtide.engine.stations_cache[id]
                return Models::Station.from_hash({
                    'name' => data['name'],
                    'id' => id,
                    'public_id' => id,
                    'region' => data['region'],
                    'location' => data['name'],
                    'provider' => data['provider'] || 'xtide', # or ticon? engine knows.
                    'type' => data['type']
                })
            end
        end
        nil
    end

    # nil == any, units == [ mi, km ]
    def find_tide_stations(by:nil, within:nil, units:'mi')
        by ||= [""]
        by &&= Array(by).map(&:downcase)

        logger.debug("finding tide stations by #{by} within #{within}#{units}")
        by_stations = tide_stations.select do |s|
            by.all? do |b|
                s.id.downcase == b ||
                s.alternate_names.any? { |n| (n.downcase.include?(b) rescue false) } ||
                (s.region.downcase.include?(b) rescue false) ||
                (s.name.downcase.include?(b) rescue false) ||
                s.public_id.downcase.include?(b) rescue false
            end
        end

        # can only do radius search with one result, ignore otherwise
        return by_stations unless within and by_stations.size == 1

        station = by_stations.first

        return find_tide_stations_by_gps(station.lat, station.lon, within:within, units:units)
    end

    def find_tide_stations_by_gps(lat, long, within:nil, units:'mi')
        within = within.to_i
        return tide_stations.select do |s|
            Geocoder::Calculations.distance_between([lat, long], [s.lat,s.lon], units: units.to_sym) <= within
        end
    end

    def cache_tide_data_for(station, at:, around:)
        return false unless station

        if tide_data = tide_clients(station.provider).tide_data_for(station, around)
            logger.debug "storing tide data at #{at}"
            File.write(at, tide_data.map(&:to_h).to_json)
        end

        return tide_data && tide_data.length > 0
    end

    def tide_data_for(station, around: Time.current.utc)
        return nil unless station

        datestamp = around.utc.strftime("%Y%m")
        filename  = "#{settings.cache_dir}/tides_v#{Models::TideData.version}_#{station.id}_#{datestamp}.json"
        return nil unless File.exist?(filename) || cache_tide_data_for(station, at:filename, around:around)

        logger.debug "reading #{filename}"
        json = File.read(filename)

        logger.debug "parsing tides for #{station.id}"
        data = JSON.parse(json) rescue []

        return data.map{ |js| Models::TideData.from_hash(js) }
    end

    # Returns the next high and low tide events for a station
    # Returns array of hashes: [{ type: 'High', time: DateTime, height: Float, units: String }, ...]
    def next_tide_events(id, around: Time.current.utc)
        station = tide_station_for(id) or return nil
        data = tide_data_for(station, around: around) or return nil

        now = Time.current.utc
        future_data = data.select { |d| d.time > now }.sort_by(&:time)

        next_high = future_data.find { |d| d.type == 'High' }
        next_low = future_data.find { |d| d.type == 'Low' }

        tz = timezone_for(station.lat, station.lon)

        events = []
        if next_high
            events << {
                type: 'High',
                time: next_high.time.in_time_zone(tz),
                height: next_high.prediction,
                units: next_high.units
            }
        end
        if next_low
            events << {
                type: 'Low',
                time: next_low.time.in_time_zone(tz),
                height: next_low.prediction,
                units: next_low.units
            }
        end

        # Sort by time so first tide is the soonest
        events.sort_by { |e| e[:time] }
    end

    def tide_calendar_for(id, around: Time.current.utc, units: 'imperial')
        depth_units = units == 'imperial' ? 'ft' : 'm'
        station = tide_station_for(id) or return nil
        data    = tide_data_for(station, around: around)

        cal = Icalendar::Calendar.new
        cal.x_wr_calname = station.name.titleize

        if station.provider.in?(['xtide', 'ticon'])
            cal.description = "NOT FOR NAVIGATION. This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  The author and the publisher each assume no liability for damages arising from use of these predictions.  They are not certified to be correct, and they do not incorporate the effects of tropical storms, El Niño, seismic events, subsidence, uplift, or changes in global sea level."
        end

        if data
            data.each do |tide|
                title = "#{tide.type} Tide #{convert_depth_to_correct_units(tide.prediction, tide.units, depth_units)} #{depth_units}"

                cal.event do |e|
                    e.summary  = title
                    e.dtstart  = Icalendar::Values::DateTime.new(tide.time, tzid: 'GMT')
                    e.dtend    = Icalendar::Values::DateTime.new(tide.time, tzid: 'GMT')
                    e.url      = tide.url
                    e.location = station.location
                end
            end
        end

        cal.define_singleton_method(:station)  { station }
        cal.define_singleton_method(:location) { station.location }

        logger.info "tide calendar for #{station.name} generated with #{cal.events.length} events"

        return cal
    end

    ##
    ## Currents
    ##

    def current_station_cache_file
        now = Time.current.utc
        datestamp = now.strftime("%YQ#{now.quarter}")
        "#{settings.cache_dir}/current_stations_v#{Models::Station.version}_#{datestamp}.json"
    end

    def cache_current_stations(at:current_station_cache_file, stations: [])
        current_clients.values.uniq.each { |c| stations.concat(c.current_stations) } if stations.empty?

        # Enrich NOAA current stations with region data from nearest NOAA tide station
        # (NOAA currents API doesn't provide state/region info, but tide stations do)
        noaa_current_stations = stations.select { |s| s.provider == 'noaa' && s.region == 'United States' }
        if noaa_current_stations.any?
            noaa_tide_stations = tide_stations.select { |s| s.provider == 'noaa' }
            logger.info "enriching #{noaa_current_stations.size} NOAA current stations with region data"

            noaa_current_stations.each do |cs|
                next unless cs.lat && cs.lon
                # Find closest tide station
                closest = noaa_tide_stations.min_by do |ts|
                    next Float::INFINITY unless ts.lat && ts.lon
                    Geocoder::Calculations.distance_between([cs.lat, cs.lon], [ts.lat, ts.lon])
                end
                cs.region = closest.region if closest&.region
            end
        end

        logger.debug "storing current station list at #{at}"
        File.write(at, stations.map(&:to_h).to_json)

        return stations.length > 0
    end

    def current_stations
        cache_file = current_station_cache_file
        File.exist?(cache_file) || cache_current_stations(at:cache_file) && @current_stations = nil

        return @current_stations ||= begin
            logger.debug "reading #{cache_file}"
            json = File.read(cache_file)

            logger.debug "parsing current station list"
            data = JSON.parse(json) rescue []

            data.map { |js| Models::Station.from_hash(js) }
        end
    end

    def remove_current_station(station_id)
        @current_stations.delete_if { |s| s.id == station_id }
        cache_current_stations(stations:@current_stations)
    end

    def current_station_for(id)
        return nil if id.blank?
        station = current_stations.select { |s| s.id == id || s.bid == id }.first
        return station if station

        # Fallback to XTide engine
        xtide = current_clients(:xtide)
        if xtide.respond_to?(:engine)
            xtide.engine.stations # Ensure loaded
            if data = xtide.engine.stations_cache[id]
                return Models::Station.from_hash({
                    'name' => data['name'],
                    'id' => id,
                    'bid' => id,
                    'public_id' => id,
                    'region' => data['region'],
                    'location' => data['name'],
                    'provider' => 'xtide',
                    'type' => data['type']
                })
            end
        end
        nil
    end

    # nil == any, units == [ mi, km ]
    def find_current_stations(by:nil, within:nil, units:'mi')
        by ||= [""]
        by &&= Array(by).map(&:downcase)

        logger.debug "finding current stations by #{by} within #{within}#{units}"

        by_stations = current_stations.select do |s|
            by.all? do |b|
                (s.bid.downcase.start_with?(b) rescue false) ||
                (s.id.downcase.start_with?(b) rescue false) ||
                (s.id.downcase.include?(b) rescue false) ||
                (s.name.downcase.include?(b) rescue false) ||
                (s.region.downcase.include?(b) rescue false)
            end
        end

        # can only do radius search with one result, ignore otherwise
        return by_stations unless within and by_stations.size == 1

        station = by_stations.first

        return find_current_stations_by_gps(station.lat, station.lon, within:within, units:units)
    end

    def find_current_stations_by_gps(lat, long, within:nil, units:'mi')
        within = within.to_i

        return current_stations.select do |s|
            Geocoder::Calculations.distance_between([lat, long], [s.lat,s.lon], units: units.to_sym) <= within
        end
    end

    def cache_current_data_for(station, at:, around:)
        return false unless station

        if current_data = current_clients(station.provider).current_data_for(station, around)
            logger.debug "storing current data at #{at}"
            File.write(at, current_data.map(&:to_h).to_json)
        end

        return current_data && current_data.length > 0
    end

    def current_data_for(station, around: Time.current.utc)
        return nil unless station

        datestamp = around.utc.strftime("%Y%m") # 202312
        filename  = "#{settings.cache_dir}/currents_v#{Models::CurrentData.version}_#{station.bid}_#{datestamp}.json"
        return nil unless File.exist?(filename) || cache_current_data_for(station, at:filename, around:around)

        logger.debug "reading #{filename}"
        json = File.read(filename)

        logger.debug "parsing currents for #{station.bid}"
        data = JSON.parse(json) rescue []

        return data.map { |jc| Models::CurrentData.from_hash(jc) }
    end

    # Returns the next slack, flood, and ebb events for a station
    # Returns array of hashes: [{ type: 'Slack'|'Flood'|'Ebb', time: DateTime, velocity: Float? }, ...]
    def next_current_events(id, around: Time.current.utc)
        station = current_station_for(id) or return nil
        data = current_data_for(station, around: around) or return nil

        now = Time.current.utc
        future_data = data.select { |d| d.time > now }.sort_by(&:time)

        next_slack = future_data.find { |d| d.type == 'slack' }
        next_flood = future_data.find { |d| d.type == 'flood' }
        next_ebb = future_data.find { |d| d.type == 'ebb' }

        tz = timezone_for(station.lat, station.lon)

        events = []
        if next_slack
            events << {
                type: 'Slack',
                time: next_slack.time.in_time_zone(tz)
            }
        end
        if next_flood
            events << {
                type: 'Flood',
                time: next_flood.time.in_time_zone(tz),
                velocity: next_flood.velocity_major
            }
        end
        if next_ebb
            events << {
                type: 'Ebb',
                time: next_ebb.time.in_time_zone(tz),
                velocity: next_ebb.velocity_major
            }
        end

        # Sort by time so first max current is the soonest
        events.sort_by { |e| e[:time] }
    end

    def current_calendar_for(id, around: Time.current.utc)
        station = current_station_for(id) or return nil
        data    = current_data_for(station, around: around)

        return nil unless data

        cal = Icalendar::Calendar.new
        cal.x_wr_calname = station.name.titleize

        if station.provider.in?(['xtide', 'ticon'])
            cal.description = "NOT FOR NAVIGATION. This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  The author and the publisher each assume no liability for damages arising from use of these predictions.  They are not certified to be correct, and they do not incorporate the effects of tropical storms, El Niño, seismic events, subsidence, uplift, or changes in global sea level."
        end

        location = "#{station.name} (#{station.bid})"

        logger.debug "generating current calendar for #{location}"

        data.each do |current|
            date  = current.time.strftime("%Y-%m-%d")
            title = case current.type
                    when "ebb"   then "Ebb #{current.velocity_major.to_f.abs}kts #{current.mean_ebb_dir}T #{current.depth}ft"
                    when "flood" then "Flood #{current.velocity_major}kts #{current.mean_flood_dir}T #{current.depth}ft"
                    when "slack" then "Slack"
                    end

            cal.event do |e|
                e.summary  = title
                e.dtstart  = Icalendar::Values::DateTime.new(current.time, tzid: 'GMT')
                e.dtend    = Icalendar::Values::DateTime.new(e.dtstart, tzid: 'GMT')
                e.url      = station.url + "?id=" + station.bid + "&d=" + date
                e.location = location if location
            end
        end

        cal.define_singleton_method(:station)  { station  }
        cal.define_singleton_method(:location) { location }

        logger.info "current calendar for #{location} generated with #{cal.events.length} events"

        return cal
    end

    ##
    ## Solar
    ##

    def solar_calendar_for(calendar, around:Time.current.utc)
        cal = Icalendar::Calendar.new
        cal.x_wr_calname = "Solar Events"

        from = beginning_of_window(around).strftime("%Y%m%d")
        to   = end_of_window(around).strftime("%Y%m%d")

        station  = calendar.station
        location = calendar.location

        logger.debug "generating solar calendar for #{from}-#{to}"

        (Date.parse(from)..Date.parse(to)).each do |date|
            tz      = timezone_for(station.lat, station.lon)
            calc    = SolarEventCalculator.new(date, station.lat, station.lon)
            sunrise = calc.compute_official_sunrise(tz)
            sunset  = calc.compute_official_sunset(tz)

            # I dunno why tzid: GMT is correct vs. tzid: tz, but it works..
            cal.event do |e|
                e.summary  = "Sunrise"
                e.dtstart  = Icalendar::Values::DateTime.new(sunrise, tzid: 'GMT')
                e.dtend    = Icalendar::Values::DateTime.new(e.dtstart, tzid: 'GMT')
                e.location = location if location
            end

            cal.event do |e|
                e.summary  = "Sunset"
                e.dtstart  = Icalendar::Values::DateTime.new(sunset, tzid: 'GMT')
                e.dtend    = Icalendar::Values::DateTime.new(e.dtstart, tzid: 'GMT')
                e.location = location if location
            end
        end

        logger.info "solar calendar for #{from}-#{to} generated with #{cal.events.length} events"

        cal.events.each do |e|
            calendar.add_event(e)
        end

        return cal
    end

    ##
    ## Lunar
    ##

    def lunar_phase_cache_file(year)
        "#{settings.cache_dir}/lunar_phases_#{year}.json"
    end

    def cache_lunar_phases(on:, phases:[])
        cache_file = lunar_phase_cache_file(on)

        logger.debug "storing #{phases.length} lunar phases for #{on} at #{cache_file}"
        File.write(cache_file, phases.to_json)

        return phases.length > 0
    end

    def lunar_phases(from, to)
        @lunar_phases ||= {}
        ret = []

        (from.year .. to.year).each do |year|
            cache_file = lunar_phase_cache_file(year)
            unless File.exist?(cache_file)
                unless phases = lunar_client.phases_for_year(year)
                    logger.error "failed to retrieve lunar phase data for #{year}"
                    return
                end

                cache_lunar_phases(on:year, phases:phases)
                @lunar_phases[year] = nil
            end

            ret << @lunar_phases[year] ||= begin
                logger.debug "reading #{cache_file}"
                json = File.read(cache_file)

                logger.debug "parsing lunar phases for #{year}"
                data = JSON.parse(json) rescue []

                data.map do |phase|
                    {
                        datetime: DateTime.parse(phase["datetime"].to_s),
                        type:     phase["type"].to_sym,
                    }
                end.sort_by { |phase| phase[:datetime] }
            end
        end

        return ret.flatten.select { |e| e[:datetime] >= from and e[:datetime] <= to }
    end

    def lunar_calendar_for(calendar, around:Time.current.utc)
        cal = Icalendar::Calendar.new
        cal.x_wr_calname = "Lunar Phases"

        from = beginning_of_window(around).strftime("%Y%m%d")
        to   = end_of_window(around).strftime("%Y%m%d")

        location = calendar.location

        logger.debug "generating lunar calendar for #{from}-#{to}"

        phase_names = {
            new_moon:      "New Moon",
            first_quarter: "First Quarter Moon",
            full_moon:     "Full Moon",
            last_quarter:  "Last Quarter Moon"
        }

        lunar_phases(Date.parse(from), Date.parse(to)).each do |phase|
            percent_full = case phase[:type]
                when :new_moon then 0
                when :first_quarter then 50
                when :last_quarter then 50
                when :full_moon then 100
                else (lunar_client.percent_full(phase[:datetime]) * 100).round # approximate
            end

            phase_time = phase[:datetime]

            cal.event do |e|
                e.summary     = phase_names[phase[:type]]
                e.description = "Moon is #{percent_full}% illuminated"
                e.dtstart     = Icalendar::Values::DateTime.new(phase_time, tzid: 'GMT')
                e.dtend       = Icalendar::Values::DateTime.new(phase_time + 1.second, tzid: 'GMT')
                e.location    = location if location
            end
        end

        logger.info "lunar calendar for #{from}-#{to} generated with #{cal.events.length} events"

        cal.events.each do |e|
            calendar.add_event(e)
        end

        return cal
    end

end

##
## Primary library of functions.  Included on Server.
##

require_relative 'clients/base'
require_relative 'clients/noaa_tides'
require_relative 'clients/chs_tides'
require_relative 'clients/noaa_currents'
require_relative 'clients/lunar'
require 'json'
require 'date'

module WebCalTides

    extend self

    include Clients::TimeWindow

    def settings; return Server.settings; end
    def logger; $LOG; end

    ##
    ## Clients
    ##

    def tide_clients(provider = nil)
        @tide_clients ||= {
            noaa: Clients::NoaaTides.new(logger),
            chs:  Clients::ChsTides.new(logger)
        }

        provider ? @tide_clients[provider.to_sym] : @tide_clients
    end

    def current_clients(provider = nil)
        @current_clients ||= {
            noaa: Clients::NoaaCurrents.new(logger)
        }

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

    # Handles decimal (-)X.YYY or deg/min/sec format:
    # Supported deg/min/sec format:
    #     "1°2.3" or "1'2.3" with explicit negative
    #     "1°2.3N" or "1'2.3W" with implicit negative (S+E -> -)
    def parse_gps(str)
        if str.match(/\d['°]/)
            str = str# blindly fix NSEW, no-op if DNE
                .gsub(/(\d['°])(\s*)/, '\1') # remove any space b/w deg
                .gsub(/([^\s])\s+([NSEW])/, '\1\2') # remove any space b/w cardinal
                .gsub(/([^\s]+)[SE]/, '-\1') # if SE exists, remove + convert to -
                .gsub(/([^\s]+)[NW]/, '\1') # if NW exists, remove + ignore (+)
                .gsub(/([-]*)(\d+)['°]\s*(\d+)\.(\d+)/) do |m| # Convert to decimal
                    $1 + ($2.to_f + $3.to_f/60 + $4.to_f/3600).to_s
                end
        end

        # In decimal form now
        res = str.split(/[, ]+/)

        return nil if res.length != 2 or
                      res.any? { |s| s.scan(/^[\d\.-]+$/).empty? } or
                     !res[0].to_f.between?(-90,90) or
                     !res[1].to_f.between?(-180,180)
        return res
    end

    def timezone_for(lat, long)
        filename = "#{settings.cache_dir}/tzs.json"

        @tzcache ||= begin
            if File.exist? filename
                logger.debug "reading #{filename}"
                json = File.read(filename)
                logger.debug "parsing tzcache"
                data = JSON.parse(json) rescue {}
            else
                logger.debug "initializing #{filename}"
                FileUtils.touch filename
                data = {}
            end
        end

        key = "#{lat} #{long}"

        return @tzcache[key] ||= begin
            logger.debug "looking up tz for GPS #{key}"

            i = 0
            begin
                i += 1
                tz = Timezone.lookup(lat, long)
            rescue Timezone::Error::GeoNames
                sleep(i)
                retry unless i >= 3
            end

            logger.debug "GPS #{key} => #{tz.name}"

            @tzcache[key] = tz.name

            logger.debug "updating tzcache at #{filename}"
            File.write(filename, @tzcache.to_json)

            tz.name
        end
    end

    def station_ids
        tide_stations.map(&:id) + current_stations.map(&:id)
    end

    ##
    ## Tides
    ##

    # Cache quarterly / every three months
    def tide_station_cache_file
        now = Time.current.utc
        datestamp = now.strftime("%YQ#{now.quarter}")
        "#{settings.cache_dir}/tide_stations_v#{DataModels::Station.version}_#{datestamp}.json"
    end

    def cache_tide_stations(at:tide_station_cache_file, stations:[])
        # stations: is used in the re-cache scenario
        tide_clients.each_value { |c| stations.concat(c.tide_stations) } if stations.empty?

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
            data.map { |js| DataModels::Station.from_hash(js) }
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
        return tide_stations.find { |s| s.id == id }
    end

    # nil == any, units == [ mi, km ]
    def find_tide_stations(by:nil, within:nil, units:'mi')
        by ||= [""]
        by &&= Array(by).map(&:downcase)

        logger.debug("finding tide stations by #{by} within '#{within}' #{units}")
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
        filename  = "#{settings.cache_dir}/tides_v#{DataModels::TideData.version}_#{station.id}_#{datestamp}.json"
        return nil unless File.exist?(filename) || cache_tide_data_for(station, at:filename, around:around)

        logger.debug "reading #{filename}"
        json = File.read(filename)

        logger.debug "parsing tides for #{station.id}"
        data = JSON.parse(json) rescue []

        return data.map{ |js| DataModels::TideData.from_hash(js) }
    end

    def tide_calendar_for(id, around: Time.current.utc, units: 'imperial')
        depth_units = units == 'imperial' ? 'ft' : 'm'
        station = tide_station_for(id) or return nil
        data    = tide_data_for(station, around: around)

        return nil unless data

        cal = Icalendar::Calendar.new
        cal.x_wr_calname = station.name.titleize

        logger.debug "generating tide calendar for #{station.name}"

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
        "#{settings.cache_dir}/current_stations_v#{DataModels::Station.version}_#{datestamp}.json"
    end

    def cache_current_stations(at:current_station_cache_file, stations: [])
        current_clients.each_value { |c| stations.concat(c.current_stations) } if stations.empty?

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

            data.map { |js| DataModels::Station.from_hash(js) }
        end
    end

    def remove_current_station(station_id)
        @current_stations.delete_if { |s| s.id == station_id }
        cache_current_stations(stations:@current_stations)
    end

    def current_station_for(id)
        return nil if id.blank?
        return current_stations.select { |s| s.id == id || s.bid == id }.first
    end

    # nil == any, units == [ mi, km ]
    def find_current_stations(by:nil, within:nil, units:'mi')
        by ||= [""]
        by &&= Array(by).map(&:downcase)

        logger.debug "finding current stations by #{by} within '#{within}' #{units}"

        by_stations = current_stations.select do |s|
            by.all? do |b|
                (s.bid.downcase.start_with?(b) rescue false) ||
                (s.id.downcase.start_with?(b) rescue false) ||
                (s.id.downcase.include?(b) rescue false) ||
                (s.name.downcase.include?(b)) rescue false
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
        filename  = "#{settings.cache_dir}/currents_v#{DataModels::CurrentData.version}_#{station.bid}_#{datestamp}.json"
        return nil unless File.exist?(filename) || cache_current_data_for(station, at:filename, around:around)

        logger.debug "reading #{filename}"
        json = File.read(filename)

        logger.debug "parsing currents for #{station.bid}"
        data = JSON.parse(json) rescue []

        return data.map { |jc| DataModels::CurrentData.from_hash(jc) }
    end

    def current_calendar_for(id, around: Time.current.utc)
        station = current_station_for(id) or return nil
        data    = current_data_for(station, around: around)

        return nil unless data

        cal = Icalendar::Calendar.new
        cal.x_wr_calname = station.name.titleize

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

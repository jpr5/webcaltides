##
## Primary library of functions.  Included on Server.
##

require 'icalendar/tzinfo'
require 'solareventcalculator'
require 'geocoder'
require_relative 'clients/noaa_client'
require_relative 'clients/chs_client'
require 'active_support/core_ext'

module WebCalTides

    # Hacks to interact with outside of Server instance

    extend self

    def settings; return Server.settings; end
    def logger; @logger ||= Server.logger || Logger.new(STDOUT) rescue Logger.new(STDOUT); end

    ##
    ## Sliding Time Window for data generation
    ##

    WINDOW_SIZE = 12.months

    def beginning_of_window(around)
        return around.utc.beginning_of_month - WebCalTides::WINDOW_SIZE
    end

    def end_of_window(around)
        return around.utc.end_of_month + WebCalTides::WINDOW_SIZE
    end

    ##
    ## Clients
    ##

    def tide_clients
        @tide_clients ||= {
            noaa: Clients::NoaaClient.new(logger),
            chs: Clients::ChsClient.new(logger)
        }
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

    # Currently only works with Decimal (no Deg/Min/Secs)
    def parse_gps(str)
        res = str.split(/[, ]+/)
        return nil if res.length != 2 or
                      res.any? { |s| s.scan(/^[\d\.\-]+$/).empty? } or
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
            tz = Timezone.lookup(lat, long)
            logger.debug "GPS #{key} => #{tz.name}"

            @tzcache[key] = tz.name

            logger.debug "storing tzcache at #{filename}"
            File.write(filename, @tzcache.to_json)

            tz.name
        end
    end

    ##
    ## Tides
    ##

    def cache_tide_stations(at:nil)
        at ||= "#{settings.cache_dir}/tide_stations.json"

        stations = []
        tide_clients.each_value { |c| stations.concat(c.tide_stations) }
        logger.debug "storing tide station list at #{at}"
        File.write(at, stations.map{ |station| station.to_hash }.to_json )

        return stations.length > 0
    end

    def tide_stations
        return @tide_stations ||= begin
            filename = "#{settings.cache_dir}/tide_stations_v#{DataModels::Station.version}.json"

            File.exist? filename or cache_tide_stations(at:filename)

            logger.debug "reading #{filename}"
            json = File.read(filename)

            logger.debug "parsing tide station list"
            data = JSON.parse(json) rescue []

            data.map{ |js| DataModels::Station.from_hash(js) }
        end
    end

    def tide_station_for(id)
        return nil if id.blank?
        return tide_stations.find { |s| s.id == id }
    end

    # nil == any, units == [ mi, km ]
    def find_tide_stations(by:nil, within:nil, units:'mi')
        by ||= [""]
        by &&= Array(by).map(&:downcase)

        logger.debug("finding tide stations by '#{by}' within '#{within}' #{units}")
        by_stations = tide_stations.select do |s|
            by.any? do |b|
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

        id = station.id
        tide_data = tide_clients[station.provider.to_sym].tide_data_for(id, around, station.public_id)

        logger.debug "storing tide data at #{at}"
        File.write(at, tide_data.map{ |td| td.to_hash }.to_json)

        return tide_data.length > 0
    end

    def tide_data_for(station, around: Time.current.utc)
        return nil unless station

        id = station.id
        datestamp = around.utc.strftime("%Y%m")
        filename  = "#{settings.cache_dir}/tide_data_v#{DataModels::TideData.version}_#{id}_#{datestamp}.json"
        File.exist? filename or cache_tide_data_for(station, at:filename, around:around)

        logger.debug "reading #{filename}"
        json = File.read(filename)

        logger.debug "parsing tides for #{id}"
        data = JSON.parse(json) rescue []

        data.map{ |js| DataModels::TideData.from_hash(js) }
    end

    def tide_calendar_for(id, around: Time.current.utc, units: 'imperial')
        depth_units = units == 'imperial' ? 'ft' : 'm'
        station = tide_station_for(id) or return nil
        data    = tide_data_for(station, around: around)

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

        solar_calendar_for(station.lat, station.lon, around:around, location:station.location).events.each { |e| cal.add_event(e) }

        logger.info "tide calendar for #{station.name} generated with #{cal.events.length} events"

        return cal
    end

    ##
    ## Currents
    ##

    def cache_current_stations(at:nil)
        at ||= "#{settings.cache_dir}/stations.json"

        agent = Mechanize.new
        url = 'https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json?type=currentpredictions&units=english'

        logger.info "getting current station list from #{url}"
        json = agent.get(url).body
        logger.debug "json.length = #{json.length}"

        logger.debug "storing current station list at #{at}"
        File.write(at, json)

        return json.length > 0
    end

    def current_stations
        return @current_stations ||= begin
            filename = "#{settings.cache_dir}/current_stations.json"

            File.exist? filename or cache_current_stations(at:filename)

            logger.debug "reading #{filename}"
            json = File.read(filename)

            logger.debug "parsing current station list"
            data = JSON.parse(json)["stations"] rescue {}

            # Tho we get records, anything "weak and variable" won't have a lookup page,
            # so we exclude them.
            data.reject! { |s| s["type"] == "W" }

            # Since different bins/depths use the same ID, we massage each entry with a
            # unique "bin id" aka bid.
            data.map! { |s| s["bid"] = s["id"] + "_" + s["currbin"].to_s; s }
        end
    end

    def current_station_for(id)
        return nil if id.blank?
        return current_stations.select { |s| s["id"] == id || s["bid"] == id }.first
    end

    # nil == any, units == [ mi, km ]
    def find_current_stations(by:nil, within:nil, units:'mi')
        by ||= [""]
        by &&= Array(by).map(&:downcase)

        logger.debug "finding current stations by '#{by}' within '#{within}'"

        by_stations = current_stations.select do |s|
            by.any? do |b|
                (s['bid'].downcase.start_with?(b) rescue false) ||
                (s['id'].downcase.start_with?(b) rescue false) ||
                (s['id'].downcase.include?(b) rescue false) ||
                (s['name'].downcase.include?(b)) rescue false
            end
        end

        # can only do radius search with one result, ignore otherwise
        return by_stations unless within and by_stations.size == 1

        station = by_stations.first

        return find_current_stations_by_gps(station["lat"], station["lng"], within:within, units:units)
    end

    def find_current_stations_by_gps(lat, long, within:nil, units:'mi')
        within = within.to_i

        return current_stations.select do |s|
            Geocoder::Calculations.distance_between([lat, long], [s["lat"],s["lng"]], units: units.to_sym) <= within
        end
    end

    def cache_current_data_for(station, at:, around:)
        return false unless station

        (_, id, bin) = /(\w+)_(\d+)/.match(station).to_a
        id = station unless id

        agent = Mechanize.new
        from = beginning_of_window(around).strftime("%Y%m%d")
        to   = end_of_window(around).strftime("%Y%m%d")
        url = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=currents_predictions&begin_date=#{from}&end_date=#{to}&station=#{id}&time_zone=gmt&interval=MAX_SLACK&units=english&format=json"
        url += "&bin=#{bin}" if bin

        logger.info "getting json from #{url}"
        json = agent.get(url).body
        logger.debug "json.length = #{json.length}"

        logger.debug "storing current data at #{at}"
        File.write(at, json)

        return json.length > 0
    end

    def current_data_for(station, around: Time.current.utc)
        return nil unless station

        datestamp = around.utc.strftime("%Y%m") # 202312
        filename  = "#{settings.cache_dir}/currents_#{station}_#{datestamp}.json"
        File.exist? filename or cache_current_data_for(station, at:filename, around:around)

        logger.debug "reading #{filename}"
        json = File.read(filename)

        logger.debug "parsing currents for #{station}"
        data = JSON.parse(json)["current_predictions"]["cp"] rescue nil

        return data
    end

    def current_calendar_for(id, around: Time.current.utc)
        station = current_station_for(id) or return nil
        data    = current_data_for(id, around: around)

        cal = Icalendar::Calendar.new
        cal.x_wr_calname = station["name"].titleize

        url      = "https://tidesandcurrents.noaa.gov/noaacurrents/Predictions"
        location = "#{station["name"]} (#{station["bid"]})"

        logger.debug "generating current calendar for #{location}"

        data.each do |current|
            date  = DateTime.parse(current['Time']).strftime("%Y-%m-%d")
            title = case current["Type"]
                    when "ebb"   then "Ebb #{current["Velocity_Major"].to_f.abs}kts #{current["meanEbbDir"]}T #{current["Depth"]}ft"
                    when "flood" then "Flood #{current["Velocity_Major"]}kts #{current["meanFloodDir"]}T #{current["Depth"]}ft"
                    when "slack" then "Slack"
                    end

            cal.event do |e|
                e.summary  = title
                e.dtstart  = Icalendar::Values::DateTime.new(DateTime.parse(current["Time"]), tzid: 'GMT')
                e.dtend    = Icalendar::Values::DateTime.new(e.dtstart, tzid: 'GMT')
                e.url      = url + "?id=" + station["bid"] + "&d=" + date
                e.location = location if location
            end
        end

        solar_calendar_for(station["lat"], station["lng"], around:around, location:location).events.each { |e| cal.add_event(e) }

        logger.info "current calendar for #{location} generated with #{cal.events.length} events"

        return cal
    end


    ##
    ## Solar
    ##

    def solar_calendar_for(lat, long, around:Time.current.utc, location:nil)
        cal = Icalendar::Calendar.new
        cal.x_wr_calname = "Solar Events"

        from = beginning_of_window(around).strftime("%Y%m%d")
        to   = end_of_window(around).strftime("%Y%m%d")

        logger.debug "generating solar calendar for #{from}-#{to}"

        (Date.parse(from)..Date.parse(to)).each do |date|
            tz      = timezone_for(lat, long)
            calc    = SolarEventCalculator.new(date, lat, long)
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

        return cal
    end

end

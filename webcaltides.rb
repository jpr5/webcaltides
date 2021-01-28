##
## Primary library of functions.  Included on Server.
##

require 'icalendar/tzinfo'
require 'solareventcalculator'
require 'geocoder'


module WebCalTides

    # Hacks to interact with outside of Server instance

    extend self

    def settings; return Server.settings; end
    def logger; Server.logger rescue @logger ||= Logger.new(STDOUT); end

    ##
    ## Util
    ##

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
            if File.exists? filename
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

        agent = Mechanize.new
        url = 'https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/tidepredstations.json?q='

        logger.info "getting tide station list from #{url}"
        json = agent.get(url).body
        logger.debug "json.length = #{json.length}"

        logger.debug "storing tide station list at #{at}"
        File.write(at, json)

        return json.length > 0
    end

    def tide_stations
        return @tide_stations ||= begin
            filename = "#{settings.cache_dir}/tide_stations.json"

            File.exists? filename or cache_tide_stations(at:filename)

            logger.debug "reading #{filename}"
            json = File.read(filename)

            logger.debug "parsing tide station list"
            data = JSON.parse(json)["stationList"] rescue {}
        end
    end

    def tide_station_for(id)
        return nil if id.blank?
        return tide_stations.find { |s| s["stationId"] == id }
    end

    # nil == any, units == [ mi, km ]
    def find_tide_stations(by:nil, within:nil, units:'mi')
        by ||= [""]
        by &&= Array(by).map(&:downcase)

        logger.debug("finding tide stations by '#{by}' within '#{within}'")

        by_stations = tide_stations.select do |s|
            by.any? do |b|
                s['stationId'].downcase == b ||
                (s['etidesStnName'].downcase.include?(b) rescue false) ||
                (s['commonName'].downcase.include?(b) rescue false) ||
                (s['stationFullName'].downcase.include?(b) rescue false) ||
                (s['region'].downcase.include?(b)) rescue false
            end
        end

        # can only do radius search with one result, ignore otherwise
        return by_stations unless within and by_stations.size == 1

        station = by_stations.first

        return find_tide_stations_by_gps(station["lat"], station["lon"], within:within, units:units)
    end

    def find_tide_stations_by_gps(lat, long, within:nil, units:'mi')
        within = within.to_i
        return tide_stations.select do |s|
            Geocoder::Calculations.distance_between([lat, long], [s["lat"],s["lon"]], units: units.to_sym) <= within
        end
    end

    def cache_tide_data_for(station, at:nil, year:)
        return false unless station
        at ||= "#{settings.cache_dir}/#{station}_#{year}.json"

        agent = Mechanize.new
        url = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=predictions&datum=MLLW&time_zone=gmt&interval=hilo&units=english&application=web_services&format=json&begin_date=#{year}0101&end_date=#{year}1231&station=#{station}"

        logger.info "getting json from #{url}"
        json = agent.get(url).body
        logger.debug "json.length = #{json.length}"

        logger.debug "storing tide data at #{at}"
        File.write(at, json)

        return json.length > 0
    end

    def tide_data_for(station, year:Time.now.year)
        return nil unless station

        filename = "#{settings.cache_dir}/tides_#{station}_#{year}.json"
        File.exists? filename or cache_tide_data_for(station, at:filename, year:year)

        logger.debug "reading #{filename}"
        json = File.read(filename)

        logger.debug "parsing tides for #{station}"
        data = JSON.parse(json)["predictions"] rescue nil

        return data
    end

    def tide_calendar_for(id, year:Time.now.year)
        station = tide_station_for(id) or return nil
        data    = tide_data_for(id, year:year)

        cal = Icalendar::Calendar.new
        cal.x_wr_calname = station["name"].titleize

        url      = "https://tidesandcurrents.noaa.gov/noaatidepredictions.html"
        location = [
                station["etidesStnName"], station["region"], station["state"]
            ].join(", ")

        logger.debug "generating tide calendar for #{station["name"]}"

        data.each do |tide|
            date   = DateTime.parse(tide['t']).strftime("%Y%m%d")
            title  = tide["type"] == "H" ? "High" : "Low"
            title += " Tide   #{tide["v"]} ft"

            cal.event do |e|
                e.summary  = title
                e.dtstart  = Icalendar::Values::DateTime.new(DateTime.parse(tide["t"]), tzid: 'GMT')
                e.dtend    = Icalendar::Values::DateTime.new(e.dtstart, tzid: 'GMT')
                e.url      = url + "?id=" + station["stationId"] + "&bdate=" + date
                e.location = location
            end
        end

        solar_calendar_for(station["lat"], station["lon"], year:year, location:location).events.each { |e| cal.add_event(e) }

        logger.info "tide calendar for #{station["name"]} generated with #{cal.events.length} events"

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

            File.exists? filename or cache_current_stations(at:filename)

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

    def cache_current_data_for(station, at:nil, year:)
        return false unless station
        at ||= "#{settings.cache_dir}/#{station}_#{year}.json"

        (_, id, bin) = /(\w+)_(\d+)/.match(station).to_a
        id = station unless id

        agent = Mechanize.new
        url = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=currents_predictions&begin_date=#{year}0101&end_date=#{year}1231&station=#{id}&time_zone=gmt&interval=MAX_SLACK&units=english&format=json"
        url += "&bin=#{bin}" if bin

        logger.info "getting json from #{url}"
        json = agent.get(url).body
        logger.debug "json.length = #{json.length}"

        logger.debug "storing current data at #{at}"
        File.write(at, json)

        return json.length > 0
    end

    def current_data_for(station, year:Time.now.year)
        return nil unless station

        filename = "#{settings.cache_dir}/currents_#{station}_#{year}.json"
        File.exists? filename or cache_current_data_for(station, at:filename, year:year)

        logger.debug "reading #{filename}"
        json = File.read(filename)

        logger.debug "parsing currents for #{station}"
        data = JSON.parse(json)["current_predictions"]["cp"] rescue nil

        return data
    end

    def current_calendar_for(id, year:Time.now.year)
        station = current_station_for(id) or return nil
        data    = current_data_for(id, year:year)

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

        solar_calendar_for(station["lat"], station["lng"], year:year, location:location).events.each { |e| cal.add_event(e) }

        logger.info "current calendar for #{location} generated with #{cal.events.length} events"

        return cal
    end


    ##
    ## Solar
    ##

    def solar_calendar_for(lat, long, year:Time.now.year, location:nil)
        cal = Icalendar::Calendar.new
        cal.x_wr_calname = "Solar Events"

        logger.debug "generating solar calendar for #{year}"

        (Date.parse("#{year}0101")..Date.parse("#{year}1231")).each do |date|
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

        logger.info "solar calendar for #{year} generated with #{cal.events.length} events"

        return cal
    end

end

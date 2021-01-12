##
## Copyright (C) 2021 Jordan Ritter <jpr5@darkridge.com>
##
## WebCal server for Tides & Sunset information.  Meant to replace
## sailwx.info/tides.mobilegraphics.com, which as of 2021 appears no longer to
## work.
##

# FIXME: fix tide event URLs to reference the right day from tz (not GMT)

require 'bundler/setup'
Bundler.require

require 'icalendar/tzinfo'
require 'solareventcalculator'

class Server < ::Sinatra::Base

    set :app_file,   File.expand_path(__FILE__)
    set :root,       File.expand_path(File.dirname(__FILE__))
    set :views,      settings.root + '/views'
    set :cache_dir,  settings.root + '/cache'

    configure do
        set :logging, Logger::DEBUG
        disable :sessions

        Timezone::Lookup.config(:geonames) do |c|
            c.username = ENV['USER']
        end

        FileUtils.mkdir settings.cache_dir unless File.directory? settings.cache_dir

        not_found do
            msg = "URL not recognized: %s" % env['REQUEST_URI']
            puts msg
            halt 404
        end

        error do
            e = env['sinatra.error']
            puts "exception raised during processing: #{e.inspect}"
        end
    end

    configure :development do
        enable :show_exceptions

        before(/debug(|ger)/) { debugger }
    end

    configure :production do
        set :logging, Logger::INFO
        disable :reload_templates, :reloader
    end

    ###############
    ### Methods ###
    ###############

    def cache_tide_data_for(station, at:nil, year:)
        return unless station
        at ||= "#{settings.cache_dir}/#{station}_#{year}.json"

        agent = Mechanize.new
        url = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=predictions&datum=MLLW&time_zone=gmt&interval=hilo&units=english&application=web_services&format=json&begin_date=#{year}0101&end_date=#{year}1231&station=#{station}"

        logger.info "getting json from #{url}"
        json = agent.get(url).body
        logger.debug "json.length = #{json.length}"

        logger.debug "storing tide data at #{at}"
        File.open(at, "w+") do |f|
            f.write json
        end

        return json.length > 0
    end

    def tide_data_for(station, year:Time.now.year)
        return nil unless station

        filename = "#{settings.cache_dir}/#{station}_#{year}.json"
        File.exists? filename or cache_tide_data_for(station, at: filename, year: year)

        logger.debug "reading #{filename}"
        json = File.read(filename)

        logger.debug "parsing tides for #{station}"
        data = JSON.parse(json)["predictions"] rescue nil

        return data
    end

    def cache_stations(at:nil)
        at ||= "#{settings.cache_dir}/stations.json"

        agent = Mechanize.new
        url = 'https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/tidepredstations.json?q='

        logger.info "getting station list from #{url}"
        json = agent.get(url).body
        logger.debug "json.length = #{json.length}"

        logger.debug "storing station list at #{at}"
        File.open(at, "w+") do |f|
            f.write json
        end

        return json.length > 0
    end

    def stations
        return @stations ||= begin
            filename = "#{settings.cache_dir}/stations.json"

            File.exists? filename or cache_stations(at:filename)

            logger.debug "reading #{filename}"
            json = File.read(filename)

            logger.debug "parsing station list"
            data = JSON.parse(json)["stationList"] rescue {}
        end
    end

    def station_for(id)
        return nil if id.blank?
        return stations.find { |s| s["stationId"] == id }
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

    def calendar_for(station_data, station:)
        tideurl = "https://tidesandcurrents.noaa.gov/noaatidepredictions.html"
        cal = Icalendar::Calendar.new

        cal.x_wr_calname = station["name"].titleize

        date_seen = {}

        logger.debug "generating calendar for #{station["name"]}"

        station_data.each do |tide|
            date     = DateTime.parse(tide['t']).strftime("%Y%m%d")
            title    = tide["type"] == "H" ? "High" : "Low"
            title   += " Tide   #{tide["v"]} feet"
            location = [
                    station["etidesStnName"], station["region"], station["state"]
                ].join(", ")

            e             = Icalendar::Event.new
            e.summary     = title
            e.dtstart     = Icalendar::Values::DateTime.new(DateTime.parse(tide["t"]), tzid: 'GMT')
            e.dtend       = Icalendar::Values::DateTime.new(e.dtstart, tzid: 'GMT')
            e.url         = tideurl +
                            "?id=" + station["stationId"] +
                            "&bdate=" + date
            e.location    = location

            cal.add_event(e)

            # Calculate sunrise/sunset only once per day
            unless date_seen[date]
                lat  = station["lat"]
                long = station["lon"]

                tz      = timezone_for(lat, long)
                calc    = SolarEventCalculator.new(Date.parse(date), lat, long)
                sunrise = calc.compute_official_sunrise(tz)
                sunset  = calc.compute_official_sunset(tz)

                # I dunno why tzid: GMT is correct vs. tzid: tz, but it works..
                e          = Icalendar::Event.new
                e.summary  = "Sunrise"
                e.dtstart  = Icalendar::Values::DateTime.new(sunrise, tzid: 'GMT')
                e.dtend    = Icalendar::Values::DateTime.new(e.dtstart, tzid: 'GMT')
                e.location = location

                cal.add_event(e)

                e          = Icalendar::Event.new
                e.summary  = "Sunset"
                e.dtstart  = Icalendar::Values::DateTime.new(sunset, tzid: 'GMT')
                e.dtend    = Icalendar::Values::DateTime.new(e.dtstart, tzid: 'GMT')
                e.location = location

                cal.add_event(e)

                date_seen[date] = true
            end

        end

        logger.info "calendar for #{station["name"]} generated with #{cal.events.length} events"

        return cal
    end

    get "/tides/:station.ics" do
        id       = params[:station]
        year     = Time.now.year
        filename = "#{settings.cache_dir}/#{id}_#{year}.ics"

        ics = File.read filename rescue begin
            data     = tide_data_for(id) or halt 404
            calendar = calendar_for(data, station:station_for(id)) or halt 500
            calendar.publish
            logger.info "caching to #{filename}"
            File.write filename, ical = calendar.to_ical
            ical
        end

        content_type 'text/calendar'
        body ics
    end

    get "/" do
        erb :index
    end

    post "/" do
        text = params['searchtext'].downcase rescue ""

        results = stations.select do |s|
            s['stationId'] == text ||
            s['etidesStnName'].downcase.include?(text) rescue false ||
            s['commonName'].downcase.include?(text) rescue false ||
            s['stationFullName'].downcase.include?(text) rescue false ||
            s['region'].downcase.include?(text) rescue false
        end

        erb :index, locals: { results: results, request_url: request.url }
    end

end

##
## Copyright (C) 2024 Jordan Ritter <jpr5@darkridge.com>
##
## WebCal server for Tides & Sunset information.  Meant to replace
## sailwx.info/tides.mobilegeographics.com, which as of 2021 appears no longer
## to work.
##

# FIXME: fix tide event URLs to reference the right day from tz (not GMT)

require 'bundler/setup'
Bundler.require

require_relative 'webcaltides'

::Sinatra::Helpers.send(:include, ::Rack::Utils)

class Server < ::Sinatra::Base

    set :app_file,      File.expand_path(__FILE__)
    set :root,          File.expand_path(File.dirname(__FILE__))
    set :cache_dir,     settings.root + '/cache'
    set :static,        true
    set :public_folder, settings.root + '/public'
    set :views,         settings.root + '/views'

    configure do
        set :logging, Logger::DEBUG
        disable :sessions

        Timezone::Lookup.config(:geonames) do |c|
            c.username = ENV['USER']
        end

        FileUtils.mkdir_p settings.cache_dir
    end

    configure :development do
        enable :show_exceptions
    end

    configure :production do
        set :logging, Logger::INFO
        disable :reload_templates, :reloader, :show_exceptions
    end

    ##
    ## URL entry points
    ##

    get "/" do
        erb :index
    end

    post "/" do
        text   = params['searchtext'].downcase rescue nil
        radius = params['within']
        radius_units = params['units'] == 'metric' ? 'km' : 'mi'

        # If we see anything like "42.1234, 1234.0132" then treat it like a GPS search
        if ((lat, long) = WebCalTides.parse_gps(text))
            how = "near"
            tokens = [lat, long]

            radius ||= "10" # default;

            tide_results    = WebCalTides.find_tide_stations_by_gps(lat, long, within:radius, units: radius_units)
            current_results = WebCalTides.find_current_stations_by_gps(lat, long, within:radius, units: radius_units)
        else
            how = "by"

            # Parse search terms.  Matched quotes are taken as-is (still
            # lowercased), while everything else is tokenized via [ ,]+.
            tokens = text.scan(/["]([^"]+)["]/).flatten
            text.gsub!(/["]([^"]+)["]/, '')
            tokens += text.split(/[, ]+/).reject(&:empty?)

            tide_results    = WebCalTides.find_tide_stations(by:tokens, within:radius, units: radius_units)
            current_results = WebCalTides.find_current_stations(by:tokens, within:radius, units: radius_units)
        end

        tide_results    ||= []
        current_results ||= []

        for_what  = "#{text}"
        for_what += " within [#{radius}]" if radius

        logger.info "search #{how} #{for_what} yields #{tide_results.count + current_results.count} results"

        erb :index, locals: { tide_results: tide_results, current_results: current_results,
                              request: request, searchtext: tokens, params: params }
    end

    # For currents, station can be either an ID (we'll use the first bin) or a BID (specific bin)
    get "/:type/:station.ics" do
        type     = params[:type]
        id       = params[:station]
        date     = Date.parse(params[:date]) rescue Time.current.utc # e.g. 20231201, for utility but unsupported in UI
        units    = params[:units] || 'imperial'
        stamp    = date.utc.strftime("%Y%m")
        version  = type == "currents" ? DataModels::CurrentData.version : DataModels::TideData.version
        filename = "#{settings.cache_dir}/#{type}_v#{version}_#{id}_#{stamp}_#{units}.ics"

        # NOTE: Changed my mind on retval's.  In the shit-fucked-up case, we end up sending out a
        # full stack trace + 500, so really if we muck something up internally we should let the
        # exception float up.  Then we can assume that if we arrive without a calendar, then it's
        # simply not there -> 404.

        ics = File.read filename rescue begin
            calendar = case type
                       when "tides"    then WebCalTides.tide_calendar_for(id, around: date, units: units) or halt 404
                       when "currents" then WebCalTides.current_calendar_for(id, around: date)            or halt 404
                       else halt 404
                       end

            calendar.publish
            logger.info "caching to #{filename}"
            File.write filename, ical = calendar.to_ical
            ical
        end

        content_type 'text/calendar', charset: 'utf-8'
        body ics
    end

end

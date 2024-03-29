##
## Copyright (C) 2024 Jordan Ritter <jpr5@darkridge.com>
##
## WebCal server for Tides & Sunset information.  Meant to replace
## sailwx.info/tides.mobilegeographics.com, which as of 2021 appears no longer
## to work.
##

# FIXME: fix tide event URLs to reference the right day from tz (not GMT)

require 'logger'
$LOG = Logger.new(STDOUT).tap do |log|
    log.formatter = proc { |s, d, _, m| "#{d.strftime("%Y-%m-%d %H:%M:%S")} #{s} #{m}\n" }
end

require 'bundler/setup'
Bundler.require

require_relative 'webcaltides'


class Server < ::Sinatra::Base

    set :app_file,      File.expand_path(__FILE__)
    set :root,          File.expand_path(File.dirname(__FILE__))
    set :cache_dir,     settings.root + '/cache'
    set :public_folder, settings.root + '/public'
    set :views,         settings.root + '/views'
    set :static,        true

    configure do
        enable :show_exceptions
        disable :sessions, :logging

        helpers ::Rack::Utils

        Timezone::Lookup.config(:geonames) do |c|
            c.username = ENV['USER']
        end

        FileUtils.mkdir_p settings.cache_dir
    end

    configure :development do
        set :logging, $LOG
    end

    ##
    ## URL entry points
    ##

    get "/" do
        erb :index
    end

    post "/" do
        # Depending on your font these quotes may look the same -- but they're not
        radius       = params['within']
        radius_units = params['units'] == 'metric' ? 'km' : 'mi'
        text         = params['searchtext'].downcase.tr('“”', '""') rescue ''

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

        for_what  = "#{tokens}"
        for_what += " within [#{radius}]" if radius

        $LOG.info "search #{how} #{for_what} yields #{tide_results.count + current_results.count} results"

        erb :index, locals: { tide_results: tide_results, current_results: current_results,
                              request: request, searchtext: tokens, params: params }
    end

    # For currents, station can be either an ID (we'll use the first bin) or a BID (specific bin)
    get "/:type/:station.ics" do
        type       = params[:type]
        id         = params[:station]
        date       = Date.parse(params[:date]) rescue Time.current.utc # e.g. 20231201, for utility but unsupported in UI
        units      = params[:units] || 'imperial'
        stamp      = date.utc.strftime("%Y%m")
        version    = type == "currents" ? DataModels::CurrentData.version : DataModels::TideData.version
        cached_ics = "#{settings.cache_dir}/#{type}_v#{version}_#{id}_#{stamp}_#{units}.ics"

        # NOTE: Changed my mind on retval's.  In the shit-fucked-up case, we end up sending out a
        # full stack trace + 500, so really if we muck something up internally we should let the
        # exception float up.  Then we can assume that if we arrive without a calendar, then it's
        # simply not there -> 404.

        ics = File.read cached_ics rescue begin
            calendar = case type
                       when "tides"    then WebCalTides.tide_calendar_for(id, around: date, units: units) or halt 404
                       when "currents" then WebCalTides.current_calendar_for(id, around: date)            or halt 404
                       else halt 404
                       end

            calendar.publish

            $LOG.info "caching to #{cached_ics}"
            File.write cached_ics, ical = calendar.to_ical

            ical
        end

        content_type 'text/calendar', charset: 'utf-8'
        body ics
    end

end

##
## Copyright (C) 2025 Jordan Ritter <jpr5@darkridge.com>
##
## WebCal server for Tides, Currents, Sunset and Lunar information.  Meant to
## replace sailwx.info/tides.mobilegeographics.com, which as of 2021 appears no
## longer to work.
##

require 'logger'
$LOG = Logger.new(STDOUT).tap do |log|
    log.formatter = proc { |s, d, _, m| "#{d.strftime("%Y-%m-%d %H:%M:%S")} #{s} #{m}\n" }
end

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
        register Sinatra::Reloader
        also_reload File.expand_path("./webcaltides.rb")
        also_reload File.expand_path("./gps.rb")
        after_reload { $LOG.debug 'reloaded' }
    end

    ##
    ## URL entry points
    ##

    get "/" do
        erb :index, locals: { searchtext: nil, units: nil }
    end

    post "/" do
        radius       = params['within'].to_i
        radius_units = params['units'] == 'metric' ? 'km' : 'mi'
        searchparam  = (params['searchtext'] || '').strip
        searchtext   = searchparam.dup.downcase.tr('“”', '""')
        # ^^^ Depending on your font these quotes may look the same -- but they're not

        return erb :index, locals: { searchtext: nil, units: nil } unless searchtext.length > 0

        # If we see anything like "42.1234, 1234.0132" then treat it like a GPS search
        if ((lat, long) = WebCalTides.parse_gps(searchtext))
            how = "near"
            tokens = [lat, long]

            radius = 10 # default

            tide_results    = WebCalTides.find_tide_stations_by_gps(lat, long, within:radius, units: radius_units)
            current_results = WebCalTides.find_current_stations_by_gps(lat, long, within:radius, units: radius_units)
        else
            how = "by"

            # Parse search terms.  Matched quotes are taken as-is (still
            # lowercased), while everything else is tokenized via [ ,]+.
            tokens = searchtext.scan(/["]([^"]+)["]/).flatten
            searchtext.gsub!(/["]([^"]+)["]/, '')
            tokens += searchtext.split(/[, ]+/).reject(&:empty?)

            unless tokens.empty?
                tide_results    = WebCalTides.find_tide_stations(by:tokens, within:radius, units: radius_units)
                current_results = WebCalTides.find_current_stations(by:tokens, within:radius, units: radius_units)
            end
        end

        tide_results    ||= []
        current_results ||= []

        for_what  = "#{tokens}"
        for_what += " within #{radius}#{radius_units}" if radius

        $LOG.info "search #{how} #{for_what} yields #{tide_results.count + current_results.count} results"

        erb :index, locals: { tide_results: tide_results, current_results: current_results,
                              tokens: tokens, how:how, radius: radius, units: ERB::Util.html_escape_once(params['units'] || 'imperial'),
                              placeholder: ERB::Util.html_escape_once(searchparam.empty? ? 'Station...' : searchparam)
                            }
    end

    # For currents, station can be either an ID (we'll use the first bin) or a BID (specific bin)
    get "/:type/:station.ics" do
        type       = params[:type].tap { |type| type.in?(%w[tides currents]) or halt 404 }
        id         = params[:station].tap { |station| station.in?(WebCalTides.station_ids) or halt 404 }
        date       = Date.parse(params[:date]) rescue Time.current.utc # e.g. 20231201, for utility but unsupported in UI
        units      = params.fetch(:units, 'imperial').tap { |units| units.in?(%w[imperial metric]) or halt 422 }
        no_solar   = params[:solar].in?(%w[0 false]) # on by default
        add_lunar  = params[:lunar].in?(%w[1 true])  # off by default
        stamp      = date.utc.strftime("%Y%m")
        version    = type == "currents" ? Models::CurrentData.version : Models::TideData.version
        cached_ics = "#{settings.cache_dir}/#{type}_v#{version}_#{id}_#{stamp}_#{units}_#{no_solar ?"0":"1"}_#{add_lunar ?"1":"0"}.ics"

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

            # Add solar events if requested
            WebCalTides.solar_calendar_for(calendar, around:date) unless no_solar

            # Add lunar phase events if requested
            WebCalTides.lunar_calendar_for(calendar, around:date) if add_lunar

            calendar.publish

            $LOG.debug "caching to #{cached_ics}"
            File.write cached_ics, ical = calendar.to_ical

            ical
        end

        content_type 'text/calendar', charset: 'utf-8'
        body ics
    end

end

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

$: << "."
require 'webcaltides'


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

        FileUtils.mkdir settings.cache_dir unless File.directory? settings.cache_dir
    end

    configure :development do
        enable :show_exceptions
    end

    configure :production do
        set :logging, Logger::INFO
        disable :reload_templates, :reloader
    end

    ##
    ## URL entry points
    ##

    get "/" do
        erb :index
    end

    post "/" do
        text   = params['searchtext'].downcase rescue nil
        radius = params['within'] rescue nil

        tide_results    = WebCalTides.find_tide_stations(by:text, within:radius)
        current_results = WebCalTides.find_current_stations(by:text, within:radius)

        erb :index, locals: { tide_results: tide_results, current_results: current_results, request_url: request.url }
    end

    get "/tides/:station.ics" do
        id       = params[:station]
        year     = params[:year] || Time.now.year
        filename = "#{settings.cache_dir}/tides_#{id}_#{year}.ics"

        ics = File.read filename rescue begin
            calendar = WebCalTides.tide_calendar_for(id, year:year) or halt 500
            calendar.publish
            logger.info "caching to #{filename}"
            File.write filename, ical = calendar.to_ical
            ical
        end

        content_type 'text/calendar'
        body ics
    end

    # station can be either an ID (we'll use the first bin) or a BID (specific bin)
    get "/currents/:station.ics" do
        id      = params[:station]
        year     = Time.now.year
        filename = "#{settings.cache_dir}/currents_#{id}_#{year}.ics"

        ics = File.read filename rescue begin
            calendar = WebCalTides.current_calendar_for(id, year:year) or halt 500
            calendar.publish
            logger.info "caching to #{filename}"
            File.write filename, ical = calendar.to_ical
            ical
        end

        content_type 'text/calendar'
        body ics
    end

end

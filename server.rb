##
## Copyright (C) 2021 Jordan Ritter <jpr5@darkridge.com>
##
## WebCal server for Tides & Sunset information.  Meant to replace
## sailwx.info/tides.mobilegraphics.com, which as of 2021 appears no longer to
## work.
##

# TODO: Rework main index page to allow searching on tide vs. current stations
# FIXME: fix tide event URLs to reference the right day from tz (not GMT)

require 'bundler/setup'
Bundler.require

$: << "."
require 'webcaltides'


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
        text = params['searchtext'].downcase rescue ""

        tide_results = WebCalTides.tide_stations.select do |s|
            s['stationId'] == text ||
            s['etidesStnName'].downcase.include?(text) rescue false ||
            s['commonName'].downcase.include?(text) rescue false ||
            s['stationFullName'].downcase.include?(text) rescue false ||
            s['region'].downcase.include?(text) rescue false
        end

        current_results = WebCalTides.current_stations.select do |s|
            s['id'].downcase.start_with?(text.downcase) rescue false ||
            s['id'].downcase.include?(text.downcase) rescue false ||
            s['name'].downcase.include?(text) rescue false
        end

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

require_relative 'base'
require_relative '../models/station'
require_relative '../models/current_data'

module Clients

    class NoaaCurrents < Base

        API_URL            = 'https://api.tidesandcurrents.noaa.gov'
        PUBLIC_STATION_URL = 'https://tidesandcurrents.noaa.gov/noaacurrents/predictions?id=%s'

        include TimeWindow

        ## NOAA currents generation won't do more than 366 days.
        self.window_size = 12.months

        def current_stations
            url = "#{API_URL}/mdapi/prod/webapi/stations.json?type=currentpredictions&units=english"

            logger.info "getting current station list from #{url}"

            return nil unless json = get_url(url)

            logger.debug "parsing current station list from API #{API_URL}"
            data = JSON.parse(json)["stations"] || {} rescue {}

            # Tho we get records, anything "weak and variable" won't have a lookup page,
            # so we exclude them.
            data.reject! { |s| s["type"] == "W" }

            # Since different bins/depths use the same ID, we massage each entry with a
            # unique "bin id" aka bid.
            data.map! { |s| s["bid"] = s["id"] + "_" + s["currbin"].to_s; s }

            stations = data.map do |js|
                Models::Station.new(
                    bid:      js["bid"],
                    id:       js["id"],
                    name:     js["name"],
                    lat:      js["lat"],
                    lon:      js["lng"],
                    depth:    js["depth"],
                    url:      PUBLIC_STATION_URL % [ js["bid"] ],
                    provider: "noaa",
                )
            end

            return stations
        end

        def current_data_for(station, around)
            (_, id, bin) = /(\w+)_(\d+)/.match(station.bid).to_a

            from  = beginning_of_window(around).strftime("%Y%m%d")
            to    = end_of_window(around).strftime("%Y%m%d")
            url   = "#{API_URL}/api/prod/datagetter?product=currents_predictions&begin_date=#{from}&end_date=#{to}&station=#{id}&time_zone=gmt&interval=MAX_SLACK&units=english&format=json"
            url   += "&bin=#{bin}" if bin

            logger.info "getting current data from #{url}"

            return nil unless json = get_url(url)

            logger.debug "parsing current predictions for #{station.bid} from API #{API_URL}"
            data = JSON.parse(json)["current_predictions"]["cp"] || [] rescue []

            return data.map do |jc|
                jc["Time"] = DateTime.parse(jc["Time"])
                jc["Url"]  = station.url + "&d=#{jc["Time"].strftime("%Y-%m-%d")}"
                Models::CurrentData.from_hash(jc)
            end
        end

    end

end

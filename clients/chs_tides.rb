require_relative 'base'
require_relative '../data_models/station'
require_relative '../data_models/tide_data'

module Clients
    class ChsTides < Base

        API_URL            = 'https://api-iwls.dfo-mpo.gc.ca/api/v1'
        PUBLIC_STATION_URL = "https://www.tides.gc.ca/en/stations/%s"

        include TimeWindow

        ## CHS tides generation won't do more than 366 days.
        self.window_size = 12.months

        def tide_stations
            url = "#{API_URL}/stations"

            logger.info "getting tide station list from #{url}"

            return nil unless json = get_url(url)

            logger.debug "parsing tide station list from API #{API_URL}"
            data = JSON.parse(json) rescue []

            stations = data.map do |js|
                DataModels::Station.new(
                    name: js['officialName'],
                    alternate_names: [],
                    id: js['id'],
                    public_id: js['code'],
                    region: region_for(js['latitude'], js['longitude']),
                    location: [js["officialName"], "Canada"].join(", "),
                    lat: js['latitude'],
                    lon: js['longitude'],
                    url: PUBLIC_STATION_URL % [ js['code'] ],
                    provider: 'chs'
                )
            end

            return stations
        end

        def tide_data_for(station, around)
            from  = beginning_of_window(around).iso8601
            to    = end_of_window(around).iso8601
            url   = "#{API_URL}/stations/#{station.id}/data?time-series-code=wlp-hilo&from=#{from}&to=#{to}"

            logger.info "getting tide data from #{url}"

            return nil unless json = get_url(url)

            logger.debug "parsing tide predictions for #{station.id} from API #{API_URL}"
            data = JSON.parse(json) rescue []

            # So this happened: station 5cebf1e23d0f4a073c4bbfb4 returned empty data.  It is type:
            # DISCONTINUED, operating: false, which would strongly imply we should filter those out
            # from the list, however a different station 5cebf1df3d0f4a073c4bbcb9 also has the same
            # type/operating but *DOES* return tide data.
            #
            # Upon further research, it turns out the CHS metadata is a fucking mess.  Of the tons
            # of possible indicators in the metadata, none are reliable as way to know if an
            # "active" server in the station list will return actual tide data.  This is to say,
            # some stations that are Temporary, Discontinued, or operating:false, etc, will return
            # data, while others that are status:OK, Permanent, etc, won't.  The only way to know is
            # to try to retrieve it.
            #
            # Which means we can't filter them out of the list ahead of time, since there are API
            # ratelimits and over 1k stations to double-check.  All we can do is (1) return nil to
            # the caller, and (2) nuke the station from the list post-facto.  This means bad
            # stations will show up in the search results until someone attempts to use one -- then
            # it will get nuked.
            #
            # Super lame.

            if data.length == 0
                logger.error "!! got empty tide data for station #{station.id}, nuking from list"
                WebCalTides.remove_tide_station(station.id)
                return nil
            end

            prev_value = data[1]['value']

            return data.map do |jt|
                time = DateTime.parse(jt["eventDate"])
                td = DataModels::TideData.new(
                    type: jt['value'] >= prev_value ? "High" : "Low",
                    units: "m",
                    prediction: jt["value"],
                    time: time,
                    url: station.url + "/#{time.strftime("%Y-%m-%d")}"
                )
                prev_value = td.prediction
                td
            end
        end

        private

        def region_for(lat, long)
            if long < -75 && long > -96 && lat < 64 && lat > 51
                'Hudson\'s Bay, Canada'
            elsif lat > 60
                'Northern Canada'
            elsif long < -120
                'Pacific Canada'
            elsif long > -75
                'Atlantic Canada'
            else
                'Canada'
            end
        end

    end
end
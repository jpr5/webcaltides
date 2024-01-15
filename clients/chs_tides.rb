require_relative 'base'
require_relative '../data_models/station'
require_relative '../data_models/tide_data'

module Clients
    class ChsTides < Base

        API_URL            = 'https://api-iwls.dfo-mpo.gc.ca/api/v1'
        PUBLIC_STATION_URL = "https://www.tides.gc.ca/en/stations/%s"

        include TimeWindow

        ## NOAA currents generation won't do more than 366 days - so we can't do 1 year back/forwards.
        self.window_size = 5.months

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
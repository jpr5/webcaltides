require 'mechanize'

module Clients

    class Base

        attr_accessor :logger

        def initialize(logger)
            @logger = logger
        end

        def get_url(url)
            agent = Mechanize.new
            json  = nil

            begin
                json = agent.get(url).body
                logger.debug "json.length = #{json.length}"
            rescue Mechanize::ResponseCodeError => e
                logger.error "GET #{url} failed: #{e.detailed_message} (#{e.page&.content})"
                raise e # doing this will pop out to an HTTP 500
            end

            return json
        end

    end

    module TimeWindow
        # around: base date, with a +/- N month window.  More processing per year, but this at least
        # solves the EOY data starvation problem, data is cached to disk and the 12x processing
        # (monthly vs. yearly) is cheap. 🤷‍♂️

        def self.included(klass)
            klass.class_eval do
                cattr_accessor :window_size
                self.window_size = 12.months # default
            end
        end

        def beginning_of_window(around)
            return around.utc.beginning_of_month - self.window_size
        end

        def end_of_window(around)
            return around.utc.end_of_month + self.window_size
        end

    end

end

require 'mechanize'

module Clients

    class Base

        attr_accessor :logger

        def initialize(logger)
            @logger = logger
        end

        def get_url(url)
            agent   = Mechanize.new
            json    = nil
            retries = 0

            begin

                json = agent.get(url).body
                logger.debug "json.length = #{json.length}"

            rescue Mechanize::ResponseCodeError => e

                # seeing gateway timeouts from time to time
                if e.response_code == "502" || e.response_code == "504" and retries < 3
                    retries += 1
                    sleep rand(0..Float(2**retries))
                    retry
                end

                logger.error "GET failed: #{e.detailed_message} (#{e.page&.content})"
                raise e # doing this will pop out to an HTTP 500

            end

            return json
        end

    end

    module TimeWindow

        # around: base date, with a sliding window around the date -- PRIOR_MONTHS months before,
        # and current month + window_size - (PRIOR_MONTHS + 1.month) after.
        #
        # More processing per year, but this at least solves the EOY data starvation problem, data
        # is cached to disk and the 12x processing (monthly vs. yearly) is still super cheap. 🤷‍♂️

        def self.included(klass)
            klass.class_eval do
                cattr_accessor :window_size
                self.window_size = 12.months # default
            end
        end

        # Always do 1 month prior - so beginning of range is this month + last month.
        PRIOR_MONTHS = 1.month

        def beginning_of_window(around)
            return around.utc.beginning_of_month - PRIOR_MONTHS
        end

        def end_of_window(around)
            # subtract prior + current month
            return around.utc.end_of_month + self.window_size - (PRIOR_MONTHS + 1.month)
        end

    end

end

require 'mechanize'

module Clients

    class Base

        attr_accessor :logger

        def initialize(logger)
            @logger = logger
        end

        MAX_RETRIES = 5

        def get_url(url)
            agent   = Mechanize.new
            json    = nil
            retries = 0

            begin
                json = agent.get(url).body
                logger.debug "json.length = #{json.length}"
            rescue Mechanize::ResponseCodeError => e
                # Retry on gateway errors with exponential backoff
                if (e.response_code == "502" || e.response_code == "504") && retries < MAX_RETRIES
                    retries += 1
                    delay = rand(0.5..(2.0 ** retries))
                    logger.warn "#{e.response_code} from #{url.split('?').first}, retry #{retries}/#{MAX_RETRIES} in #{delay.round(1)}s"
                    sleep delay
                    retry
                end

                logger.error "GET failed after #{retries} retries: #{e.detailed_message}"
                raise e
            rescue Net::OpenTimeout, Net::ReadTimeout => e
                # Retry on network timeouts
                if retries < MAX_RETRIES
                    retries += 1
                    delay = rand(0.5..(2.0 ** retries))
                    logger.warn "Timeout for #{url.split('?').first}, retry #{retries}/#{MAX_RETRIES} in #{delay.round(1)}s"
                    sleep delay
                    retry
                end

                logger.error "Timeout after #{retries} retries: #{e.message}"
                raise e
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

env = ENV["FU_ENV"] || ENV["RAILS_ENV"] || ENV["RACK_ENV"]
log_requests false # controlled from sinatra, don't want dupe

# LEARNING: we could significantly parallelize to increase throughpu under puma, but
# at the end of the day, we're limited by calls to NOAA and CHS, who will throttle
# us if we send them too many concurrent requests.  So it does us no good to scale
# very far, because it'll just stall our users' requests as they retry.  IOW we'll
# get more net throughput not triggering the throttle.

workers 0
threads 4, 10

if env == "production" || env == "staging"
    if ENV["RAILWAY_ENVIRONMENT"]
        # Take advantage of Railway's available resources
        port ENV.fetch("PORT", 3000)
    else
        root = File.expand_path(".")
        bind "unix://" + root + "/webcaltides.sock"
        stdout_redirect("/srv/webcaltides/logs/webcaltides.log", "/srv/webcaltides/logs/webcaltides.log", true)
    end
end

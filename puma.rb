env = ENV["FU_ENV"] || ENV["RAILS_ENV"] || ENV["RACK_ENV"]
log_requests false # controlled from sinatra, don't want dupe

if env == "production" || env == "staging"
    if ENV["RAILWAY_ENVIRONMENT"]
        # Take advantage of Railway's available resources
        workers 8
        threads 4, 10
        port = ENV.fetch("PORT", 3000)
    else
        workers 0
        threads 4, 20 # high max because agents tend to sync on time, which can overwhelm us

        root = File.expand_path(".")
        bind "unix://" + root + "/webcaltides.sock"
        stdout_redirect("/srv/webcaltides/logs/webcaltides.log", "/srv/webcaltides/logs/webcaltides.log", true)
    end
end

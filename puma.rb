workers 0
threads 4, 10 # high max because agents tend to sync on time, which can overwhelm us

log_requests false # controlled from sinatra, don't want dupe

env = ENV["FU_ENV"] || ENV["RAILS_ENV"] || ENV["RACK_ENV"]

if env == "production"
    root = File.expand_path(".")
    bind "unix://" + root + "/webcaltides.sock"
    stdout_redirect "/srv/webcaltides/webcaltides.log", "/srv/webcaltides/webcaltides.log", true
end

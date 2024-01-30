root = File.expand_path(".")
  
bind  "unix://" + root + "/webcaltides.sock"
workers 0
threads 4, 10

stdout_redirect "/srv/http/logs/webcaltides.log", "/srv/http/logs/webcaltides.log", true

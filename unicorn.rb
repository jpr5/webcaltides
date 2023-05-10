root = File.expand_path(".")

listen  root + "/webcaltides.sock"
worker_processes 2
timeout 30

stderr_path "/srv/http/logs/webcaltides.log"

require_relative "server"

use Rack::Deflater
run Server

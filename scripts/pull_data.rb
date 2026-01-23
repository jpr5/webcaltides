# Utility script for systems like railway that stupidly don't support LFS.

require "net/http"
require "uri"
require "fileutils"

BASE_URL = "https://github.com/jpr5/webcaltides/releases/download/data-v1"

FILES = %w[
    data/harmonics-dwf-20241229.sql
    data/GESLA4_ALL.csv
    data/TICON_3.csv
    data/ticon.json
]

def download(url, path, limit = 5)
    raise "too many redirects" if limit == 0
    uri = URI(url)
    Net::HTTP.start(uri.host, uri.port, use_ssl: true) do |http|
        resp = http.get(uri.request_uri)
        if resp.is_a?(Net::HTTPRedirection)
            download(resp["location"], path, limit - 1)
        else
            FileUtils.mkdir_p(File.dirname(path))
            File.binwrite(path, resp.body)
            puts "Downloaded #{path} (#{resp.body.size} bytes)"
        end
    end
end

FILES.each do |file|
    url = "#{BASE_URL}/#{File.basename(file)}"
    download(url, file)
end

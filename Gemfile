source 'https://rubygems.org'

ruby '~> 3.2'

gem 'irb'
gem 'byebug'
gem 'pry'

gem 'activesupport', '>= 7.2.3.1', require: ['active_support', 'active_support/core_ext']

gem 'date'
gem 'json'
gem 'openssl', '>= 3.1.2'  # Fix for OpenSSL 3.5/3.6 CRL verification failures
gem 'nokogiri', '>= 1.19.3'
gem 'mechanize', '>= 2.9.1'

gem 'icalendar', '>= 2.12.2', require: [ 'icalendar', 'icalendar/tzinfo' ]
gem 'RubySunrise', require: 'solareventcalculator'
gem 'timezone'
# Zone data for TZInfo (ActiveSupport, RubySunrise, icalendar) that doesn't depend on the OS image.
# Debian trixie moved the backward links (Asia/Saigon, America/Godthab, ...) to tzdata-legacy, so
# with /usr/share/zoneinfo TZInfo rejected ids that Google's lookup still returns.
gem 'tzinfo-data'
gem 'geocoder'
gem 'tcd'

gem 'rack', '>= 3.2.6'
gem 'rack-session', '>= 2.1.2'
gem 'rackup', '>= 2.2.0'
gem 'webrick', '>= 1.8.2'
gem 'puma', '~> 7.2', '>= 7.2.1'
gem 'sinatra', '~> 4.2.1', require: 'sinatra/base'

gem 'dotenv'

# BP S1: scripts/gesla reads the GESLA release zip (zip64, deflate).
group :gesla do
    gem 'rubyzip', '~> 2.4', require: false
end

group :development do
    gem 'debug'
    gem 'sinatra-reloader', require: 'sinatra/reloader'
end

group :test do
    gem 'rspec', '~> 3.13'
    gem 'rack-test', '~> 2.1'
    gem 'webmock', '~> 3.23'
    gem 'vcr', '~> 6.2'
    gem 'timecop', '~> 0.9'
    gem 'simplecov', '~> 0.22', require: false
end

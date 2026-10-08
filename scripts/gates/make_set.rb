#!/usr/bin/env ruby
# Build a station-set JSON for runner '@file' mode.
# Usage: ruby tools/make_set.rb <name> <src,src,...> [extra_set.json ...]
# Example: ruby tools/make_set.rb others bsh,chs,kartverket,imi,rws sets/linz.json
require_relative 'lib'
name, srcs, *extra = ARGV
set = Eval.reference_set(srcs.split(','))
extra.each { |f| set += JSON.parse(File.read(File.expand_path(f, Eval::ROOT))) }
set = set.uniq { |s| s['id'] }.sort_by { |s| s['id'] }
FileUtils.mkdir_p("#{Eval::ROOT}/sets")
File.write("#{Eval::ROOT}/sets/#{name}.json", JSON.pretty_generate(set))
puts "sets/#{name}.json: #{set.size} stations #{set.map { |s| s['src'] }.tally}"

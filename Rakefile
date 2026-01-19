require 'bundler/setup'

begin
  require 'rspec/core/rake_task'
  RSpec::Core::RakeTask.new(:spec) do |t|
    t.pattern = 'spec/**/*_spec.rb'
  end
  task default: :spec
rescue LoadError
  # RSpec not available in production
end

# Usage: ruby tools/rn_summary.rb <rn_run> [W1,W2,W3] -- engine control mean time/height MAE vs NOAA per window
require 'csv'
H = File.expand_path('..', __dir__)
run = ARGV[0]; (ARGV[1] || 'W1,W2,W3').split(',').each do |w|
    r = CSV.read("#{H}/results/#{run}/#{w}/stations.csv", headers: true)
    t = r.map { |x| x['t_mae'].to_f }; h = r.filter_map { |x| x['h_mae_al'].to_s.empty? ? nil : x['h_mae_al'].to_f }
    m = ->(a) { a.sum / a.size }; s = t.sort
    printf("%s %s n=%d t_mae mean=%.3f median=%.3f max=%.2f | h_mae_al mean=%.3f (n=%d) | missed=%d extra=%d\n", run, w, t.size, m[t], s[s.size / 2], s[-1], m[h], h.size, r.sum { |x| x['missed'].to_i }, r.sum { |x| x['extra'].to_i })
end

# Usage: ruby tools/gateA_rollup.rb -- combines compare_R0_R1.csv + compare_R0_other_R1_other.csv per window:
# TICON mean t_mae R0 vs R1 and §6.2 class counts (TICON combined, XTide, all), minor share.
require 'csv'
require_relative 'paths'
H = GatePaths::ROOT
rows = CSV.read("#{H}/results/compare_R0_R1.csv", headers: true).map(&:to_h) +
       CSV.read("#{H}/results/compare_R0_other_R1_other.csv", headers: true).map(&:to_h)
classes = %w[improved meaningless minor real]
%w[W1 W2 W3].each do |w|
    r = rows.select { |x| x['window'] == w }
    { 'ticon' => r.select { |x| x['stratum'].start_with?('ticon') }, 'xtide' => r.select { |x| x['stratum'].start_with?('xtide') }, 'all' => r }.each do |g, s|
        c = Hash.new(0); s.each { |x| c[x['class']] += 1 }
        mi = s.count { |x| x['improved_t'] == 'true' || x['class'] == 'improved' }
        mf = s.count { |x| x['meaningful'] == 'true' }
        line = format('%s %-5s n=%4d', w, g, s.size)
        if g == 'ticon'
            a = s.sum { |x| x['a_t_mae'].to_f } / s.size; b = s.sum { |x| x['b_t_mae'].to_f } / s.size
            line += format(' meanT R0=%.2f R1=%.2f (%s)', a, b, b <= a ? 'not worse' : 'WORSE')
        end
        line += ' classes=' + c.sort.map { |k, v| "#{k}:#{v}" }.join(',') + " meaningful=#{mf}"
        line += format(' real%%=%.2f minor%%=%.2f', 100.0 * c['real'] / s.size, 100.0 * c['minor'] / s.size)
        puts line
    end
end

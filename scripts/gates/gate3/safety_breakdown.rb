# Active-set new rows vs a base safety run, by rule and provider (T = TICON, X = XTide). ruby safety_breakdown.rb <compare dir>...
ARGV.each do |d|
    rows = File.readlines("#{d}/new_violations.tsv").drop(1).map { |l| l.chomp.split("\t") }
    puts "== #{File.basename(d)} all=#{rows.size}"
    %w[active].each do |o|
        rs = rows.select { |r| r[2] == o }
        t = rs.group_by { |r| [r[4], r[6], r[1][0]] }.transform_values(&:size).sort
        puts "  #{o}: #{rs.size} rows, fail=#{rs.count { |r| r[6] == 'fail' }} (stations #{rs.select { |r| r[6] == 'fail' }.map { |r| r[1] }.uniq.size}; TICON #{rs.count { |r| r[6] == 'fail' && r[1].start_with?('T') }} rows/#{rs.select { |r| r[6] == 'fail' && r[1].start_with?('T') }.map { |r| r[1] }.uniq.size} st)"
        t.each { |k, v| puts "    #{k.join(' ')} #{v}" }
        rs.select { |r| r[4] == 'count' && r[6] == 'fail' || r[4].start_with?('yb') }.each { |r| puts "    > #{r.join(' | ')}" }
    end
    rows.select { |r| r[4] == 'yb_duplicate' }.each { |r| puts "  yb_duplicate(all): #{r.join(' | ')}" }
end

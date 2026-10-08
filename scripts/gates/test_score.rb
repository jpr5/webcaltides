# Minimal checks for Score.match / Score.score (run: ruby tools/test_score.rb)
require_relative 'score'
t = ->(h, m = 0) { Time.utc(2026, 11, 1, h, m) }
ok = true
chk = ->(name, cond) { puts "#{cond ? 'ok  ' : 'FAIL'} #{name}"; ok &&= cond }
ref = [{ t: t[0], ty: 'High', h: 100.0 }, { t: t[6], ty: 'Low', h: 0.0 }, { t: t[12], ty: 'High', h: 100.0 }]
# model event at 1:00 is nearest to ref 0 only; a second model High at 0:30 must not double-match
model = [{ t: t[0, 30], ty: 'High', hr: 102.0 }, { t: t[1], ty: 'High', hr: 101.0 }, { t: t[6, 10], ty: 'Low', hr: 1.0 },
         { t: t[16], ty: 'High', hr: 99.0 }]
pairs, un = Score.match(ref, model)
chk['one-to-one, nearest wins', pairs == [[0, 0], [1, 2]]]
chk['outside 3h unmatched', un == [1, 3]]
m, rows = Score.score(ref, model, t[0], t[23])
chk['missed=1 extra=2', m[:missed] == 1 && m[:extra] == 2]
chk['t_mae', (m[:t_mae] - 20.0).abs < 1e-9]
chk['type must match', Score.match([{ t: t[0], ty: 'Low', h: 0 }], [{ t: t[0], ty: 'High', hr: 0 }])[0].empty?]
chk['range error', m[:n_range] == 1 && (m[:range_bias] - 1.0).abs < 1e-9]
chk['bias-removed height', rows.map { |r| r[:dh_al] }.sum.abs < 1e-9]
exit(ok ? 0 : 1)

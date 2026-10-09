require "json"; require "date"
O = JSON.parse(File.read(File.expand_path("~/.local/share/copilotkit/plans/2026-10-09-wct-nodal-oracle.json")))
V = { "O1"=>[[1,-2,1,0],90],"K1"=>[[1,0,1,0],-90],"P1"=>[[1,0,-1,0],90],"Q1"=>[[1,-3,1,1],90],"S1"=>[[1,0,0,0],0],
 "M1"=>[[1,-1,1,1],-90],"M2"=>[[2,-2,2,0],0],"S2"=>[[2,0,0,0],0],"N2"=>[[2,-3,2,1],0],"NU2"=>[[2,-3,4,-1],0],
 "LDA2"=>[[2,-1,0,1],180],"L2"=>[[2,-1,2,-1],180],"K2"=>[[2,0,2,0],0] }
EP = Date.new(1899,12,31,Date::GREGORIAN).jd
def days(y,m) ; s=(Date.new(y,1,1,Date::GREGORIAN).jd-EP)*86400 - 43200 + m.to_r*3600; Rational(s,86400); end
def lon_a(t) # deg per term
  s = 270 + 26/60.0 + 14.72/3600.0 + (1336*360.0 + 1108411.20/3600.0)*t + (9.09/3600.0)*t**2 + (0.0068/3600.0)*t**3
  h = 279 + 41/60.0 + 48.04/3600.0 + (129602768.13/3600.0)*t + (1.089/3600.0)*t**2
  p = 334 + 19/60.0 + 40.87/3600.0 + (11*360.0 + 392515.94/3600.0)*t - (37.24/3600.0)*t**2 - (0.045/3600.0)*t**3
  [s,h,p]
end
def lon_b(t) # arcsec sum then /3600
  s = (270*3600 + 26*60 + 14.72 + (1336*1296000 + 1108411.20)*t + 9.09*t**2 + 0.0068*t**3)/3600.0
  h = (279*3600 + 41*60 + 48.04 + 129602768.13*t + 1.089*t**2)/3600.0
  p = (334*3600 + 19*60 + 40.87 + (11*1296000 + 392515.94)*t - 37.24*t**2 - 0.045*t**3)/3600.0
  [s,h,p]
end
def lon_c(t) # deg constants precomputed with decimal rates
  s = (270 + 26/60.0 + 14.72/3600.0) + (1336*360 + 1108411.20/3600)*t + (9.09/3600)*t*t + (0.0068/3600)*t*t*t
  h = (279 + 41/60.0 + 48.04/3600.0) + (129602768.13/3600)*t + (1.089/3600)*t*t
  p = (334 + 19/60.0 + 40.87/3600.0) + (11*360 + 392515.94/3600)*t - (37.24/3600)*t*t - (0.045/3600)*t*t*t
  [s,h,p]
end
def lon_d(t) # rev and arcsec separated: 1336*360*t + 1108411.20/3600*t
  s = 270 + 26/60.0 + 14.72/3600.0 + 1336*360.0*t + 1108411.20/3600.0*t + 9.09/3600.0*t**2 + 0.0068/3600.0*t**3
  h = 279 + 41/60.0 + 48.04/3600.0 + 129602768.13/3600.0*t + 1.089/3600.0*t**2
  p = 334 + 19/60.0 + 40.87/3600.0 + 11*360.0*t + 392515.94/3600.0*t - 37.24/3600.0*t**2 - 0.045/3600.0*t**3
  [s,h,p]
end
tfs = { "rat"=>->(d){ (d/36525).to_f }, "flt"=>->(d){ d.to_f/36525.0 },
        "jd"=>->(d){ ((d + 2415020.0).to_f - 2415020.0)/36525.0 } }
ths = { "exact"=>->(d){ ((d-d.floor)*360).to_f }, "fracf"=>->(d){ x=d.to_f; (x-x.floor)*360.0 } }
seen = {}
O["cases"].each { |c| seen[[c["year"],c["shift_hours"],c["constituent"]]] ||= c["V0_raw"] }
%w[lon_a lon_b lon_c lon_d].each do |lf| tfs.each do |tn,tf| ths.each do |thn,thf| [:dot,:seq].each do |sum|
  worst=0.0; nbad=0
  seen.each do |(y,m,name),v0r|
    d=days(y,m); t=tf.(d); th=thf.(d); s,h,p=send(lf,t); co,k=V[name]
    v = sum==:dot ? co.zip([th,s,h,p]).sum{|a,b| a*b}+k : co[0]*th + co[1]*s + co[2]*h + co[3]*p + k
    e=(v-v0r).abs; worst=e if e>worst
  end
  puts format("%-6s t=%-4s th=%-6s %-4s worst raw dV0=%.3e", lf,tn,thn,sum,worst)
end end end end

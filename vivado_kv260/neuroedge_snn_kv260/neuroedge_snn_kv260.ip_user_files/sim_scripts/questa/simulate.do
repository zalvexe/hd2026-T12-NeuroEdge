onbreak {quit -f}
onerror {quit -f}

vsim  -lib xil_defaultlib snn_axi_lite_opt

set NumericStdNoWarnings 1
set StdArithNoWarnings 1

do {wave.do}

view wave
view structure
view signals

do {snn_axi_lite.udo}

run 1000ns

quit -force

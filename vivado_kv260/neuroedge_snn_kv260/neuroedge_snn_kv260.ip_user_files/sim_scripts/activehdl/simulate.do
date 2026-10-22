transcript off
onbreak {quit -force}
onerror {quit -force}
transcript on

asim +access +r +m+snn_axi_lite  -L xilinx_vip -L xpm -L xil_defaultlib -L xilinx_vip -L unisims_ver -L unimacro_ver -L secureip -O5 xil_defaultlib.snn_axi_lite xil_defaultlib.glbl

do {snn_axi_lite.udo}

run 1000ns

endsim

quit -force

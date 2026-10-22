transcript off
onbreak {quit -force}
onerror {quit -force}
transcript on

vlib work
vlib riviera/xilinx_vip
vlib riviera/xpm
vlib riviera/xil_defaultlib

vmap xilinx_vip riviera/xilinx_vip
vmap xpm riviera/xpm
vmap xil_defaultlib riviera/xil_defaultlib

vlog -work xilinx_vip  -incr -l axi_vip_v1_1_17 -l zynq_ultra_ps_e_vip_v1_0_17 "+incdir+C:/Xilinx/Vivado/2024.1/data/xilinx_vip/include" -l xilinx_vip -l xpm -l xil_defaultlib \
"C:/Xilinx/Vivado/2024.1/data/xilinx_vip/hdl/axi4stream_vip_axi4streampc.sv" \
"C:/Xilinx/Vivado/2024.1/data/xilinx_vip/hdl/axi_vip_axi4pc.sv" \
"C:/Xilinx/Vivado/2024.1/data/xilinx_vip/hdl/xil_common_vip_pkg.sv" \
"C:/Xilinx/Vivado/2024.1/data/xilinx_vip/hdl/axi4stream_vip_pkg.sv" \
"C:/Xilinx/Vivado/2024.1/data/xilinx_vip/hdl/axi_vip_pkg.sv" \
"C:/Xilinx/Vivado/2024.1/data/xilinx_vip/hdl/axi4stream_vip_if.sv" \
"C:/Xilinx/Vivado/2024.1/data/xilinx_vip/hdl/axi_vip_if.sv" \
"C:/Xilinx/Vivado/2024.1/data/xilinx_vip/hdl/clk_vip_if.sv" \
"C:/Xilinx/Vivado/2024.1/data/xilinx_vip/hdl/rst_vip_if.sv" \

vlog -work xpm  -incr -l axi_vip_v1_1_17 -l zynq_ultra_ps_e_vip_v1_0_17 "+incdir+../../../neuroedge_snn_kv260.gen/sources_1/bd/snn_soc_kv260/ipshared/ec67/hdl" "+incdir+../../../neuroedge_snn_kv260.gen/sources_1/bd/snn_soc_kv260/ipshared/a317/hdl" "+incdir+C:/Xilinx/Vivado/2024.1/data/xilinx_vip/include" -l xilinx_vip -l xpm -l xil_defaultlib \
"C:/Xilinx/Vivado/2024.1/data/ip/xpm/xpm_cdc/hdl/xpm_cdc.sv" \
"C:/Xilinx/Vivado/2024.1/data/ip/xpm/xpm_memory/hdl/xpm_memory.sv" \

vcom -work xpm -93  -incr \
"C:/Xilinx/Vivado/2024.1/data/ip/xpm/xpm_VCOMP.vhd" \

vlog -work xil_defaultlib  -incr -v2k5 "+incdir+../../../neuroedge_snn_kv260.gen/sources_1/bd/snn_soc_kv260/ipshared/ec67/hdl" "+incdir+../../../neuroedge_snn_kv260.gen/sources_1/bd/snn_soc_kv260/ipshared/a317/hdl" "+incdir+C:/Xilinx/Vivado/2024.1/data/xilinx_vip/include" -l xilinx_vip -l xpm -l xil_defaultlib \
"../../../../../rtl/argmax.v" \
"../../../../../rtl/lif_step.v" \
"../../../../../rtl/snn_controller.v" \
"../../../../../rtl/snn_core.v" \
"../../../../../rtl/snn_layer.v" \
"../../../../../rtl/spike_counter.v" \
"../../../../../rtl/synapse_accumulator.v" \
"../../../../../rtl/synapse_lif_unit.v" \
"../../../../../rtl/snn_axi_lite.v" \

vlog -work xil_defaultlib \
"glbl.v"


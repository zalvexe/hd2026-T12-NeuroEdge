// SNN95 AXI4-Lite slave wrapper (32-bit data, 8-bit address)
// Byte-addressed register map:
// 0x00 CTRL       W1P bit0 START, bit1 SOFT_RESET; W1C bit2 DONE_FLAG
// 0x04 STATUS     RO bit0 BUSY, bit1 DONE_STICKY, bit2 CORE_ERROR,
//                   bit3 INPUT_READY, bit4 ALL_125_INPUTS_WRITTEN
// 0x08 INPUT_IDX  RW bits[6:0] time-step index 0..124
// 0x0C INPUT_DATA WO bits[7:0] spike byte; auto-increment index modulo 125
// 0x10 CLASS      RO bits[1:0] predicted class
// 0x14 COUNTS     RO 4 x uint8 class spike counts; class0 in byte0
// 0x18 CYCLES     RO inference cycles reported by core
// 0x1C ID         RO 0x534E4E31 ('SNN1')
// All accesses must be 32-bit word-aligned; CTRL/IDX/DATA writes need WSTRB[0].
// AXI4-Lite AW and W channels are buffered separately; one outstanding transaction.
// All interfaces run on s_axi_aclk; no clock-domain crossing is required.
module snn_axi_lite #(
    parameter integer C_S_AXI_ADDR_WIDTH = 8
) (
    input  wire                               s_axi_aclk,
    input  wire                               s_axi_aresetn,
    input  wire [C_S_AXI_ADDR_WIDTH-1:0]      s_axi_awaddr,
    input  wire [2:0]                         s_axi_awprot,
    input  wire                               s_axi_awvalid,
    output wire                               s_axi_awready,
    input  wire [31:0]                        s_axi_wdata,
    input  wire [3:0]                         s_axi_wstrb,
    input  wire                               s_axi_wvalid,
    output wire                               s_axi_wready,
    output reg [1:0]                         s_axi_bresp,
    output reg                                s_axi_bvalid,
    input  wire                               s_axi_bready,
    input  wire [C_S_AXI_ADDR_WIDTH-1:0]      s_axi_araddr,
    input  wire [2:0]                         s_axi_arprot,
    input  wire                               s_axi_arvalid,
    output wire                               s_axi_arready,
    output reg [31:0]                        s_axi_rdata,
    output reg [1:0]                         s_axi_rresp,
    output reg                                s_axi_rvalid,
    input  wire                               s_axi_rready,
    output wire [3:0]                         class_led
);
    localparam [1:0] AXI_OKAY = 2'b00;
    localparam [1:0] AXI_SLVERR = 2'b10;
    localparam [C_S_AXI_ADDR_WIDTH-1:0] REG_CTRL   = 'h00;
    localparam [C_S_AXI_ADDR_WIDTH-1:0] REG_STATUS = 'h04;
    localparam [C_S_AXI_ADDR_WIDTH-1:0] REG_INDEX  = 'h08;
    localparam [C_S_AXI_ADDR_WIDTH-1:0] REG_DATA   = 'h0C;
    localparam [C_S_AXI_ADDR_WIDTH-1:0] REG_CLASS  = 'h10;
    localparam [C_S_AXI_ADDR_WIDTH-1:0] REG_COUNTS = 'h14;
    localparam [C_S_AXI_ADDR_WIDTH-1:0] REG_CYCLES = 'h18;
    localparam [C_S_AXI_ADDR_WIDTH-1:0] REG_ID     = 'h1C;

    reg aw_saved, w_saved;
    reg [C_S_AXI_ADDR_WIDTH-1:0] awaddr_reg;
    reg [31:0] wdata_reg;
    reg [3:0] wstrb_reg;
    reg [6:0] index_reg;
    reg [124:0] written_mask;
    reg done_sticky;
    reg core_start;
    reg core_soft_reset;
    reg core_input_we;
    reg [6:0] core_input_addr;
    reg [7:0] core_input_data;

    wire core_busy;
    wire core_input_ready;
    wire core_done;
    wire core_error;
    wire [1:0] core_class;
    wire [31:0] core_counts;
    wire [31:0] core_cycles;
    wire all_written = &written_mask;
    wire core_resetn = s_axi_aresetn && !core_soft_reset;
    
    // LED Classification 
    // Class 0 -> 0001 -> PD in air (12kV)
    // Class 1 -> 0010 -> PD in void (25kV)
    // Class 2 -> 0100 -> Corona (11kV)
    // Class 3 -> 1000 -> No PD (noise)
    assign class_led = (done_sticky && !core_error && s_axi_aresetn)
                     ? (4'b0001 << core_class)
                     : 4'b0000;
                     
    // AXI4-Lite ready signals
    assign s_axi_awready = s_axi_aresetn && !aw_saved && !s_axi_bvalid;
    assign s_axi_wready  = s_axi_aresetn && !w_saved && !s_axi_bvalid;
    assign s_axi_arready = s_axi_aresetn && !s_axi_rvalid;

    // PROT is ignored
    wire unused_prot = &{1'b0, s_axi_awprot, s_axi_arprot};

    snn_core core (
        .clk(s_axi_aclk), .reset_n(core_resetn),
        .input_write_enable(core_input_we),
        .input_write_address(core_input_addr),
        .input_write_data(core_input_data),
        .start(core_start), .busy(core_busy),
        .input_ready(core_input_ready), .done(core_done),
        .error(core_error), .output_class(core_class),
        .spike_counts(core_counts), .cycle_count(core_cycles)
    );

    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            aw_saved       <= 1'b0;
            w_saved        <= 1'b0;
            awaddr_reg     <= {C_S_AXI_ADDR_WIDTH{1'b0}};
            wdata_reg      <= 32'd0;
            wstrb_reg      <= 4'd0;
            s_axi_bresp    <= AXI_OKAY;
            s_axi_bvalid   <= 1'b0;
            s_axi_rdata    <= 32'd0;
            s_axi_rresp    <= AXI_OKAY;
            s_axi_rvalid   <= 1'b0;
            index_reg      <= 7'd0;
            written_mask   <= 125'd0;
            done_sticky    <= 1'b0;
            core_start     <= 1'b0;
            core_soft_reset<= 1'b0;
            core_input_we  <= 1'b0;
            core_input_addr<= 7'd0;
            core_input_data<= 8'd0;
        end else begin
            // Strobe outputs are one clock wide.
            core_start      <= 1'b0;
            core_input_we   <= 1'b0;
            core_soft_reset <= 1'b0;

            if (core_done)
                done_sticky <= 1'b1;

            if (s_axi_awvalid && s_axi_awready) begin
                aw_saved <= 1'b1;
                awaddr_reg <= s_axi_awaddr;
            end
            if (s_axi_wvalid && s_axi_wready) begin
                w_saved <= 1'b1;
                wdata_reg <= s_axi_wdata;
                wstrb_reg <= s_axi_wstrb;
            end
            if (s_axi_bvalid && s_axi_bready)
                s_axi_bvalid <= 1'b0;

            if (aw_saved && w_saved && !s_axi_bvalid) begin
                aw_saved <= 1'b0;
                w_saved <= 1'b0;
                s_axi_bvalid <= 1'b1;
                s_axi_bresp <= AXI_OKAY;
                case (awaddr_reg)
                    REG_CTRL: begin
                        if (!wstrb_reg[0]) begin
                            s_axi_bresp <= AXI_SLVERR;
                        end else if (wdata_reg[1]) begin
                            // Abort and synchronously reset the SNN core.
                            core_soft_reset <= 1'b1;
                            written_mask <= 125'd0;
                            index_reg <= 7'd0;
                            done_sticky <= 1'b0;
                        end else begin
                            if (wdata_reg[2])
                                done_sticky <= 1'b0;
                            if (wdata_reg[0]) begin
                                if (!core_busy && core_input_ready && all_written) begin
                                    core_start <= 1'b1;
                                    done_sticky <= 1'b0;
                                    written_mask <= 125'd0;
                                end else begin
                                    s_axi_bresp <= AXI_SLVERR;
                                end
                            end
                        end
                    end
                    REG_INDEX: begin
                        if (!wstrb_reg[0] || core_busy || !core_input_ready ||
                            wdata_reg[31:7] != 0 || wdata_reg[6:0] >= 7'd125)
                            s_axi_bresp <= AXI_SLVERR;
                        else
                            index_reg <= wdata_reg[6:0];
                    end
                    REG_DATA: begin
                        if (!wstrb_reg[0] || core_busy || !core_input_ready ||
                            index_reg >= 7'd125) begin
                            s_axi_bresp <= AXI_SLVERR;
                        end else begin
                            core_input_we <= 1'b1;
                            core_input_addr <= index_reg;
                            core_input_data <= wdata_reg[7:0];
                            written_mask[index_reg] <= 1'b1;
                            index_reg <= (index_reg == 7'd124) ? 7'd0 : index_reg + 7'd1;
                        end
                    end
                    default: s_axi_bresp <= AXI_SLVERR; // read-only or invalid
                endcase
            end

            if (s_axi_rvalid && s_axi_rready)
                s_axi_rvalid <= 1'b0;
            if (s_axi_arvalid && s_axi_arready) begin
                s_axi_rvalid <= 1'b1;
                s_axi_rresp <= AXI_OKAY;
                case (s_axi_araddr)
                    REG_CTRL:   s_axi_rdata <= 32'd0;
                    REG_STATUS: s_axi_rdata <= {27'd0, all_written,
                                                core_input_ready, core_error,
                                                done_sticky, core_busy};
                    REG_INDEX:  s_axi_rdata <= {25'd0, index_reg};
                    REG_DATA:   begin
                        s_axi_rdata <= 32'd0;
                        s_axi_rresp <= AXI_SLVERR;
                    end
                    REG_CLASS:  s_axi_rdata <= {30'd0, core_class};
                    REG_COUNTS: s_axi_rdata <= core_counts;
                    REG_CYCLES: s_axi_rdata <= core_cycles;
                    REG_ID:     s_axi_rdata <= 32'h534E4E31;
                    default:    begin
                        s_axi_rdata <= 32'd0;
                        s_axi_rresp <= AXI_SLVERR;
                    end
                endcase
            end
        end
    end
endmodule
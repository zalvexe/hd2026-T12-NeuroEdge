// Accumulates one spike bit for each of four output neurons per time step.
// 8 bits per counter are sufficient for 125 time steps.
module spike_counter (
    input  wire clk,
    input  wire reset_n,
    input  wire clear,
    input  wire enable,
    input  wire [3:0] spikes,
    output reg  [31:0] counts
);
    integer index;
    always @(posedge clk) begin
        if (!reset_n || clear) begin
            counts <= 32'd0;
        end else if (enable) begin
            for (index = 0; index < 4; index = index + 1)
                counts[index*8 +: 8] <= counts[index*8 +: 8] + {7'd0, spikes[index]};
        end
    end
endmodule

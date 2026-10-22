// Signed fixed-point LIF step for the supplied NeuroEdge model.
// Pure datapath: membrane state is maintained by the calling controller.
// Saturation happens BEFORE threshold detection; reset uses subtraction.
module lif_step #(
    parameter integer THRESHOLD = 944
) (
    input  wire signed [15:0] membrane_in,
    input  wire signed [31:0] synaptic_sum_in,
    output reg  signed [15:0] membrane_out,
    output reg                spike_out
);
    wire signed [31:0] membrane_extended = {{16{membrane_in[15]}}, membrane_in};
    reg signed [31:0] intermediate;
    reg signed [31:0] saturated;

    always @* begin
        intermediate = membrane_extended
            - (membrane_extended >>> 3)
            + (synaptic_sum_in <<< 4);
        if (intermediate > 32'sd32767)
            saturated = 32'sd32767;
        else if (intermediate < -32'sd32768)
            saturated = -32'sd32768;
        else
            saturated = intermediate;

        spike_out = (saturated >= THRESHOLD);
        if (spike_out)
            membrane_out = saturated - THRESHOLD;
        else
            membrane_out = saturated;
    end
endmodule

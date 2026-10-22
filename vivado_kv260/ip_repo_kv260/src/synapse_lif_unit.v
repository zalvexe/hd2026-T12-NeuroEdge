// Runs one synaptic accumulation, then one LIF update for a selected neuron.
// The caller owns the membrane state and supplies membrane_in at start.
// start is accepted when busy=0; done pulses for one clock when outputs are valid.
module synapse_lif_unit #(
    parameter integer INPUT_COUNT = 8,
    parameter integer OUTPUT_COUNT = 128,
    parameter integer THRESHOLD = 944,
    parameter WEIGHT_FILE = "weights/w1_int8.mem"
) (
    input  wire clk,
    input  wire reset_n,
    input  wire start,
    input  wire [INPUT_COUNT-1:0] spikes,
    input  wire [$clog2(OUTPUT_COUNT)-1:0] neuron_index,
    input  wire signed [15:0] membrane_in,
    output reg  busy,
    output reg  done,
    output reg  signed [31:0] synaptic_sum,
    output reg  signed [15:0] membrane_out,
    output reg  spike_out
);
    reg signed [15:0] saved_membrane;

    wire accumulator_start = start && !busy && reset_n;
    wire accumulator_done;
    wire signed [31:0] accumulated_sum;
    wire signed [15:0] next_membrane;
    wire next_spike;

    synapse_accumulator #(
        .INPUT_COUNT(INPUT_COUNT),
        .OUTPUT_COUNT(OUTPUT_COUNT),
        .WEIGHT_FILE(WEIGHT_FILE)
    ) synapse (
        .clk(clk),
        .reset_n(reset_n),
        .start(accumulator_start),
        .spikes(spikes),
        .neuron_index(neuron_index),
        .busy(),
        .done(accumulator_done),
        .synaptic_sum(accumulated_sum)
    );

    lif_step #(.THRESHOLD(THRESHOLD)) neuron (
        .membrane_in(saved_membrane),
        .synaptic_sum_in(accumulated_sum),
        .membrane_out(next_membrane),
        .spike_out(next_spike)
    );

    always @(posedge clk) begin
        if (!reset_n) begin
            busy <= 1'b0;
            done <= 1'b0;
            saved_membrane <= 16'sd0;
            synaptic_sum <= 32'sd0;
            membrane_out <= 16'sd0;
            spike_out <= 1'b0;
        end else begin
            done <= 1'b0;
            if (!busy) begin
                if (start) begin
                    saved_membrane <= membrane_in;
                    busy <= 1'b1;
                end
            end else if (accumulator_done) begin
                synaptic_sum <= accumulated_sum;
                membrane_out <= next_membrane;
                spike_out <= next_spike;
                busy <= 1'b0;
                done <= 1'b1;
            end
        end
    end
endmodule
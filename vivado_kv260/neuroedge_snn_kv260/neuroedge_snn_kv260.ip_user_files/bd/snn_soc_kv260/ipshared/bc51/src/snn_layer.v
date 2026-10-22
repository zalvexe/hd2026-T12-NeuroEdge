// One complete SNN layer, evaluated one neuron at a time.
// Each start processes exactly one time step for every output neuron.
// Membrane state persists between steps and is cleared at sample boundaries.
module snn_layer #(
    parameter integer INPUT_COUNT = 8,
    parameter integer OUTPUT_COUNT = 128,
    parameter integer THRESHOLD = 944,
    parameter WEIGHT_FILE = "weights/w1_int8.mem"
) (
    input  wire clk,
    input  wire reset_n,
    input  wire clear_state,
    input  wire start,
    input  wire [INPUT_COUNT-1:0] input_spikes,
    output reg  busy,
    output reg  done,
    output reg  [OUTPUT_COUNT-1:0] output_spikes
);
    localparam integer NEURON_BITS = (OUTPUT_COUNT > 1) ? $clog2(OUTPUT_COUNT) : 1;
    localparam [1:0] IDLE = 2'd0;
    localparam [1:0] LAUNCH = 2'd1;
    localparam [1:0] WAIT_NEURON = 2'd2;

    reg [1:0] state;
    reg [NEURON_BITS-1:0] neuron_index;
    reg [INPUT_COUNT-1:0] saved_input_spikes;
    reg signed [15:0] membranes [0:OUTPUT_COUNT-1];

    wire neuron_start = (state == LAUNCH);
    wire neuron_done;
    wire neuron_busy;
    wire signed [15:0] neuron_membrane_out;
    wire neuron_spike_out;
    wire signed [31:0] neuron_current;

    synapse_lif_unit #(
        .INPUT_COUNT(INPUT_COUNT),
        .OUTPUT_COUNT(OUTPUT_COUNT),
        .THRESHOLD(THRESHOLD),
        .WEIGHT_FILE(WEIGHT_FILE)
    ) processing_unit (
        .clk(clk),
        .reset_n(reset_n),
        .start(neuron_start),
        .spikes(saved_input_spikes),
        .neuron_index(neuron_index),
        .membrane_in(membranes[neuron_index]),
        .busy(neuron_busy),
        .done(neuron_done),
        .synaptic_sum(neuron_current),
        .membrane_out(neuron_membrane_out),
        .spike_out(neuron_spike_out)
    );

    integer i;
    always @(posedge clk) begin
        if (!reset_n) begin
            state <= IDLE;
            neuron_index <= {NEURON_BITS{1'b0}};
            saved_input_spikes <= {INPUT_COUNT{1'b0}};
            output_spikes <= {OUTPUT_COUNT{1'b0}};
            busy <= 1'b0;
            done <= 1'b0;
            for (i = 0; i < OUTPUT_COUNT; i = i + 1)
                membranes[i] <= 16'sd0;
        end else begin
            done <= 1'b0;
            case (state)
                IDLE: begin
                    if (clear_state) begin
                        for (i = 0; i < OUTPUT_COUNT; i = i + 1)
                            membranes[i] <= 16'sd0;
                        output_spikes <= {OUTPUT_COUNT{1'b0}};
                    end else if (start) begin
                        saved_input_spikes <= input_spikes;
                        neuron_index <= {NEURON_BITS{1'b0}};
                        output_spikes <= {OUTPUT_COUNT{1'b0}};
                        busy <= 1'b1;
                        state <= LAUNCH;
                    end
                end
                LAUNCH: begin
                    // The processing unit accepts start on this clock edge.
                    state <= WAIT_NEURON;
                end
                WAIT_NEURON: begin
                    if (neuron_done) begin
                        membranes[neuron_index] <= neuron_membrane_out;
                        output_spikes[neuron_index] <= neuron_spike_out;
                        if (neuron_index == OUTPUT_COUNT - 1) begin
                            busy <= 1'b0;
                            done <= 1'b1;
                            state <= IDLE;
                        end else begin
                            neuron_index <= neuron_index + 1'b1;
                            state <= LAUNCH;
                        end
                    end
                end
                default: begin
                    state <= IDLE;
                    busy <= 1'b0;
                end
            endcase
        end
    end
endmodule

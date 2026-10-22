// Compute one neuron's signed synaptic sum using one weight per cycle.
// Weight layout: row-major (output neuron, input neuron).
// Input spikes are one bit per input neuron. No multiplier is needed.
module synapse_accumulator #(
    parameter integer INPUT_COUNT = 8,
    parameter integer OUTPUT_COUNT = 128,
    parameter WEIGHT_FILE = "weights/w1_int8.mem"
) (
    input  wire clk,
    input  wire reset_n,
    input  wire start,
    input  wire [INPUT_COUNT-1:0] spikes,
    input  wire [$clog2(OUTPUT_COUNT)-1:0] neuron_index,
    output reg  busy,
    output reg  done,
    output reg  signed [31:0] synaptic_sum
);
    localparam integer INPUT_INDEX_WIDTH = $clog2(INPUT_COUNT + 1);
    localparam integer NEURON_INDEX_WIDTH = $clog2(OUTPUT_COUNT);
    localparam integer WEIGHT_ADDRESS_WIDTH = $clog2(INPUT_COUNT * OUTPUT_COUNT);

    reg signed [7:0] weights [0:INPUT_COUNT*OUTPUT_COUNT-1];
    reg [INPUT_COUNT-1:0] saved_spikes;
    reg [NEURON_INDEX_WIDTH-1:0] saved_neuron_index;
    reg [INPUT_INDEX_WIDTH-1:0] input_index;
    reg signed [31:0] accumulator;

    wire [WEIGHT_ADDRESS_WIDTH-1:0] weight_address =
        saved_neuron_index * INPUT_COUNT + input_index;
    wire signed [7:0] current_weight = weights[weight_address];
    wire signed [31:0] extended_weight =
        {{24{current_weight[7]}}, current_weight};
    wire signed [31:0] updated_sum = accumulator +
        (saved_spikes[input_index] ? extended_weight : 32'sd0);

    initial begin
        $readmemh(WEIGHT_FILE, weights);
    end

    always @(posedge clk) begin
        if (!reset_n) begin
            busy <= 1'b0;
            done <= 1'b0;
            synaptic_sum <= 32'sd0;
            accumulator <= 32'sd0;
            saved_spikes <= {INPUT_COUNT{1'b0}};
            saved_neuron_index <= {NEURON_INDEX_WIDTH{1'b0}};
            input_index <= {INPUT_INDEX_WIDTH{1'b0}};
        end else begin
            done <= 1'b0;
            if (busy) begin
                accumulator <= updated_sum;
                if (input_index == INPUT_COUNT - 1) begin
                    synaptic_sum <= updated_sum;
                    busy <= 1'b0;
                    done <= 1'b1;
                end else begin
                    input_index <= input_index + 1'b1;
                end
            end else if (start) begin
                saved_spikes <= spikes;
                saved_neuron_index <= neuron_index;
                input_index <= {INPUT_INDEX_WIDTH{1'b0}};
                accumulator <= 32'sd0;
                busy <= 1'b1;
            end
        end
    end
endmodule

// Standalone inference accelerator: load exactly 125 bytes, then pulse start.
// Input byte bit i holds input neuron i at one time step.
// done is a one-cycle pulse. Results remain valid until the next completed run.
// This module has NO AXI protocol yet; an AXI4-Lite wrapper will be added later.
module snn_core (
    input  wire clk,
    input  wire reset_n,
    input  wire input_write_enable,
    input  wire [6:0] input_write_address,
    input  wire [7:0] input_write_data,
    input  wire start,
    output wire busy,
    output wire input_ready,
    output wire done,
    output reg  error,
    output reg  [1:0] output_class,
    output reg  [31:0] spike_counts,
    output reg  [31:0] cycle_count
);
    localparam integer TIME_STEPS = 125;
    reg [7:0] spike_buffer [0:TIME_STEPS-1];
    reg [TIME_STEPS-1:0] written_steps;

    wire all_steps_written = &written_steps;
    wire accepted_start = start && !busy && all_steps_written;
    wire [6:0] time_step;
    wire clear_state;
    wire start_l1;
    wire start_l2;
    wire count_enable;
    wire finish;
    wire done_l1;
    wire done_l2;
    wire busy_l1;
    wire busy_l2;
    wire [127:0] hidden_spikes;
    wire [3:0] output_spikes;
    wire [31:0] current_counts;
    wire [1:0] current_class;
    reg [31:0] elapsed_cycles;

    assign input_ready = !busy;

    snn_controller #(.TIME_STEPS(TIME_STEPS)) controller (
        .clk(clk), .reset_n(reset_n), .start(accepted_start),
        .done_l1(done_l1), .done_l2(done_l2),
        .busy(busy), .done(done), .clear_state(clear_state),
        .start_l1(start_l1), .start_l2(start_l2),
        .count_enable(count_enable), .finish(finish), .time_step(time_step)
    );
    
    `ifdef SYNTHESIS
        localparam W1_FILE = "w1_int8.mem";
        localparam W2_FILE = "w2_int8.mem";
    `else
        localparam W1_FILE = "weights/w1_int8.mem";
        localparam W2_FILE = "weights/w2_int8.mem";
    `endif
    
    snn_layer #(
        .INPUT_COUNT(8), .OUTPUT_COUNT(128), .THRESHOLD(944),
        .WEIGHT_FILE(W1_FILE)
    ) hidden_layer (
        .clk(clk), .reset_n(reset_n), .clear_state(clear_state),
        .start(start_l1), .input_spikes(spike_buffer[time_step]),
        .busy(busy_l1), .done(done_l1), .output_spikes(hidden_spikes)
    );

    snn_layer #(
        .INPUT_COUNT(128), .OUTPUT_COUNT(4), .THRESHOLD(860),
        .WEIGHT_FILE(W2_FILE)
    ) output_layer (
        .clk(clk), .reset_n(reset_n), .clear_state(clear_state),
        .start(start_l2), .input_spikes(hidden_spikes),
        .busy(busy_l2), .done(done_l2), .output_spikes(output_spikes)
    );

    spike_counter spike_counts_unit (
        .clk(clk), .reset_n(reset_n), .clear(clear_state),
        .enable(count_enable), .spikes(output_spikes), .counts(current_counts)
    );

    argmax output_selection (
        .counts(current_counts), .predicted_class(current_class)
    );

    integer index;
    always @(posedge clk) begin
        if (!reset_n) begin
            written_steps <= {TIME_STEPS{1'b0}};
            error <= 1'b0;
            output_class <= 2'd0;
            spike_counts <= 32'd0;
            cycle_count <= 32'd0;
            elapsed_cycles <= 32'd0;
            for (index = 0; index < TIME_STEPS; index = index + 1)
                spike_buffer[index] <= 8'd0;
        end else begin
            if (accepted_start) begin
                written_steps <= {TIME_STEPS{1'b0}};
                error <= 1'b0;
                elapsed_cycles <= 32'd0;
            end else begin
                if (input_write_enable) begin
                    if (!busy && input_write_address < TIME_STEPS) begin
                        spike_buffer[input_write_address] <= input_write_data;
                        written_steps[input_write_address] <= 1'b1;
                    end else begin
                        error <= 1'b1;
                    end
                end
                if (start && !busy && !all_steps_written)
                    error <= 1'b1;
            end

            if (busy)
                elapsed_cycles <= elapsed_cycles + 32'd1;

            if (finish) begin
                output_class <= current_class;
                spike_counts <= current_counts;
                cycle_count <= elapsed_cycles + 32'd1;
            end
        end
    end
endmodule

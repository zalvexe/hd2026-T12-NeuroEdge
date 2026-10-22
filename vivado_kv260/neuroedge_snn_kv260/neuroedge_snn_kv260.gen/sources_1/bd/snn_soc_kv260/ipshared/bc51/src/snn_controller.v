// Coordinates one complete 125-step inference across two existing SNN layers.
// Layer done signals are single-clock pulses. Each start is also one clock.
module snn_controller #(
    parameter integer TIME_STEPS = 125
) (
    input  wire clk,
    input  wire reset_n,
    input  wire start,
    input  wire done_l1,
    input  wire done_l2,
    output wire busy,
    output reg  done,
    output wire clear_state,
    output wire start_l1,
    output wire start_l2,
    output wire count_enable,
    output wire finish,
    output reg  [6:0] time_step
);
    localparam [2:0] IDLE      = 3'd0;
    localparam [2:0] CLEAR     = 3'd1;
    localparam [2:0] START_L1  = 3'd2;
    localparam [2:0] WAIT_L1   = 3'd3;
    localparam [2:0] START_L2  = 3'd4;
    localparam [2:0] WAIT_L2   = 3'd5;
    localparam [2:0] COUNT     = 3'd6;
    localparam [2:0] FINISH    = 3'd7;

    reg [2:0] state;

    assign busy         = (state != IDLE);
    assign clear_state  = (state == CLEAR);
    assign start_l1     = (state == START_L1);
    assign start_l2     = (state == START_L2);
    assign count_enable = (state == COUNT);
    assign finish       = (state == FINISH);

    always @(posedge clk) begin
        if (!reset_n) begin
            state <= IDLE;
            time_step <= 7'd0;
            done <= 1'b0;
        end else begin
            done <= 1'b0;
            case (state)
                IDLE: if (start) begin
                    time_step <= 7'd0;
                    state <= CLEAR;
                end
                CLEAR: state <= START_L1;
                START_L1: state <= WAIT_L1;
                WAIT_L1: if (done_l1) state <= START_L2;
                START_L2: state <= WAIT_L2;
                WAIT_L2: if (done_l2) state <= COUNT;
                COUNT: begin
                    if (time_step == TIME_STEPS - 1)
                        state <= FINISH;
                    else begin
                        time_step <= time_step + 7'd1;
                        state <= START_L1;
                    end
                end
                FINISH: begin
                    done <= 1'b1;
                    state <= IDLE;
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule

`timescale 1ns/1ps
// Dataset-driven AXI4-Lite testbench for SNN95 / KV260.
// Uses 4 preprocessed spike samples from test_vectors.npz (converted to .mem).
// DUT = snn_axi_lite, which contains snn_core and the full SNN datapath.
// 100 MHz AXI clock. Real power cannot be obtained from this testbench.
module tb_snn_fpga;
    localparam int PERIOD_NS = 10;
    localparam int N_STEPS = 125;
    localparam int N_CASES = 4;  // NPZ indices: 0,150,300,450 (classes 0..3)
    localparam int POLL_GAP_CYCLES = 1000;
    localparam int MAX_POLLS = 1200;

    localparam logic [7:0] REG_CTRL   = 8'h00;
    localparam logic [7:0] REG_STATUS = 8'h04;
    localparam logic [7:0] REG_IDX    = 8'h08;
    localparam logic [7:0] REG_DATA   = 8'h0C;
    localparam logic [7:0] REG_CLASS  = 8'h10;
    localparam logic [7:0] REG_COUNTS = 8'h14;
    localparam logic [7:0] REG_CYCLES = 8'h18;
    localparam logic [7:0] REG_ID     = 8'h1C;

    localparam logic [1:0] AXI_OKAY   = 2'b00;
    localparam logic [1:0] AXI_SLVERR = 2'b10;

    logic clk = 1'b0;
    always #(PERIOD_NS/2) clk = ~clk;

    logic resetn;
    logic [7:0] awaddr, araddr;
    logic [2:0] awprot, arprot;
    logic awvalid, wvalid, bready, arvalid, rready;
    wire awready, wready, bvalid, arready, rvalid;
    logic [31:0] wdata;
    logic [3:0] wstrb;
    wire [1:0] bresp, rresp;
    wire [31:0] rdata;

    // Inputs / reference outputs created by export_npz_for_xsim.py
    logic [7:0] sample [0:N_STEPS-1];
    logic [7:0] all_samples [0:N_CASES*N_STEPS-1];
    logic [31:0] golden_counts [0:N_CASES-1];
    logic [1:0] golden_pred [0:N_CASES-1];
    logic [1:0] true_label [0:N_CASES-1];
    integer fail_count = 0;
    integer successful_runs = 0;
    logic [31:0] counts_first, cycles_first;
    logic [1:0] class_first;
    logic [31:0] counts_second, cycles_second;
    logic [1:0] class_second;
    logic [31:0] data_read;
    logic [1:0] resp_read, resp_write;
    integer i, j, case_idx, correct_vs_label;

    snn_axi_lite #(.C_S_AXI_ADDR_WIDTH(8)) dut (
        .s_axi_aclk(clk), .s_axi_aresetn(resetn),
        .s_axi_awaddr(awaddr), .s_axi_awprot(awprot),
        .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata), .s_axi_wstrb(wstrb),
        .s_axi_wvalid(wvalid), .s_axi_wready(wready),
        .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
        .s_axi_araddr(araddr), .s_axi_arprot(arprot),
        .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rdata(rdata), .s_axi_rresp(rresp),
        .s_axi_rvalid(rvalid), .s_axi_rready(rready)
    );

    // MODEL ONLY: illustrates one-hot LED decoding from CLASS and sticky DONE.
    // This wire is NOT a physical PL LED pin and is NOT part of the DUT.
    wire [3:0] class_led_sim = dut.done_sticky ? (4'b0001 << dut.core_class) : 4'b0000;

    // Visual helper for the waveform: keep the most recently displayed class.
    // Unlike class_led_sim, this does NOT return to zero on CTRL W1C DONE.
    // TB-ONLY: this is not a synthesizable physical LED output of the KV260.
    logic [3:0] class_led_hold = 4'b0000;

    // ================================================================
    // TB-ONLY WAVEFORM: sample's known label vs SNN's actual prediction.
    // The real input to the SNN is STILL 125 bytes x 8 spike bits.
    // input_label_led is a reference/annotation, NOT data fed to the DUT.
    // output_class_led is decoded from CLASS read through AXI AFTER DONE.
    // ================================================================
    logic [3:0] input_label_led = 4'b0000;
    logic [3:0] output_class_led = 4'b0000;
    logic       output_valid_wave = 1'b0;
    logic [9:0] npz_sample_index_wave = 10'd0;
    wire label_matches_output_wave = output_valid_wave &&
                                     (input_label_led == output_class_led);

    // Optional: show actual spike bytes being written into the SNN core.
    wire [7:0] input_spike_byte_wave = dut.core_input_data;
    wire       input_spike_we_wave   = dut.core_input_we;

    // -----------------------------------------------------------------------
    // SIMULATION-ONLY WEIGHT INITIALIZATION (NO RTL MODIFICATIONS REQUIRED)
    // Vivado exported w1_int8.mem / w2_int8.mem to the XSim working directory,
    // but snn_core.v still tries to load weights/w1_int8.mem etc. at time 0.
    // Reinitialize those exact DUT memories from their exported names at 1 ns.
    // Must run before reset is released and any inference begins.
    // -----------------------------------------------------------------------
    initial begin : INIT_WEIGHTS_FOR_XSIM
        integer k;
        integer bad_weights;
        #1;
        $readmemh("w1_int8.mem",
                  dut.core.hidden_layer.processing_unit.synapse.weights);
        $readmemh("w2_int8.mem",
                  dut.core.output_layer.processing_unit.synapse.weights);

        bad_weights = 0;
        for (k = 0; k < 1024; k = k + 1)
            if ((^dut.core.hidden_layer.processing_unit.synapse.weights[k]) === 1'bx)
                bad_weights = bad_weights + 1;
        for (k = 0; k < 512; k = k + 1)
            if ((^dut.core.output_layer.processing_unit.synapse.weights[k]) === 1'bx)
                bad_weights = bad_weights + 1;

        if (bad_weights != 0)
            $fatal(1, "Weight initialization failed: %0d X/Z bytes; verify the two .mem files are added as Memory Initialization Files in Vivado simulation sources", bad_weights);
        $display("[%0t] TB WEIGHTS LOADED: W1=1024 bytes, W2=512 bytes (simulation only)", $time);
    end

    task automatic report_fail(input string message);
        fail_count = fail_count + 1;
        $display("[%0t] FAIL: %s", $time, message);
    endtask

    task automatic expect_resp(input logic [1:0] got,
                               input logic [1:0] wanted,
                               input string what);
        if (got !== wanted)
            report_fail($sformatf("%s: AXI resp=%02b expected=%02b", what, got, wanted));
    endtask

    // Every bus signal is driven on the negative edge to avoid races with the RTL.
    // Address and data channels are independent, exactly as in AXI4-Lite.
    task automatic send_aw(input logic [7:0] addr);
        @(negedge clk);
        awaddr = addr;
        awvalid = 1'b1;
        do @(posedge clk); while (awready !== 1'b1);
        @(negedge clk);
        awvalid = 1'b0;
    endtask

    task automatic send_w(input logic [31:0] value,
                          input logic [3:0] strb);
        @(negedge clk);
        wdata = value;
        wstrb = strb;
        wvalid = 1'b1;
        do @(posedge clk); while (wready !== 1'b1);
        @(negedge clk);
        wvalid = 1'b0;
    endtask

    // mode 0: AW and W simultaneously; 1: AW before W; 2: W before AW.
    task automatic axi_write(input logic [7:0] addr,
                             input logic [31:0] value,
                             input logic [3:0] strb,
                             input integer mode,
                             output logic [1:0] response);
        begin
            case (mode)
                0: fork
                    send_aw(addr);
                    send_w(value, strb);
                join
                1: begin
                    send_aw(addr);
                    repeat (2) @(posedge clk);
                    send_w(value, strb);
                end
                2: begin
                    send_w(value, strb);
                    repeat (2) @(posedge clk);
                    send_aw(addr);
                end
                default: report_fail("Invalid AW/W mode in testbench");
            endcase

            // Hold BREADY low initially: the DUT must retain its response.
            repeat (2) @(posedge clk);
            @(negedge clk);
            bready = 1'b1;
            do @(posedge clk); while (bvalid !== 1'b1);
            response = bresp;
            @(negedge clk);
            bready = 1'b0;
        end
    endtask

    task automatic axi_read(input logic [7:0] addr,
                            output logic [31:0] value,
                            output logic [1:0] response);
        begin
            @(negedge clk);
            araddr = addr;
            arvalid = 1'b1;
            do @(posedge clk); while (arready !== 1'b1);
            @(negedge clk);
            arvalid = 1'b0;

            // Delayed RREADY also tests AXI read-response backpressure.
            repeat (2) @(posedge clk);
            @(negedge clk);
            rready = 1'b1;
            do @(posedge clk); while (rvalid !== 1'b1);
            value = rdata;
            response = rresp;
            @(negedge clk);
            rready = 1'b0;
        end
    endtask

    task automatic write_ok(input logic [7:0] addr,
                            input logic [31:0] value,
                            input integer mode);
        logic [1:0] response;
        begin
            axi_write(addr, value, 4'b0001, mode, response);
            expect_resp(response, AXI_OKAY, $sformatf("write @%02h", addr));
        end
    endtask

    task automatic read_ok(input logic [7:0] addr,
                           output logic [31:0] value);
        logic [1:0] response;
        begin
            axi_read(addr, value, response);
            expect_resp(response, AXI_OKAY, $sformatf("read @%02h", addr));
        end
    endtask

    task automatic check_argmax(input logic [31:0] counts,
                                input logic [1:0] predicted);
        integer k, best_idx, best_count;
        integer this_count;
        begin
            best_idx = 0;
            best_count = counts[7:0];
            for (k = 1; k < 4; k = k + 1) begin
                this_count = counts[(8*k)+:8];
                if (this_count > best_count) begin
                    best_count = this_count;
                    best_idx = k;
                end
            end
            if (predicted !== best_idx[1:0])
                report_fail($sformatf("argmax mismatch CLASS=%0d expected=%0d COUNTS=%h",
                                      predicted, best_idx, counts));
        end
    endtask

    task automatic perform_inference(input string label_text,
                                     input logic use_zero_input,
                                     output logic [1:0] result_class,
                                     output logic [31:0] result_counts,
                                     output logic [31:0] result_cycles);
        logic [31:0] status, index_value, class_value;
        logic [1:0] response;
        logic completed;
        integer n, polls;
        logic [7:0] spike_byte;
        begin
            $display("\n[%0t] CASE: %s", $time, label_text);

            write_ok(REG_IDX, 32'd0, 1);  // AW before W
            for (n = 0; n < N_STEPS; n = n + 1) begin
                spike_byte = use_zero_input ? 8'h00 : sample[n];
                write_ok(REG_DATA, {24'd0, spike_byte}, n % 3);
            end
            read_ok(REG_IDX, index_value);
            if (index_value !== 32'd0)
                report_fail("INPUT_IDX should wrap to 0 after 125 DATA writes");
            read_ok(REG_STATUS, status);
            if (status[4] !== 1'b1 || status[3] !== 1'b1 || status[0] !== 1'b0)
                report_fail($sformatf("Unexpected status before START: %h", status));

            write_ok(REG_CTRL, 32'h00000001, 2); // W before AW
            completed = 1'b0;
            for (polls = 0; polls < MAX_POLLS && !completed; polls = polls + 1) begin
                repeat (POLL_GAP_CYCLES) @(posedge clk);
                read_ok(REG_STATUS, status);
                if (status[2] === 1'b1)
                    report_fail("SNN core asserted ERROR during inference");
                if (status[1] === 1'b1)
                    completed = 1'b1;
            end
            if (!completed) begin
                report_fail($sformatf("TIMEOUT waiting for DONE: %s", label_text));
                $fatal(1, "Inference timeout");
            end
            if (status[0] !== 1'b0 || status[3] !== 1'b1)
                report_fail($sformatf("Unexpected final status: %h", status));

            read_ok(REG_CLASS, class_value);
            read_ok(REG_COUNTS, result_counts);
            read_ok(REG_CYCLES, result_cycles);
            result_class = class_value[1:0];
            if (class_value[31:2] !== 0)
                report_fail("Upper bits of CLASS should be zero");
            if ((^result_counts === 1'bx) || (^result_cycles === 1'bx))
                report_fail("X/Z value detected in result registers (check .mem weights)");
            if (result_cycles == 0)
                report_fail("CYCLES is zero after completed inference");
            for (n = 0; n < 4; n = n + 1)
                if (result_counts[(8*n)+:8] > 8'd125)
                    report_fail($sformatf("Count for class %0d exceeds 125", n));
            check_argmax(result_counts, result_class);

            // Retain the last classification for a readable 0->1->2->3 waveform.
            // The holding signal is a VISUALIZATION only; the original DUT is unchanged.
            class_led_hold = (4'b0001 << result_class);
            $display("  Held LED (waveform/TB only) = %04b", class_led_hold);

            if (class_led_sim !== (4'b0001 << result_class))
                report_fail($sformatf("Virtual LED one-hot mismatch: got=%04b class=%0d",
                                      class_led_sim, result_class));
            $display("  Virtual LED (TB only) = %04b", class_led_sim);

            $display("[%0t] DONE: CLASS=%0d COUNTS=[%0d,%0d,%0d,%0d] CYCLES=%0d (~%0.2f us at 100 MHz)",
                     $time, result_class,
                     result_counts[7:0], result_counts[15:8],
                     result_counts[23:16], result_counts[31:24],
                     result_cycles, result_cycles / 100.0);
            successful_runs = successful_runs + 1;
            // Clear sticky DONE before next sample.
            write_ok(REG_CTRL, 32'h00000004, 0);
            read_ok(REG_STATUS, status);
            if (status[1] !== 1'b0 || status[4] !== 1'b0)
                report_fail($sformatf("DONE or all-written flag did not clear: %h", status));
            if (class_led_sim !== 4'b0000)
                report_fail("Virtual LED should turn off after clearing DONE");
        end
    endtask

    initial begin : MAIN
        resetn = 1'b0;
        awaddr = 0; awprot = 0; awvalid = 0;
        wdata = 0; wstrb = 0; wvalid = 0; bready = 0;
        araddr = 0; arprot = 0; arvalid = 0; rready = 0;
        // Mem files are exported from the attached NPZ, one hex value per line.
        // Dataset .mem files are located in the user's tb_data directory.
        // Absolute path avoids dependence on XSim working directory.
        #2;
        $readmemh("D:/Documents/Semester 7/Hackathon/FPGA_SNN/tb_data/tb_dataset_inputs.mem", all_samples);
        $readmemh("D:/Documents/Semester 7/Hackathon/FPGA_SNN/tb_data/tb_dataset_counts.mem", golden_counts);
        $readmemh("D:/Documents/Semester 7/Hackathon/FPGA_SNN/tb_data/tb_dataset_pred.mem", golden_pred);
        $readmemh("D:/Documents/Semester 7/Hackathon/FPGA_SNN/tb_data/tb_dataset_label.mem", true_label);
        for (i = 0; i < N_CASES*N_STEPS; i = i + 1)
            if ((^all_samples[i]) === 1'bx)
                $fatal(1, "Dataset not loaded: tb_dataset_inputs.mem element %0d", i);
        for (i = 0; i < N_CASES; i = i + 1)
            if ((^golden_counts[i]) === 1'bx ||
                (^golden_pred[i]) === 1'bx ||
                (^true_label[i]) === 1'bx)
                $fatal(1, "Golden data not loaded: check .mem files, case %0d", i);
        $display("NPZ vectors loaded: %0d samples x %0d timesteps x 8 input spikes", N_CASES, N_STEPS);
        correct_vs_label = 0;

        repeat (8) @(negedge clk);
        resetn = 1'b1;
        repeat (4) @(posedge clk);
        $display("\n===== SNN95 AXI-Lite realistic host test =====");

        // Test hardware identity: same read as Linux devmem / host app.
        read_ok(REG_ID, data_read);
        if (data_read !== 32'h534E4E31)
            report_fail($sformatf("Invalid SNN ID: %h", data_read));
        $display("ID = 0x%08h (expected SNN1)", data_read);

        // Negative tests: the hardware should reject malformed transactions.
        axi_write(REG_CTRL, 32'd1, 4'h1, 0, resp_write);
        expect_resp(resp_write, AXI_SLVERR, "START before full input");
        axi_write(REG_IDX, 32'd125, 4'h1, 1, resp_write);
        expect_resp(resp_write, AXI_SLVERR, "INPUT_IDX out of range");
        axi_write(REG_IDX, 32'd0, 4'h0, 2, resp_write);
        expect_resp(resp_write, AXI_SLVERR, "WSTRB[0] missing");
        axi_write(REG_CLASS, 32'd1234, 4'h1, 0, resp_write);
        expect_resp(resp_write, AXI_SLVERR, "write to read-only CLASS");
        axi_read(REG_DATA, data_read, resp_read);
        expect_resp(resp_read, AXI_SLVERR, "read from write-only DATA");
        read_ok(REG_STATUS, data_read);
        if (data_read[4] !== 1'b0 || data_read[0] !== 1'b0)
            report_fail("Rejected operations must not start inference");

        // A: sanity check. No input spikes -> no output spikes -> class 0.
        perform_inference("ZERO INPUT / idle-like datapath baseline", 1'b1,
                          class_first, counts_first, cycles_first);
        if (counts_first !== 32'h00000000 || class_first !== 2'd0)
            report_fail($sformatf("Zero-input reference wrong: counts=%h class=%0d",
                                  counts_first, class_first));

        // B: four true NPZ test vectors (0, 150, 300, 450), classes 0..3.
        // Each run uses exactly 125 bytes sent through real AXI-Lite transactions.
        for (case_idx = 0; case_idx < N_CASES; case_idx = case_idx + 1) begin
            // Assert the input sample's REFERENCE CLASS immediately.
            // This represents the label, not the binary spike-train payload.
            npz_sample_index_wave = case_idx * 150;
            input_label_led = (4'b0001 << true_label[case_idx]);
            output_class_led = 4'b0000;   // No valid prediction for this sample yet
            output_valid_wave = 1'b0;
            $display("WAVE INPUT: NPZ index=%0d label=%0d input_label_led=%04b",
                     npz_sample_index_wave, true_label[case_idx], input_label_led);

            for (j = 0; j < N_STEPS; j = j + 1)
                sample[j] = all_samples[case_idx*N_STEPS+j];
            perform_inference($sformatf("NPZ REAL CASE #%0d (class label %0d)",
                                          case_idx, true_label[case_idx]),
                              1'b0, class_first, counts_first, cycles_first);
            $display("    expected golden CLASS=%0d COUNTS=%08h; true label=%0d",
                     golden_pred[case_idx], golden_counts[case_idx], true_label[case_idx]);
            if (class_first !== golden_pred[case_idx])
                report_fail($sformatf("NPZ case %0d: CLASS=%0d != golden=%0d",
                                      case_idx, class_first, golden_pred[case_idx]));
            if (counts_first !== golden_counts[case_idx])
                report_fail($sformatf("NPZ case %0d: COUNTS=%08h != golden=%08h",
                                      case_idx, counts_first, golden_counts[case_idx]));
            if (class_first === true_label[case_idx])
                correct_vs_label = correct_vs_label + 1;
            else
                $display("    NOTE: prediction differs from true label: predicted=%0d truth=%0d",
                         class_first, true_label[case_idx]);

            // ONLY NOW reveal the result, as the SNN has completed inference
            // and the host has read its CLASS register over AXI4-Lite.
            output_class_led = (4'b0001 << class_first);
            output_valid_wave = 1'b1;
            $display("WAVE OUTPUT: FPGA CLASS=%0d output_class_led=%04b match=%0b",
                     class_first, output_class_led, (input_label_led == output_class_led));
            // Hold the input/output comparison long enough to SEE on XSim.
            // At 100 MHz, 50,000 clocks = 0.5 ms per selected sample.
            repeat (50000) @(posedge clk);
        end

        $display("NPZ selected-sample accuracy vs labels: %0d / %0d (not full-dataset accuracy)",
                 correct_vs_label, N_CASES);

        // Soft reset, as host software can request after a fault.
        write_ok(REG_CTRL, 32'h00000002, 0);
        repeat (4) @(posedge clk);
        read_ok(REG_STATUS, data_read);
        if (data_read[4] !== 1'b0 || data_read[1] !== 1'b0 || data_read[0] !== 1'b0)
            report_fail($sformatf("Soft reset status incorrect: %h", data_read));

        $display("\n===== SUMMARY: successful inferences=%0d, failures=%0d =====",
                 successful_runs, fail_count);
        if (fail_count == 0)
            $display("TEST PASS: AXI + NPZ dataset cases match golden references");
        else
            $fatal(1, "TEST FAIL: %0d checks failed", fail_count);
        $finish;
    end

    initial begin : WATCHDOG
        #50000000; // 50 ms maximum simulation time (5 inference tests + 4 x 0.5 ms display)
        $fatal(1, "GLOBAL TIMEOUT: simulation exceeded 50 ms");
    end
endmodule

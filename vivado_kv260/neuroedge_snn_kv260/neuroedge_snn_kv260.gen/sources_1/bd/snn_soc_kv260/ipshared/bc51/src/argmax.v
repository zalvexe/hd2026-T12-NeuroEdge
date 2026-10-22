// First (lowest-numbered) class wins in a tie, matching NumPy argmax.
// Packed byte 0 is class 0; byte 3 is class 3.
module argmax (
    input  wire [31:0] counts,
    output reg  [1:0] predicted_class
);
    reg [7:0] largest;
    integer index;
    always @* begin
        largest = counts[7:0];
        predicted_class = 2'd0;
        for (index = 1; index < 4; index = index + 1) begin
            if (counts[index*8 +: 8] > largest) begin
                largest = counts[index*8 +: 8];
                predicted_class = index;
            end
        end
    end
endmodule

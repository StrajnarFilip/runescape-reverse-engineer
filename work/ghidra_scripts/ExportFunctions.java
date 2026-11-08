// Export only the functions selected for this local research session.
// @category RS3
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.Function;
import ghidra.program.model.address.Address;
import java.io.PrintWriter;
import java.util.HashSet;

public class ExportFunctions extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        HashSet<Address> seen = new HashSet<>();
        try (PrintWriter output = new PrintWriter(args[0], "UTF-8")) {
            for (int i = 1; i < args.length; i++) {
                boolean entryOnly = args[i].startsWith("entry:");
                Address address = toAddr(entryOnly ? args[i].substring(6) : args[i]);
                Function function = entryOnly ? getFunctionAt(address) : getFunctionContaining(address);
                if (entryOnly && function == null) {
                    Function overlapping = getFunctionContaining(address);
                    if (overlapping != null) removeFunction(overlapping);
                    clearListing(address, address);
                }
                if (function == null) {
                    disassemble(address);
                    function = createFunction(address, null);
                    if (function == null) {
                        output.println("// No function at " + address);
                        continue;
                    }
                }
                if (!seen.add(function.getEntryPoint())) continue;
                output.println("// Requested " + address + "; function " + function.getEntryPoint());
                DecompileResults result = decompiler.decompileFunction(function, 90, monitor);
                if (result.decompileCompleted()) output.println(result.getDecompiledFunction().getC());
                else output.println("// " + result.getErrorMessage());
                output.flush();
            }
        } finally {
            decompiler.dispose();
        }
    }
}

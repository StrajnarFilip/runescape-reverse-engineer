// Exports the decompiled C of the functions selected for this local research session.
//
// Arguments: OUTPUT_FILE ADDRESS [ADDRESS ...]. An "entry:" prefix marks an exact
// function entry point; a stale overlapping function there is replaced.
// @category RS3
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import java.io.PrintWriter;
import java.util.HashSet;
import java.util.Set;

public class ExportFunctions extends GhidraScript
{
    private static final String ENTRY_PREFIX = "entry:";
    private static final int DECOMPILE_TIMEOUT_SECONDS = 90;

    @Override
    public void run() throws Exception
    {
        String[] scriptArguments = getScriptArgs();
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        Set<Address> exportedEntryPoints = new HashSet<>();
        try (PrintWriter output = new PrintWriter(scriptArguments[0], "UTF-8"))
        {
            for (int index = 1; index < scriptArguments.length; index++)
            {
                exportFunction(decompiler, output, scriptArguments[index], exportedEntryPoints);
            }
        }
        finally
        {
            decompiler.dispose();
        }
    }

    private void exportFunction(DecompInterface decompiler, PrintWriter output,
                                String requestedAddress, Set<Address> exportedEntryPoints)
        throws Exception
    {
        boolean exactEntry = requestedAddress.startsWith(ENTRY_PREFIX);
        Address address = toAddr(exactEntry ? requestedAddress.substring(ENTRY_PREFIX.length())
                                            : requestedAddress);
        Function function = exactEntry ? getFunctionAt(address) : getFunctionContaining(address);
        if (exactEntry && function == null)
        {
            Function overlappingFunction = getFunctionContaining(address);
            if (overlappingFunction != null)
            {
                removeFunction(overlappingFunction);
            }
            clearListing(address, address);
        }
        if (function == null)
        {
            disassemble(address);
            function = createFunction(address, null);
            if (function == null)
            {
                output.println("// No function at " + address);
                return;
            }
        }
        if (!exportedEntryPoints.add(function.getEntryPoint()))
        {
            return;
        }
        output.println("// Requested " + address + "; function " + function.getEntryPoint());
        DecompileResults result =
            decompiler.decompileFunction(function, DECOMPILE_TIMEOUT_SECONDS, monitor);
        if (result.decompileCompleted())
        {
            output.println(result.getDecompiledFunction().getC());
        }
        else
        {
            output.println("// " + result.getErrorMessage());
        }
        output.flush();
    }
}

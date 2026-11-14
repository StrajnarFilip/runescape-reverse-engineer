// Lists every reference to exact ASCII strings, or to "addr:<hex>" addresses, and the functions
// containing those references.
// Usage (headless post-script): FindStringRefs.java <output file> <string|addr:hex> [...]
// @category RS3

import java.io.FileWriter;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;

public class FindStringRefs extends GhidraScript
{
    private static final int MAX_MATCHES_PER_STRING = 32;
    private static final String ADDRESS_PREFIX = "addr:";

    @Override
    protected void run() throws Exception
    {
        String[] arguments = getScriptArgs();
        try (PrintWriter output = new PrintWriter(new FileWriter(arguments[0])))
        {
            for (int index = 1; index < arguments.length; index++)
            {
                if (arguments[index].startsWith(ADDRESS_PREFIX))
                {
                    Address target = toAddr(arguments[index].substring(ADDRESS_PREFIX.length()));
                    output.println("ADDRESS " + target);
                    reportReferences(output, target);
                }
                else
                {
                    reportString(output, arguments[index]);
                }
            }
        }
    }

    private void reportString(PrintWriter output, String text) throws Exception
    {
        byte[] pattern = (text + "\0").getBytes(StandardCharsets.US_ASCII);
        Address[] matches = findBytes(currentProgram.getMinAddress(), escape(pattern), MAX_MATCHES_PER_STRING);
        output.println("STRING " + text + " matches=" + matches.length);
        for (Address match : matches)
        {
            output.println("  at " + match);
            reportReferences(output, match);
        }
    }

    private void reportReferences(PrintWriter output, Address target)
    {
        for (Reference reference : getReferencesTo(target))
        {
            Address from = reference.getFromAddress();
            Function function = getFunctionContaining(from);
            String owner = function == null ? "<no function>"
                                            : function.getName() + "@" + function.getEntryPoint();
            output.println("    ref " + from + " " + reference.getReferenceType() + " in " + owner);
        }
    }

    private static String escape(byte[] bytes)
    {
        StringBuilder builder = new StringBuilder();
        for (byte value : bytes)
        {
            builder.append(String.format("\\x%02x", value & 0xff));
        }
        return builder.toString();
    }
}

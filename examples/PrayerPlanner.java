import java.lang.foreign.*;
import java.lang.invoke.MethodHandle;
import java.nio.file.Path;
import static java.lang.foreign.ValueLayout.*;

/** Java 22+ consumer of the C prayer ABI. Synthetic data; no game input. */
public final class PrayerPlanner {
    private static MethodHandle function(SymbolLookup lookup, String name, FunctionDescriptor type) {
        return Linker.nativeLinker().downcallHandle(lookup.findOrThrow("rs3_prayer_" + name), type);
    }
    private static void check(int result) {
        if (result != 0) throw new IllegalStateException("C API result " + result);
    }
    public static void main(String[] args) throws Throwable {
        if (args.length != 1) throw new IllegalArgumentException("Pass the absolute rs3_native.dll path");
        try (Arena arena = Arena.ofConfined()) {
            SymbolLookup lookup = SymbolLookup.libraryLookup(Path.of(args[0]).toAbsolutePath(), arena);
            MethodHandle size = function(lookup, "sizeof", FunctionDescriptor.of(JAVA_INT, JAVA_INT));
            int[] expected = {32, 56, 40, 56};
            for (int i=0; i<expected.length; i++)
                if ((int)size.invokeExact(i+1) != expected[i]) throw new IllegalStateException("ABI size mismatch");
            MethodHandle create = function(lookup, "create", FunctionDescriptor.of(JAVA_INT, ADDRESS, ADDRESS));
            MethodHandle step = function(lookup, "step", FunctionDescriptor.of(JAVA_INT,
                    ADDRESS, ADDRESS, ADDRESS, JAVA_INT, ADDRESS, JAVA_INT));
            MethodHandle destroy = function(lookup, "destroy", FunctionDescriptor.ofVoid(ADDRESS));
            MemorySegment config = arena.allocate(32,8), pointer = arena.allocate(ADDRESS);
            int[] settings = {32, 5, 200, 50, 100, 100, 100, 0};
            for (int i=0; i<settings.length; i++) config.set(JAVA_INT, i*4L, settings[i]);
            check((int)create.invokeExact(config, pointer));
            MemorySegment planner = pointer.get(ADDRESS,0);
            try {
                MemorySegment frame = arena.allocate(56,8), threat = arena.allocate(40,8), out = arena.allocate(56,8);
                long[] times = {10_000_000_000L, 10_000_000_000L, 1, 1, 15_000_000_000L};
                for (int i=0; i<times.length; i++) frame.set(JAVA_LONG,i*8L,times[i]);
                frame.set(JAVA_INT,40,15); frame.set(JAVA_INT,44,5);
                frame.set(JAVA_INT,48,0x7c); frame.set(JAVA_INT,52,840);
                threat.set(JAVA_LONG,0,1); threat.set(JAVA_LONG,8,42);
                threat.set(JAVA_LONG,16,10_200_000_000L); threat.set(JAVA_LONG,24,10_200_000_000L);
                threat.set(JAVA_INT,32,3); threat.set(JAVA_INT,36,1);
                check((int)step.invokeExact(planner,frame,threat,1,out,56));
                int action=out.get(JAVA_INT,40), prayer=out.get(JAVA_INT,44);
                if (action!=2 || prayer!=3) throw new IllegalStateException("Unexpected proposal");
                System.out.println("Synthetic proposal: action="+action+" prayer="+prayer+"; no input sent");
            } finally {
                destroy.invokeExact(planner);
            }
        }
    }
}

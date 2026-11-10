import static java.lang.foreign.ValueLayout.ADDRESS;
import static java.lang.foreign.ValueLayout.JAVA_INT;
import static java.lang.foreign.ValueLayout.JAVA_LONG;

import java.lang.foreign.Arena;
import java.lang.foreign.FunctionDescriptor;
import java.lang.foreign.Linker;
import java.lang.foreign.MemorySegment;
import java.lang.foreign.SymbolLookup;
import java.lang.invoke.MethodHandle;
import java.nio.file.Path;

/** Java 22+ consumer of the C prayer planner ABI. Synthetic data; no game input. */
public final class PrayerPlanner
{
    private static final int PRAYER_MAGIC = 3;
    private static final int PRAYER_SOUL_SPLIT = 5;
    private static final int OVERHEAD_PRAYER_MASK = 0x7c;
    private static final int ALL_FRAME_FLAGS = 15;
    private static final int TIMING_VALIDATED = 1;
    private static final int ACTION_SET = 2;
    private static final int PRAYER_POINTS = 840;

    /** Expected rs3_prayer_sizeof() results for structure IDs 1..4. */
    private static final int[] STRUCTURE_SIZES = {32, 56, 40, 56};

    /**
     * rs3_prayer_config: size, idle prayer, lead, hold, idle gap, ack timeout, max age, reserved.
     */
    private static final int[] CONFIG_VALUES = {32, PRAYER_SOUL_SPLIT, 200, 50, 100, 100, 100, 0};
    private static final long CONFIG_SIZE = 32;

    /** rs3_prayer_frame. */
    private static final long FRAME_SIZE = 56;
    private static final long FRAME_FLAGS = 40;
    private static final long FRAME_ACTIVE_PRAYER = 44;
    private static final long FRAME_AVAILABLE_MASK = 48;
    private static final long FRAME_PRAYER_POINTS = 52;

    /** rs3_prayer_threat. */
    private static final long THREAT_SIZE = 40;
    private static final long THREAT_EVENT_ID = 0;
    private static final long THREAT_SOURCE_INSTANCE = 8;
    private static final long THREAT_CHECK_EARLIEST_NS = 16;
    private static final long THREAT_CHECK_LATEST_NS = 24;
    private static final long THREAT_PRAYER = 32;
    private static final long THREAT_FLAGS = 36;

    /** rs3_prayer_decision. */
    private static final long DECISION_SIZE = 56;
    private static final long DECISION_ACTION = 40;
    private static final long DECISION_PRAYER = 44;

    private static MethodHandle bind(SymbolLookup lookup, String name,
                                     FunctionDescriptor descriptor)
    {
        return Linker.nativeLinker().downcallHandle(lookup.findOrThrow("rs3_prayer_" + name),
                                                    descriptor);
    }

    private static void check(int result)
    {
        if (result != 0)
        {
            throw new IllegalStateException("C API result " + result);
        }
    }

    private static void verifyLayouts(SymbolLookup lookup) throws Throwable
    {
        MethodHandle sizeOf = bind(lookup, "sizeof", FunctionDescriptor.of(JAVA_INT, JAVA_INT));
        for (int index = 0; index < STRUCTURE_SIZES.length; index++)
        {
            if ((int)sizeOf.invokeExact(index + 1) != STRUCTURE_SIZES[index])
            {
                throw new IllegalStateException("ABI size mismatch");
            }
        }
    }

    /** Soul Split is active, every overhead is available and coverage extends 5 s. */
    private static void writeFrame(MemorySegment frame)
    {
        // now, observed, session, sequence and coverage-until times.
        long[] frameTimes = {10_000_000_000L, 10_000_000_000L, 1, 1, 15_000_000_000L};
        for (int index = 0; index < frameTimes.length; index++)
        {
            frame.set(JAVA_LONG, index * 8L, frameTimes[index]);
        }
        frame.set(JAVA_INT, FRAME_FLAGS, ALL_FRAME_FLAGS);
        frame.set(JAVA_INT, FRAME_ACTIVE_PRAYER, PRAYER_SOUL_SPLIT);
        frame.set(JAVA_INT, FRAME_AVAILABLE_MASK, OVERHEAD_PRAYER_MASK);
        frame.set(JAVA_INT, FRAME_PRAYER_POINTS, PRAYER_POINTS);
    }

    /** A validated magic attack checked 200 ms after the frame. */
    private static void writeThreat(MemorySegment threat)
    {
        threat.set(JAVA_LONG, THREAT_EVENT_ID, 1);
        threat.set(JAVA_LONG, THREAT_SOURCE_INSTANCE, 42);
        threat.set(JAVA_LONG, THREAT_CHECK_EARLIEST_NS, 10_200_000_000L);
        threat.set(JAVA_LONG, THREAT_CHECK_LATEST_NS, 10_200_000_000L);
        threat.set(JAVA_INT, THREAT_PRAYER, PRAYER_MAGIC);
        threat.set(JAVA_INT, THREAT_FLAGS, TIMING_VALIDATED);
    }

    public static void main(String[] arguments) throws Throwable
    {
        if (arguments.length != 1)
        {
            throw new IllegalArgumentException("Pass the absolute rs3_native.dll path");
        }
        try (Arena arena = Arena.ofConfined())
        {
            SymbolLookup lookup =
                SymbolLookup.libraryLookup(Path.of(arguments[0]).toAbsolutePath(), arena);
            verifyLayouts(lookup);
            MethodHandle create =
                bind(lookup, "create", FunctionDescriptor.of(JAVA_INT, ADDRESS, ADDRESS));
            MethodHandle step = bind(lookup, "step",
                                     FunctionDescriptor.of(JAVA_INT, ADDRESS, ADDRESS, ADDRESS,
                                                           JAVA_INT, ADDRESS, JAVA_INT));
            MethodHandle destroy = bind(lookup, "destroy", FunctionDescriptor.ofVoid(ADDRESS));

            MemorySegment config = arena.allocate(CONFIG_SIZE, 8);
            MemorySegment plannerOutput = arena.allocate(ADDRESS);
            for (int index = 0; index < CONFIG_VALUES.length; index++)
            {
                config.set(JAVA_INT, index * 4L, CONFIG_VALUES[index]);
            }
            check((int)create.invokeExact(config, plannerOutput));
            MemorySegment planner = plannerOutput.get(ADDRESS, 0);
            try
            {
                MemorySegment frame = arena.allocate(FRAME_SIZE, 8);
                MemorySegment threat = arena.allocate(THREAT_SIZE, 8);
                MemorySegment decision = arena.allocate(DECISION_SIZE, 8);
                writeFrame(frame);
                writeThreat(threat);
                check(
                    (int)step.invokeExact(planner, frame, threat, 1, decision, (int)DECISION_SIZE));
                int action = decision.get(JAVA_INT, DECISION_ACTION);
                int prayer = decision.get(JAVA_INT, DECISION_PRAYER);
                if (action != ACTION_SET || prayer != PRAYER_MAGIC)
                {
                    throw new IllegalStateException("Unexpected proposal");
                }
                System.out.println("Synthetic proposal: action=" + action + " prayer=" + prayer +
                                   "; no input sent");
            }
            finally
            {
                destroy.invokeExact(planner);
            }
        }
    }
}

import static java.lang.foreign.ValueLayout.ADDRESS;
import static java.lang.foreign.ValueLayout.JAVA_INT;
import static java.lang.foreign.ValueLayout.JAVA_LONG;

import java.lang.foreign.Arena;
import java.lang.foreign.FunctionDescriptor;
import java.lang.foreign.Linker;
import java.lang.foreign.MemorySegment;
import java.lang.foreign.SymbolLookup;
import java.lang.foreign.ValueLayout;
import java.lang.invoke.MethodHandle;
import java.nio.ByteOrder;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Java 22+ Foreign Function and Memory consumer of rs3_native: no JNI bridge,
 * JNA jar or game agent.
 *
 * This class owns one native context and snapshot; use it on its creating
 * thread. close() joins the native collector before unloading the DLL and
 * closing the arena. Field offsets mirror include/rs3/rs3.h and are checked
 * against rs3_sizeof() at construction.
 */
public final class Rs3Reader implements AutoCloseable
{
    private static final ValueLayout.OfInt INT32 = JAVA_INT.withOrder(ByteOrder.LITTLE_ENDIAN);
    private static final ValueLayout.OfLong INT64 = JAVA_LONG.withOrder(ByteOrder.LITTLE_ENDIAN);

    private static final int ABI_VERSION = 1;
    private static final int MAX_NPCS = 1024;
    private static final int MAX_HITS = 65536;
    private static final int MAX_ITEMS = 47;
    private static final int MAX_ABILITIES = 2048;
    private static final int HIT_EVENT_BATCH = 256;
    private static final int HIT_EVENT_BATCHES = 16;
    private static final int ITEM_EVENT_BATCH = 128;
    private static final int ITEM_EVENT_BATCHES = 8;
    private static final int CONTAINER_ALL = 0;
    private static final int STATUS_AVAILABLE = 1;
    private static final int STATUS_PARTIAL = 2;
    private static final int NPC_FLAG_NAME = 1;
    private static final int NPC_FLAG_CONFIG = 2;
    private static final int NPC_FLAG_HEALTH = 4;
    private static final int ABILITY_TIMER_VALID = 1;
    private static final int ABILITY_BASE_COOLDOWN_KNOWN = 4;
    private static final int INVENTORY_BONE_SHIELD_KNOWN = 16;
    private static final int VARIABLE_HEALTH = 0;
    private static final int VARIABLE_MAX_HEALTH = 1;
    private static final int VARIABLE_PRAYER_PACKED = 2;
    private static final int VARIABLE_ADRENALINE = 3;
    private static final long PRAYER_POINTS_MASK = 0x7fff;

    /** Expected rs3_sizeof() results for structure IDs 1..10. */
    private static final int[] STRUCTURE_SIZES = {16, 384, 136, 568, 40, 608, 168, 192, 192, 424};

    /** rs3_options. */
    private static final class OptionsLayout
    {
        static final long SIZE = 16;
        static final long STRUCT_SIZE = 0;
        static final long PID = 4;
    }

    /** rs3_info. */
    private static final class InfoLayout
    {
        static final long SIZE = 384;
        static final long STATUS = 4;
        static final long PID = 12;
        static final long SEQUENCE = 16;
        static final long TIMESTAMP_NS = 24;
        static final long SAMPLE_DURATION_NS = 40;
        static final long MESSAGE = 192;
    }

    /** rs3_player. */
    private static final class PlayerLayout
    {
        static final long SIZE = 136;
        static final long VALID_VARIABLES = 0;
        static final long RAW_VARIABLES = 8;
        static final long PRAYER_BASE_LEVEL = 120;
        static final long GCD_REMAINING_MS = 124;
        static final long GCD_REASON = 132;
    }

    /** rs3_npc. */
    private static final class NpcLayout
    {
        static final long SIZE = 568;
        static final long INSTANCE_ID = 0;
        static final long RUNTIME_ID = 8;
        static final long CONFIG_ID = 12;
        static final long FLAGS = 16;
        static final long HEALTH_REASON = 20;
        static final long HEALTH_CURRENT = 24;
        static final long HEALTH_MAX = 28;
        static final long HIT_OFFSET = 32;
        static final long HIT_COUNT = 36;
        static final long NAME = 48;
    }

    /** rs3_hit. */
    private static final class HitLayout
    {
        static final long SIZE = 40;
        static final long SLOT = 0;
        static final long TYPE_ID = 4;
        static final long AMOUNT = 8;
        static final long SECONDARY_TYPE_ID = 12;
        static final long SECONDARY_AMOUNT = 16;
        static final long START_TICK = 20;
        static final long END_TICK = 24;
        static final long DURATION_TICKS = 28;
        static final long STATE = 32;
    }

    /** rs3_event. */
    private static final class EventLayout
    {
        static final long SIZE = 608;
        static final long SEQUENCE = 0;
        static final long OBSERVED_AT_NS = 8;
        static final long PROCESS_CREATION_TIME = 16;
        static final long INSTANCE_ID = 24;
        static final long PID = 32;
        static final long RUNTIME_ID = 36;
        static final long CONFIG_ID = 40;
        static final long HIT = 48;
        static final long NAME = 88;
    }

    /** rs3_inventory_info. */
    private static final class InventoryInfoLayout
    {
        static final long SIZE = 168;
        static final long STATUS = 0;
        static final long CATALOG_STATUS = 16;
        static final long BONE_SHIELD_TIER = 24;
        static final long FLAGS = 28;
        static final long MESSAGE = 40;
    }

    /** rs3_item. */
    private static final class ItemLayout
    {
        static final long SIZE = 192;
        static final long TOKEN = 0;
        static final long CONTAINER_ID = 8;
        static final long SLOT = 12;
        static final long ITEM_ID = 16;
        static final long QUANTITY = 20;
        static final long FLAGS = 24;
        static final long FAMILY_ID = 28;
        static final long DOSES = 32;
        static final long NEXT_DOSE_ITEM_ID = 36;
        static final long EQUIP_SLOT = 40;
        static final long COMBAT_STYLE = 44;
        static final long EQUIPMENT_FLAGS = 48;
        static final long EFFECT_FLAGS = 52;
        static final long BASE_HEAL = 56;
        static final long NAME = 60;
    }

    /** rs3_ability. */
    private static final class AbilityLayout
    {
        static final long SIZE = 192;
        static final long STRUCT_ID = 0;
        static final long FLAGS = 4;
        static final long START_VAR = 8;
        static final long END_VAR = 12;
        static final long REMAINING_MS = 24;
        static final long DURATION_MS = 28;
        static final long BASE_COOLDOWN_MS = 32;
        static final long REQUIREMENTS = 36;
        static final long COMBAT_STYLE = 40;
        static final long EQUIPMENT_MATCH = 44;
        static final long REASON = 48;
        static final long NAME = 64;
    }

    /** rs3_item_event. */
    private static final class ItemEventLayout
    {
        static final long SIZE = 424;
        static final long SEQUENCE = 0;
        static final long OBSERVED_AT_NS = 8;
        static final long PROCESS_CREATION_TIME = 16;
        static final long PID = 24;
        static final long KIND = 28;
        static final long BEFORE = 40;
        static final long AFTER = 232;
    }

    private final Arena arena = Arena.ofConfined();
    private final Map<String, MethodHandle> functions = new HashMap<>();
    private MemorySegment context = MemorySegment.NULL;
    private MemorySegment snapshot = MemorySegment.NULL;
    private MemorySegment infoBuffer;
    private MemorySegment playerBuffer;
    private MemorySegment npcBuffer;
    private MemorySegment hitBuffer;
    private MemorySegment hitEventBuffer;
    private MemorySegment countOutput;
    private MemorySegment nextCursorOutput;
    private MemorySegment lostOutput;
    private MemorySegment inventoryInfoBuffer;
    private MemorySegment itemBuffer;
    private MemorySegment abilityBuffer;
    private MemorySegment itemEventBuffer;
    private long hitEventCursor;
    private long itemEventCursor;
    private boolean closed;

    public record Hit(int slot, int typeId, int amount, int secondaryTypeId, int secondaryAmount,
                      long startTick, long endTick, long durationTicks, int state)
    {
    }

    public record Npc(long instanceId, long runtimeId, Integer configId, String name, Integer hp,
                      Integer maxHp, int healthReason, List<Hit> hits)
    {
    }

    public record Event(long sequence, long observedAtNs, long processCreationTime, long instanceId,
                        long pid, long runtimeId, Integer configId, String name, Hit hit)
    {
    }

    public record Sample(long sequence, int status, String message, long pid, Long hp, Long maxHp,
                         Double prayer, Integer maxPrayer, Double adrenaline,
                         Integer gcdRemainingMs, long timestampNs, double readMs, List<Npc> npcs,
                         List<Event> events, long lostEvents)
    {
    }

    public record Item(long token, int containerId, int slot, Integer itemId, long quantity,
                       int flags, long familyId, Integer doses, Integer nextDoseItemId,
                       int equipSlot, int combatStyle, int equipmentFlags, int effectFlags,
                       int baseHeal, String name)
    {
    }

    public record Ability(int structId, int flags, String name, Integer remainingMs,
                          Integer observedDurationMs, Integer baseCooldownMs, int requirements,
                          int combatStyle, int equipmentMatch, int timerReason, int startVarc,
                          int endVarc)
    {
    }

    public record ItemChange(long sequence, long observedAtNs, long processCreationTime, long pid,
                             int kind, Item before, Item after)
    {
    }

    public record Inventory(int status, String message, int catalogStatus, Integer boneShieldTier,
                            List<Item> items, List<Ability> abilities, List<ItemChange> changes,
                            long lostChanges)
    {
    }

    private void bind(SymbolLookup lookup, String name, FunctionDescriptor descriptor)
    {
        MethodHandle handle =
            Linker.nativeLinker().downcallHandle(lookup.findOrThrow("rs3_" + name), descriptor);
        functions.put(name, handle);
    }

    /** Calls an export and throws on a negative rs3_result. */
    private int call(String name, Object... arguments) throws Throwable
    {
        int result = (int)functions.get(name).invokeWithArguments(arguments);
        if (result < 0)
        {
            throw new IllegalStateException(name + " failed: C result " + result);
        }
        return result;
    }

    private static long readUnsigned32(MemorySegment memory, long offset)
    {
        return Integer.toUnsignedLong(memory.get(INT32, offset));
    }

    private static Integer nonNegativeOrNull(int value)
    {
        return value < 0 ? null : value;
    }

    /** Returns raw_variables[index], or null when its validity bit is clear. */
    private static Long rawVariable(MemorySegment player, int index)
    {
        long validVariables = player.get(INT64, PlayerLayout.VALID_VARIABLES);
        if ((validVariables & (1L << index)) == 0)
        {
            return null;
        }
        return player.get(INT64, PlayerLayout.RAW_VARIABLES + index * 8L);
    }

    private static Hit readHit(MemorySegment memory, long offset)
    {
        return new Hit(memory.get(INT32, offset + HitLayout.SLOT),
                       memory.get(INT32, offset + HitLayout.TYPE_ID),
                       memory.get(INT32, offset + HitLayout.AMOUNT),
                       memory.get(INT32, offset + HitLayout.SECONDARY_TYPE_ID),
                       memory.get(INT32, offset + HitLayout.SECONDARY_AMOUNT),
                       readUnsigned32(memory, offset + HitLayout.START_TICK),
                       readUnsigned32(memory, offset + HitLayout.END_TICK),
                       readUnsigned32(memory, offset + HitLayout.DURATION_TICKS),
                       memory.get(INT32, offset + HitLayout.STATE));
    }

    private static Item readItem(MemorySegment memory, long offset)
    {
        return new Item(memory.get(INT64, offset + ItemLayout.TOKEN),
                        memory.get(INT32, offset + ItemLayout.CONTAINER_ID),
                        memory.get(INT32, offset + ItemLayout.SLOT),
                        nonNegativeOrNull(memory.get(INT32, offset + ItemLayout.ITEM_ID)),
                        readUnsigned32(memory, offset + ItemLayout.QUANTITY),
                        memory.get(INT32, offset + ItemLayout.FLAGS),
                        readUnsigned32(memory, offset + ItemLayout.FAMILY_ID),
                        nonNegativeOrNull(memory.get(INT32, offset + ItemLayout.DOSES)),
                        nonNegativeOrNull(memory.get(INT32, offset + ItemLayout.NEXT_DOSE_ITEM_ID)),
                        memory.get(INT32, offset + ItemLayout.EQUIP_SLOT),
                        memory.get(INT32, offset + ItemLayout.COMBAT_STYLE),
                        memory.get(INT32, offset + ItemLayout.EQUIPMENT_FLAGS),
                        memory.get(INT32, offset + ItemLayout.EFFECT_FLAGS),
                        memory.get(INT32, offset + ItemLayout.BASE_HEAL),
                        memory.getString(offset + ItemLayout.NAME));
    }

    private static Ability readAbility(MemorySegment memory, long offset)
    {
        int flags = memory.get(INT32, offset + AbilityLayout.FLAGS);
        boolean timerValid = (flags & ABILITY_TIMER_VALID) != 0;
        boolean baseCooldownKnown = (flags & ABILITY_BASE_COOLDOWN_KNOWN) != 0;
        return new Ability(
            memory.get(INT32, offset + AbilityLayout.STRUCT_ID), flags,
            memory.getString(offset + AbilityLayout.NAME),
            timerValid ? memory.get(INT32, offset + AbilityLayout.REMAINING_MS) : null,
            timerValid ? memory.get(INT32, offset + AbilityLayout.DURATION_MS) : null,
            baseCooldownKnown ? memory.get(INT32, offset + AbilityLayout.BASE_COOLDOWN_MS) : null,
            memory.get(INT32, offset + AbilityLayout.REQUIREMENTS),
            memory.get(INT32, offset + AbilityLayout.COMBAT_STYLE),
            memory.get(INT32, offset + AbilityLayout.EQUIPMENT_MATCH),
            memory.get(INT32, offset + AbilityLayout.REASON),
            memory.get(INT32, offset + AbilityLayout.START_VAR),
            memory.get(INT32, offset + AbilityLayout.END_VAR));
    }

    private void bindFunctions(SymbolLookup lookup)
    {
        FunctionDescriptor cursorDescriptor =
            FunctionDescriptor.of(INT32, ADDRESS, INT64, ADDRESS, INT32, ADDRESS, ADDRESS, ADDRESS);
        bind(lookup, "abi_version", FunctionDescriptor.of(INT32));
        bind(lookup, "sizeof", FunctionDescriptor.of(INT32, INT32));
        bind(lookup, "create", FunctionDescriptor.of(INT32, ADDRESS, ADDRESS));
        bind(lookup, "destroy", FunctionDescriptor.ofVoid(ADDRESS));
        bind(lookup, "snapshot_create", FunctionDescriptor.of(INT32, ADDRESS));
        bind(lookup, "snapshot_destroy", FunctionDescriptor.ofVoid(ADDRESS));
        bind(lookup, "start", FunctionDescriptor.of(INT32, ADDRESS, INT32));
        bind(lookup, "copy_latest", FunctionDescriptor.of(INT32, ADDRESS, ADDRESS));
        bind(lookup, "snapshot_info", FunctionDescriptor.of(INT32, ADDRESS, ADDRESS, INT32));
        bind(lookup, "snapshot_player", FunctionDescriptor.of(INT32, ADDRESS, ADDRESS, INT32));
        bind(lookup, "snapshot_npcs",
             FunctionDescriptor.of(INT32, ADDRESS, ADDRESS, INT32, ADDRESS));
        bind(lookup, "snapshot_hits",
             FunctionDescriptor.of(INT32, ADDRESS, ADDRESS, INT32, ADDRESS));
        bind(lookup, "events_since", cursorDescriptor);
        bind(lookup, "snapshot_inventory_info",
             FunctionDescriptor.of(INT32, ADDRESS, ADDRESS, INT32));
        bind(lookup, "snapshot_items",
             FunctionDescriptor.of(INT32, ADDRESS, INT32, ADDRESS, INT32, ADDRESS));
        bind(lookup, "snapshot_abilities",
             FunctionDescriptor.of(INT32, ADDRESS, ADDRESS, INT32, ADDRESS));
        bind(lookup, "item_events_since", cursorDescriptor);
    }

    private void verifyLayouts() throws Throwable
    {
        if (call("abi_version") != ABI_VERSION)
        {
            throw new IllegalStateException("Unsupported ABI");
        }
        for (int index = 0; index < STRUCTURE_SIZES.length; index++)
        {
            int structureId = index + 1;
            if (call("sizeof", structureId) != STRUCTURE_SIZES[index])
            {
                throw new IllegalStateException("Struct layout mismatch: " + structureId);
            }
        }
    }

    private void allocateBuffers()
    {
        infoBuffer = arena.allocate(InfoLayout.SIZE, 8);
        playerBuffer = arena.allocate(PlayerLayout.SIZE, 8);
        npcBuffer = arena.allocate(NpcLayout.SIZE * MAX_NPCS, 8);
        hitBuffer = arena.allocate(HitLayout.SIZE * MAX_HITS, 8);
        hitEventBuffer = arena.allocate(EventLayout.SIZE * HIT_EVENT_BATCH, 8);
        countOutput = arena.allocate(INT32);
        nextCursorOutput = arena.allocate(INT64);
        lostOutput = arena.allocate(INT64);
        inventoryInfoBuffer = arena.allocate(InventoryInfoLayout.SIZE, 8);
        itemBuffer = arena.allocate(ItemLayout.SIZE * MAX_ITEMS, 8);
        abilityBuffer = arena.allocate(AbilityLayout.SIZE * MAX_ABILITIES, 8);
        itemEventBuffer = arena.allocate(ItemEventLayout.SIZE * ITEM_EVENT_BATCH, 8);
    }

    public Rs3Reader(Path library, int pid, int hz) throws Throwable
    {
        try
        {
            if (ADDRESS.byteSize() != 8 || !System.getProperty("os.name").startsWith("Windows"))
            {
                throw new IllegalStateException("Windows x64 JVM required");
            }
            SymbolLookup lookup = SymbolLookup.libraryLookup(library.toAbsolutePath(), arena);
            bindFunctions(lookup);
            verifyLayouts();
            // Unset option fields stay zero: default retry interval and reserved.
            MemorySegment options = arena.allocate(OptionsLayout.SIZE, 8);
            MemorySegment handleOutput = arena.allocate(ADDRESS);
            options.set(INT32, OptionsLayout.STRUCT_SIZE, (int)OptionsLayout.SIZE);
            options.set(INT32, OptionsLayout.PID, pid);
            call("create", options, handleOutput);
            context = handleOutput.get(ADDRESS, 0);
            call("snapshot_create", handleOutput);
            snapshot = handleOutput.get(ADDRESS, 0);
            allocateBuffers();
            call("start", context, hz);
        }
        catch (Throwable failure)
        {
            try
            {
                close();
            }
            catch (Exception cleanupFailure)
            {
                failure.addSuppressed(cleanupFailure);
            }
            throw failure;
        }
    }

    private void requireOpen()
    {
        if (closed)
        {
            throw new IllegalStateException("Reader is closed");
        }
    }

    private List<ItemChange> drainItemChanges(long[] lostTotal) throws Throwable
    {
        List<ItemChange> changes = new ArrayList<>();
        for (int batch = 0; batch < ITEM_EVENT_BATCHES; batch++)
        {
            call("item_events_since", context, itemEventCursor, itemEventBuffer, ITEM_EVENT_BATCH,
                 countOutput, nextCursorOutput, lostOutput);
            itemEventCursor = nextCursorOutput.get(INT64, 0);
            lostTotal[0] += lostOutput.get(INT64, 0);
            int written = countOutput.get(INT32, 0);
            for (int index = 0; index < written; index++)
            {
                long offset = index * ItemEventLayout.SIZE;
                Item before = readItem(itemEventBuffer, offset + ItemEventLayout.BEFORE);
                Item after = readItem(itemEventBuffer, offset + ItemEventLayout.AFTER);
                changes.add(new ItemChange(
                    itemEventBuffer.get(INT64, offset + ItemEventLayout.SEQUENCE),
                    itemEventBuffer.get(INT64, offset + ItemEventLayout.OBSERVED_AT_NS),
                    itemEventBuffer.get(INT64, offset + ItemEventLayout.PROCESS_CREATION_TIME),
                    readUnsigned32(itemEventBuffer, offset + ItemEventLayout.PID),
                    itemEventBuffer.get(INT32, offset + ItemEventLayout.KIND),
                    before.itemId() == null ? null : before,
                    after.itemId() == null ? null : after));
            }
            if (written < ITEM_EVENT_BATCH)
            {
                break;
            }
        }
        return changes;
    }

    /**
     * Returns inventory, equipment and abilities from one coherent copied frame.
     *
     * slot is the native zero-based slot; itemId null is an empty cell. Timers
     * without the valid flag are unknown. Equipment compatibility is not full
     * castability. This call has its own item-event cursor, independent of the
     * hit observations.
     */
    public Inventory inventory() throws Throwable
    {
        requireOpen();
        call("copy_latest", context, snapshot);
        call("snapshot_inventory_info", snapshot, inventoryInfoBuffer,
             (int)InventoryInfoLayout.SIZE);

        call("snapshot_items", snapshot, CONTAINER_ALL, itemBuffer, MAX_ITEMS, countOutput);
        List<Item> items = new ArrayList<>();
        int itemCount = countOutput.get(INT32, 0);
        for (int index = 0; index < itemCount; index++)
        {
            items.add(readItem(itemBuffer, index * ItemLayout.SIZE));
        }

        call("snapshot_abilities", snapshot, abilityBuffer, MAX_ABILITIES, countOutput);
        List<Ability> abilities = new ArrayList<>();
        int abilityCount = countOutput.get(INT32, 0);
        for (int index = 0; index < abilityCount; index++)
        {
            abilities.add(readAbility(abilityBuffer, index * AbilityLayout.SIZE));
        }

        long[] lostTotal = {0};
        List<ItemChange> changes = drainItemChanges(lostTotal);
        int inventoryFlags = inventoryInfoBuffer.get(INT32, InventoryInfoLayout.FLAGS);
        Integer boneShieldTier =
            (inventoryFlags & INVENTORY_BONE_SHIELD_KNOWN) != 0
                ? inventoryInfoBuffer.get(INT32, InventoryInfoLayout.BONE_SHIELD_TIER)
                : null;
        return new Inventory(inventoryInfoBuffer.get(INT32, InventoryInfoLayout.STATUS),
                             inventoryInfoBuffer.getString(InventoryInfoLayout.MESSAGE),
                             inventoryInfoBuffer.get(INT32, InventoryInfoLayout.CATALOG_STATUS),
                             boneShieldTier, List.copyOf(items), List.copyOf(abilities),
                             List.copyOf(changes), lostTotal[0]);
    }

    private List<Npc> readNpcs(int npcCount)
    {
        List<Npc> npcs = new ArrayList<>();
        for (int index = 0; index < npcCount; index++)
        {
            long offset = index * NpcLayout.SIZE;
            int flags = npcBuffer.get(INT32, offset + NpcLayout.FLAGS);
            int hitOffset = npcBuffer.get(INT32, offset + NpcLayout.HIT_OFFSET);
            int hitCount = npcBuffer.get(INT32, offset + NpcLayout.HIT_COUNT);
            List<Hit> hitMarks = new ArrayList<>();
            for (int hitIndex = 0; hitIndex < hitCount; hitIndex++)
            {
                hitMarks.add(readHit(hitBuffer, (hitOffset + hitIndex) * HitLayout.SIZE));
            }
            boolean healthKnown = (flags & NPC_FLAG_HEALTH) != 0;
            npcs.add(new Npc(
                npcBuffer.get(INT64, offset + NpcLayout.INSTANCE_ID),
                readUnsigned32(npcBuffer, offset + NpcLayout.RUNTIME_ID),
                (flags & NPC_FLAG_CONFIG) != 0 ? npcBuffer.get(INT32, offset + NpcLayout.CONFIG_ID)
                                               : null,
                (flags & NPC_FLAG_NAME) != 0 ? npcBuffer.getString(offset + NpcLayout.NAME) : null,
                healthKnown ? npcBuffer.get(INT32, offset + NpcLayout.HEALTH_CURRENT) : null,
                healthKnown ? npcBuffer.get(INT32, offset + NpcLayout.HEALTH_MAX) : null,
                npcBuffer.get(INT32, offset + NpcLayout.HEALTH_REASON), List.copyOf(hitMarks)));
        }
        return npcs;
    }

    private List<Event> drainHitEvents(long[] lostTotal) throws Throwable
    {
        List<Event> observed = new ArrayList<>();
        for (int batch = 0; batch < HIT_EVENT_BATCHES; batch++)
        {
            call("events_since", context, hitEventCursor, hitEventBuffer, HIT_EVENT_BATCH,
                 countOutput, nextCursorOutput, lostOutput);
            hitEventCursor = nextCursorOutput.get(INT64, 0);
            lostTotal[0] += lostOutput.get(INT64, 0);
            int written = countOutput.get(INT32, 0);
            for (int index = 0; index < written; index++)
            {
                long offset = index * EventLayout.SIZE;
                int configId = hitEventBuffer.get(INT32, offset + EventLayout.CONFIG_ID);
                observed.add(
                    new Event(hitEventBuffer.get(INT64, offset + EventLayout.SEQUENCE),
                              hitEventBuffer.get(INT64, offset + EventLayout.OBSERVED_AT_NS),
                              hitEventBuffer.get(INT64, offset + EventLayout.PROCESS_CREATION_TIME),
                              hitEventBuffer.get(INT64, offset + EventLayout.INSTANCE_ID),
                              readUnsigned32(hitEventBuffer, offset + EventLayout.PID),
                              readUnsigned32(hitEventBuffer, offset + EventLayout.RUNTIME_ID),
                              nonNegativeOrNull(configId),
                              hitEventBuffer.getString(offset + EventLayout.NAME),
                              readHit(hitEventBuffer, offset + EventLayout.HIT)));
            }
            if (written < HIT_EVENT_BATCH)
            {
                break;
            }
        }
        return observed;
    }

    /**
     * Copies the latest native frame and drains up to 4096 queued observations.
     *
     * Returned records contain owned Java values, not views of reused buffers.
     * status 0 or 3 means the player fields are unavailable; consult the
     * validity mask.
     */
    public Sample sample() throws Throwable
    {
        requireOpen();
        call("copy_latest", context, snapshot);
        call("snapshot_info", snapshot, infoBuffer, (int)InfoLayout.SIZE);
        call("snapshot_player", snapshot, playerBuffer, (int)PlayerLayout.SIZE);
        call("snapshot_npcs", snapshot, npcBuffer, MAX_NPCS, countOutput);
        int npcCount = countOutput.get(INT32, 0);
        call("snapshot_hits", snapshot, hitBuffer, MAX_HITS, countOutput);
        List<Npc> npcs = readNpcs(npcCount);
        long[] lostTotal = {0};
        List<Event> observed = drainHitEvents(lostTotal);

        Long prayerPacked = rawVariable(playerBuffer, VARIABLE_PRAYER_PACKED);
        Long adrenaline = rawVariable(playerBuffer, VARIABLE_ADRENALINE);
        int status = infoBuffer.get(INT32, InfoLayout.STATUS);
        boolean available = status == STATUS_AVAILABLE || status == STATUS_PARTIAL;
        boolean globalCooldownKnown =
            available && playerBuffer.get(INT32, PlayerLayout.GCD_REASON) == 0;
        return new Sample(
            infoBuffer.get(INT64, InfoLayout.SEQUENCE), status,
            infoBuffer.getString(InfoLayout.MESSAGE), readUnsigned32(infoBuffer, InfoLayout.PID),
            rawVariable(playerBuffer, VARIABLE_HEALTH),
            rawVariable(playerBuffer, VARIABLE_MAX_HEALTH),
            prayerPacked == null ? null : (prayerPacked & PRAYER_POINTS_MASK) / 10.0,
            available ? playerBuffer.get(INT32, PlayerLayout.PRAYER_BASE_LEVEL) * 10 : null,
            adrenaline == null ? null : adrenaline / 10.0,
            globalCooldownKnown ? playerBuffer.get(INT32, PlayerLayout.GCD_REMAINING_MS) : null,
            infoBuffer.get(INT64, InfoLayout.TIMESTAMP_NS),
            infoBuffer.get(INT64, InfoLayout.SAMPLE_DURATION_NS) / 1e6, List.copyOf(npcs),
            List.copyOf(observed), lostTotal[0]);
    }

    @Override
    public void close()
    {
        if (closed)
        {
            return;
        }
        closed = true;
        try
        {
            if (!context.equals(MemorySegment.NULL))
            {
                functions.get("destroy").invokeExact(context);
            }
            if (!snapshot.equals(MemorySegment.NULL))
            {
                functions.get("snapshot_destroy").invokeExact(snapshot);
            }
        }
        catch (Throwable error)
        {
            throw new IllegalStateException("Native cleanup failed", error);
        }
        finally
        {
            arena.close();
        }
    }

    public static void main(String[] arguments) throws Throwable
    {
        if (arguments.length < 1)
        {
            throw new IllegalArgumentException("Usage: Rs3Reader path/to/rs3_native.dll [seconds]");
        }
        int durationSeconds = arguments.length > 1 ? Integer.parseInt(arguments[1]) : 5;
        try (Rs3Reader reader = new Rs3Reader(Path.of(arguments[0]), 0, 100))
        {
            for (int refresh = 0; refresh < durationSeconds * 20; refresh++)
            {
                Thread.sleep(50);
                Sample sample = reader.sample();
                Inventory inventory = reader.inventory();
                System.out.printf(Locale.ROOT,
                                  "sample=%d status=%d HP=%s/%s prayer=%s adrenaline=%s NPCs=%d "
                                      + "items=%d abilities=%d changes=%d read=%.3fms %s%n",
                                  sample.sequence(), sample.status(), sample.hp(), sample.maxHp(),
                                  sample.prayer(), sample.adrenaline(), sample.npcs().size(),
                                  inventory.items().size(), inventory.abilities().size(),
                                  inventory.changes().size(), sample.readMs(), sample.message());
            }
        }
    }
}

import java.lang.foreign.*;
import java.lang.invoke.MethodHandle;
import java.nio.ByteOrder;
import java.nio.file.Path;
import java.util.*;
import static java.lang.foreign.ValueLayout.*;

/** Java 22+ FFM consumer: no JNI bridge, JNA jar, or game agent.
 * This class owns one native context/snapshot. Use on its creating thread.
 * close() joins the collector before unloading the DLL or closing the arena.
 */
public final class Rs3Reader implements AutoCloseable {
    private static final ValueLayout.OfInt I = JAVA_INT.withOrder(ByteOrder.LITTLE_ENDIAN);
    private static final ValueLayout.OfLong L = JAVA_LONG.withOrder(ByteOrder.LITTLE_ENDIAN);
    private final Arena arena = Arena.ofConfined();
    private final Map<String, MethodHandle> functions = new HashMap<>();
    private MemorySegment context = MemorySegment.NULL, snapshot = MemorySegment.NULL;
    private MemorySegment info, player, npcs, hits, events, count, next, lost;
    private long cursor;
    private boolean closed;

    public record Hit(int slot, int typeId, int amount, int secondaryTypeId,
                      int secondaryAmount, long startTick, long endTick, long durationTicks, int state) {}
    public record Npc(long instanceId, long runtimeId, Integer configId, String name,
                      Integer hp, Integer maxHp, int healthReason, List<Hit> hits) {}
    public record Event(long sequence, long observedAtNs, long processCreationTime,
                        long instanceId, long pid, long runtimeId, Integer configId, String name, Hit hit) {}
    public record Sample(long sequence, int status, String message, long pid, Long hp, Long maxHp,
                         Double prayer, Integer maxPrayer, Double adrenaline, Integer gcdRemainingMs,
                         long timestampNs, double readMs, List<Npc> npcs, List<Event> events, long lostEvents) {}

    private void bind(SymbolLookup lookup, String name, FunctionDescriptor descriptor) {
        functions.put(name, Linker.nativeLinker().downcallHandle(lookup.findOrThrow("rs3_"+name), descriptor));
    }
    private int call(String name, Object... args) throws Throwable {
        int result = (int) functions.get(name).invokeWithArguments(args);
        if (result < 0) throw new IllegalStateException(name+" failed: C result "+result);
        return result;
    }
    private static long unsigned(MemorySegment memory, long offset) {
        return Integer.toUnsignedLong(memory.get(I, offset));
    }
    private static Long raw(MemorySegment player, int index) {
        return (player.get(L, 0) & (1L << index)) == 0 ? null : player.get(L, 8+index*8L);
    }
    private static Hit hit(MemorySegment m, long o) {
        return new Hit(m.get(I,o), m.get(I,o+4), m.get(I,o+8), m.get(I,o+12), m.get(I,o+16),
                unsigned(m,o+20), unsigned(m,o+24), unsigned(m,o+28), m.get(I,o+32));
    }

    public Rs3Reader(Path library, int pid, int hz) throws Throwable {
        try {
            if (ADDRESS.byteSize()!=8 || !System.getProperty("os.name").startsWith("Windows"))
                throw new IllegalStateException("Windows x64 JVM required");
            SymbolLookup lookup = SymbolLookup.libraryLookup(library.toAbsolutePath(), arena);
            bind(lookup,"abi_version",FunctionDescriptor.of(I));
            bind(lookup,"sizeof",FunctionDescriptor.of(I,I));
            bind(lookup,"create",FunctionDescriptor.of(I,ADDRESS,ADDRESS));
            bind(lookup,"destroy",FunctionDescriptor.ofVoid(ADDRESS));
            bind(lookup,"snapshot_create",FunctionDescriptor.of(I,ADDRESS));
            bind(lookup,"snapshot_destroy",FunctionDescriptor.ofVoid(ADDRESS));
            bind(lookup,"start",FunctionDescriptor.of(I,ADDRESS,I));
            bind(lookup,"copy_latest",FunctionDescriptor.of(I,ADDRESS,ADDRESS));
            bind(lookup,"snapshot_info",FunctionDescriptor.of(I,ADDRESS,ADDRESS,I));
            bind(lookup,"snapshot_player",FunctionDescriptor.of(I,ADDRESS,ADDRESS,I));
            bind(lookup,"snapshot_npcs",FunctionDescriptor.of(I,ADDRESS,ADDRESS,I,ADDRESS));
            bind(lookup,"snapshot_hits",FunctionDescriptor.of(I,ADDRESS,ADDRESS,I,ADDRESS));
            bind(lookup,"events_since",FunctionDescriptor.of(I,ADDRESS,L,ADDRESS,I,ADDRESS,ADDRESS,ADDRESS));
            if (call("abi_version")!=1) throw new IllegalStateException("Unsupported ABI");
            int[] sizes={16,384,136,568,40,608};
            for (int i=0;i<sizes.length;i++)
                if (call("sizeof",i+1)!=sizes[i]) throw new IllegalStateException("Struct layout mismatch: "+(i+1));
            MemorySegment options=arena.allocate(16,8), pointer=arena.allocate(ADDRESS);
            options.set(I,0,16); options.set(I,4,pid); // Other fields are zero-initialized.
            call("create",options,pointer); context=pointer.get(ADDRESS,0);
            call("snapshot_create",pointer); snapshot=pointer.get(ADDRESS,0);
            info=arena.allocate(384,8); player=arena.allocate(136,8);
            npcs=arena.allocate(568L*1024,8); hits=arena.allocate(40L*65536,8);
            events=arena.allocate(608L*256,8); count=arena.allocate(I);
            next=arena.allocate(L); lost=arena.allocate(L);
            call("start",context,hz);
        } catch (Throwable failure) {
            try { close(); } catch (Exception cleanup) { failure.addSuppressed(cleanup); }
            throw failure;
        }
    }

    /** Copies the latest native frame and drains up to 4096 queued observations.
     * Returned records contain owned Java values, not views of reused buffers.
     * status=0/3 means player fields are unavailable; consult the validity mask.
     */
    public Sample sample() throws Throwable {
        if (closed) throw new IllegalStateException("Reader is closed");
        call("copy_latest",context,snapshot);
        call("snapshot_info",snapshot,info,384); call("snapshot_player",snapshot,player,136);
        call("snapshot_npcs",snapshot,npcs,1024,count);
        int npcCount=count.get(I,0);
        call("snapshot_hits",snapshot,hits,65536,count);
        List<Npc> entities=new ArrayList<>();
        for (int i=0;i<npcCount;i++) {
            long o=i*568L; int flags=npcs.get(I,o+16);
            int offset=npcs.get(I,o+32), n=npcs.get(I,o+36);
            List<Hit> marks=new ArrayList<>();
            for(int j=0;j<n;j++) marks.add(hit(hits,(offset+j)*40L));
            entities.add(new Npc(npcs.get(L,o),unsigned(npcs,o+8),
                (flags&2)!=0?npcs.get(I,o+12):null, (flags&1)!=0?npcs.getString(o+48):null,
                (flags&4)!=0?npcs.get(I,o+24):null, (flags&4)!=0?npcs.get(I,o+28):null,
                npcs.get(I,o+20),List.copyOf(marks)));
        }
        List<Event> observed=new ArrayList<>(); long lostTotal=0;
        for (int batch=0;batch<16;batch++) {
            call("events_since",context,cursor,events,256,count,next,lost);
            cursor=next.get(L,0); lostTotal+=lost.get(L,0);
            int n=count.get(I,0);
            for(int i=0;i<n;i++) {
                long o=i*608L; int config=events.get(I,o+40);
                observed.add(new Event(events.get(L,o),events.get(L,o+8),events.get(L,o+16),events.get(L,o+24),
                    unsigned(events,o+32),unsigned(events,o+36), config<0?null:config,
                    events.getString(o+88),hit(events,o+48)));
            }
            if(n<256) break;
        }
        Long prayer=raw(player,2), adrenaline=raw(player,3);
        boolean available=info.get(I,4)==1 || info.get(I,4)==2;
        return new Sample(info.get(L,16),info.get(I,4),info.getString(192),unsigned(info,12),
            raw(player,0),raw(player,1),prayer==null?null:(prayer&32767)/10.0,
            available?player.get(I,120)*10:null,adrenaline==null?null:adrenaline/10.0,
            !available || player.get(I,132)!=0?null:player.get(I,124),
            info.get(L,24),info.get(L,40)/1e6,List.copyOf(entities),List.copyOf(observed),lostTotal);
    }

    @Override public void close() {
        if(closed) return;
        closed=true;
        try {
            if(!context.equals(MemorySegment.NULL)) functions.get("destroy").invokeExact(context);
            if(!snapshot.equals(MemorySegment.NULL)) functions.get("snapshot_destroy").invokeExact(snapshot);
        } catch(Throwable error) { throw new IllegalStateException("Native cleanup failed",error); }
        finally { arena.close(); }
    }

    public static void main(String[] args) throws Throwable {
        if(args.length<1) throw new IllegalArgumentException("Usage: Rs3Reader path/to/rs3_native.dll [seconds]");
        int seconds=args.length>1?Integer.parseInt(args[1]):5;
        try(Rs3Reader reader=new Rs3Reader(Path.of(args[0]),0,100)) {
            for(int i=0;i<seconds*20;i++) {
                Thread.sleep(50);
                Sample s=reader.sample();
                System.out.printf(Locale.ROOT,"sample=%d status=%d HP=%s/%s prayer=%s adrenaline=%s NPCs=%d events=%d lost=%d read=%.3fms %s%n",
                    s.sequence,s.status,s.hp,s.maxHp,s.prayer,s.adrenaline,s.npcs.size(),s.events.size(),s.lostEvents,s.readMs,s.message);
            }
        }
    }
}

Raziskovalni zapis za RS3 951-3
=============================

Identiteta in metoda
-------------------

SHA-256 odjemalca::

    fd8afd32f9829b1fb0a953b238c504e75526b7af5e2920120aa027777a358d45

PE image base za statično analizo: ``0x140000000``. Vsi odmiki v profilu so
relativni do dejansko naloženega modula ali preverjenega objekta. Dinamični
naslovi in PID-ji niso del profila. Native funkcij ne izvajamo; njihove poti
do podatkov smo rekonstruirali z Ghidro in preverili z read-only branjem.

Registry in tabele spremenljivk
-----------------------------

Registry domen je drevo pri RVA ``0x1721ee0``. Koren je pri ``+0x10``, ključ
vozlišča pri ``+0x20``, desni in levi kazalec pri ``+0`` in ``+8``.
Objekt funkcije je pri vozlišče ``+0x60``, zajeti kontekst pri funkcija ``+8``.
Vtable callback pri ``+0x10`` mora ustrezati pripetemu handlerju:

* Domena 61: VarPlayer, handler RVA ``0x4819d0``.
* Domena 57: VarClient, handler RVA ``0x481ac0``; kontekst je Client.

VarPlayer tabela: kontekst ``+0x1c0c0``; število bucketov ``+0x1c0c8``.
VarClient tabela: Client ``+0x7308``; število bucketov ``+0x7310``.
Bucket je ``id % count``. Vozlišče vsebuje ključ uint32 pri ``+0``, numerično
vrednost pri ``+8``, tip pri ``+0x20`` in naslednji kazalec pri ``+0x28``.
Tip 0 je int32; tip 1 je int64. End sentinel na koncu bucket tabele je lahko
``UINTPTR_MAX`` in se ne sme dereferencirati kot kazalec.
Manjkajoča vrednost ni avtomatično ničla.

Potrjene meritve
---------------

* VARP 13537 / 13538: trenutni in maksimalni HP; uporabnik potrdil 8700/8700,
  nato zajete spremembe med bojem in regeneracijo.
* VARP 3274, biti 0..14 / 10: prayer. Prehod 840 na 763,5; uporabnikov UI kaže 763.
* Client ``+0x72e8``: skill manager, entries pri manager ``+0x10``.
  Skill stride 24; osnovni level pri ``+16``. Prayer skill index 5, level 84;
  prikazani max prayer je izpeljan kot level * 10.
* VARP 679 / 10: adrenalin v odstotkih; zajete žive spremembe do 100 %.
* VARP 4501 / 4502 / 9847: server GCD začetek / konec / trajanje.
* VARC 2091 / 2092: client GCD začetek / konec. Client ``+8`` kaže na stanje,
  stanje ``+0x528`` na logic clock. Opaženih 90 tickov pri 20 ms/tick.
  Pet začetkov je bilo ujetih neposredno ob spremembi skupaj s tem števcem.

GCD je izračunan s predznačeno 32-bitno razliko, tudi ob prelivu števca.
``last_change_age_ms`` opisuje čas od zadnje opažene spremembe, ne starosti
strežniških podatkov. Čas zadnje posodobitve s strežnika ostaja neznan.

NPC identiteta, HP in zadetki
----------------------------

Pot do NPC managerja je potrjena: ``Client +8 -> stanje +0x19940 -> manager``.
Pri konstrukciji native vezav se ta isti manager prenese v NPCList.All.
Manager vtable RVA je ``0x153ae88``, NPC vtable RVA ``0x15dc938``.
Seznam indeksov je pri manager ``+0xa0a0``, count pri ``+0xb0a0``; lookup
``0x140038c00`` uporablja tabelo pri ``+0x10`` in bucket count pri ``+0x18``.
Ključ vozlišča je pri ``+0``, entity pointer pri ``+0x10``, next pri ``+0x18``.

NPC runtime index je uint16 pri ``+0x88``; vtable getter ``0x140c50b70`` ga
kopira v entity UID s tipom NPC. Prikazano ime je fixed string pri ``+0x90``.
Config ID getter ``0x14044bc00`` najprej uporabi config pri ``+0x10b8``,
sicer osnovnega pri ``+0x10a8``, ID pa prebere pri config ``+8``.
Ta ista pot v živo vrne Giant spider 20499 in različne Gobline 11232–11240
ter 12353–12357. Runtime indeks loči primerke istega tipa.

NPC ima dve tabeli po sedem int32 statistik: current pri ``+0x1150``, max
pri ``+0x116c``. HP je element 3, zato current HP ``+0x115c`` in max HP
``+0x1178``. Native get_npc_stat pri ``0x14072401e`` bere obe tabeli s
CS2 indeksom, NPC update pot pri ``0x1403f7e5f`` in ``0x1403f7e67`` pa ju
zapiše z indeksom iz strežniške posodobitve. Živo ujemanje preveri pomen
indeksa in enote: Goblin 550/550, Giant spider 1000/1000; uporabnikov zadetek
224 se hkrati pojavi v hitmarku in zmanjša Goblina runtime 22749 na 326/550.
To je neposredni številčni HP, brez rekonstrukcije iz healthbara.

Pathing actor ``+0xf18`` kaže na HitmarksAndHeadbars; native forwarderja
``0x140c42f80`` in ``0x140c42fc0`` to potrdita. AddHitmark je
``0x140cd0870``. Ring ima capacity uint32 pri ``+4``, count uint64 pri
``+0x18`` in pointer na zapise pri ``+0x20``. Zapis ima stride 24:

* ``+0`` int32 tip ID, ``+4`` int32 znesek.
* ``+8`` int32 končni client tick.
* ``+0xc`` in ``+0x10`` drugi tip in znesek, sicer -1.
* ``+0x14`` trajanje v client tickih.

Začetek je end - duration. Filtriranje uporablja 32-bitne razlike s prelivom
in isti logic clock pri stanje ``+0x528``. Render pot ``0x1403c1d50`` bere
isto šestico števil in časovni interval. Tudi že začeti zapisi ob priklopu
so lahko prvič opaženi; to ni hook v strežniški dogodek. Ring ne pove napadalca.
Surovi tip ID ostane v izhodu: vsak znesek ni avtomatično damage.

Preizkus je ujel uporabnikove vrednosti 224, 550 in 1000; primeri in hash
celotnega lokalnega zajema so v ``npc-validation-summary.json``.
Reader ponovno preveri ring, entity identiteto in seznam; spremembo seznama
poskusi enkrat ponovno. Prehodne napake ne resetirajo deduplikacije vseh
drugih NPC-jev. Izginuli NPC-ji se odstranijo iz živega prikaza.

Headbar AddHeadbar ``0x140cd0e90`` in render ``0x1403c3120`` sta dodatno
pregledana. Fill gre skozi deljenje z 255 in interpolacijo. Runtime tega
približka ne uporablja namesto natančnega HP; če HP max ni poslan, ostane null.

Telos: raziskava še odprta
-------------------------

Lokalna definicija VARBIT 32640 je bila potrjena kot VARP 6363, biti 21..23.
Pomen številk faz še ni bil potrjen v Telosovem boju. Odsotnega VARP 6363 zunaj
boja ne interpretiramo kot fazo 0.

Javno objavljeni NPC config ID-ji 22891, 22892, 22908 in 23137 so raziskovalne sledi
za različne Telosove oblike. Sekvence 28929, 28931–28935, 28938, 28942, 28950,
28952, 28959 in 28963–28966 so sledi za animacije. V našem živem bralniku še
niso potrjene in ne predstavljajo delujočega klasifikatorja napadov.

Funkcija ``0x14044d8b0`` pripada objektom na tleh in ni registracija NPCEntity.
Registracije z ``configID`` pri ``0x14046d840``, ``0x14046db20`` in ``0x14046de80``
so LocEntityProxy, LocSectionEntityProxy in NPCEntityProxy.
Ghidra izvozi z naključno izbranim sredinskim naslovom funkcije lahko dajo
neveljavno kodo; zato niso dokaz potrjenega odmika.

Preostalo preverjanje
--------------------

* Namenski preizkus odjave, menjave sveta in ponovnega zagona.
* Preslikava prijavljenega stanja, da stari podatki ne ostanejo ob odjavi.
* Preslikava NPC animacije in preverjanje na Telosu.
* Natančnejša identiteta napadalca, če bo odjemalec sploh dal tak podatek.
* Telosov boj za potrditev faz in animacijskih ID-jev.

Viri in orodja
-------------

* Uradna Ghidra: https://github.com/NationalSecurityAgency/ghidra
* Javni gameval identifikatorji kot sledi:
  https://github.com/BotWithUs/NXTDebugger/tree/c771c1d374c30373662f55776760762300097d8d
* Referenca formatov lokalnega cache-a za naš samostojni dekoder:
  https://github.com/skillbert/rsmv/tree/a92b8920b9b9842adde78822646ecad1a6f4fc15
* Uradna API dokumentacija: https://developer.runescape.com/rs
* Javni class/offset zapisi kot sledi; v našem 951-3 preverjeni z native kodo
  in živim branjem (stari odmiki med različicami niso prenosljivi na slepo):
  https://github.com/iEasyScript/reclass-data/tree/2ae22f569b529e71e97361039976e3b5c53073a3

BotWithUs agent ni nameščen. Lastni runtime ne uporablja njihovega agenta,
JNI modula ali komunikacijskega protokola.

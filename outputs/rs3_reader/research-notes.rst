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

Telos, NPC in zadetki: raziskava še odprta
---------------------------------------

Lokalna definicija VARBIT 32640 je bila potrjena kot VARP 6363, biti 21..23.
Pomen številk faz še ni bil potrjen v Telosovem boju. Odsotnega VARP 6363 zunaj
boja ne interpretiramo kot fazo 0.

Javno objavljeni NPC config ID-ji 22891, 22892, 22908 in 23137 so raziskovalne sledi
za različne Telosove oblike. Sekvence 28929, 28931–28935, 28938, 28942, 28950,
28952, 28959 in 28963–28966 so sledi za animacije. V našem živem bralniku še
niso potrjene in ne predstavljajo delujočega klasifikatorja napadov.

NPCList.All native binding pri statičnem naslovu ``0x14069c150`` registrira
callback ``0x1406cd220`` / ``0x1406cd070``. Ta prehodi indeksni seznam
pri zajeti manager ``+0xa0a0`` s count pri ``+0xb0a0``. Lookup ``0x140038c00``
uporablja tabelo pri manager ``+0x10`` in bucket count pri ``+0x18``; ključ
vozlišča je pri ``+0`` in next pri ``+0x18``. Povezava managerja z našim
preverjenim Client kontekstom še ni najdena, zato ta raziskovalna struktura
ni uporabljena v runtime dekoderju.

Funkcija ``0x14044d8b0`` pripada objektom na tleh in ni registracija NPCEntity.
Registracije z ``configID`` pri ``0x14046d840``, ``0x14046db20`` in ``0x14046de80``
so dodatne sledi za entity tipe. Njihove vloge in polja je treba še razločiti.
Ghidra izvozi z naključno izbranim sredinskim naslovom funkcije lahko dajo
neveljavno kodo; zato niso dokaz potrjenega odmika.

NPC HP in hit splati še niso dekodirani. Morebiten kvantiziran headbar se ne sme
predstaviti kot natančen HP brez potrditve podatkovnega tipa in enot.

Preostalo preverjanje
--------------------

* Namenski preizkus odjave, menjave sveta in ponovnega zagona.
* Preslikava prijavljenega stanja, da stari podatki ne ostanejo ob odjavi.
* Pridobitev NPC seznama, config ID-ja, runtime ID-ja, animacije in HP vira.
* Dogodki zadetkov s stabilno identiteto, časom in preprečevanjem podvajanja.
* Telosov boj za potrditev faz in animacijskih ID-jev.

Viri in orodja
-------------

* Uradna Ghidra: https://github.com/NationalSecurityAgency/ghidra
* Javni gameval identifikatorji kot sledi:
  https://github.com/BotWithUs/NXTDebugger/tree/c771c1d374c30373662f55776760762300097d8d
* Referenca formatov lokalnega cache-a za naš samostojni dekoder:
  https://github.com/skillbert/rsmv/tree/a92b8920b9b9842adde78822646ecad1a6f4fc15
* Uradna API dokumentacija: https://developer.runescape.com/rs

BotWithUs agent ni nameščen. Lastni runtime ne uporablja njihovega agenta,
JNI modula ali komunikacijskega protokola.

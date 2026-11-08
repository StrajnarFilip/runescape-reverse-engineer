RS3 native-memory reader in live tracker
======================================

Lokalni projekt za razumevanje podatkov odjemalca RuneScape 3.
Samostojen Python program bere pomnilnik ``rs2client.exe`` in prikazuje
HP, prayer, adrenalin in global cooldown. Podatki prihajajo iz pomnilnika;
bralnik uporablja Windows ``ctypes`` in standardno knjižnico Pythona.

Trenutno stanje
---------------

* Preverjen odjemalec: 64-bitni RS3 **951-3**, pripet na SHA-256 izvršne datoteke.
* HP in max HP: neposredni igralčevi spremenljivki.
* Prayer: dekodirano trenutno stanje; maksimum je izpeljan iz osnovnega levela.
* Adrenalin: v živem boju opažene spremembe v razponu 0–100 %.
* GCD: začetek/konec in odjemalčev števec; opaženo trajanje 90 tickov = 1,8 s.
* Tkinter tracker in JSON Lines izhod, privzeto 20 meritev na sekundo.
* NPC ime, tip ID in runtime ID ter trenutni in maksimalni HP iz številčnih statistik.
* Native hitmark ring: prejemnik, znesek, surov tip ID in čas; tracker ima zgodovino.
* 20 avtomatskih preverjanj uspešnih na lokalnem Windows sistemu.

Živo preverjeno: Goblin **550/550 HP**, Giant spider **1000/1000 HP** ter Goblin
**550 -> 326 HP** ob zadetku **224**, ki ga je uporabnik neodvisno navedel.
Zajem se ujema tudi z uporabnikovima zadetkoma 550 in 1000.
Telosove animacije in faze **še niso potrjene**.
Odjava, menjava sveta in ponovni zagon odjemalca potrebujejo dodatno namensko
preverjanje. Frekvenca branja 20 Hz ne pomeni 20 strežniških posodobitev na sekundo.

Zagon
-----

Potrebuješ 64-bitni Windows Python s Tkinterjem in podprti odjemalec igre.
Runtime ne potrebuje zunanjih Python paketov.

V korenu repozitorija::

    python outputs/rs3_reader/tracker.py

Na trenutnem razvojnem računalniku deluje tudi dvoklik na
``outputs/rs3_reader/Start Tracker.cmd``; ta zaganjalnik uporablja
``C:\Python39\pythonw.exe``.

Ukazna vrstica::

    python outputs/rs3_reader/reader.py --once --raw
    python outputs/rs3_reader/reader.py --hz 20 --duration 60 --raw --output capture.jsonl

Tracker beleži vzorce v ``outputs/rs3_reader/logs/``. V oknu lahko odpreš mapo
z dnevniki ali kopiraš zadnji JSON. Dnevnik se rotira pri približno 25 MB s tremi
dodatnimi deli na posamezen zagon. Program zahteva samo
``PROCESS_QUERY_INFORMATION | PROCESS_VM_READ``; v igro ne piše in ne pošilja tipk.

Datoteke
--------

* ``outputs/rs3_reader/winmem.py``: Windows branje pomnilnika in identiteta procesa.
* ``outputs/rs3_reader/rs3.py``: prepoznavanje struktur in dekodiranje.
* ``outputs/rs3_reader/entities.py``: NPC identiteta, številčni HP in hitmark ringi.
* ``outputs/rs3_reader/profile_951_3.json``: hash, preverjeni podpisi, relativni odmiki in ID-ji.
* ``outputs/rs3_reader/telemetry.py``: povezava, izguba procesa in ponovno povezovanje.
* ``outputs/rs3_reader/reader.py``: CLI z JSONL izhodom.
* ``outputs/rs3_reader/tracker.py``: lokalni živi prikaz.
* ``outputs/rs3_reader/README.txt``: podrobna navodila in omejitve.
* ``outputs/rs3_reader/research-notes.rst``: potrjene strukture in odprta vprašanja.
* ``outputs/rs3_reader/validation-summary.json``: povzetek dejansko zajetih meritev.
* ``outputs/rs3_reader/npc-validation-summary.json``: NPC/HP/zadetki in ujemanje z uporabnikovim preizkusom.
* ``outputs/rs3-static-inspection.json``: začetni statični pregled, pred živim bralnikom.
* ``work/*.py`` in ``work/ghidra_scripts/``: naše pomožne raziskovalne skripte.

Preverjanje
-----------

::

    python -m unittest discover -s outputs/rs3_reader -v

Testi preverjajo dejansko read-only branje lastnega procesa, napake in meje branja,
prayer bitfield, GCD pred in po izteku, preliv števca ter zamik ponovne povezave.
NPC testi pokrivajo ponovno branje istega zadetka, izginotje NPC-ja, spremembo
seznama med branjem, bucket cikel, trgan zapis ringa, manjkajoč in ničelni HP,
čas zadetka ob prelivu števca ter zgodovino ob združevanju UI osvežitev.
Živi preizkusi so ločeni od avtomatskih testov; povzetek ima izvorne hashe lokalnih
datotek zajema in izbrane dejanske vrednosti. Celotni dnevniki so lokalni.
Vizualni pregled okna prek Computer Use je bil zavrnjen z dovoljenji; ustvaritev
okna in zapisovanje živih vzorcev sta bila preverjena.

NPC tabela zajema naloženo okolico in omogoča filter imena/ID-ja; razdalje do
igralca ne izračunava. Manjkajoče HP statistike ostanejo neznane. Znesek hitmarka
je lahko tudi zdravljenje ali text mark; napadalec iz ringa ni znan. Polling
lahko izpusti zapise, ki se prepišejo ali izginejo med meritvama. Tabela zgodovine
kaže čas prvega opažanja in ne predstavlja zagotovljenega toka vseh zadetkov.
Za uvoz že opaženih zadetkov ob hkratnem živem branju::

    python outputs/rs3_reader/tracker.py --history capture.jsonl

Raziskovalna orodja
------------------

``work/bootstrap_tools.py`` prenese uradno Ghidro in JDK v ``work/tools/`` ter
preveri pripete SHA-256 hashe. Prenesena orodja niso del repozitorija.
``work/xrefs.py``, ``work/find_clock.py`` in ``work/discover_context.py`` za statično
PE analizo potrebujejo ``pefile==2024.8.26`` in ``capstone==5.0.6`` v ``work/vendor/``::

    python -m pip install --target work/vendor pefile==2024.8.26 capstone==5.0.6

``work/read_cache.py`` je samostojen read-only dekoder lokalnega SQLite predpomnilnika.
``work/decompile.ps1`` pričakuje lokalni Ghidra projekt ``work/ghidra_projects/RS3_Targeted``
z uvoženim ``rs2client.exe``. Naslove podaj kot nize, npr.::

    .\work\decompile.ps1 -Name native_vars -Addresses '140c08610','140c08db0'

Ghidra projekt, generirani C-izpisi, odjemalec igre in predpomnilnik ostanejo lokalno.
Pomožne iskalne skripte so raziskovalni prototipi; njihov rezultat je kandidat,
ne samodejno potrjen odmik za produkcijski profil.

Nadaljnje spremembe
------------------

Uporabnik je zahteval objavo dosedanjega in prihodnjega dela v tem repozitoriju.
``AGENTS.md`` ohranja ta dogovor: preverjene projektne spremembe se po končanem
delu shranijo v commit in pošljejo v ``origin``. Sprotni lokalni dnevniki in
zunanji binarni paketi so izključeni z ``.gitignore``.

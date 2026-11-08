RS3 LIVE TRACKER — lokalni bralnik pomnilnika

ZAGON
  Dvoklikni Start Tracker.cmd. Uporabi obstoječi Python C:\Python39.
  Okno že teče. Za izhod zapri okno; s tem ustaviš njegov zajem.
  Ni potrebnih dodatnih Python paketov ali native agenta.

POTRJENI PODATKI V RAZLIČICI 951-3
  HP in max HP: neposredni VARP 13537 in 13538.
  Prayer: VARP 3274, biti 0..14 / 10. UI prikazuje celoštevilski del.
  Max prayer: osnovni prayer level iz igralčeve tabele spretnosti * 10;
    to je izpeljana vrednost, ne ločeno prebran maksimum prayerja.
  Adrenalin: VARP 679 / 10, v živem boju opažen razpon 0..100 %.
  GCD: VARC 2091 in 2092, primerjana z odjemalčevim logic clockom.
    V živih zajemih pet začetkov potrdilo trajanje 90 tickov * 20 ms.

  NPC-ji: ime, config ID (tip), runtime ID (primerek), current/max HP.
  HP prihaja iz številčne statistike NPC-ja, ne iz približka healthbara.
  Preverjeno: Goblin 550/550, Giant spider 1000/1000 in Goblin 550 -> 326
    ob uporabnikovem zadetku 224. Ujeti tudi uporabnikovi zadetki 550 in 1000.
  Hit splati: surov tip ID, znesek, prejemnik, začetek/konec client tickov.
  Tracker ima filter in zgodovino; ranjeni NPC-ji so prikazani pred zdravimi.
  Telosove faze in animacije še niso potrjene.

PREVERLJIVOST
  Meritve gredo v logs/capture-DATUM-CAS-PID.jsonl pri 20 Hz.
  Tracker rotira dnevnik pri približno 25 MB in ohrani tri dodatne dele.
  Vsaka vrstica ima čas, zaporedno številko, PID, hash izvršne datoteke,
  dekodirane in surove vrednosti ter trajanje branja.
  Gumba v oknu odpreta mapo z dnevniki oziroma kopirata zadnji JSON.
  Koda je v tej mapi. OpenProcess zahteva le QUERY_INFORMATION | VM_READ.
  Nobene funkcije v igri ne kličemo, vanjo ne pišemo in ne pošiljamo tipk.
  Podatki ne prihajajo iz slik. Program nima omrežnega strežnika.

OMEJITVE
  Preverjen je le SHA-256:
    fd8afd32f9829b1fb0a953b238c504e75526b7af5e2920120aa027777a358d45
  Ob drugem hashu ali drugačnih preverjenih podpisih se branje zavrne.
  Ob napaki branja, izgubi procesa ali ustavljenem zajemu so polja neznana.
  Menjava sveta in odjava še potrebujeta namensko preverjanje. Sam obstoj
  procesa in premikanje odjemalčevega števca ne dokazujeta prijave v svet.
  20 Hz pomeni pogostost branja, ne pogostosti posodobitev s strežnika.
  Čas zadnje strežniške posodobitve ni znan. last_change_age_ms pomeni
  čas od zadnje opažene spremembe vrednosti, ne starosti strežniških podatkov.
  Trenutni bralnik ni atomski posnetek celotnega procesa.
  NPC seznam pomeni naloženo okolico; tabela ne meri razdalje do igralca.
  NPC-ji brez poslane HP statistike imajo null / not_transmitted, ne nič HP.
  Znesek hit splata je lahko tudi zdravljenje ali ničelni/text mark.
  Napadalec iz tega zapisa ni znan; prikazan NPC je prejemnik zadetka.
  Polling lahko izpusti zapis, če je prepisan ali odstranjen med meritvama.
  newly_observed pomeni prvič opaženo, ne zagotovljen tok vseh zadetkov.

UKAZNA VRSTICA
  C:\Python39\python.exe reader.py --once --raw
  C:\Python39\python.exe reader.py --hz 20 --duration 60 --raw --output capture.jsonl

PREVERJANJE
  C:\Python39\python.exe -m unittest discover -s . -v
  Dvajset avtomatskih preverjanj: Windows read-only pomnilnik, meje branja,
  GCD in overflow, prayer bitfield, ponovna povezava, NPC identiteta/izginotje,
  spremenjen seznam, bucket cikli, trgan hitmark zapis, deduplikacija in zgodovina.
  Živi preizkusi so zajeli spremembe HP-ja, adrenalina in pet sprožitev GCD-ja.
  NPC živ preizkus je v npc-validation-summary.json z dejanskimi primeri.

  Za prikaz že zajetih zadetkov poleg novih živih meritev:
    python tracker.py --history POT_DO_PREJSNJEGA_ZAJEMA.jsonl

  Samodejni pregled dovoljenj je zavrnil vizualni pregled okna pythonw.
  Okno se je ustvarilo in dejanski zajem ter dnevniki so bili preverjeni.

# ODDS-ROOM

Orodja za kanal **The Odds Room**.

| Datoteka | Namen |
|---|---|
| `start.html` | Javna stran: kako kanal deluje. |
| `index.html` | Matchday Scanner (API-Football, ključ v brskalniku). |
| `app/` | **Odds Room Analytics**: napovedi, rang lestvica, value, igralci, dnevnik, generator objav. |
| `pipeline/` | Python koda, ki zbira podatke in računa modele. |

## Odds Room Analytics

### Kaj prikaže
- **Tekme**: nogomet (16 evropskih lig + Brasileirão + Liga prvakov, Evropska liga, Konferenčna liga), NHL, NBA, Euroliga za 7 dni. Pričakovani goli/točke, najverjetnejši izid,
  porazdelitev skupnih golov, vsi trgi z verjetnostjo, fer kvoto in prednostjo proti kvoti.
- **Lestvica**: vsi dogodki (ekipe + igralci), razvrščeni po verjetnosti. Filter po verjetnosti in trgu.
- **Value**: dogodki, kjer je kvota višja od fer kvote modela.
- **Igralci**: nogomet (Understat: streli/90, xG/90, xA/90), NHL (streli na gol, točke), NBA (točke, skoki, podaje).
  Za naslednjo tekmo: verjetnost npr. 1+ strel v okvir, 3+ SOG, več kot X točk.
- **Postave in poškodbe**: odsotni igralci, potrjene postave (~1 h pred tekmo); model prilagodi moč ekipe in napovedi igralcev.
- **Dnevnik**: model sam zabeleži value pick pred tekmo in ga po tekmi zaključi. Profit, ROI, kalibracija.
- **Objave**: besedilo za Threads/Instagram (Tip of the Day, Value Pick, Goals Galore, Lockdown, Trap + FLIP, Bold Play, Acca/Builder).

### Viri (brezplačni)
| Šport | Vir | Kaj |
|---|---|---|
| Nogomet | football-data.co.uk | rezultati, streli, koti, kartoni, kvote (1X2, O/U 2.5), tekme tega tedna |
| Nogomet | ESPN | Brasileirão, Liga prvakov, Evropska liga, Konferenčna liga; potrjene postave |
| Nogomet | Fantasy Premier League | poškodbe in kazni (Premier League) |
| Nogomet | Understat | igralci top 5 lig: minute, streli, xG, xA, ključne podaje |
| NHL | ESPN + NHL stats API | razpored, rezultati, kvote; statistika igralcev in ekip |
| NBA | ESPN | razpored, rezultati, kvote, box score igralcev, poškodbe |
| Euroliga | EuroLeague API | razpored in rezultati |
| (opcija) | The Odds API | dodatne kvote, če nastaviš ključ `ODDS_API_KEY` |
| (opcija) | API-Football | poškodbe za vse lige, če nastaviš ključ `API_FOOTBALL_KEY` |

### Modeli
- **Nogomet in NHL**: Poissonov model moči napada/obrambe + prednost domačega igrišča. Novejše tekme
  imajo večjo težo; prejšnja sezona šteje pol. Za nogomet še Dixon-Coles popravek za nizke izide.
  Isti model za kote, kartone in strele v okvir.
- **Košarka**: normalni model napad/obramba (ridge regresija), raztros iz ostankov.
- **Igralci**: stopnja na 90 min (nogomet) ali na tekmo (NHL), prilagojena nasprotniku in domu,
  Poissonova porazdelitev. NBA: povprečje in raztros zadnjih 15 tekem.

- **Evropska tekmovanja**: en Poissonov model za LP, EL in KL. Ekipa začne z oceno iz domače lige
  (k = 0.6 domače ocene) + jakost lige (iz UEFA koeficientov, raztegnjena ×1.5), ridge 15 omeji vpliv malega
  vzorca pokalnih tekem.
  Domače ocene: football-data.co.uk (16 lig) + ESPN (Avstrija, Norveška, Danska, Švedska, Švica, Češka, Ciper,
  Izrael, Romunija, Irska); za klube iz ostalih lig se uporabi jakost njihove lige.
- **Varovalka trga**: če se model pri 1X2 ali O/U 2.5 od Pinnacla brez marže (sicer od ESPN ali povprečja
  stavnic) razlikuje za več kot 10 o. t., je trg označen »model nezanesljiv« in se ne beleži kot pick.
  Kjer je Pinnacle na voljo, so verjetnosti 0.4 model + 0.6 Pinnacle.

Model ne ve za motivacijo in vse novice. Vedno preveri novice pred objavo.

### Backtest
`python -m pipeline.backtest` (ali workflow *Backtest*): walk-forward log-loss in Brier za pokalni model
(staro proti novo), Championship proti Pinnaclu in kalibracija NHL pickov. Poročilo: `backtest_report.md`.

### Nastavitev (enkrat)
1. **Workflow** *Update data* teče vsakih 30 minut. Vsakih ~6 ur naredi polno osvežitev (vsi viri),
   vmes hitro (tekme blizu, postave, poškodbe). Stran objavi na veji `gh-pages`.
   Ročni zagon: *Actions → Update data → Run workflow* (mode: auto / full / quick).
2. **GitHub Pages**: *Settings → Pages → Deploy from a branch → **gh-pages** / (root)*.
   Stran je na `https://<uporabnik>.github.io/ODDS-ROOM/app/`.
3. **(Opcija) poškodbe za vse nogometne lige**: ključ API-Football (brezplačno 100 klicev/dan) kot secret
   `API_FOOTBALL_KEY`. Porabi ~16 klicev na dan.
4. **(Opcija) kvote za NHL/NBA/Euroligo**: brezplačen ključ na the-odds-api.com, nato
   *Settings → Secrets and variables → Actions → New secret* `ODDS_API_KEY`.
   Kvote prenese enkrat na dan: okoli 6 kreditov na dan, 180 na mesec (`ODDS_API_SPORTS` določi športe).

### Lokalno
```bash
pip install -r requirements.txt
python -m pytest -q tests
python -m pipeline.build            # vse
python -m pipeline.build --sports football
cd app && python -m http.server     # odpri http://localhost:8000
```

18+ · Ocene modela, ne obljube.

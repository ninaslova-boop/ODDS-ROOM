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
- **Value**: p_final × povprečna kvota (samo za orientacijo).
- **bet365**: seznam trgov za preverjanje (min. kvota = 1 / p_final), vnos kvote in linije bet365, PICK / NO PICK, lasten dnevnik v napravi.
- **Igralci**: nogomet (Understat: streli/90, xG/90, xA/90), NHL (streli na gol, točke), NBA (točke, skoki, podaje).
  Za naslednjo tekmo: verjetnost npr. 1+ strel v okvir, 3+ SOG, več kot X točk.
- **Postave in poškodbe**: odsotni igralci, potrjene postave (~1 h pred tekmo); model prilagodi moč ekipe in napovedi igralcev.
- **NHL vratarja**: ocena verjetnega vratarja (največ začetih tekem; na 2. tekmi v 2 dneh pogosto rezervni) in njegova obramba proti povprečju ekipe spremeni gole nasprotnika (največ ±10 %).
- **Dnevnik**: pick velja, ko je p_final × kvota bet365 > 1.00 (◆ pri ≥ 1.10, brez drugih oznak). Samodejno samo s pravo kvoto bet365 (football-data.co.uk). Profit, ROI, kalibracija vseh ocen in samo pickov, poročilo po w.
- **Backtest**: walk-forward po tekmovanjih (Brier, zadetki proti pričakovanim, ROI pri zaključnih kvotah). Tekmovanje brez backtesta je »ni validirano«.
- **Za Claude** (gumb zgoraj desno): povzetek izbranega dne (navodilo za analizo, odprti picki, value kandidati,
  model proti trgu, nezanesljivi trgi, postave, poškodbe, igralci, stanje dnevnika). Kopiraj in prilepi v Claude.
- **Objave**: besedilo za Threads/Instagram (Pick, Acca/Builder, Trap + FLIP, Lineup alert). Brez oznak in stopenj.

### Viri (brezplačni)
| Šport | Vir | Kaj |
|---|---|---|
| Nogomet | football-data.co.uk | rezultati, streli, koti, kartoni, kvote (1X2, O/U 2.5), tekme tega tedna |
| Nogomet | ESPN | Brasileirão, Liga prvakov, Evropska liga, Konferenčna liga; potrjene postave |
| Nogomet | Fantasy Premier League | poškodbe in kazni (Premier League) |
| Nogomet | Understat | igralci top 5 lig: minute, streli, xG, xA, ključne podaje |
| NHL | ESPN + NHL stats API | razpored, rezultati, kvote; statistika igralcev in ekip |
| NBA | ESPN | razpored, rezultati, kvote, box score igralcev, poškodbe |
| Euroliga | EuroLeague API (api-live.euroleague.net) | razpored, rezultati; samo tekoča sezona: box score igralcev, povprečje sezone in zadnjih 5 (točke, skoki, podaje, 3P, T+S+P), statistika ekip; odsotnost = stalni igralec izpusti zadnji 2 tekmi; brez sezone → "player data: none" |
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
- **Domače lige**: ridge po ligi iz backtesta (prve lige 8–10, druge lige 15).
- **Varovalka trga**: če se model pri 1X2 ali O/U 2.5 od Pinnacla brez marže (sicer od ESPN ali povprečja
  stavnic) razlikuje za več kot 10 o. t. na kateremkoli glavnem trgu, je CELA tekma »model nezanesljiva«: noben pick, ni v »zanesljivih« seznamih.
- **Mešanje s trgom (vsi športi)**: p_final = w × model + (1 − w) × trg brez marže. w = 0.4 (nastavitev po športu,
  `MARKET_W_<ŠPORT>`), tanek trg (< 4 stavnice, npr. samo ESPN) w = 0.6. Parametri modela se prilagodijo, zato vsaka
  linija (tudi 177.5 namesto 178.5) dobi p_final iz iste porazdelitve.
- **Ekipe**: ESPN ekipe po ID-ju, imena samo strogo (ime ali alias, brez ugibanja). Tekma, pri kateri se viri ne
  ujemajo (čas ± 15 min, obe ekipi), gre v DATA MISMATCH. Ročne preverjene povezave: `pipeline/team_ids.json`.
- **Počitek**: 2. tekma v 2 dneh (NHL, NBA) in dvojni krog (Euroliga) iz podatkov sezone, z velikostjo vzorca v izvozu.
  Enako za NHL in NBA (izid in skupaj proti ESPN kvotam brez marže).
  Kjer je Pinnacle na voljo, so verjetnosti 0.4 model + 0.6 Pinnacle.

Model ne ve za motivacijo in vse novice. Vedno preveri novice pred objavo.

### Validacija
`python -m pipeline.validate` (workflow *Validate models*, vsak ponedeljek): `data/validation.json`, stran Backtest.

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

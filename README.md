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
- **Tekme**: nogomet (16 lig), NHL, NBA, Euroliga za 7 dni. Pričakovani goli/točke, najverjetnejši izid,
  porazdelitev skupnih golov, vsi trgi z verjetnostjo, fer kvoto in prednostjo proti kvoti.
- **Lestvica**: vsi dogodki (ekipe + igralci), razvrščeni po verjetnosti. Filter po verjetnosti in trgu.
- **Value**: dogodki, kjer je kvota višja od fer kvote modela.
- **Igralci**: nogomet (Understat: streli/90, xG/90, xA/90), NHL (streli na gol, točke), NBA (točke, skoki, podaje).
  Za naslednjo tekmo: verjetnost npr. 1+ strel v okvir, 3+ SOG, več kot X točk.
- **Dnevnik**: model sam zabeleži value pick pred tekmo in ga po tekmi zaključi. Profit, ROI, kalibracija.
- **Objave**: besedilo za Threads/Instagram (Tip of the Day, Value Pick, Goals Galore, Lockdown, Trap + FLIP, Bold Play, Acca/Builder).

### Viri (brezplačni)
| Šport | Vir | Kaj |
|---|---|---|
| Nogomet | football-data.co.uk | rezultati, streli, koti, kartoni, kvote (1X2, O/U 2.5), tekme tega tedna |
| Nogomet | Understat | igralci top 5 lig: minute, streli, xG, xA, ključne podaje |
| NHL | ESPN + NHL stats API | razpored, rezultati, kvote; statistika igralcev in ekip |
| NBA | ESPN | razpored, rezultati, kvote, box score igralcev |
| Euroliga | EuroLeague API | razpored in rezultati |
| (opcija) | The Odds API | dodatne kvote, če nastaviš ključ |

### Modeli
- **Nogomet in NHL**: Poissonov model moči napada/obrambe + prednost domačega igrišča. Novejše tekme
  imajo večjo težo; prejšnja sezona šteje pol. Za nogomet še Dixon-Coles popravek za nizke izide.
  Isti model za kote, kartone in strele v okvir.
- **Košarka**: normalni model napad/obramba (ridge regresija), raztros iz ostankov.
- **Igralci**: stopnja na 90 min (nogomet) ali na tekmo (NHL), prilagojena nasprotniku in domu,
  Poissonova porazdelitev. NBA: povprečje in raztros zadnjih 15 tekem.

Model ne ve za poškodbe, postave in motivacijo. Vedno preveri novice pred objavo.

### Nastavitev (enkrat)
1. **Zaženi workflow**: GitHub → *Actions* → *Update data* → *Run workflow*. Ko konča, je v `app/data/` JSON.
   Po združitvi v `main` teče samodejno 3× na dan (05:40, 11:40, 17:40 UTC).
2. **GitHub Pages**: *Settings → Pages → Deploy from a branch → main / (root)*.
   Stran je na `https://<uporabnik>.github.io/ODDS-ROOM/app/`.
3. **(Opcija) kvote za NHL/NBA/Euroligo**: brezplačen ključ na the-odds-api.com, nato
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

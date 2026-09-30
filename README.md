# SSE Energy — HACS integrácia pre Home Assistant

Custom integrácia, ktorá **vnútri Home Assistantu** sťahuje dáta o spotrebe elektriny
zo **Stredoslovenskej energetiky** (portál eZona) a zobrazí ich v **Energy dashboarde**
(electricity grid) aj ako entity. Prihlasuje sa do SSE priamo (obíde F5 WAF cez
`curl_cffi`, ktorý sa nainštaluje automaticky ako requirement). Žiadny MQTT ani
externý server netreba.

**Všetky ceny a sadzby ťahá naživo z API** — nič nie je zadané natvrdo
(VT/NT kWh, náklady, preplatok/nedoplatok).

## Inštalácia cez HACS

1. HACS → ⋮ → **Custom repositories** → pridaj URL tohto repozitára, typ **Integration**.
2. Nájdi **„SSE Energy"** → **Download** → reštartuj Home Assistant.
3. Settings → Devices & Services → **Add Integration** → **SSE Energy**.
4. Zadaj email a heslo. Integrácia si z API vytiahne tvoje odberné miesta — ak ich
   máš viac, jedno vyberieš zo zoznamu (žiadny EAN sa neopisuje ručne).

Bez HACS: skopíruj `custom_components/sse_energy/` do `config/custom_components/` a reštartuj.

## Čo vznikne

Zariadenie **„SSE Energy"** s entitami (čítané priamo z portálu eZóna):
- **Ceny** VT/NT (€/kWh) — **automaticky z API** (aktuálna zmluvná cena × DPH)
- **Tento mesiac** (odhad z 15-min dát + zmluvné ceny): spotreba total/VT/NT, náklady
- **Včera**: spotreba total/VT/NT
- **Zúčtovací rok** (oficiálne od SSE, autoritatívne): spotreba total/VT/NT + náklady silová/distribúcia/**spolu**
- **Financie**: na úhradu, preplatok, posledné vyúčtovanie (suma/obdobie/splatnosť), posledná platba, zaplatené (3 r.), **výška zálohy** (€ / frekvencia)
- **Zmluva/distribúcia**: produkt (DD5), distribučná tarifa (D5), istič (A), fázy, sériové č. meradla, číslo zmluvy, cyklus
- **Diagnostika**: platnosť tokenu, posledná aktualizácia, posledný deň dát, stav, výpadok portálu

A **dlhodobé štatistiky** pre Energy dashboard (presné hodinové stĺpce, lebo SSE dáta
meškajú 1–2 dni): `sse_energy:grid_consumption`, `…_vt`, `…_nt`, `sse_energy:grid_cost`,
`…_cost_vt`, `…_cost_nt`.

> Energetické ceny sa berú zo `delivery-point` API, distribúcia sa **kalibruje** z oficiálnych
> ročných nákladov (`consumption` API) a oficiálne ročné sumy idú priamo z API. Nič sa
> nezadáva natvrdo — jediné, čo API nevracia, je **DPH** (23 %) a **VT/NT hodiny** tarify.

## Energy dashboard

Settings → **Energy** → *Grid consumption* → **Add consumption**:

- **jeden zdroj**: štatistika **„SSE Grid consumption"** (`sse_energy:grid_consumption`),
  náklady **„SSE Grid cost"** (`sse_energy:grid_cost`), alebo
- **VT a NT zvlášť** (dashboard potom ukazuje drahú a lacnú sadzbu oddelene): dva zdroje,
  **„SSE Grid consumption VT"** s nákladmi **„SSE Grid cost VT"** a **„SSE Grid consumption NT"**
  s **„SSE Grid cost NT"**. Nepridávaj popri nich aj celkový zdroj — spotreba by sa rátala dvakrát.

Merané spotrebiče (zásuvky, svetlá) patria do **Individual devices**, nie medzi zdroje zo
siete — SSE ich spotrebu už obsahuje.

> Štatistiky sa napĺňajú automaticky pri každej aktualizácii (predvolene každých 6 h).
> Nový rad sa naplní od začiatku zúčtovacieho roka; každý ďalší beh **prepíše posledných
> 30 dní** (SSE najprv zverejní predbežné dáta a neskôr ich finalizuje) a doplní nové hodiny.
> Keďže SSE dáta meškajú 1–2 dni, dnešok je v Energy dashboarde zo siete prázdny.

## Nastavenia (Configure)

Ceny sa nenastavujú — ťahajú sa z API. Po pridaní klikni **Configure** len pre to,
čo API nevracia: **DPH** (násobiteľ, default `1.23` = 23 %), **VT hodiny** (prázdne =
odvodené z tarify, napr. DD5 → `0,1,10,15`), mena, interval aktualizácie a zapnutie/
vypnutie importu štatistík.

## Poznámky

- **Architektúra**: HA OS beží na Alpine/musl; `curl_cffi` má musllinux wheels pre
  amd64/aarch64, takže sa nainštaluje automaticky. Na netypickej architektúre, kde
  wheel nie je k dispozícii, sa integrácia nemusí načítať.
- **Bezpečnosť**: heslo sa ukladá do config entry HA; v logoch sa nezobrazuje.
- **Výpočty**: `kWh = round(Σ kW / 4, 2)` (zaokrúhľovanie polovice nahor cez `Decimal`).
  Distribúcia sa kalibruje ako €/kWh z posledného oficiálneho roka (`distribúcia ÷ kWh`);
  ceny energie sú z API × DPH. Keď API cenu nevráti, cenové entity sú „unavailable"
  (spotreba beží ďalej) — nič sa nevymýšľa. Pozri `tests/test_tariff.py`.

## Testovanie

Dve úrovne:

- **Čistá logika** (`test_portal.py`, `test_tariff.py`) — bez Home Assistantu, beží kdekoľvek:
  ```bash
  pip install pytest && pytest tests
  ```
- **HA config/options flow + coordinator** (`test_config_flow.py`, `test_coordinator.py`)
  — používajú `pytest-homeassistant-custom-component`. HA sa importuje len na **Linuxe**
  (`fcntl`/`resource`), takže na Windowse sa tieto testy preskočia; spusti ich vo
  WSL / Docker / CI:
  ```bash
  pip install pytest-homeassistant-custom-component && pytest tests
  ```

CI (`.github/workflows/test.yml`) púšťa obe úrovne na Linuxe pri každom pushi.

# Corridor explorer (Berth / Signal page)

The **Signal Mappings** page (`/signal-mappings`) includes a corridor explorer that shows
trains, observed berth movements and signal evidence between two stations, e.g.
Faversham → Ramsgate, split by direction.

## URL parameters

| Parameter      | Meaning                                                                      |
|----------------|------------------------------------------------------------------------------|
| `from_station` | Name, CRS, TIPLOC or STANOX (e.g. `Faversham`, `FAV`)                        |
| `to_station`   | As above                                                                     |
| `direction`    | `both` (default), `up`, `down`, `a_to_b`, `b_to_a`                           |
| `date`         | Timetable date `YYYY-MM-DD` (default today) used to filter CIF schedules     |

Example: `/signal-mappings?from_station=Faversham&to_station=Ramsgate&direction=down`

Ambiguous names (e.g. a substring matching several stations) show a list of choices; unknown
names show a message. Nothing here raises if tables or data are missing.

## Data requirements and what each source contributes

| Source | Tables | Used for |
|--------|--------|----------|
| CORPUS | `corpus_locations` | Resolving names / CRS / TIPLOC / STANOX |
| CIF    | `cif_schedules`, `cif_schedule_locations` | Which services call at/pass both stations (ordered locations), headcodes, times, travel direction |
| SMART  | `smart_berths` | Tagging berths to corridor stations by STANOX |
| TD     | `td_berth_movements`, `td_state` | Observed berth steps and live/last-seen trains |
| Signals | `physical_signal_mappings`, `td_sclass_movement_scores`, `topology_edges` | Signal evidence and reconstructed links |

## Inference limitations

* **CIF alone does not determine** physical berth order, track connectivity, signal placement
  or direction. It only provides the timetable context (which trains, in which order, at what time).
* Corridor berths are those SMART tags to the corridor's stations. Berths between stations
  without SMART entries only appear if an observed movement links them to a tagged berth. Gaps
  are left visible rather than filled in.
* Historical movement rows do not retain a train UID. They are assigned a direction only when
  their headcode maps to one direction on the chosen date; if several schedules share that
  headcode, the direction may be known while the schedule identity remains explicitly ambiguous.
  Matches in both directions remain "unresolved direction".
* Live trains use the stored train UID when it matches an active corridor schedule. Without a UID,
  a live row is attached only when exactly one loaded active corridor schedule has that headcode;
  otherwise it is shown as unresolved instead of being copied to every matching schedule.
  Observations from a different date are not matched to the selected timetable date.
* **Up/Down** is labelled only for station pairs listed in `KNOWN_DIRECTION_HINTS`
  (`nrod_railhub/corridor.py`; currently Faversham ↔ Ramsgate, Down = away from London). For other
  pairs directions are shown as "A → B" / "B → A" and no Up/Down assumption is made.
* Signal rows are labelled by status/confidence: physical mappings carry their verification status;
  S-class correlations and topology edges are **inferred** and shown with confidence and source.
* When evidence is insufficient the page lists the gaps and still shows whatever schedule/live data exists.

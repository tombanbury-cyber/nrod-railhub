# Signalling topology reconstruction

`RailDB` stores topology as typed nodes and directed edges. Node types include
berth, signal, route, points, track section, and transition, so a confirmed or
manually supplied path can describe
`berth → signal → route → points/track section → next berth`.

## Evidence and verification

Each edge has a confidence in `[0, 1]`, a provenance, a verification status,
and zero or more supporting evidence records. Reconstructed candidates link
back to normalized berth movements, S-class correlation scores, or physical
signal mapping revisions. Candidate edges have status `inferred`; reviewers
can promote or reject an individual edge with `RailDB.set_topology_edge_status`.
Confirming a physical signal identity does not automatically confirm the
topology edge that uses it.

Different destinations or hypothesis labels remain separate edges. Rebuilds
replace only inferred edges produced by reconstruction and preserve confirmed,
rejected, and manually supplied edges. The deterministic node and edge
identifiers, plus unique evidence source keys, make repeated historical
reprocessing idempotent while allowing newly observed evidence to be added.

## Rebuilding and traversing

Call `RailDB.rebuild_signalling_topology(td_area=None)` after berth movement or
S-class correlation data is available. The operation can be repeated after new
observations or physical mappings are added, or scoped to one TD area.

The web API supports:

- `POST /api/signalling-topology/rebuild` with `{"area": "EK"}` (area optional)
- `GET /api/signalling-topology?area=EK` for the structured graph
- `GET /api/signalling-topology?start=EK:berth:0152&depth=5` to traverse outgoing
  edges while retaining all alternatives
- `PATCH /api/signalling-topology/edges/{edge_id}` with `verification_status`
  (`confirmed`, `rejected`, or `inferred`) and optional reviewer/notes

The traversal response includes nodes, edge status, confidence, provenance,
hypothesis labels, and evidence details. The database methods
`add_topology_node` and `add_topology_edge` allow additional confirmed or
inferred layout details to be added, including points and track sections.

## Limitations

Observed berth transitions create candidate route nodes and route-to-berth
edges. S-class correlations and reviewed physical signal mappings add candidate
signal relationships; they do not prove interlocking logic or physical track
connectivity. Points and track sections are not synthesized from current feed
data because the feeds do not establish those relationships. Missing evidence
therefore remains an incomplete path rather than being filled with an assumed
connection. Competing mappings and route hypotheses are exposed together and
must be reviewed rather than silently resolved by confidence ranking.

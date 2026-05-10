# agd-memory benchmarks

Benchmark per l'**addressable memory layer** del plugin
[agd-memory](../README.md). La domanda non è "AGD batte markdown sui
tokens": è **"quando posso indirizzare un blocco di memoria per
nome, quanto risparmio rispetto al caricarmi tutto?"**

Quattro misure complementari:

1. **Token shipped** (S0–S5): quanti token finiscono nel prompt.
2. **Backlink fan-in** (S6): quando il graph esplode contro di te.
3. **Soldi reali** (S7): chiamate Anthropic API vere, con prompt
   cache, su Haiku 4.5.
4. **Latenza CLI**: il costo di parsing di `agd` rispetto a I/O.

I numeri vengono dall'esecuzione effettiva. Niente formule, niente
mock: `agd` lanciato come subprocess, stdout tokenizzato; per S7,
`messages.create` chiamata vera, `usage` letto dalla risposta.

## TL;DR

Su un corpus sintetico di 200 blocchi (~18k token whole-doc):

| metrica | numero | dove |
|---|---:|---|
| TOC + 1 blocco vs whole-doc, in token | **7,9× meno** | §S1 |
| 10 agenti shared-TOC vs naive, in token | **45,7× meno** | §S2 |
| Filtro `--kind x-project`, in token | **25,1× meno** | §S4 |
| Selective vs whole-doc cached, in **dollari reali** (5 turni Haiku) | **2,15× meno** | §S7 |
| Backlink explosion: anchor con 200 fan-in | **81% del file** | §S6 |

**Tre cose da dire chiare prima di citare i numeri:**

1. **Sul file reale** (31 blocchi, MDF2) il rapporto TOC/whole è
   **6,4×**, non 8×. I sintetici hanno varianza zero — il reale ha
   `desc=` lunghi che gonfiano il TOC. §S0.
2. **Token ≠ soldi.** S3 dice 3,4× a token grezzi; con prompt cache
   reale il vantaggio scende a **2,15×** in $ (§S7). Comunica i
   numeri token solo quando hai detto "non incluso prompt cache".
3. **Multi-agente non è gratis.** Il 45,7× di S2 richiede che il
   parent orchestri esplicitamente le slice. Subagent Claude Code
   di default girano "independent" (~7,5×), non "shared" (45×).

## S0 — sintetico vs reale

| corpus | blocchi | whole-doc | TOC | rapporto |
|---|---:|---:|---:|---:|
| sintetico mem-50 | 50 | 4.446 | 541 | **8,2×** |
| reale memory.agd MDF2 | 31 | 4.790 | 748 | **6,4×** |

Il sintetico è ~28% più ottimista. Quando si pubblica un numero su
file reali, **5–7×** è la fascia onesta a parità di size.

## S1 — agente singolo, quattro size

| blocchi | whole-doc | TOC | TOC + 1 | TOC + 3 | rapporto TOC | rapporto TOC+3 |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 915 | 104 | 172 | 381 | 8,8× | 2,4× |
| 50 | 4.446 | 541 | 650 | 770 | 8,2× | 5,8× |
| 200 | 18.245 | 2.233 | 2.313 | 2.535 | 8,2× | 7,2× |
| 1.000 | 90.428 | 11.152 | 11.246 | 11.394 | 8,1× | **7,9×** |

Più grande il file, più conviene la lettura selettiva. A 1k blocchi
prendere un blocco specifico costa lo 0,18% in più del solo TOC.

## S2 — N agenti paralleli

Corpus 200 blocchi. Ogni agente prende 1–3 blocchi a caso.

| agenti | naive | independent | shared TOC | shared vs naive |
|---:|---:|---:|---:|---:|
| 1 | 18.245 | 2.346 | 2.346 | 7,8× |
| 3 | 54.735 | 7.247 | 2.781 | 19,7× |
| 5 | 91.225 | 12.086 | 3.154 | 28,9× |
| 10 | 182.450 | 24.093 | 3.996 | **45,7×** |
| 20 | 364.900 | 48.217 | 5.790 | **63,0×** |

**Nota architetturale.** "Shared TOC" = il padre legge il TOC una
volta e dispatcha ai figli solo le slice di blocchi che servono.
**Non è il default di Claude Code.** Lanciando `Agent` tool ognuno
ha il proprio contesto e fa retrieval indipendente — quindi sei
sulla riga "independent" (7,5×), non "shared" (45×). Per arrivare a
45× serve scrivere apposta l'orchestrazione parent-figli.

## S3 — sessione multi-turn (token grezzi)

20 turni sullo stesso corpus 200, ogni turno 1–2 blocchi diversi.

| turni | whole-doc tenuto | selective cumulativo | per-turn medio | rapporto token |
|---:|---:|---:|---:|---:|
| 20 | 18.245 | 5.297 | 153 | **3,4×** |

**In token grezzi** selective vince 3,4×. **In dollari reali** vince
2,15× (vedi §S7). Cita sempre la metrica giusta per il contesto.

## S4 — filtro per `--kind`

| contesto | kinds | whole-doc | TOC scoped | rapporto |
|---|---|---:|---:|---:|
| blog-write | `x-user,x-feedback` | 18.245 | 1.157 | 15,8× |
| code-review | `x-feedback,x-reference` | 18.245 | 1.340 | 13,6× |
| project-status | `x-project` | 18.245 | 726 | **25,1×** |

Filtro per categoria = modo più economico per scoprare una sessione
prima ancora di entrare nel contenuto.

## S5 — addressable memory: cosa vuol dire pagare l'indirizzabilità

Il pitch di AGD non è "sostituire markdown". È: **se serve poter
chiamare un blocco per nome e ottenerlo isolato dal resto, qual è
il costo di quella capacità?**

Stesso contenuto del corpus 200, tre rappresentazioni:

| formato | whole-doc | id-list | fetch selettivo | indirizzabile? |
|---|---:|---:|---:|:---:|
| **Markdown** | **14.342** | — | 14.342 (= whole) | ✗ |
| **AGD** | 18.245 | 2.233 | 2.340 | ✓ |
| **XML** | 20.163 | 2.229 | 2.345 | ✓ |

**Lettura corretta.** Markdown è il **27% più piccolo** sul
whole-doc — è il formato vincente *se* la memoria è abbastanza
piccola da starci tutta in contesto e non hai bisogno di
indirizzarla. Sopra una certa size questo non è più vero, e
markdown collassa: senza ID per blocco, l'unica strategia è
caricare tutto.

AGD e XML sono entrambi indirizzabili. La differenza tra i due è
del **10,5% sul whole-doc** (markup tag) e **trascurabile sul
fetch selettivo** (~5 token su ~2.300). Il vantaggio di AGD su XML
in tokens è marginale — la differenza vera è in **scrittura
umana**: prefisso di riga + ID tra parentesi quadre vs aprire e
chiudere tag.

**Quindi: AGD non vince contro markdown. Vince contro "memoria non
indirizzabile". Markdown smette di essere un'alternativa quando il
file diventa troppo grande per stargli dentro.**

## S6 — backlink explosion

`agd get '#anchor' --with-backlinks` restituisce l'anchor più tutti
i blocchi che dichiarano `refs="#anchor"`. Pattern utile, ma scala
linearmente nel fan-in. Cosa succede su un anchor "popolare"?

| inbound refs | anchor da solo | anchor + backlinks | fan-in vs anchor | % del whole-doc |
|---:|---:|---:|---:|---:|
| 5 | 33 | 505 | 15× | **3%** |
| 50 | 33 | 4.804 | 146× | **26%** |
| 200 | 33 | 19.246 | 583× | **81%** |

A 200 backlink stai praticamente caricando il file intero via la
porta di servizio. Il vantaggio "selettivo" è scomparso.

**Implicazione pratica.** Per anchor con fan-in alto serve un
meccanismo `--limit` o paginazione (oggi assente). Senza, il pattern
"scope query con `--with-backlinks`" diventa pericoloso man mano
che la memoria cresce e certi blocchi diventano hub.

## S7 — soldi reali con Anthropic API

`scripts/bench_cost.py` fa chiamate API vere a `claude-haiku-4-5`
con prompt caching attivo, registra `cache_creation_input_tokens`,
`cache_read_input_tokens`, `input_tokens`, `output_tokens` dal
campo `usage` della risposta, e li prezza al listino pubblico
(input $1/M, cache write $1,25/M, cache read $0,10/M, output $5/M).

5 turni sullo stesso corpus 200, stesse domande in entrambe le
strategie:

### Strategia A — whole-doc cached

| turno | input | cache write | cache read | output | $ |
|---:|---:|---:|---:|---:|---:|
| 1 | 45 | **20.041** | 0 | 120 | $0,0257 |
| 2 | 45 | 0 | 20.041 | 119 | $0,0026 |
| 3 | 45 | 0 | 20.041 | 120 | $0,0026 |
| 4 | 45 | 0 | 20.041 | 120 | $0,0026 |
| 5 | 45 | 0 | 20.041 | 120 | $0,0026 |
| **tot** | 225 | 20.041 | 80.164 | 599 | **$0,0363** |

Latenza media: **1,85 s/turno**.

### Strategia B — selective uncached

| turno | input | output | $ |
|---:|---:|---:|---:|
| 1 | 2.923 | 66 | $0,0033 |
| 2 | 2.922 | 98 | $0,0034 |
| 3 | 2.882 | 86 | $0,0033 |
| 4 | 2.931 | 119 | $0,0035 |
| 5 | 2.876 | 97 | $0,0034 |
| **tot** | 14.534 | 466 | **$0,0169** |

Latenza media: **1,87 s/turno**.

### Verdetto onesto

| dimensione | whole-doc cached | selective uncached | rapporto |
|---|---:|---:|---:|
| token shipped (visibili al modello) | 100.205 | 14.534 | 6,9× |
| **dollari reali** | $0,0363 | $0,0169 | **2,15×** |
| latenza media | 1,85 s | 1,87 s | ≈ |

Il prompt cache **claws back ~70%** del vantaggio in token: in $
selective vince ancora ma di poco più del doppio, non di un fattore
sette.

**Crossover.** Estrapolando linearmente (write una volta, reads
costanti, selective lineare in N), su questo corpus la strategia
A diventa più economica della B intorno al **turno ~30**, *a patto
che la cache resti calda*. La cache TTL default è 5 minuti: se
intervalli tra turni superano i 5 min, A paga di nuovo il write
(~$0,025) e B torna a vincere indefinitamente.

**Latenza.** Identica entro il rumore. Per Haiku 4.5 a queste size
di prompt il tempo è dominato dalla generazione output, non dal
processing input. Su Sonnet/Opus o su contesti molto più grandi
(>50k token) la differenza si vedrebbe.

## Concurrency model — cosa intende AGD per "multi-agent safe"

Per evitare aspettative sbagliate dopo aver visto S2:

- **Atomic writes**: ogni edit è scritto su file temporaneo +
  rinominato. File mai corrotto a metà.
- **`flock` advisory**: scritture concorrenti serializzate via
  lock di sistema operativo. Non c'è merge, c'è coda.
- **Last-write-wins per ID**: due agenti che scrivono lo stesso
  blocco — l'ultimo che ottiene il lock vince. Il primo viene
  perso senza conflict marker.

Quello che AGD **non** è:

- ✗ CRDT
- ✗ merge automatico tra versioni divergenti
- ✗ distributed consistency
- ✗ vector clock / causal ordering

Se serve coordinare K agenti che modificano stessi blocchi
contemporaneamente, AGD da solo non basta — serve uno scheduler a
monte.

## Latenza del CLI

Mediana di 20 esecuzioni sul corpus 1.000 blocchi (~310KB):

| operazione | tempo |
|---|---:|
| `agd ids` | 22,5 ms |
| `agd ids --kind` | 21,4 ms |
| `agd get` 1 blocco | 21,0 ms |
| `cat` (I/O baseline) | 16,7 ms |

`agd` aggiunge ~5 ms di parsing sopra l'I/O. Trascurabile davanti
a una chiamata LLM (1–5 s).

## Limiti noti

- **Corpora sintetici**. Body da pool fisso di 8 stringhe lorem.
  Varianza ~30% inferiore al reale. I rapporti sono ottimistici di
  pari misura.
- **`tiktoken` ≠ tokenizer Claude**. Proxy stabile in rapporto, non
  in conteggio assoluto. S7 usa l'API: lì i numeri sono esatti.
- **S7 su Haiku 4.5**. Su Sonnet/Opus le ratio sarebbero diverse:
  costi base più alti rendono ogni token risparmiato più prezioso.
  Su modelli senza prompt caching la strategia A perde sempre.
- **Multi-agente simulato (S2)**. Conta token come job indipendenti.
  Nessuna simulazione di overhead framework, dispatch latency,
  serializzazione subagent.
- **XML idealizzato (S5)**. Schema compatto. HTML reale (`<div
  class="block">`) sarebbe ~30% più verboso e farebbe peggiorare
  ulteriormente il confronto contro AGD.
- **Backlink explosion non mitigata (S6)**. `agd` non ha ancora
  `--limit` né paginazione: il pattern `--with-backlinks` non
  protegge contro hub ad alto fan-in.

## Riprodurre

```sh
cd benchmarks
pip install tiktoken anthropic

# scenari token-only (S0-S6, latenza)
python3 scripts/generate.py --out corpora/mem-200.agd --blocks 200 --seed 42
python3 scripts/generate.py --out corpora/anchor-fanin-050.agd \
  --blocks 200 --anchor-inbound 50 --seed 42
python3 scripts/bench.py --real-memory ~/path/to/your/memory.agd

# scenario costo reale (S7) — ~$0,05 per run con i default
export ANTHROPIC_API_KEY=sk-ant-...
python3 scripts/bench_cost.py --turns 5

cat results/summary.json results/cost.json
```

Tutti i corpora sintetici sono regenerabili identici (`--seed 42`).

## Layout

```
benchmarks/
├── README.md                  # questo file
├── corpora/                   # .agd sintetici, deterministici
│   ├── mem-{10,50,200,1000}.agd
│   └── anchor-fanin-{005,050,200}.agd
├── results/
│   ├── summary.json           # output S0-S6 + latenza
│   └── cost.json              # output S7 (Anthropic API)
└── scripts/
    ├── generate.py            # generatore corpus
    ├── bench.py               # token-accounting harness
    └── bench_cost.py          # real-money harness
```
